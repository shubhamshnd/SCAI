"use client";

import { useEffect, useRef } from "react";

export function Composer({
  value,
  onChange,
  disabled,
  onSend,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [value]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    onChange("");
  };

  return (
    <div className="composer">
      <textarea
        ref={textareaRef}
        className="composer-input"
        placeholder="Ask about procurement, routes, pricing..."
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <button className="send-button" disabled={disabled || !value.trim()} onClick={submit}>
        Send
      </button>
    </div>
  );
}
