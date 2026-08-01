"use client";

export function ToolStatusRow({ labels }: { labels: string[] }) {
  if (labels.length === 0) return null;
  return (
    <div>
      {labels.map((label, i) => (
        <div key={i} className={`tool-status ${i === labels.length - 1 ? "tool-status--active" : ""}`}>
          {label}
        </div>
      ))}
    </div>
  );
}
