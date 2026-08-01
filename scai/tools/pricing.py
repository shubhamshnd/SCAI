"""Real-time material pricing tool (design.md Section 4, item 2) — per-category fetcher registry.

The dedicated per-category sources (MCX/LME, SteelMint/JPC, Agmarknet/eNAM, ICIS) all need paid
or licensed API access and are not wired up. Until they are, every category resolves through the
web-search fallback, which returns real quoted prices but as unstructured snippets — good enough
to cite with a source, not good enough to treat as a settled benchmark rate.
"""

from __future__ import annotations

from scai.tools import web_search

_CATEGORY_SOURCES = {
    "metals": "MCX/LME",
    "steel": "SteelMint/JPC",
    "agri": "Agmarknet/eNAM",
    "chemicals": "ICIS",
}


class ToolUnavailableError(Exception):
    pass


def get_price(material_category: str, sku_code: str, material_name: str | None = None) -> dict:
    subject = material_name or sku_code or material_category
    query = f"current {subject} price India per tonne {material_category}".strip()

    try:
        results = web_search.search(query, max_results=4)
    except web_search.ToolUnavailableError as exc:
        raise ToolUnavailableError(str(exc)) from exc

    if not results:
        raise ToolUnavailableError(f"Web search returned no price information for {subject!r}.")

    return {
        "method": "web_search_fallback",
        "dedicated_source_would_be": _CATEGORY_SOURCES.get(material_category, "none for this category"),
        "caveat": "Unstructured web results, not a verified benchmark rate — cite the source and date.",
        "query": query,
        "results": results,
    }


def get_freight_rate(
    origin: str, destination: str, modes: list[str], material_name: str | None = None
) -> dict:
    """Prevailing freight rates for a corridor — the other half of a landed cost.

    Material price alone can't produce a quotable total; without this the model was left
    inventing per-tonne freight from memory. Same web-search caveat applies: these are quoted
    market rates from unstructured results, useful as a sanity band around an estimate rather
    than as a contract rate.
    """
    mode_phrase = " ".join(dict.fromkeys(m for m in modes if m)) or "road"
    subject = f"{material_name} " if material_name else ""
    query = (
        f"{mode_phrase} freight rate per tonne {subject}{origin} to {destination} India "
        "transport charges"
    ).strip()

    try:
        results = web_search.search(query, max_results=4)
    except web_search.ToolUnavailableError as exc:
        raise ToolUnavailableError(str(exc)) from exc

    if not results:
        raise ToolUnavailableError(
            f"Web search returned no freight rates for {origin!r} to {destination!r}."
        )

    return {
        "method": "web_search_fallback",
        "modes_queried": modes,
        "caveat": "Indicative market freight, not a booked rate — treat as a band and cite the source.",
        "query": query,
        "results": results,
    }
