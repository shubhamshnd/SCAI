"""Postgres access layer: sessions/messages persistence and pgvector similarity search."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from scai.config import Config


@contextmanager
def connect(config: Config) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(config.db_dsn, autocommit=True)
    try:
        register_vector(conn)
        yield conn
    finally:
        conn.close()


def create_session(conn: psycopg.Connection, user_id: str) -> UUID:
    row = conn.execute(
        "INSERT INTO sessions (user_id) VALUES (%s) RETURNING id", (user_id,)
    ).fetchone()
    return row[0]


def append_message(
    conn: psycopg.Connection,
    session_id: UUID,
    role: str,
    content: str,
    slots_filled: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO messages (session_id, role, content, slots_filled)
        VALUES (%s, %s, %s, %s)
        """,
        (session_id, role, content, json.dumps(slots_filled)),
    )


def list_sessions(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, user_id, started_at FROM sessions ORDER BY started_at DESC"
    ).fetchall()
    return [{"id": r[0], "user_id": r[1], "started_at": r[2]} for r in rows]


def list_messages(conn: psycopg.Connection, session_id: UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, role, content, slots_filled, created_at FROM messages
        WHERE session_id = %s
        ORDER BY created_at ASC
        """,
        (session_id,),
    ).fetchall()
    return [
        {"id": r[0], "role": r[1], "content": r[2], "slots_filled": r[3], "created_at": r[4]}
        for r in rows
    ]


def delete_session(conn: psycopg.Connection, session_id: UUID) -> bool:
    """Messages cascade with the session (see db/schema.sql). Returns False if it didn't exist,
    so the API can answer 404 rather than reporting a delete that deleted nothing."""
    result = conn.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
    return result.rowcount > 0


def latest_slots(conn: psycopg.Connection, session_id: UUID) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT slots_filled FROM messages
        WHERE session_id = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    return row[0] if row else {}


def retrieve_chunks(
    conn: psycopg.Connection,
    query_embedding: list[float],
    top_k: int,
    similarity_threshold: float,
    source_type: str | None = None,
    material_category: str | None = None,
) -> list[dict[str, Any]]:
    """pgvector cosine similarity search, filtered by metadata (design.md Section 2/4)."""
    embedding = Vector(query_embedding)
    filters = ["1=1"]
    params: list[Any] = [embedding]
    if source_type is not None:
        filters.append("d.source_type = %s")
        params.append(source_type)
    if material_category is not None:
        filters.append("d.material_category = %s")
        params.append(material_category)

    params.extend([embedding, top_k])
    sql = f"""
        SELECT c.id, c.chunk_text, d.title, d.source_type, d.source_url,
               1 - (c.embedding <=> %s) AS similarity
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE {' AND '.join(filters)}
        ORDER BY c.embedding <=> %s
        LIMIT %s
    """
    rows = conn.execute(sql, params).fetchall()
    results = [
        {
            "chunk_id": r[0],
            "chunk_text": r[1],
            "title": r[2],
            "source_type": r[3],
            "source_url": r[4],
            "similarity": r[5],
        }
        for r in rows
    ]
    return [r for r in results if r["similarity"] >= similarity_threshold]


def lookup_benchmarks(conn: psycopg.Connection, origin: str, destination: str) -> list[dict[str, Any]]:
    """Static per-mode transit reference for a route — the rail/air/sea counterpart to live OSRM
    road routing (design.md Section 4, item 3). Matches node names case-insensitively since the
    user types city names, not node IDs."""
    rows = conn.execute(
        """
        SELECT o.name, d.name, t.mode, t.distance_km, t.typical_transit_days, t.source
        FROM transport_benchmarks t
        JOIN logistics_nodes o ON o.id = t.origin_node_id
        JOIN logistics_nodes d ON d.id = t.destination_node_id
        WHERE o.name ILIKE %s AND d.name ILIKE %s
        """,
        (f"%{origin}%", f"%{destination}%"),
    ).fetchall()
    return [
        {
            "origin": r[0],
            "destination": r[1],
            "mode": r[2],
            "distance_km": float(r[3]) if r[3] is not None else None,
            "typical_transit_days": float(r[4]) if r[4] is not None else None,
            "source": r[5],
        }
        for r in rows
    ]


def find_suppliers_near(
    conn: psycopg.Connection,
    lat: float,
    lon: float,
    radius_km: float,
    material: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Suppliers within `radius_km` of a point, nearest first — the lookup that answers "which
    plants are near me?" (design.md Section 4).

    Great-circle distance computed in SQL rather than PostGIS: the schema stores plain lat/lon
    (design.md Section 3) and haversine over a few thousand rows is far cheaper than adding a
    spatial extension. `least(1, ...)` guards the acos domain against float error at zero
    distance, which otherwise raises for a supplier sitting exactly on the query point.
    """
    distance_sql = """
        6371 * acos(least(1, greatest(-1,
            cos(radians(%(lat)s)) * cos(radians(lat)) * cos(radians(lon) - radians(%(lon)s))
            + sin(radians(%(lat)s)) * sin(radians(lat))
        )))
    """
    material_filter = ""
    params: dict[str, Any] = {"lat": lat, "lon": lon, "radius": radius_km, "limit": limit}
    if material:
        # Substring match in BOTH directions. A broad query ("copper wire") must find a plant
        # listed as "copper wire rod", and a specific catalogue name ("Copper wire rod 8mm")
        # must find a plant listed more generally as "copper wire rod" — one-way ILIKE only
        # ever handles the first case and silently returns nothing for the second.
        material_filter = """
            AND EXISTS (
                SELECT 1 FROM unnest(materials_supplied) AS m
                WHERE m ILIKE %(material_like)s
                   OR position(lower(m) in lower(%(material)s)) > 0
            )
        """
        params["material"] = material
        params["material_like"] = f"%{material}%"

    sql = f"""
        SELECT name, location_city, location_state, lat, lon, materials_supplied,
               certifications, {distance_sql} AS distance_km
        FROM suppliers
        WHERE lat IS NOT NULL AND lon IS NOT NULL
          AND {distance_sql} <= %(radius)s
          {material_filter}
        ORDER BY distance_km
        LIMIT %(limit)s
    """
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "name": r[0],
            "city": r[1],
            "state": r[2],
            "lat": r[3],
            "lon": r[4],
            "materials_supplied": r[5],
            "certifications": r[6],
            "distance_km": round(float(r[7]), 1),
        }
        for r in rows
    ]


def lookup_sku(conn: psycopg.Connection, sku_code: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT sku_code, material_name, material_category, hsn_code, unit_of_measure "
        "FROM sku_master WHERE sku_code = %s",
        (sku_code,),
    ).fetchone()
    if row is None:
        # Users say "copper wire", not "CU-WR-8MM". Falling back to the material name is what
        # lets the HSN code (and so the GST rate) resolve for a plain-language material — an
        # exact-code-only lookup silently returned None for almost every real question.
        row = conn.execute(
            "SELECT sku_code, material_name, material_category, hsn_code, unit_of_measure "
            "FROM sku_master WHERE material_name ILIKE %s "
            "ORDER BY length(material_name) LIMIT 1",
            (f"%{sku_code}%",),
        ).fetchone()
    if row is None:
        return None
    return {
        "sku_code": row[0],
        "material_name": row[1],
        "material_category": row[2],
        "hsn_code": row[3],
        "unit_of_measure": row[4],
    }
