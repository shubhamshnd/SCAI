"use client";

/* Inline charts for assistant answers. The model emits a ```scaichart fenced block containing a
   JSON spec (see _SYNTHESIZE_SYSTEM in scai/graph/nodes.py); MessageBubble routes those fences
   here instead of rendering them as code.

   Deliberately dependency-free CSS/DOM rather than a charting library: the shapes needed are
   proportional bars and a dated rail, and a chart library would pull a bundle plus its own
   visual language into a UI that already has design tokens for this. */

import dynamic from "next/dynamic";

import type { MapMarker } from "./MapCanvas";

// Leaflet is client-only and comparatively heavy, so the map chunk loads on demand — an answer
// with no map never pays for it.
const MapCanvas = dynamic(() => import("./MapCanvas"), {
  ssr: false,
  loading: () => <div className="map-loading">Loading map…</div>,
});

type Tag = "sourced" | "benchmark" | "assumed";

type ChartItem = {
  label: string;
  value?: number;
  date?: string;
  lat?: number;
  lon?: number;
  role?: "destination" | "supplier";
  distance_km?: number;
  duration_hours?: number;
  note?: string;
  tag?: Tag;
  highlight?: boolean;
};

type ChartSpec = {
  type: "breakdown" | "bar" | "timeline" | "map";
  title?: string;
  unit?: string;
  items: ChartItem[];
};

const TAG_LABELS: Record<Tag, string> = {
  sourced: "Sourced",
  benchmark: "Benchmark",
  assumed: "Assumed",
};

/* Indian grouping (1,23,456) — these are rupee figures for Indian procurement teams, and
   western grouping misreads at lakh/crore scale. */
const numberFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

function formatValue(value: number, unit?: string): string {
  const n = numberFormat.format(value);
  if (!unit) return n;
  const u = unit.trim();
  // Currency-ish units lead the number ("INR 97,560"); rate units trail it ("11.2 days").
  if (/^(inr|₹|rs\.?|usd|\$)$/i.test(u)) return `${u === "INR" ? "₹" : u} ${n}`;
  return `${n} ${u}`;
}

function formatDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function TagDot({ tag }: { tag?: Tag }) {
  if (!tag) return null;
  return (
    <span className={`chart-tag chart-tag--${tag}`} title={`${TAG_LABELS[tag]} figure`}>
      {TAG_LABELS[tag]}
    </span>
  );
}

function ValueChart({ spec }: { spec: ChartSpec }) {
  const items = spec.items.filter((i) => typeof i.value === "number");
  if (items.length === 0) return null;

  const total = items.reduce((sum, i) => sum + (i.value as number), 0);
  const max = Math.max(...items.map((i) => i.value as number));
  const isBreakdown = spec.type === "breakdown";
  // Breakdown bars read as shares of the whole; comparison bars scale to the largest option so
  // the difference between them stays legible.
  const denominator = isBreakdown ? total : max;

  return (
    <div className="chart-rows">
      {items.map((item, idx) => {
        const value = item.value as number;
        const pct = denominator > 0 ? (value / denominator) * 100 : 0;
        return (
          <div
            className={`chart-row${item.highlight ? " chart-row--highlight" : ""}`}
            key={`${item.label}-${idx}`}
          >
            <div className="chart-row-head">
              <span className="chart-row-label">
                {item.label}
                {item.highlight && <span className="chart-pick">recommended</span>}
              </span>
              <span className="chart-row-value">{formatValue(value, spec.unit)}</span>
            </div>
            <div className="chart-track">
              <div className="chart-fill" style={{ width: `${Math.max(pct, 0.6)}%` }} />
            </div>
            <div className="chart-row-foot">
              {isBreakdown && total > 0 && (
                <span className="chart-share">{((value / total) * 100).toFixed(1)}% of total</span>
              )}
              <TagDot tag={item.tag} />
              {item.note && <span className="chart-note">{item.note}</span>}
            </div>
          </div>
        );
      })}

      {isBreakdown && (
        <div className="chart-total">
          <span>Total</span>
          <span className="chart-total-value">{formatValue(total, spec.unit)}</span>
        </div>
      )}
    </div>
  );
}

