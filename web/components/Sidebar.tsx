"use client";

import type { Session } from "@/lib/api";

function relativeTime(iso?: string): string {
  if (!iso) return "";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function Sidebar({
  sessions,
  activeSessionId,
  onSelect,
  onNewSession,
  onDelete,
}: {
  sessions: Session[];
  activeSessionId: string | null;
  onSelect: (id: string) => void;
  onNewSession: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">SUPPLY CHAIN AGENT</div>
      <button className="new-session-button" onClick={onNewSession}>
        + New session
      </button>
      <div className="session-list">
        {sessions.map((s) => (
          <div
            key={s.id}
            className={`session-row ${s.id === activeSessionId ? "session-row--active" : ""}`}
            onClick={() => onSelect(s.id)}
          >
            <div className="session-row-main">
              <span className="session-row-label">{s.id.slice(0, 8)}</span>
              <span className="session-row-time">{relativeTime(s.started_at)}</span>
            </div>
            <button
              className="session-delete"
              aria-label={`Delete session ${s.id.slice(0, 8)}`}
              title="Delete session"
              onClick={(e) => {
                // Without this the row's onClick also fires and selects what we just deleted.
                e.stopPropagation();
                onDelete(s.id);
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </aside>
  );
}
