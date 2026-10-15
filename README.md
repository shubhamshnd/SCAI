# SCAI — Indian Supply Chain Agent

An agentic RAG chatbot that acts as a domain expert on Indian supply chains: sourcing raw materials and moving them. It is built on LangGraph, Postgres + pgvector, and Gemini (with Ollama as a local fallback), and ships with a FastAPI backend, a Next.js web UI and a terminal REPL.

Before answering a procurement or logistics question, the agent makes sure it knows **what** is being moved (an internal SKU code) and **how**: origin, destination and transport mode. If any of these is missing or ambiguous, it asks a targeted clarifying question instead of answering generically. Once it has them, it grounds the answer in retrieved documents (regulatory, logistics network, supplier) and in live tool calls (pricing, web search, road routing, supplier discovery). It can also return charts, tables and maps.

The full design lives in [`design.md`](design.md).

## Architecture

```
User ─► Web UI (Next.js) / CLI
          │
          ▼
   FastAPI (scai/api.py, SSE streaming)
          │
          ▼
   LangGraph agent (scai/graph)
   parse intent → check required slots ─► ask clarifying question
                         │
                         ▼
          retrieve (pgvector) → select tools → call tools → synthesize answer
          │                         │
          ▼                         ▼
   PostgreSQL + pgvector     Tools: web search (Tavily), pricing,
   (RAG chunks, SKU/HSN,     OSRM/Nominatim routing, suppliers
    suppliers, logistics
    nodes, sessions)
```

- **LLM provider router** (`scai/providers`): Gemini is the primary provider for generation and embeddings, and Ollama is the generation fallback when Gemini fails or is rate-limited. Embeddings never fall back automatically, because changing the embedding model changes the vector space.
- **Slot filling** is enforced by the graph's structure, not only by the prompt. Filled slots persist per session in `messages.slots_filled`, so the agent never asks for the same thing twice.

## Repository layout

| Path | Contents |
|---|---|
| `scai/` | Python package: config, DB access, ingest pipeline, API, CLI |
| `scai/graph/` | LangGraph state, nodes and graph builder |
| `scai/providers/` | `LLMProvider` interface, Gemini and Ollama adapters, fallback router |
| `scai/tools/` | Web search, pricing, routing and supplier tools |
| `db/` | `schema.sql`, `seed_reference.sql` (starter reference data), `setup.sh` |
| `knowledge_base/` | `sources.yaml` manifest of ingested primary sources, plus local documents |
| `web/` | Next.js chat UI: sessions sidebar, streaming messages, slot tracker, map and charts |
| `tests/` | pytest suite |
| `config.yaml` | Non-secret settings: models, DB DSN, retrieval parameters |

## Getting started

### Prerequisites

- Python 3.10+
- PostgreSQL with the [pgvector](https://github.com/pgvector/pgvector) extension
- Node.js 20+ (only needed for the web UI)
- A Gemini API key, and optionally a Tavily API key for web search
- Optionally, [Ollama](https://ollama.com) with a model pulled (`ollama pull llama3.1`) to serve as the fallback

### 1. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure secrets

Create a `.env` file in the project root. It is gitignored.

```bash
GEMINI_API_KEY=...
TAVILY_API_KEY=...        # optional; enables live web search and price fallback
```

Environment variables override `config.yaml`:

| Variable | Overrides |
|---|---|
| `SCAI_DB_DSN` | `db.dsn` (default `postgresql:///scai`) |
| `SCAI_GENERATION_PROVIDER` / `SCAI_GENERATION_MODEL` | `llm.generation.*` |
| `SCAI_EMBEDDING_PROVIDER` / `SCAI_EMBEDDING_MODEL` | `llm.embedding.*` |
| `SCAI_FALLBACK_PROVIDER` / `SCAI_FALLBACK_MODEL` | `llm.fallback.*` |
| `OLLAMA_BASE_URL` | `llm.fallback.base_url` |

### 3. Set up the database

```bash
./db/setup.sh                          # creates the 'scai' DB and applies schema.sql (safe to re-run)
psql -d scai -f db/seed_reference.sql  # optional starter suppliers, ports, corridors, HSN data
```

The seed rows are tagged `data_source = 'seed:reference'`. They are development-grade reference data, not a verified vendor master.

### 4. Build the knowledge base

```bash
python -m scai.ingest --manifest knowledge_base/sources.yaml --replace
```

You can also ingest individual sources:

```bash
python -m scai.ingest --file path/to/doc.pdf --type regulatory --title "E-Way Bill Rules"
python -m scai.ingest --dir knowledge_base/regulatory --type regulatory
python -m scai.ingest --url https://example.gov.in/circular --type regulatory
```

`--type` must be one of `regulatory`, `logistics_network`, `supplier` or `general`. Ingesting a source that is already stored is refused unless you pass `--replace`.

## Running

**Terminal REPL**

```bash
python -m scai.cli
```

**API + web UI**

```bash
uvicorn scai.api:app --reload --port 8000

cd web
npm install
npm run dev     # http://localhost:3000
```

The web UI reads the backend URL from `NEXT_PUBLIC_API_BASE`, which defaults to `http://localhost:8000`.

## Tests

```bash
pytest
```

## Notes

- Road routing uses the public Nominatim and OSRM demo servers. Use a self-hosted OSRM instance for production traffic. Rail, air and sea legs come from the `transport_benchmarks` table.
- Prices currently come from web search and carry an explicit caveat. No licensed price feed is wired in yet.
- Ingested text is a snapshot from its ingest date. Re-ingest with `--replace` periodically, and treat the live portals as authoritative for anything legally binding.
