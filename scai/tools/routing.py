"""Distance/routing tool (design.md Section 4, item 3).

Road: live lookup via public Nominatim (geocoding) + OSRM (routing) demo instances — no API key
needed, fine for development; swap for a self-hosted OSRM instance for production traffic.
Rail/air/sea: not wired to a live tool yet — those come from the static `transport_benchmarks`
table (design.md Section 3), which the caller should query directly rather than through this
function.
"""

from __future__ import annotations

import requests

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}"
_USER_AGENT = "scai-supply-chain-agent/0.1 (dev)"


class ToolUnavailableError(Exception):
    pass


def geocode(place: str) -> tuple[float, float]:
    """Public alias — supplier radius search needs a centre point when the user typed a place
    name instead of dropping a pin on the map."""
    return _geocode(place)


def _geocode(place: str) -> tuple[float, float]:
    resp = requests.get(
        _NOMINATIM_URL,
        params={"q": place, "format": "json", "limit": 1, "countrycodes": "in"},
        headers={"User-Agent": _USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ToolUnavailableError(f"Could not geocode location: {place!r}")
    return float(results[0]["lon"]), float(results[0]["lat"])


def road_route_between(
    origin_lon: float, origin_lat: float, dest_lon: float, dest_lat: float
) -> dict:
    """Road route between two known points, skipping geocoding.

    Supplier discovery already has coordinates for each candidate plant, and the straight-line
    radius distance understates a real haul — this is what turns "48 km away" into a road
    distance and drive time the freight estimate can actually use.
    """
    coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    resp = requests.get(_OSRM_URL.format(coords=coords), params={"overview": "false"}, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ToolUnavailableError(
            f"OSRM could not find a road route between {origin_lat},{origin_lon} and {dest_lat},{dest_lon}"
        )

    route = payload["routes"][0]
    return {
        "distance_km": round(route["distance"] / 1000, 1),
        "duration_hours": round(route["duration"] / 3600, 1),
        "mode": "road",
        "source": "OSRM public demo instance",
    }


def get_route(origin: str, destination: str, mode: str) -> dict:
    if mode != "road":
        raise ToolUnavailableError(
            f"Live routing only covers 'road' mode; {mode!r} comes from the static "
            "transport_benchmarks table, not this tool."
        )

    origin_lon, origin_lat = _geocode(origin)
    dest_lon, dest_lat = _geocode(destination)
    try:
        return road_route_between(origin_lon, origin_lat, dest_lon, dest_lat)
    except ToolUnavailableError:
        raise ToolUnavailableError(
            f"OSRM could not find a road route between {origin!r} and {destination!r}"
        ) from None