function TimelineChart({ spec }: { spec: ChartSpec }) {
  const items = spec.items.filter((i) => i.date);
  if (items.length === 0) return null;

  const times = items.map((i) => new Date(`${i.date}T00:00:00`).getTime());
  const start = Math.min(...times);

  return (
    <ol className="chart-timeline">
      {items.map((item, idx) => {
        const days = Math.round((times[idx] - start) / 86_400_000);
        return (
          <li className="chart-milestone" key={`${item.label}-${idx}`}>
            <div className="chart-milestone-head">
              <span className="chart-milestone-date">{formatDate(item.date as string)}</span>
              {idx > 0 && <span className="chart-milestone-offset">day {days}</span>}
            </div>
            <span className="chart-milestone-label">{item.label}</span>
            <div className="chart-row-foot">
              <TagDot tag={item.tag} />
              {item.note && <span className="chart-note">{item.note}</span>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function VendorMap({ spec }: { spec: ChartSpec }) {
  const points = spec.items.filter(
    (i) => typeof i.lat === "number" && typeof i.lon === "number"
  );
  if (points.length === 0) return null;

  const markers: MapMarker[] = points.map((item, idx) => ({
    id: `${item.label}-${idx}`,
    label: item.label,
    lat: item.lat as number,
    lon: item.lon as number,
    role: item.role === "destination" ? "destination" : "supplier",
    highlight: item.highlight,
    distanceKm: item.distance_km ?? null,
    durationHours: item.duration_hours ?? null,
    note: item.note,
  }));

  const vendors = points.filter((i) => i.role !== "destination");

  return (
    <>
      <MapCanvas markers={markers} showSpokes height={300} />
      {vendors.length > 0 && (
        <ul className="map-legend">
          {vendors.map((v, idx) => (
            <li
              className={`map-legend-row${v.highlight ? " map-legend-row--highlight" : ""}`}
              key={`${v.label}-${idx}`}
            >
              <span className={`map-pin map-pin--supplier${v.highlight ? " map-pin--highlight" : ""}`} />
              <span className="map-legend-label">
                {v.label}
                {v.highlight && <span className="chart-pick">recommended</span>}
              </span>
              <span className="map-legend-distance">
                {typeof v.distance_km === "number" ? `${v.distance_km.toFixed(1)} km` : "—"}
                {typeof v.duration_hours === "number" && (
                  <span className="map-legend-duration"> · ~{v.duration_hours.toFixed(1)} h</span>
                )}
              </span>
              <TagDot tag={v.tag} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function isValidSpec(spec: unknown): spec is ChartSpec {
  if (typeof spec !== "object" || spec === null) return false;
  const s = spec as Partial<ChartSpec>;
  return (
    (s.type === "breakdown" || s.type === "bar" || s.type === "timeline" || s.type === "map") &&
    Array.isArray(s.items) &&
    s.items.length > 0
  );
}

export function ChartBlock({ source, isStreaming }: { source: string; isStreaming?: boolean }) {
  let spec: unknown;
  try {
    spec = JSON.parse(source);
  } catch {
    // A fence that is still arriving token-by-token is not an error — show a quiet placeholder
    // and let it resolve into a chart when the closing brace lands.
    if (isStreaming) return <div className="chart-pending">Building chart…</div>;
    return (
      <pre className="chart-invalid">
        <code>{source}</code>
      </pre>
    );
  }

  if (!isValidSpec(spec)) {
    if (isStreaming) return <div className="chart-pending">Building chart…</div>;
    return (
      <pre className="chart-invalid">
        <code>{source}</code>
      </pre>
    );
  }

  return (
    <figure className="chart">
      {spec.title && <figcaption className="chart-title">{spec.title}</figcaption>}
      {spec.type === "map" ? (
        <VendorMap spec={spec} />
      ) : spec.type === "timeline" ? (
        <TimelineChart spec={spec} />
      ) : (
        <ValueChart spec={spec} />
      )}
    </figure>
  );
}
