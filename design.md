# Design: Indian Supply Chain Agentic RAG Chatbot

## 1. Overview & Goals

**What we're building:** A Postgres-backed agentic RAG chatbot that acts as a domain expert on Indian supply chains — sourcing raw materials and moving them. Primary users: procurement managers, logistics planners, and internal analysts, all served by one unified agent persona.

**Core behavior:** For any procurement/movement query, the agent checks whether it has (a) the material's internal SKU code and (b) an unambiguous origin-destination + transport mode. If either is missing or ambiguous, it asks a targeted clarifying question before answering — it does not answer generically. Once it has both, it grounds its answer in retrieved knowledge (regulatory docs, logistics network data, supplier data) plus live tool calls (real-time material prices, web search, route/distance lookups), and can produce a chart, table, or map as part of the response.

**Non-goals for v1:**
- No multi-tenant isolation (single-org internal tool)
- No Hindi/regional language support (English only, but language handling stays pluggable)
- No live/inline chart rendering in a custom UI — visuals are generated as exportable files (PNG/HTML/PDF)

**Key technical commitments:**
- Postgres + pgvector as the single datastore for both RAG embeddings and structured domain data (SKU master, HSN mapping, supplier directory, logistics network reference)
- LangGraph for agent orchestration — explicit state graph so the "always clarify missing fields" rule is enforced structurally, not just prompted
- LLM-provider-agnostic: Gemini is the active provider for v1 (both generation and embeddings — see Section 5 for exact models), with Ollama wired in as a local/offline fallback; Groq, OpenAI, and Anthropic adapters follow the same interface and can be enabled by config alone
- Tools: web search, real-time commodity/material pricing APIs, distance/routing (OSRM), chart generation, Leaflet-based map export

## 2. Architecture

```
                     ┌─────────────────────────────┐
                     │        User (chat)          │
                     └──────────────┬──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │   LangGraph Agent Runtime    │
                     │  (orchestration + state)     │
                     └──────────────┬──────────────┘
                                    │
        ┌───────────────┬──────────┼──────────┬───────────────┐
        │               │          │          │               │
 ┌──────▼─────┐  ┌───────▼──────┐ ┌▼────────┐ ┌▼───────────┐ ┌─▼──────────┐
 │ Slot-Fill/  │  │ RAG Retriever│ │  LLM    │ │  Tool Layer │ │  Output    │
 │ Clarify Node│  │ (pgvector)   │ │ Provider│ │ (web search,│ │  Renderer  │
 │             │  │              │ │ Router  │ │ price APIs, │ │ (charts,   │
 │             │  │              │ │         │ │ OSRM, etc.) │ │ maps, PDF) │
 └──────┬──────┘  └──────┬───────┘ └────┬────┘ └──────┬──────┘ └─────┬──────┘
        │                │              │              │              │
        └────────────────┴──────────────┴──────────────┴──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │         PostgreSQL           │
                     │  ─ pgvector (RAG chunks)     │
                     │  ─ SKU / HSN master          │
                     │  ─ Supplier directory        │
                     │  ─ Logistics network ref     │
                     │  ─ Conversation/session state│
                     └───────────────────────────────┘
```

**Components:**
- **LangGraph Agent Runtime** — the state graph. Nodes: `parse_intent` → `check_required_slots` (SKU code, origin/destination + mode) → either `ask_clarifying_question` (loop back to user) or `retrieve_and_reason` → `select_tools` → `call_tools` → `synthesize_answer` → `render_output`.
- **LLM Provider Router** — thin abstraction (`LLMProvider` interface) with adapters for Gemini, Groq, OpenAI, Anthropic; picks provider from config, falls back on rate-limit/failure.
- **RAG Retriever** — pgvector similarity search over chunked regulatory/logistics/supplier docs, filtered by metadata (doc type, region, material category).
- **Tool Layer** — stateless callables the agent invokes (Section 4).
- **Output Renderer** — generates chart/table/map artifacts as files and returns a link/path in the chat response.
- **Postgres** — single datastore, described in Section 3.

## 3. Postgres Schema

