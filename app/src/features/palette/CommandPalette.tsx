// CommandPalette — a Cmd/Ctrl+K style overlay for navigating Inspira.
//
// This component is intentionally domain-agnostic: it receives a flat list
// of commands from the caller and renders them. The caller decides which
// commands exist (navigation, project actions, per-topic shortcuts, etc.)
// and what each command does. The palette only handles:
//   - Opening/closing the overlay (controlled `open` prop).
//   - Keyboard focus and Esc.
//   - Fuzzy-matching the query against each command's label, keywords,
//     and group.
//   - Grouping matched results by `command.group` and rendering them.
//   - Arrow-key navigation + Enter to run the selected command.
//
// The palette does NOT:
//   - Know which commands exist at rest (Cmd+K binding lives with the
//     caller, which decides when to flip `open` on).
//   - Mutate app state. Running a command calls back into `command.run`;
//     the palette just invokes it and closes.

import { useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";
import { t } from "../../i18n";
import { useFuzzyMatch } from "./useFuzzyMatch";
import "./palette.css";

export type Command = {
  id: string;
  label: string;
  hint?: string;
  group?: string;
  keywords?: string[];
  run: () => void | Promise<void>;
};

export type CommandPaletteProps = {
  open: boolean;
  onClose: () => void;
  commands: Command[];
  placeholder?: string;
};

// The fuzzy matcher operates on a flat candidate string. We build it once
// per command from its label + group + keywords so a user can search
// "navigate projects" or just "proj" and land on the right item.
function commandHaystack(c: Command): string {
  const parts: string[] = [c.label];
  if (c.group) parts.push(c.group);
  if (c.keywords && c.keywords.length > 0) parts.push(...c.keywords);
  return parts.join(" ");
}

export function CommandPalette(props: CommandPaletteProps): JSX.Element | null {
  const { open, onClose, commands, placeholder } = props;
  const resolvedPlaceholder = placeholder ?? t("command_palette.placeholder");
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Reset query each time the palette opens so the user lands on a clean
  // slate. We also restore focus to the input.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelectedIndex(0);
    // Defer focus one microtask so the element is mounted.
    const raf = requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [open]);

  // The palette scores commands against the *haystack* (label + group +
  // keywords) but renders the *label* highlighted. So we compute scores
  // against the haystack, then derive a separate highlighted label from
  // just the label substring. Cheaper than double-matching: we reuse the
  // hook by passing the label as the user-visible string; extra keywords
  // simply boost the score via the concatenated string we pass below.
  const haystackFn = useMemo(() => (c: Command) => commandHaystack(c), []);
  const scored = useFuzzyMatch(commands, query, haystackFn);

  // For the displayed highlight we re-fuzzy on just the label. This gives
  // us <mark> spans on the visible text without exposing the internal
  // keyword concat.
  const labelFn = useMemo(() => (c: Command) => c.label, []);
  const labelMatches = useFuzzyMatch(commands, query, labelFn);
  const labelHighlight = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of labelMatches) {
      map.set(m.item.id, m.highlightedLabel);
    }
    return map;
  }, [labelMatches]);

  // Group the sorted fuzzy results by their `group` field, keeping the
  // first-seen order of groups so the rendering order is stable.
  type Group = { name: string; items: typeof scored };
  const groups: Group[] = useMemo(() => {
    const byName = new Map<string, Group>();
    const order: string[] = [];
    for (const r of scored) {
      const name = r.item.group ?? "";
      if (!byName.has(name)) {
        byName.set(name, { name, items: [] });
        order.push(name);
      }
      byName.get(name)!.items.push(r);
    }
    return order.map((n) => byName.get(n)!);
  }, [scored]);

  // Flat ordered list matching the on-screen selection index.
  const flatOrdered = useMemo(
    () => groups.flatMap((g) => g.items),
    [groups],
  );

  // Clamp selection when the result set changes.
  useEffect(() => {
    if (selectedIndex >= flatOrdered.length) {
      setSelectedIndex(flatOrdered.length > 0 ? 0 : 0);
    }
  }, [flatOrdered.length, selectedIndex]);

  // Scroll selected item into view on selection change.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-palette-index="${selectedIndex}"]`,
    );
    if (el) {
      el.scrollIntoView({ block: "nearest" });
    }
  }, [selectedIndex, open]);

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) =>
        flatOrdered.length === 0 ? 0 : (i + 1) % flatOrdered.length,
      );
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) =>
        flatOrdered.length === 0
          ? 0
          : (i - 1 + flatOrdered.length) % flatOrdered.length,
      );
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const pick = flatOrdered[selectedIndex];
      if (!pick) return;
      void Promise.resolve(pick.item.run()).finally(() => {
        onClose();
      });
    }
  };

  // Running index is assigned as we render each group in order.
  let runningIndex = 0;

  return (
    <div
      className="palette-scrim"
      role="dialog"
      aria-modal="true"
      aria-label={t("command_palette.aria")}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="palette-card palette-card--command" onKeyDown={onKeyDown}>
        <div className="palette-input-row">
          <input
            ref={inputRef}
            className="palette-input"
            type="text"
            placeholder={resolvedPlaceholder}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            aria-label={t("command_palette.query_aria")}
          />
          <span className="palette-kbd">esc</span>
        </div>

        <div className="palette-list" ref={listRef}>
          {flatOrdered.length === 0 ? (
            <div className="palette-empty">
              <p className="palette-empty__line">
                {t("command_palette.no_matches")}
              </p>
              <p className="palette-empty__hint">
                {t("command_palette.no_matches_hint")}
              </p>
            </div>
          ) : (
            groups.map((g) => (
              <div className="palette-group" key={g.name || "_ungrouped"}>
                {g.name ? (
                  <div className="palette-group__heading">{g.name}</div>
                ) : null}
                {g.items.map((r) => {
                  const idx = runningIndex++;
                  const isSelected = idx === selectedIndex;
                  const highlighted =
                    labelHighlight.get(r.item.id) ?? escapeHtml(r.item.label);
                  return (
                    <button
                      key={r.item.id}
                      type="button"
                      data-palette-index={idx}
                      className={
                        "palette-item" +
                        (isSelected ? " palette-item--selected" : "")
                      }
                      onMouseEnter={() => setSelectedIndex(idx)}
                      onClick={() => {
                        void Promise.resolve(r.item.run()).finally(() => {
                          onClose();
                        });
                      }}
                    >
                      <span className="palette-item__main">
                        <span
                          className="palette-item__label"
                          dangerouslySetInnerHTML={{ __html: highlighted }}
                        />
                      </span>
                      {r.item.hint ? (
                        <span className="palette-item__hint">{r.item.hint}</span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

// Safe-by-default fallback for when the label-highlight map is missing an
// entry (e.g. the label-based fuzzy couldn't match but the haystack did
// because of keywords). We mirror the escape logic in useFuzzyMatch so
// the DOM never sees raw HTML from user-provided strings.
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
