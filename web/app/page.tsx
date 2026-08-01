"use client";

import { useEffect, useState } from "react";
import { Sidebar } from "@/components/Sidebar";
import { SlotTracker } from "@/components/SlotTracker";
import { MessageBubble, type DisplayMessage } from "@/components/MessageBubble";
import { ToolStatusRow } from "@/components/ToolStatusRow";
import { Composer } from "@/components/Composer";
import { LocationPicker } from "@/components/LocationPicker";
import {
  createSession,
  deleteSession,
  listSessions,
  listMessages,
  streamMessage,
  type PickedLocation,
  type Session,
  type SupplierPin,
} from "@/lib/api";

const EXAMPLE_QUERIES = [
  "Move 20 tons of TMT steel bars from Chennai to Delhi",
  "Find copper wire rod suppliers near my plant — you pick the best one",
  "What's the current price for SKU STL-1042?",
];

export default function Home() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [slots, setSlots] = useState<Record<string, string>>({});
  const [statusTrail, setStatusTrail] = useState<string[]>([]);
  const [composerValue, setComposerValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [pickedLocation, setPickedLocation] = useState<PickedLocation | null>(null);
  const [supplierPins, setSupplierPins] = useState<SupplierPin[]>([]);
  const [isPickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    listSessions().then(setSessions).catch(() => setSessions([]));
  }, []);

  async function handleNewSession() {
    const session = await createSession();
    setSessions((prev) => [session, ...prev]);
    setActiveSessionId(session.id);
    setMessages([]);
    setSlots({});
  }

  async function handleDeleteSession(id: string) {
    if (!window.confirm("Delete this session and all its messages? This can't be undone.")) return;

    const previous = sessions;
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (id === activeSessionId) {
      setActiveSessionId(null);
      setMessages([]);
      setSlots({});
    }

    try {
      await deleteSession(id);
    } catch {
      setSessions(previous); // put the row back rather than pretending it was deleted
    }
  }

  async function handleSelectSession(id: string) {
    setActiveSessionId(id);
    const history = await listMessages(id);
    setMessages(
      history.map((m) => ({ id: m.id, role: m.role as "user" | "assistant", content: m.content }))
    );
    const last = history[history.length - 1];
    setSlots(last?.slots_filled ?? {});
  }

  async function handleSend(text: string) {
    let sessionId = activeSessionId;
    if (!sessionId) {
      const session = await createSession();
      setSessions((prev) => [session, ...prev]);
      sessionId = session.id;
      setActiveSessionId(sessionId);
    }

    const userMsgId = crypto.randomUUID();
    const assistantMsgId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: "user", content: text },
      { id: assistantMsgId, role: "assistant", content: "", isStreaming: true },
    ]);
    setIsSending(true);
    setStatusTrail([]);

    await streamMessage(sessionId, text, {
      onStatus: (label) => setStatusTrail((prev) => [...prev, label]),
      onToken: (chunk) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantMsgId ? { ...m, content: m.content + chunk } : m))
        ),
      onDone: ({ slots: newSlots, missing_fields, supplier_pins }) => {
        setSlots(newSlots);
        setStatusTrail([]);
        // Keep the last search's vendors on the picker map, so reopening it shows who was found
        // rather than an empty radius.
        if (supplier_pins && supplier_pins.length > 0) setSupplierPins(supplier_pins);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, isClarifying: missing_fields.length > 0 }
              : m
          )
        );
      },
      onError: (message) => {
        setStatusTrail([]);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, isError: true, content: `Couldn't get an answer: ${message}` }
              : m
          )
        );
      },
    }, pickedLocation);

    setIsSending(false);
  }

  return (
    <div className="app-shell">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={handleSelectSession}
        onNewSession={handleNewSession}
        onDelete={handleDeleteSession}
      />
      <main className="main-column">
        <div className="thread-column">
          <div className="thread-scroll">
            {messages.length === 0 ? (
              <div className="empty-state">
                <p>Ask a procurement or logistics question — I&apos;ll ask for the SKU code and route/mode if I need them.</p>
                <div className="empty-state-chips">
                  {EXAMPLE_QUERIES.map((q) => (
                    <button key={q} className="empty-state-chip" onClick={() => setComposerValue(q)}>
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m) => <MessageBubble key={m.id} message={m} />)
            )}
            <ToolStatusRow labels={statusTrail} />
          </div>
          <div className="composer-region">
            <SlotTracker slots={slots} />
            <div className="location-bar">
              <button
                type="button"
                className={`location-button${pickedLocation ? " location-button--set" : ""}`}
                onClick={() => setPickerOpen(true)}
              >
                <span aria-hidden="true">◎</span>
                {pickedLocation
                  ? `${pickedLocation.label ?? "Pinned site"} · ${pickedLocation.radiusKm} km`
                  : "Pin delivery site"}
              </button>
              {pickedLocation && (
                <button
                  type="button"
                  className="location-clear"
                  onClick={() => {
                    setPickedLocation(null);
                    setSupplierPins([]);
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            <Composer value={composerValue} onChange={setComposerValue} disabled={isSending} onSend={handleSend} />
          </div>
        </div>
      </main>

      {isPickerOpen && (
        <LocationPicker
          value={pickedLocation}
          suppliers={supplierPins}
          onChange={setPickedLocation}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