**RAG store (pgvector):**
```sql
documents(
  id UUID PK,
  source_type TEXT,        -- 'regulatory' | 'logistics_network' | 'supplier' | 'general'
  title TEXT,
  source_url TEXT,
  region TEXT,              -- state/national, nullable
  material_category TEXT,   -- nullable, for filtering
  ingested_at TIMESTAMPTZ
)

document_chunks(
  id UUID PK,
  document_id UUID FK -> documents,
  chunk_text TEXT,
  embedding VECTOR(768),    -- 768 = Gemini gemini-embedding-001 output, truncated (Matryoshka) from its native 3072 dims
  chunk_index INT
)
-- HNSW or IVFFlat index on embedding
```

**Domain / structured data:**
```sql
sku_master(
  sku_code TEXT PK,          -- internal/custom SKU
  material_name TEXT,
  material_category TEXT,
  hsn_code TEXT,              -- optional cross-reference, nullable
  unit_of_measure TEXT
)

suppliers(
  id UUID PK,
  name TEXT,
  location_city TEXT,
  location_state TEXT,
  lat DOUBLE PRECISION,
  lon DOUBLE PRECISION,
  materials_supplied TEXT[],  -- or normalized junction table
  certifications TEXT[]
)

logistics_nodes(
  id UUID PK,
  node_type TEXT,             -- 'port' | 'rail_terminal' | 'warehouse' | 'icd'
  name TEXT,
  lat DOUBLE PRECISION,
  lon DOUBLE PRECISION,
  state TEXT
)

transport_benchmarks(
  id UUID PK,
  origin_node_id UUID FK,
  destination_node_id UUID FK,
  mode TEXT,                  -- 'road' | 'rail' | 'air' | 'sea' | 'multimodal'
  distance_km NUMERIC,
  typical_transit_days NUMERIC,
  source TEXT,
  updated_at TIMESTAMPTZ
)
```

**Conversation/session state:**
```sql
sessions(id UUID PK, user_id TEXT, started_at TIMESTAMPTZ)

messages(
  id UUID PK,
  session_id UUID FK,
  role TEXT,                  -- 'user' | 'assistant' | 'tool'
  content TEXT,
  slots_filled JSONB,          -- tracks sku_code, origin, destination, mode as gathered
  created_at TIMESTAMPTZ
)
```

`slots_filled` on the messages/session row is how the clarify-loop tracks what's already known across turns, so the agent doesn't re-ask something the user already gave.

Note: `sku_master` is seeded from the org's SKU/HSN mapping table (provided externally) — the design assumes that import happens as a one-time/periodic ETL, not live lookup.

## 4. Tools

Each tool is a plain function the LangGraph agent calls; results get fed back into `synthesize_answer`.

**1. Web search** — general fallback for anything not covered by a dedicated API (news, regulation changes, one-off supplier lookups). Use a search API with clean structured results (e.g. Tavily or Brave Search) rather than scraping raw HTML.

**2. Real-time material pricing** — a small registry of per-category price fetchers, since India's commodity data is fragmented across sources:
```
price_tools = {
  'metals':     MCX/LME fetcher,
  'steel':      SteelMint / JPC fetcher,
  'agri':       Agmarknet / eNAM fetcher,
  'chemicals':  ICIS fetcher (if licensed) or web_search fallback,
  'default':    web_search fallback
}
```
Router picks the fetcher by `material_category` from `sku_master`; falls back to web search if no dedicated source or the call fails.

**3. Distance/routing** — OSRM (self-hosted or public instance) for road distance/time; static `transport_benchmarks` table for rail/air/sea where real-time routing APIs don't apply well. Returns distance_km, estimated transit time, and a polyline/route geometry for map rendering.

**4. Chart/table generation** — Python: matplotlib/plotly for charts, saved as PNG; pandas → styled HTML/PDF for tables. Triggered when the agent's answer involves a comparison (e.g. price trends, multi-supplier comparison, multi-mode cost/time tradeoff).

**5. Map generation** — Folium (Leaflet wrapper) to plot supplier/route/node locations, exported as standalone HTML. Used for origin-destination visualizations, supplier proximity maps.

**6. RAG retrieval** — not user-facing as a "tool call" but structurally the same pattern: query → pgvector similarity search → filtered by metadata → top-k chunks returned to the LLM context.

All tool outputs are logged against the session for traceability (which source backed which claim in the answer).

## 5. LLM Provider Layer

A single `LLMProvider` interface (`generate(messages, tools, **kwargs) -> response`) with adapters per provider:

