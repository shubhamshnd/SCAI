"use client";

import { isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ChartBlock } from "./ChartBlock";

/* A ```scaichart fence is a chart spec, not source code. react-markdown renders fenced blocks as
   <pre><code class="language-scaichart">…</code></pre>, so the swap happens at the <pre> level —
   returning the chart from the <code> override would leave it wrapped in a code block's chrome. */
function chartSourceOf(children: ReactNode): string | null {
  const child = Array.isArray(children) ? children[0] : children;
  if (!isValidElement(child)) return null;
  const { className, children: code } = child.props as {
    className?: string;
    children?: ReactNode;
  };
  if (!className?.split(/\s+/).includes("language-scaichart")) return null;
  return String(code ?? "");
}

export type DisplayMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  isClarifying?: boolean;
  isStreaming?: boolean;
  isError?: boolean;
};

export function MessageBubble({ message }: { message: DisplayMessage }) {
  if (message.role === "user") {
    return (
      <div className="message message--user">
        <span className="message-body">{message.content}</span>
      </div>
    );
  }

  if (message.isClarifying) {
    return (
      <div className="message message--assistant">
        <span className="message-label">SUPPLY CHAIN AGENT</span>
        <div className="clarify-banner">
          <span className="message-body">{message.content}</span>
        </div>
      </div>
    );
  }

  // Errors stay plain text — they're not authored markdown, and API error payloads contain
  // braces/underscores that a markdown parser would mangle.
  if (message.isError) {
    return (
      <div className="message message--assistant">
        <span className="message-label">SUPPLY CHAIN AGENT</span>
        <span className="message-body error-text">{message.content}</span>
      </div>
    );
  }

  return (
    <div className="message message--assistant">
      <span className="message-label">SUPPLY CHAIN AGENT</span>
      <div className="message-body markdown-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            // Wide comparison tables scroll within the bubble instead of the page.
            table: ({ node, ...props }) => (
              <div className="table-scroll">
                <table {...props} />
              </div>
            ),
            pre: ({ node, children, ...props }) => {
              const chart = chartSourceOf(children);
              if (chart !== null) {
                return <ChartBlock source={chart} isStreaming={message.isStreaming} />;
              }
              return <pre {...props}>{children}</pre>;
            },
            a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
          }}
        >
          {message.content}
        </ReactMarkdown>
        {message.isStreaming && <span className="stream-caret"> </span>}
      </div>
    </div>
  );
}
