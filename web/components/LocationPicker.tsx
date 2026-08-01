"use client";

/* Pin the delivery site and size a sourcing radius.

   Typed locations are the weak point in the whole flow: "Raigad district" is a 7,000 km²
   polygon, and geocoding it yields a centroid that may be nowhere near the actual plant. A pin
   plus a radius gives the supplier search an exact centre and an explicit search distance, which
   is what "find plants near me" actually needs. */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { geocode, type PickedLocation, type SupplierPin } from "@/lib/api";
import type { MapMarker } from "./MapCanvas";

// Leaflet reads `window` at import time, so it must never be part of the server bundle.
const MapCanvas = dynamic(() => import("./MapCanvas"), {
  ssr: false,
  loading: () => <div className="map-loading">Loading map…</div>,
});

const RADIUS_STOPS = [50, 100, 200, 300, 500, 800];

type Props = {
  value: PickedLocation | null;
  suppliers: SupplierPin[];
  onChange: (next: PickedLocation | null) => void;
  onClose: () => void;
};

export function LocationPicker({ value, suppliers, onChange, onClose }: Props) {
  const [query, setQuery] = useState(value?.label ?? "");
  const [radiusKm, setRadiusKm] = useState(value?.radiusKm ?? 300);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    panelRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const setPoint = useCallback(
    (lat: number, lon: number, label?: string) => {
      setError(null);
      onChange({ lat, lon, radiusKm, label: label ?? value?.label });
    },
    [onChange, radiusKm, value?.label]
  );

  const search = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setError(null);
    try {
      const { lat, lon } = await geocode(q);
      onChange({ lat, lon, radiusKm, label: q });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lookup failed");
    } finally {
      setSearching(false);
    }
  }, [query, onChange, radiusKm]);

  const markers: MapMarker[] = useMemo(() => {
    const list: MapMarker[] = [];
    if (value) {
      list.push({
        id: "destination",
        label: value.label ? `Delivery site — ${value.label}` : "Delivery site",
        lat: value.lat,
        lon: value.lon,
        role: "destination",
      });
    }
    suppliers.forEach((s, i) => {
      list.push({
        id: `supplier-${i}`,
        label: s.name,
        lat: s.lat,
        lon: s.lon,
        role: "supplier",
        distanceKm: s.distance_km ?? null,
        durationHours: s.duration_hours ?? null,
        note: [s.city, s.state].filter(Boolean).join(", ") || undefined,
      });
    });
    return list;
  }, [value, suppliers]);

  const applyRadius = (next: number) => {
    setRadiusKm(next);
    if (value) onChange({ ...value, radiusKm: next });
  };

  return (
    <div className="picker-backdrop" role="dialog" aria-modal="true" aria-label="Pick delivery site">
      <div className="picker-panel" ref={panelRef} tabIndex={-1}>
        <div className="picker-head">
          <div>
            <h2 className="picker-title">Delivery site &amp; sourcing radius</h2>
            <p className="picker-sub">
              Search a place, click the map, or drag the pin. Suppliers are searched within the
              radius of this exact point.
            </p>
          </div>
          <button type="button" className="picker-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="picker-search">
          <input
            className="picker-input"
            value={query}
            placeholder="e.g. Dolvi, Raigad"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void search();
              }
            }}
          />
          <button type="button" className="picker-find" onClick={() => void search()} disabled={searching}>
            {searching ? "Finding…" : "Find"}
          </button>
        </div>
        {error && <p className="picker-error">{error}</p>}

        <div className="picker-radius">
          <span className="picker-radius-label">Sourcing radius</span>
          <div className="picker-radius-stops">
            {RADIUS_STOPS.map((stop) => (
              <button
                key={stop}
                type="button"
                className={`picker-chip${stop === radiusKm ? " picker-chip--on" : ""}`}
                onClick={() => applyRadius(stop)}
              >
                {stop} km
              </button>
            ))}
          </div>
        </div>

        <MapCanvas
          markers={markers}
          radiusKm={radiusKm}
          onPick={(lat, lon) => setPoint(lat, lon)}
          showSpokes={suppliers.length > 0}
          height={320}
        />

        <div className="picker-foot">
          <span className="picker-coords">
            {value
              ? `Pinned at ${value.lat.toFixed(4)}, ${value.lon.toFixed(4)} · ${radiusKm} km radius`
              : "No point pinned yet"}
          </span>
          <div className="picker-actions">
            {value && (
              <button type="button" className="picker-clear" onClick={() => onChange(null)}>
                Clear
              </button>
            )}
            <button type="button" className="picker-done" onClick={onClose} disabled={!value}>
              Use this location
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