```
providers/
  gemini_provider.py    # active — generation + embeddings
  ollama_provider.py    # active — local fallback (offline dev, no API key needed)
  groq_provider.py      # supported, not configured
  openai_provider.py    # supported, not configured
  anthropic_provider.py # supported, not configured
```

Config (env var or config file) picks the active provider and a fallback order, e.g. `primary: gemini, fallback: [ollama]`. On rate-limit or error from the primary, the router retries with the next provider in the chain.

Tool-calling format differs slightly per provider (OpenAI-style function calling vs Anthropic tool_use vs Gemini function declarations) — the adapter layer normalizes LangGraph's tool schema into each provider's expected format on the way in, and normalizes the response back into a common `ToolCall` structure on the way out. This is the main complexity cost of multi-provider support; it's isolated entirely inside the adapters so the LangGraph graph logic stays provider-agnostic.

### 5.1 Active model configuration (v1)

Both roles run on Gemini for v1 — one provider, one API key, simplest setup. Config lives in `config.yaml` (checked in, no secrets) layered with `.env` (gitignored, holds keys); env vars override file values.

| Role | Config key | Default value | Notes |
|---|---|---|---|
| Generation | `llm.generation.provider` | `gemini` | Chat/reasoning/tool-calling model driving the LangGraph graph |
| Generation | `llm.generation.model` | `gemini-3.6-flash` | Current GA flash model; `gemini-2.5-flash` also works but is being retired Oct 2026 — don't build new default configs against it |
| Embedding | `llm.embedding.provider` | `gemini` | Used only at ingest time and query time for pgvector similarity search |
| Embedding | `llm.embedding.model` | `gemini-embedding-001` | GA text embedding model |
| Embedding | `llm.embedding.dimensions` | `768` | Truncated from the model's native 3072 via the API's `output_dimensionality` param (Matryoshka representation — truncating loses little quality and keeps the HNSW index smaller/faster). Must match `document_chunks.embedding VECTOR(768)` in Section 3 exactly |
| Fallback | `llm.fallback.provider` | `ollama` | Local, no API key — used if Gemini errors/rate-limits. Requires a model pulled locally (e.g. `ollama pull llama3.1`); not pulled yet in this environment |
| Fallback | `llm.fallback.base_url` | `http://localhost:11434` | Ollama's default local endpoint |

**Embeddings are not part of the generation fallback chain.** Switching embedding provider/model changes the vector space, so every existing `document_chunks` row would need re-embedding — this is a deliberate, explicit migration, never an automatic runtime fallback. If Ollama is ever promoted to the embedding role (e.g. `nomic-embed-text`, 768-dim — a coincidental dimension match with the current Gemini default, not a guarantee for other Ollama embedding models), the whole corpus must be re-embedded and the `VECTOR(N)` column width re-verified.

`GEMINI_API_KEY` is read from `.env` (already present in this project). `OLLAMA_BASE_URL` is optional and only needed if Ollama runs somewhere other than localhost:11434.

## 6. Output & Export Handling

Every response that includes a visual produces a file under a session-scoped output directory (`outputs/{session_id}/{artifact_id}.{ext}`), and the chat message references it by path/link rather than inlining binary data. Supported artifact types: PNG (charts), HTML (Leaflet maps, styled tables), PDF (multi-section reports, on request — built by combining chart PNGs + tables via a simple HTML→PDF step). No live rendering UI in v1 — this stays file-based and works with any chat frontend.

## 7. Error Handling

- **Tool failure** (API down, rate-limited, malformed response): caught at the tool-call node, logged, and either retried with the fallback provider in the same category (e.g. price fetcher falls back to web search) or surfaced to the user as "couldn't fetch live X, here's what I have from cached/RAG data" rather than failing the whole response.
- **LLM provider failure**: handled by the provider router's fallback chain (Section 5); a full chain exhaustion returns a clear error to the user rather than a silent hang.
- **Ambiguous/conflicting slots** (e.g. user gives two different SKU codes in one message): the clarify node treats this the same as a missing slot — asks the user to confirm which one, rather than guessing.
- **RAG retrieval empty/low-confidence**: if no chunks clear a similarity threshold, the agent says so explicitly instead of fabricating a regulatory/logistics claim; it can still answer using tool data (e.g. live price) if that's independently available.

## 8. Testing Strategy

