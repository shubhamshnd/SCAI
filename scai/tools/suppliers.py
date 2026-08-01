"""Supplier discovery (design.md Section 4) — answers "which plants can I buy this from near me?".

Two tiers, in order:
  1. The seeded `suppliers` table, searched by great-circle radius. Structured, has coordinates,
     so the result feeds straight into distance and freight maths.
  2. A web-search fallback when the table returns too few candidates inside the radius. Wider
     reach, but unverified and without coordinates — flagged as such so the model labels those
     candidates differently from seeded ones.

Sourcing the origin is the core procurement question, so this tool exists to let the agent
*propose* an origin rather than demanding the user supply one.
"""

from __future__ import annotations

import psycopg

from scai import db
from scai.tools import routing, web_search

# Below this many seeded hits, the radius is treated as under-covered and the web tier runs too.
_MIN_SEEDED_CANDIDATES = 3

# Road distance is fetched only for the nearest few. Each is a live OSRM call, and a buyer
# compares a shortlist, not a directory.
_ROAD_ROUTED_CANDIDATES = 4

DEFAULT_RADIUS_KM = 300.0


class ToolUnavailableError(Exception):
    pass


def _discover_via_web(material: str, near_place: str) -> dict:
    query = f"{material} manufacturers suppliers plants near {near_place} India".strip()
    try:
        results = web_search.search(query, max_results=5)
    except web_search.ToolUnavailableError as exc:
        raise ToolUnavailableError(str(exc)) from exc
    return {
        "query": query,
        "caveat": (
            "Unverified web results with no coordinates — treat as leads to qualify, not as "
            "confirmed suppliers, and do not compute distances from them."
        ),
        "results": results,
    }


def find_suppliers(
    conn: psycopg.Connection,
    material: str,
    near_place: str,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = DEFAULT_RADIUS_KM,
) -> dict:
    """Seeded suppliers inside the radius, topped up with web leads when coverage is thin.

    Without coordinates the radius search cannot run at all, so the web tier carries the answer
    on its own — that path is what makes the tool useful before the map picker is used.
    """
    payload: dict = {"material": material, "near": near_place, "radius_km": radius_km}

    seeded: list[dict] = []
    if lat is not None and lon is not None:
        payload["center"] = {"lat": lat, "lon": lon}
        seeded = db.find_suppliers_near(conn, lat, lon, radius_km, material=material)
        # A material-filtered search that finds nothing may just mean the catalogue words it
        # differently; a second unfiltered pass shows what is in range at all rather than
        # reporting an empty district.
        if not seeded:
            nearby_any = db.find_suppliers_near(conn, lat, lon, radius_km, material=None)
            if nearby_any:
                payload["in_radius_but_other_materials"] = nearby_any

    # Straight-line radius is only a filter; what a buyer prices against is the road haul.
    for candidate in seeded[:_ROAD_ROUTED_CANDIDATES]:
        try:
            route = routing.road_route_between(
                candidate["lon"], candidate["lat"], lon, lat  # type: ignore[arg-type]
            )
            candidate["road_distance_km"] = route["distance_km"]
            candidate["road_duration_hours"] = route["duration_hours"]
        except Exception:  # noqa: BLE001 - a routing miss must not lose the supplier itself
            candidate["road_distance_km"] = None
            candidate["road_duration_hours"] = None

    # Re-order the routed shortlist by road distance. Straight-line order is what the radius
    # filter produces, but the model is told to price freight on the road figure — leaving the
    # list sorted by a different measure than the one it quotes invites a wrong "nearest" call.
    # Unrouted candidates keep their crow-flies order behind the shortlist.
    routed = [s for s in seeded if s.get("road_distance_km") is not None]
    unrouted = [s for s in seeded if s.get("road_distance_km") is None]
    routed.sort(key=lambda s: s["road_distance_km"])
    seeded = routed + unrouted

    payload["seeded_suppliers"] = {
        "source": "internal suppliers table (seeded reference data — verify before contracting)",
        "count": len(seeded),
        "distance_note": (
            "distance_km is straight-line; road_distance_km/road_duration_hours are live OSRM "
            "road figures for the nearest few and are what freight should be priced on. The "
            "list is ordered by road distance where known, then by straight-line distance."
        ),
        "results": seeded,
    }

    if len(seeded) < _MIN_SEEDED_CANDIDATES:
        payload["seeded_coverage"] = (
            f"Only {len(seeded)} seeded supplier(s) inside {radius_km:.0f} km — widening to web "
            "discovery for additional leads."
        )
        try:
            payload["web_discovered"] = _discover_via_web(material, near_place)
        except ToolUnavailableError as exc:
            payload["web_discovered"] = {"error": str(exc)}

    return payload
