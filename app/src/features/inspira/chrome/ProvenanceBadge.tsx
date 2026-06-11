// B1.2 — per-decision provenance badge.
//
// Renders inline with each decision bullet on a topic card:
//   - 9px gold dot when proposed_by === "planner" (AI-drafted)
//   - half-fill (gold + sage) when AI was edited by a human
//     (proposed_by === "planner" && provenance.humanEditedAt != null)
//   - nothing for user-typed decisions (proposed_by === "user")
//
// Hover (or tap on touch) reveals a small popover listing up to 5 cited
// feedback items + a "View all sources" link. Popover is portal-free —
// CSS-positioned absolute relative to the badge wrapper. ESC closes.

import { useCallback, useEffect, useRef, useState } from "react";

import type { Decision } from "../api";

export interface ProvenanceBadgeProps {
  decision: Decision;
}

export function ProvenanceBadge({ decision }: ProvenanceBadgeProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);

  // Tap-outside + ESC close. Only attached when popover is open.
  useEffect(() => {
    if (!open) return;
    const onDocPointer = (e: PointerEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (wrapRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDocPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDocPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // User-typed decisions don't render a badge at all — copy must never
  // overstate what the AI actually did (no AI claim where there's no AI
  // authorship).
  if (decision.proposed_by !== "planner") return null;

  const provenance = decision.provenance;
  const isEdited = !!provenance?.humanEditedAt;
  const sources = provenance?.sources ?? [];
  const visibleSources = sources.slice(0, 5);
  const hasMoreSources = sources.length > visibleSources.length;

  const onMouseEnter = useCallback(() => setOpen(true), []);
  const onMouseLeave = useCallback(() => setOpen(false), []);
  const onFocus = useCallback(() => setOpen(true), []);
  const onBlur = useCallback(() => setOpen(false), []);
  const onClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setOpen((v) => !v);
  }, []);

  const ariaLabel = isEdited
    ? "AI-drafted, human-edited — view provenance"
    : "AI-drafted — view provenance";

  return (
    <span
      ref={wrapRef}
      className="provenance-badge"
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <button
        type="button"
        className={
          "provenance-badge__dot" +
          (isEdited ? " provenance-badge__dot--edited" : "")
        }
        aria-label={ariaLabel}
        aria-expanded={open}
        onFocus={onFocus}
        onBlur={onBlur}
        onClick={onClick}
        // Stop pointer events from reaching TopicNode's tap-to-open handler.
        onPointerDown={(e) => e.stopPropagation()}
        onPointerUp={(e) => e.stopPropagation()}
      />
      {open ? (
        <span className="provenance-badge__popover" role="tooltip">
          <span className="provenance-badge__label">PROVENANCE · </span>
          {visibleSources.length > 0 ? (
            <ul className="provenance-badge__sources">
              {visibleSources.map((s) => (
                <li
                  key={s.feedbackItemId}
                  className="provenance-badge__source"
                >
                  <span
                    className="provenance-badge__bullet"
                    aria-hidden="true"
                  >
                    •
                  </span>
                  <span className="provenance-badge__excerpt">
                    {s.excerpt}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <span className="provenance-badge__empty">
              No source feedback recorded.
            </span>
          )}
          {hasMoreSources || sources.length > 0 ? (
            <a className="provenance-badge__view-all" href="#sources">
              View all sources →
            </a>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