- **Unit tests**: each tool function (price fetchers, OSRM wrapper, chart/map renderers) tested in isolation with mocked API responses.
- **Slot-filling/clarify logic**: table-driven tests feeding partial `slots_filled` states into `check_required_slots` to confirm the right clarifying question (or none) is produced.
- **Provider adapters**: contract tests verifying each adapter normalizes tool-call requests/responses to the common schema identically, so the graph logic behaves the same regardless of active provider.
- **RAG retrieval**: fixture corpus with known documents, asserting expected chunks are retrieved above threshold for representative queries (HSN lookup, transport regulation, supplier search).
- **End-to-end**: scripted conversations covering the core flow (missing SKU → clarify → provided → price + chart returned) and the moving-goods flow (missing route/mode → clarify → provided → distance/time + map returned).

## 9. Deployment & Extensibility

- **Deployment**: single-org internal tool — one Postgres instance (with pgvector extension enabled), no tenant isolation needed. Simple auth (SSO or basic login) at the app layer, not modeled in the schema for v1.
- **Extensibility hooks left in the design**: language layer is pluggable (translation/detection step could sit before `parse_intent` without touching the rest of the graph); LLM provider layer already supports adding providers without graph changes; new price-fetcher sources slot into the `price_tools` registry by category; new document types slot into `documents.source_type` without schema changes.

## 9A. Data Sources — what actually makes the agent smart

The model supplies reasoning; the *data* supplies the expertise. With an empty database the agent can only produce plausible-sounding generalities. Sources below are ordered by impact per unit of effort.

### Tier 1 — internal data (highest impact, only you have it)

| Data | Table | Why it matters |
|---|---|---|
| SKU/HSN master | `sku_master` | Without it, no SKU resolves to a material, so the price router always falls through to `default` and every answer is generic. This is the single highest-value import. |
| Supplier directory | `suppliers` | Enables "who can supply X near Y" — the questions a procurement manager actually asks. Needs lat/lon to support proximity and map output. |
| Historical rate cards / past PO data | *(new table)* | The strongest possible grounding: what *you* actually paid per lane and per material. Turns "market rate is ~₹55k/t" into "you paid ₹52.3k/t on this lane last quarter." Not in the v1 schema — worth adding. |
| Plant/warehouse locations | `logistics_nodes` | Lets origin/destination resolve to your own network rather than raw city geocoding. |

### Tier 2 — public reference data (free, bulk-loadable)

- **Indian Railways freight tariff & rake data** (Freight Operations Information System, IR published tariffs) → fills `transport_benchmarks` for rail, the mode currently unsupported by live routing.
- **Major/minor port distances & turnaround times** (Sagarmala, IPA port statistics) → sea leg benchmarks.
- **Agmarknet / eNAM daily mandi prices** — bulk CSV downloads, genuinely free, ideal for agri categories.
- **CBIC HSN/GST rate tables + customs tariff** → grounds duty and tax questions, which are pure regulatory lookups where hallucination is most costly.
- **E-way bill rules, state border/checkpost regulations** (GST portal circulars) → the compliance questions that block shipments in practice.
- **India Post / OSM administrative boundaries + pincode-to-district mapping** → disambiguates the "Chennai matches 2 nodes" case in the clarify loop.

### Tier 3 — commercial/licensed (highest fidelity, paid)

SteelMint / BigMint, JPC (Joint Plant Committee) for steel; MCX/LME for metals; ICIS or Platts for chemicals and polymers. These are what turn the price tool from "web snippets with a caveat" into a benchmark rate you can quote in a negotiation. Each slots into the `price_tools` registry by category without touching the graph.

### Building the knowledge base (implemented)

`scai/ingest.py` is the load → chunk → embed → store pipeline. Run it from the project root with the venv active:

```bash
# one file
python -m scai.ingest --file docs/eway_rules.pdf --type regulatory --title "E-Way Bill Rules"

# a directory, recursively (.pdf/.txt/.md); title inferred per file
python -m scai.ingest --dir knowledge_base/regulatory --type regulatory

# a web page
python -m scai.ingest --url https://example.gov.in/circular --type regulatory --title "Circular 12/2026"

# optional retrieval filters, and overwriting a previous ingest of the same source
... --region Maharashtra --category steel --replace
```

`--type` is one of `regulatory | logistics_network | supplier | general` and maps to `documents.source_type`; `--region`/`--category` populate the metadata columns the retriever filters on.

