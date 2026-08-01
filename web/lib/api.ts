const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Session = {
  id: string;
  user_id: string;
  started_at?: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  slots_filled: Record<string, string>;
  created_at: string;
};

export async function createSession(userId = "web-user"): Promise<Session> {
  const res = await fetch(`${API_BASE}/api/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!res.ok) throw new Error(`createSession failed: ${res.status}`);
  return res.json();
}

export async function listSessions(): Promise<Session[]> {
  const res = await fetch(`${API_BASE}/api/sessions`);
  if (!res.ok) throw new Error(`listSessions failed: ${res.status}`);
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`deleteSession failed: ${res.status}`);
}

export async function listMessages(sessionId: string): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error(`listMessages failed: ${res.status}`);
  return res.json();
}

export type SupplierPin = {
  name: string;
  city?: string | null;
  state?: string | null;
  lat: number;
  lon: number;
  distance_km?: number | null;
  duration_hours?: number | null;
};

/** Delivery site pinned on the map, plus how far out to look for suppliers. */
export type PickedLocation = {
  lat: number;
  lon: number;
  radiusKm: number;
  label?: string;
};

export async function geocode(query: string): Promise<{ lat: number; lon: number }> {
  const res = await fetch(`${API_BASE}/api/geocode?q=${encodeURIComponent(query)}`);
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Could not find “${query}”`);
  }
  return res.json();
}

export type StreamHandlers = {
  onStatus?: (label: string) => void;
  onToken?: (text: string) => void;
  onDone?: (payload: {
    slots: Record<string, string>;
    missing_fields: string[];
    tool_results: Record<string, unknown>;
    supplier_pins?: SupplierPin[];
  }) => void;
  onError?: (message: string) => void;
};

/** Parses `event: X\ndata: Y\n\n` frames from the POST /messages SSE stream. Plain fetch + a
 * ReadableStream reader, since the browser's EventSource API can't send a POST body. */
export async function streamMessage(
  sessionId: string,
  content: string,
  handlers: StreamHandlers,
  location?: PickedLocation | null
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      // Sent every turn while a pin is active: the backend merges it into the session slots, so
      // the anchor survives follow-up questions the same way a typed location would.
      ...(location
        ? {
            destination_lat: location.lat,
            destination_lon: location.lon,
            sourcing_radius_km: location.radiusKm,
          }
        : {}),
    }),
  });
  if (!res.ok || !res.body) {
    handlers.onError?.(`Request failed: ${res.status}`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      const eventLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      const event = eventLine.slice("event: ".length);
      const data = JSON.parse(dataLine.slice("data: ".length));

      if (event === "status") handlers.onStatus?.(data.label);
      else if (event === "token") handlers.onToken?.(data.text);
      else if (event === "done") handlers.onDone?.(data);
      else if (event === "error") handlers.onError?.(data.message);
    }
  }
}
