// Horizontal-scrolling row of template cards shown on the kickoff screen.
// Fetches the template catalog from GET /api/v2/templates on mount and
// fires `onSelect(slug)` when a card is clicked. The parent decides whether
// to prefill the textarea, call the instantiate endpoint, etc.

import { useEffect, useRef, useState } from "react";

import { t } from "../../i18n";

export type TemplateSummary = {
  slug: string;
  title: string;
  tagline: string;
  description: string;
  topic_count: number;
  relationship_count: number;
  domain_framing: string;
};

type TemplatePickerProps = {
  onSelect: (slug: string) => void;
  selectedSlug?: string | null;
  disabled?: boolean;
};

// Roughly one card width + gap — we scroll by this many pixels when the
// user clicks a chevron. Card is 164px with a 10px gap between cards.
const SCROLL_STEP_PX = 174;

export function TemplatePicker({
  onSelect,
  selectedSlug,
  disabled,
}: TemplatePickerProps) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const rowRef = useRef<HTMLDivElement | null>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const base =
          (import.meta.env.VITE_INSPIRA_API_URL as string | undefined) ??
          "http://127.0.0.1:4174";
        const res = await fetch(`${base}/api/v2/templates`, {
          credentials: "include",
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const data = (await res.json()) as { templates: TemplateSummary[] };
        if (!cancelled) setTemplates(data.templates);
      } catch {
        if (!cancelled) setError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Recompute whether the row can still scroll in each direction after
  // every scroll, resize, or template-list change. A 1px tolerance on the
  // right edge avoids flicker caused by sub-pixel rounding in some browsers.
  useEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    const update = () => {
      setCanScrollLeft(row.scrollLeft > 0);
      setCanScrollRight(
        row.scrollLeft + row.clientWidth < row.scrollWidth - 1,
      );
    };
    update();
    row.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(row);
    return () => {
      row.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [templates]);

  function scrollBy(direction: -1 | 1) {
    const row = rowRef.current;
    if (!row) return;
    row.scrollBy({ left: direction * SCROLL_STEP_PX, behavior: "smooth" });
  }

  if (loading || error || templates.length === 0) return null;

  return (
    <div className="template-picker">
      <div className="template-picker__eyebrow">
        {t("templates.picker.eyebrow")}
      </div>
      <div className="template-picker__row-wrap">
        <div
          className={
            "template-picker__mask template-picker__mask--left" +
            (canScrollLeft ? "" : " template-picker__mask--hidden")
          }
          aria-hidden="true"
        />
        <button
          type="button"
          className={
            "template-picker__arrow template-picker__arrow--left" +
            (canScrollLeft ? "" : " template-picker__arrow--hidden")
          }
          onClick={() => scrollBy(-1)}
          aria-label={t("templates.picker.scroll_left")}
          aria-hidden={!canScrollLeft}
          tabIndex={canScrollLeft ? 0 : -1}
        >
          <span aria-hidden="true">‹</span>
        </button>
        <div className="template-picker__row" role="list" ref={rowRef}>
          {templates.map((tmpl) => {
            const isSelected = tmpl.slug === selectedSlug;
            return (
              <button
                key={tmpl.slug}
                role="listitem"
                type="button"
                className={
                  "template-picker__card" +
                  (isSelected ? " template-picker__card--selected" : "")
                }
                onClick={() => !disabled && onSelect(tmpl.slug)}
                disabled={disabled}
                aria-pressed={isSelected}
                title={tmpl.description}
              >
                <span className="template-picker__card-title">{tmpl.title}</span>
                <span className="template-picker__card-tagline">
                  {tmpl.tagline}
                </span>
                <span className="template-picker__card-count">
                  {t("templates.picker.topic_count", {
                    count: String(tmpl.topic_count),
                  })}
                </span>
              </button>
            );
          })}
        </div>
        <div
          className={
            "template-picker__mask template-picker__mask--right" +
            (canScrollRight ? "" : " template-picker__mask--hidden")
          }
          aria-hidden="true"
        />
        <button
          type="button"
          className={
            "template-picker__arrow template-picker__arrow--right" +
            (canScrollRight ? "" : " template-picker__arrow--hidden")
          }
          onClick={() => scrollBy(1)}
          aria-label={t("templates.picker.scroll_right")}
          aria-hidden={!canScrollRight}
          tabIndex={canScrollRight ? 0 : -1}
        >
          <span aria-hidden="true">›</span>
        </button>
      </div>
      <style>{`
        .template-picker {
          display: flex;
          flex-direction: column;
          gap: 10px;
          /* Critical: grid/flex children default to min-width:auto which
             lets their content force the parent wider. Lock to 0 so the
             row below can actually overflow-scroll instead of busting
             out of the 720px kickoff__inner container. */
          min-width: 0;
          width: 100%;
          max-width: 100%;
        }
        .template-picker__eyebrow {
          font-family: var(--ff-serif);
          font-style: italic;
          font-size: 13px;
          color: var(--ink-3);
        }
        .template-picker__row-wrap {
          position: relative;
          min-width: 0;
          max-width: 100%;
        }
        .template-picker__row {
          display: flex;
          flex-direction: row;
          gap: 10px;
          overflow-x: auto;
          padding-bottom: 4px;
          min-width: 0;
          max-width: 100%;
          /* Hide scrollbar on most browsers while keeping scrollability */
          scrollbar-width: none;
          -ms-overflow-style: none;
        }
        .template-picker__row::-webkit-scrollbar {
          display: none;
        }
        /* Soft fade at each edge so users see the row continues beyond the
           visible window. The mask hides itself when that edge can't
           scroll any further, so it disappears at the true start/end. */
        .template-picker__mask {
          position: absolute;
          top: 0;
          bottom: 4px;
          width: 48px;
          pointer-events: none;
          opacity: 1;
          transition: opacity 160ms ease;
          z-index: 1;
        }
        .template-picker__mask--left {
          left: 0;
          background: linear-gradient(
            to right,
            var(--paper) 0%,
            transparent 100%
          );
        }
        .template-picker__mask--right {
          right: 0;
          background: linear-gradient(
            to left,
            var(--paper) 0%,
            transparent 100%
          );
        }
        .template-picker__mask--hidden {
          opacity: 0;
        }
        .template-picker__arrow {
          position: absolute;
          top: 50%;
          transform: translateY(-50%);
          width: 28px;
          height: 28px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 999px;
          background: var(--paper);
          color: var(--ink-2);
          border: 1px solid var(--paper-edge);
          cursor: pointer;
          font-family: var(--ff-serif);
          font-size: 18px;
          line-height: 1;
          padding: 0;
          box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
          transition: background-color 140ms ease, border-color 140ms ease,
            color 140ms ease, opacity 160ms ease;
          z-index: 2;
        }
        .template-picker__arrow--left {
          left: 4px;
        }
        .template-picker__arrow--right {
          right: 4px;
        }
        .template-picker__arrow:hover {
          background: var(--paper-2);
          border-color: var(--ink-5);
          color: var(--ink);
        }
        .template-picker__arrow:focus-visible {
          outline: none;
          border-color: var(--ink-5);
          box-shadow: 0 0 0 3px rgba(43, 37, 32, 0.12);
        }
        .template-picker__arrow--hidden {
          opacity: 0;
          pointer-events: none;
        }
        .template-picker__card {
          flex: 0 0 auto;
          width: 164px;
          display: flex;
          flex-direction: column;
          gap: 4px;
          text-align: left;
          background: var(--paper);
          border: 1px solid var(--paper-edge);
          border-radius: 10px;
          padding: 12px 14px;
          cursor: pointer;
          transition: background-color 160ms ease, border-color 160ms ease,
            box-shadow 200ms ease;
        }
        .template-picker__card:hover:not(:disabled) {
          background: var(--paper-2);
          border-color: var(--ink-5);
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
        }
        .template-picker__card:focus-visible {
          outline: none;
          border-color: var(--ink-5);
          box-shadow: 0 0 0 3px rgba(43, 37, 32, 0.12);
        }
        .template-picker__card--selected {
          background: var(--paper-2);
          border-color: var(--ink-3);
          box-shadow: 0 0 0 2px rgba(43, 37, 32, 0.15);
        }
        .template-picker__card:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .template-picker__card-title {
          font-family: var(--ff-serif);
          font-size: 13.5px;
          font-weight: 600;
          color: var(--ink);
          line-height: 1.2;
        }
        .template-picker__card-tagline {
          font-family: var(--ff-serif);
          font-style: italic;
          font-size: 11.5px;
          color: var(--ink-2);
          line-height: 1.35;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }
        .template-picker__card-count {
          font-family: var(--ff-sans);
          font-size: 11px;
          color: var(--ink-4);
          margin-top: 2px;
        }
        /* Touch devices: grow the scroll arrows from 28x28 to a 44x44
           tap target so thumbs can hit them reliably. Desktop pointer-
           fine users keep the compact 28x28 style above. */
        @media (pointer: coarse) {
          .template-picker__arrow {
            width: 44px;
            height: 44px;
          }
        }
      `}</style>
    </div>
  );
}