Behaviour worth knowing:
- **Chunking** splits on paragraph boundaries toward a ~1200-char target with 150-char overlap, so a fact straddling a boundary stays retrievable from either side. Oversized paragraphs are hard-split rather than allowed to blow past the target.
- **Re-ingesting the same source is refused** unless `--replace` is passed — an accidental double ingest would otherwise silently double that document's weight in every future retrieval. `--replace` deletes the old row and its chunks cascade with it.
- **Embedding is batched** (32 chunks/request) against the model in §5.1, and one failed file does not abort the rest of a directory run.
- **Scanned PDFs produce no text** and fail loudly with a message pointing at OCR — they need an OCR pass before ingestion.

**The manifest is the provenance record.** `knowledge_base/sources.yaml` lists every ingested source with its type and title; rebuild the whole store with one command:

```bash
python -m scai.ingest --manifest knowledge_base/sources.yaml --replace
```

Currently ingested (152 chunks across 9 sources, all primary/official publishers):

| Area | Source | Type |
|---|---|---|
| GST / e-way bill | NIC-GSTN official FAQ + portal advisories | `regulatory` |
| Road transport | MoRTH axle-load / GVW notification (PIB) | `regulatory` |
| Customs & trade | DGFT *Customs Import & Export Procedures* (PDF) | `regulatory` |
| National policy | National Logistics Policy — PIB, PMO, Invest India | `logistics_network` |
| Maritime | Ports & shipping sector overview (Invest India) | `logistics_network` |
| Rail | Indian Railways commodity classification | `logistics_network` |

Selection rule: **primary publishers only** (`gov.in` domains and official promotion bodies). Commercial aggregators and SEO blogs are excluded — their figures are frequently stale or wrong, and the agent cites whatever it retrieves. Two lessons learned the hard way while building this are worth preserving:

- **Never ingest model-written prose.** A sample document written to test the pipeline outranked the official NIC/GSTN FAQ (0.796 vs 0.761) for e-way bill queries. Synthetic content competing with primary sources defeats the purpose of grounding; it was removed from the manifest.
- **Freshness is not automatic.** Statutory thresholds change. Ingested text is a snapshot from its ingest date — re-run with `--replace` periodically, and treat the live portal as authoritative for anything legally binding. This is exactly why regulatory/tariff queries also route to live web search (§9B).

Recommended layout, one subdirectory per `source_type`, so each can be ingested with the right flag:

```
knowledge_base/
  regulatory/        # GST circulars, e-way bill rules, customs tariff notes
  logistics_network/ # rail tariffs, port handbooks, corridor studies
  supplier/          # supplier profiles, certifications, capability docs
```

A sample document (`knowledge_base/regulatory/eway_bill_basics.md`) is included to exercise the pipeline; it is marked as sample data and should be replaced with authoritative sources before operational use.

### What goes in RAG vs. what goes in tables

