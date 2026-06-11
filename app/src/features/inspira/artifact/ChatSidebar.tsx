import {
  ChangeEvent,
  KeyboardEvent,
  ReactElement,
  ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { t } from "../../../i18n";
import type { ArtifactChatMessage } from "../api";
import type { ArtifactStatus } from "./useArtifact";

export type ChatSidebarProps = {
  messages: ArtifactChatMessage[];
  status: ArtifactStatus;
  thinkingLabel: string | null;
  onSend: (message: string) => Promise<void>;
};

/**
 * Render assistant text with inline `code` spans. The design HTML shows
 * AI messages with at most inline backtick spans (no fenced blocks);
 * we deliberately ship a 20-line splitter rather than pulling in
 * react-markdown (~80KB) for that single feature.
 */
function renderInlineCode(body: string): ReactNode {
  const segments: ReactNode[] = [];
  const parts = body.split(/(`[^`]+`)/g);
  parts.forEach((part, idx) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
      segments.push(
        <code key={idx} className="av-chat__inline-code">
          {part.slice(1, -1)}
        </code>,
      );
    } else if (part) {
      segments.push(<span key={idx}>{part}</span>);
    }
  });
  return segments;
}

export function ChatSidebar({
  messages,
  status,
  thinkingLabel,
  onSend,
}: ChatSidebarProps): ReactElement {
  const [draft, setDraft] = useState<string>("");
  const feedRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the bottom on every new message + when the typing
  // indicator pops in/out, so the sidebar always shows the latest turn.
  useEffect(() => {
    const el = feedRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages.length, status]);

  const handleSubmit = useCallback(async () => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    if (status !== "idle") return;
    setDraft("");
    await onSend(trimmed);
  }, [draft, onSend, status]);

  const handleChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSubmit();
    }
  };

  return (
    <aside className="av-chat" aria-label="Artifact chat">
      <div className="av-chat__hd">
        <div className="av-chat__avatar" aria-hidden>
          I
        </div>
        <div className="av-chat__title">Inspira agent</div>
        <div
          className={
            "av-chat__status" +
            (status === "thinking" ? " av-chat__status--thinking" : "")
          }
          aria-live="polite"
        >
          {status === "thinking"
            ? t("artifact.chat.thinking")
            : t("artifact.chat.idle")}
        </div>
      </div>
      <div className="av-chat__feed" ref={feedRef}>
        {messages.map((m, idx) => (
          <div
            key={`${m.ts}-${idx}`}
            className={
              "av-chat__msg" +
              (m.role === "user" ? " av-chat__msg--user" : " av-chat__msg--ai")
            }
          >
            {m.role === "assistant" ? renderInlineCode(m.body) : m.body}
          </div>
        ))}
        {status === "thinking" ? (
          <div className="av-chat__msg av-chat__msg--ai av-chat__msg--typing">
            <div className="av-chat__typing" aria-label="Inspira is typing">
              <span />
              <span />
              <span />
            </div>
            {thinkingLabel ? (
              <div className="av-chat__thinking-label">{thinkingLabel}</div>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="av-chat__composer">
        <textarea
          className="av-chat__input"
          placeholder={t("artifact.chat.placeholder")}
          rows={2}
          value={draft}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={status === "thinking"}
          aria-label={t("artifact.chat.placeholder")}
        />
        <button
          type="button"
          className="av-chat__send"
          onClick={handleSubmit}
          disabled={!draft.trim() || status === "thinking"}
        >
          {t("artifact.chat.send")}
        </button>
      </div>
    </aside>
  );
}
