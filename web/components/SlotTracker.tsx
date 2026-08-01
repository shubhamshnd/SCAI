"use client";

type SlotState = "empty" | "filled" | "conflicting";

function Chip({ label, state, value }: { label: string; state: SlotState; value?: string }) {
  return (
    <span className={`slot-chip slot-chip--${state}`}>
      {label}
      {state === "filled" && value ? ` · ${value}` : state === "empty" ? " — missing" : ""}
    </span>
  );
}

export function SlotTracker({ slots }: { slots: Record<string, string> }) {
  const skuState: SlotState = slots.sku_code ? "filled" : "empty";
  const routeState: SlotState = slots.origin && slots.destination ? "filled" : "empty";
  const modeState: SlotState = slots.mode ? "filled" : "empty";

  return (
    <div className="slot-tracker">
      <Chip label="SKU" state={skuState} value={slots.sku_code} />
      <Chip
        label="ROUTE"
        state={routeState}
        value={routeState === "filled" ? `${slots.origin} → ${slots.destination}` : undefined}
      />
      <Chip label="MODE" state={modeState} value={slots.mode} />
    </div>
  );
}