The split matters: **prose goes in `documents`/`document_chunks`** (regulations, circulars, policy PDFs, supplier certifications — anything you'd want to quote or cite). **Numbers go in structured tables** (prices, distances, transit times, capacities). Embedding a rate card is a common and expensive mistake — vector similarity retrieves *approximately*, and an approximately-correct freight rate is worse than none.

## 9B. Web Search Integration

Implemented in `scai/tools/web_search.py` using **Tavily** — chosen over raw scraping or a generic search API because it returns clean, LLM-ready snippets with source URLs, needs no HTML parsing, and has a usable free tier. Requires `TAVILY_API_KEY` in `.env`.

It serves two distinct roles:

1. **Price fallback** (`scai/tools/pricing.py`) — every category currently resolves here, since no licensed price feed is wired up. Results are returned with an explicit `caveat` field marking them as unstructured web data rather than a benchmark rate, so the synthesis prompt cites them with a source instead of asserting them as settled fact.
2. **Live regulatory/news lookup** — `select_tools` routes queries mentioning regulation, GST, e-way bills, duty, tariffs, compliance, or "latest" to web search directly, because this class of information changes faster than any ingested corpus and a stale RAG answer is actively harmful.

Failures degrade rather than propagate: a missing key or empty result raises `ToolUnavailableError`, which `call_tools` catches and passes to the LLM as an `error` field. The agent then says the live lookup failed and falls back to labelled general knowledge (design.md Section 7), instead of silently inventing a number.

## 10. UI/UX Design System

*Genre: modern-minimal (technical/dev-tool register) · Theme: Cobalt-derived — cool engineered light canvas, one electric-blue signal accent, hairline structure, data/code-as-hero. Picked for the "Utilitarian" tone: an internal tool used all day by procurement/logistics staff needs calm precision and fast scanning, not marketing flourish. This is an application shell, not a landing page — Hallmark's macrostructure catalog (hero/nav/footer archetypes) doesn't apply; the structural discipline below (tokens, layout, component states, motion) governs the app shell instead.*

Scope: full app shell — session/history sidebar + conversation thread + composer + artifact rendering — per the confirmed scope decision. This section is a design spec (tokens, layout, component states, motion), not implementation code; it's what any future frontend build (Section 9 "no live rendering UI in v1" aside — this governs the *chat surface itself*, which does exist in v1) should conform to.

### 10.1 Design tokens

```css
:root {
  /* Paper / surfaces — cool engineered near-white, never pure #fff */
  --color-paper:      oklch(98.5% 0.004 250);   /* page background */
  --color-paper-2:     oklch(97%   0.006 250);   /* sidebar, recessed panels */
  --color-graphite:    oklch(22%   0.016 260);   /* dark band: tool-status / artifact code view */

  /* Ink — cool charcoal, never pure #000 */
  --color-ink:          oklch(24% 0.02  258);    /* headings, primary text */
  --color-ink-2:        oklch(34% 0.018 257);    /* body / message text */
  --color-ink-3:        oklch(52% 0.014 258);    /* muted / timestamps / placeholders */

  /* Structure */
  --color-rule:         oklch(88% 0.008 255);    /* hairline borders, light surfaces */
  --color-rule-2:       oklch(38% 0.02  258);    /* hairline borders, on graphite */

  /* The one signal */
  --color-accent:       oklch(58% 0.20 256);     /* electric cobalt — buttons, active state, focus ring */
  --color-accent-ink:   oklch(98% 0.01 250);     /* text/icon on accent fill */

  /* Semantic (kept in the cool family except error, which must stand out) */
  --color-success:      oklch(62% 0.15 152);     /* slot filled, tool call resolved */
  --color-warning:      oklch(72% 0.15 80);       /* clarifying-question state */
  --color-error:        oklch(58% 0.20 25);        /* failed send, tool failure, conflicting slot */

  /* Type */
  --font-display: "Space Grotesk", sans-serif;    /* 500/600 — section heads, sidebar wordmark */
  --font-body:    "Inter", sans-serif;             /* 400/500 — message text, UI labels */
  --font-mono:    "JetBrains Mono", monospace;     /* SKU/HSN codes, coordinates, tool-call payloads, status chips */

  /* Spacing — 4pt scale */
  --space-3xs: 0.125rem; --space-2xs: 0.25rem; --space-xs: 0.5rem;
  --space-sm: 0.75rem;   --space-md: 1rem;     --space-lg: 1.5rem;
  --space-xl: 2.5rem;    --space-2xl: 4rem;

  /* Radii — tight technical, never soft pills, never brutalist 0px */
  --radius-control: 6px;   /* buttons, inputs, chips */
  --radius-card:    10px;  /* message panels, artifact cards */

  /* Motion */
  --dur-micro: 120ms; --dur-short: 220ms; --dur-long: 420ms;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in:  cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);

  --z-sticky: 200; --z-modal: 400; --z-toast: 500;
}
```

Fonts load via `next/font` or a single Google Fonts link (Space Grotesk 500/600, Inter 400/500, JetBrains Mono 400/500). Accent is used sparingly — the send button, the active session marker, focus rings, resolved-state ticks, filled-slot chips. Everything else stays ink-on-cool-paper.

### 10.2 Layout — app shell

```
┌──────────┬──────────────────────────────────────────┐
│ Sidebar  │  Conversation Thread                       │
│ (paper-2)│  (paper)                                   │
│          │  ┌────────────────────────────────────┐    │
│ + New    │  │ assistant / user message stream     │    │
│ session  │  │ (bubbles, tool-status rows,          │    │
│          │  │  clarify banners, artifact cards)    │    │
│ session  │  └────────────────────────────────────┘    │
│ list     │  ┌────────────────────────────────────┐    │
│ (scroll) │  │ Slot Tracker: SKU · Route · Mode     │    │
│          │  ├────────────────────────────────────┤    │
│          │  │ Composer (auto-grow, Send)           │    │
│          │  └────────────────────────────────────┘    │
└──────────┴──────────────────────────────────────────┘
```

- Sidebar: fixed 280px, `position: sticky`, `--color-paper-2` background, 1px `--color-rule` right border. Collapses to an off-canvas drawer under 768px (§10.5).
- Thread column: `max-width: 42rem` (a measure that keeps long procurement answers readable), centered in the remaining space — not the full viewport width, so the page doesn't read as a bare full-bleed textarea.
- Composer is `position: sticky; bottom: 0`, 1px `--color-rule` top border, `--z-sticky`.
- No card-in-card: the thread column itself has no border/shadow; only artifact cards and the clarify banner get a visible edge.

### 10.3 Core components

**Session/history sidebar row** — plain list item, no card elevation. Active session: 2px `--color-accent` left rule (not a background fill — keeps the signal sparing). Hover: background tints to `--color-paper` over `--dur-short` `--ease-out`. Timestamps in `--color-ink-3`, relative ("2h ago").

**Message bubble** — user messages: right-biased, `--color-paper-2` fill, `--radius-card`, no border. Assistant messages: left-biased, no fill, no border — reads as the "expert" voice against the plain page, distinguished by a small mono `SUPPLY CHAIN AGENT` label above the first line of each turn, not a repeated avatar. Body copy in `--font-body`, `--color-ink-2`; any SKU code, HSN code, coordinate, or price figure inline in the text is set in `--font-mono` at the same size — the mono switch is the "this is a precise data value" signal, doing the work a highlight color would otherwise do.

**Streaming response** — content renders progressively as tokens arrive from the backend (functional, not decorative — it mirrors real generation latency, so it's exempt from the "no typewriter effects" rule). A 1ch-wide block caret blinks (`steps(2)`, 1s, per the microinteractions caret-blink recipe) only at the end of the in-progress line, removed the instant the stream completes. No fade/stagger layered on top.

**Clarifying-question banner** — this is the product's core behavior (design.md §1) and gets its own visual treatment, not a generic bubble. Appears inline as part of the assistant's turn: 2px `--color-warning` left rule, `--color-paper-2` fill, `--radius-card`. States the specific missing/ambiguous field(s) as removable chips (e.g. `SKU CODE — missing`, `ORIGIN — ambiguous: "Chennai" matches 2 nodes`). Enter: `opacity 0→1` + `translateY(6px→0)`, `--dur-long`, `--ease-out` — this state needs to register, unlike routine content, so it gets the slower/more deliberate entrance. No modal, no dismiss button — it resolves itself when the user answers.

**Slot Tracker** — persistent chip row above the composer, mono labels, mirroring `messages.slots_filled` (design.md §3) live: `SKU`, `ROUTE`, `MODE`. Three states per chip:
  - *empty* — dashed `--color-rule` outline, `--color-ink-3` label.
  - *filled* — solid `--color-rule` outline, `--color-ink` label, small accent check glyph. Transition: dashed→solid + `transform: scale(0.98→1)`, `--dur-micro`, `--ease-out` — a confirmation tick, not a toast (silent-success principle: the chip visibly changing state is the feedback).
  - *conflicting* — `--color-error` outline + inline icon, per the "ambiguous slots" error-handling rule in §7.

**Tool-call status row** — appears above the in-progress assistant turn while `select_tools` / `call_tools` run. Mono, UPPERCASE, `0.06em` tracking, e.g. `RETRIEVING · logistics_network`, `CALLING · agmarknet price api`, `ROUTING · osrm`. Active row: three-dot opacity pulse (`0.3→1→0.3`, 1.2s, `linear` — the one sanctioned use of linear easing, functional loader). On resolution: 150ms crossfade into either the result or an inline `--color-error` failure chip with a retry affordance. Sequential tool calls stack as a short vertical trail, each resolved row staying visible at one line — this is the traceability requirement from §4 made visible, not just logged.

**Artifact card (chart / table / map)** — hairline-bordered (`--color-rule`, `--radius-card`), never a fake window-bar/traffic-light chrome (real download/expand icons only — no re-drawn OS chrome). Header row: type glyph + mono uppercase label (`CHART`, `TABLE`, `MAP`) + right-aligned download/expand icon buttons. Footer (optional): data source + timestamp, muted mono. Reveal: `opacity 0→1` + `translateY(8px→0)`, `--dur-long`, `--ease-out`, fired once when the artifact finishes rendering — functional (it's the actual completion event), not a decorative page-load stagger. Expand opens a native `<dialog>` at `--z-modal`: scale `0.96→1` + opacity, `--dur-long`/`--ease-out` in, `220ms`/`--ease-in` out.

**Composer** — auto-growing textarea (content-driven height, instant, not eased), `--radius-control`, `--color-rule` border that shifts to `--color-accent` on focus (`--dur-short`, `--ease-out` — confirms the field is live before typing starts). Send button: solid `--color-accent` fill, `--radius-control`, disabled (`--color-ink-3`, no fill) when empty. Press: `scale(0.98)` in 100ms, release 150ms, per the button-press recipe. On send: the message appends to the thread optimistically (instant, no spinner on the bubble) — silent success. On failure: no corner toast (the failure is local and visible) — the sent bubble itself switches to an inline error state, `--color-error` text: "Couldn't send · Retry", since the effect (or lack of it) is already in view.

**Empty state** (new session) — no illustration, no invented imagery. One line of `--color-ink-2` body copy plus 3–4 example-query chips in `--font-mono` the user can click to pre-fill the composer (real onboarding affordance, not decoration).

**Loading skeleton** (session list on first load) — hairline-bordered placeholder rows, opacity pulse `0.5↔0.8` over 1.4s `--ease-in-out`, capped — no shimmer-gradient sweep (reads as a dated AI-generic tell).

### 10.4 Motion summary

| Element | Trigger | Change | Duration | Easing |
|---|---|---|---|---|
| User message send | submit | optimistic append, `translateY(6px→0)` + opacity | `--dur-short` | `--ease-out` |
| Streaming caret | token in-flight | `steps(2)` blink at line end | 1s loop | linear |
| Clarify banner | assistant turn needs a slot | slide + fade in | `--dur-long` | `--ease-out` |
| Slot chip fill | slot resolved | dashed→solid + scale tick | `--dur-micro` | `--ease-out` |
| Tool-status pulse | tool call active | opacity 0.3→1→0.3 loop | 1.2s | linear |
| Tool-status resolve | tool call returns | crossfade to result/error | 150ms | `--ease-out` |
| Artifact reveal | render complete | opacity + `translateY(8px→0)` | `--dur-long` | `--ease-out` |
| Artifact expand | click expand | `<dialog>` scale 0.96→1 + opacity | `--dur-long` in / 220ms out | `--ease-out` / `--ease-in` |
| Send button press | pointerdown/up | `scale(0.98)` | 100ms/150ms | `--ease-in`/`--ease-out` |
| Sidebar drawer (mobile) | menu toggle | `translateX(-100%→0)` + backdrop fade | 300ms/220ms | `--ease-out`/`--ease-in` |

Never more than three concurrent animation primitives on screen at once (streaming caret + one status pulse + one entrance is the ceiling). No parallax, no scroll-linked motion, no bounce/overshoot anywhere in the shell. `prefers-reduced-motion: reduce` collapses every spatial transition above to a ≤150ms opacity crossfade; the tool-status and skeleton pulses (functional loaders) keep running, just uneased.

### 10.5 Responsive & accessibility floor

- Verified at 320 / 375 / 414 / 768 / 1024px+. `overflow-x: clip` on `html` and `body`.
- **< 768px**: sidebar becomes an off-canvas drawer (slim top bar with a session-switcher icon replaces the fixed rail); thread column goes full-width with `--space-md` side padding; artifact cards stack full-width, never a bare `1fr` track — `minmax(0, 1fr)`; slot tracker chips wrap onto a second line rather than truncating.
- No two-line clickable text: send button, retry links, expand/download icons all stay single-line at every width.
- Touch targets ≥ 44×44px (send button, expand/download icons, sidebar rows).
- `:focus-visible` — 2px `--color-accent` ring, 3px offset, never animated in, on every interactive element (chips, session rows, composer, artifact actions).
- Keyboard: `/` focuses the composer from anywhere in the thread; `⌘K`/`Ctrl+K` opens the session switcher; `Escape` closes any open artifact dialog or the mobile drawer.
- No color-only state signaling: the warning/error states above always pair color with an icon or label text (clarify banner names the field; slot chips show a check/x glyph; tool failures show retry text).
- `aria-live="polite"` on the tool-status trail and the streaming response region; announces resolution, not every token.
