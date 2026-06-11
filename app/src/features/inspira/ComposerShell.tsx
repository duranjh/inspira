// ComposerShell — shared composer skeleton used by the canvas composer
// (ProjectCanvas) and the topic-detail composer. Both compose the same
// three-piece layout:
//
//   [ + ] [ pill with input + optional inline chips ] [ → ]
//
// Desktop: the three pieces render as three distinct elements — circle
// plus on the left, pill in the middle, circle send on the right. The
// `+` opens a small popover menu with "Attach files" and "Add link".
//
// Mobile (≤640px): CSS merges the three pieces into a single pill. When
// the input is empty (``data-typing="false"``) the `+` sits INSIDE the
// pill's left padding; once the user starts typing (``data-typing="true"``)
// the `+` translates out of the pill to its own circle, opening a small
// gap. See App.css `.composer-shell` mobile block.
//
// No local submission logic — the parent owns submit, disabled state,
// and the actual textarea/input element. We only render chrome around
// a slot.
//
// This is a minimal refactor: existing parent markup still provides
// the `form` element and the actual `<input>`. The shell wraps the
// interior (`.canvas-composer__inner` / `.topic-detail__composer-inner`
// equivalent row) with a typing-aware wrapper and a plus-menu popover.

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { t } from "../../i18n";

export type ComposerShellProps = {
  /** Variant controls the BEM prefix on the inner classes. */
  variant: "canvas" | "topic-detail";
  /**
   * The current input value — used only to toggle the mobile "typing"
   * animation. Parent keeps authoritative state.
   */
  value: string;
  /** Whether the composer is disabled (e.g. submission in-flight). */
  disabled?: boolean;
  /** Disable link affordance (URL fetch in flight, etc.). */
  addLinkDisabled?: boolean;
  /** Current "add link" label (swaps to "Fetching…" mid-fetch). */
  addLinkLabel?: string;
  /** Accessible label for the attach-files button. */
  attachAriaLabel: string;
  /** Accessible label for the add-link menu item. */
  linkAriaLabel: string;
  /** Plus-menu button aria-label. */
  plusAriaLabel?: string;
  /** Called when the user picks "Attach files" from the plus menu. */
  onOpenFilePicker: () => void;
  /** Called when the user picks "Add link" from the plus menu. */
  onAddLink: () => void;
  /** The input / textarea — rendered as-is inside the pill. */
  inputSlot: ReactNode;
  /** Optional content placed between the input and send — e.g. the ModelTierChip. */
  trailingSlot?: ReactNode;
  /** The submit button — rendered as-is. */
  sendSlot: ReactNode;
};

/**
 * Renders the inner row of a composer: plus button (with menu), pill (with
 * input inside), send button. Mobile CSS in App.css collapses the three
 * into one connected pill and animates the plus out as the user types.
 */
export function ComposerShell({
  variant,
  value,
  disabled,
  addLinkDisabled,
  addLinkLabel,
  attachAriaLabel,
  linkAriaLabel,
  plusAriaLabel,
  onOpenFilePicker,
  onAddLink,
  inputSlot,
  trailingSlot,
  sendSlot,
}: ComposerShellProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const plusRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();

  const closeMenu = useCallback(() => setMenuOpen(false), []);

  // Dismiss the menu on outside pointerdown or Escape.
  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      const plus = plusRef.current;
      const menu = menuRef.current;
      const target = e.target as globalThis.Node | null;
      if (plus && target && plus.contains(target)) return;
      if (menu && target && menu.contains(target)) return;
      setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        plusRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const typing = value.trim().length > 0;
  const rootClass =
    variant === "canvas"
      ? "canvas-composer__inner composer-shell composer-shell--canvas"
      : "topic-detail__composer-inner composer-shell composer-shell--topic";

  const plusClass =
    variant === "canvas"
      ? "canvas-composer__attach composer-shell__plus"
      : "topic-detail__composer-attach composer-shell__plus";

  const pickAttach = useCallback(() => {
    setMenuOpen(false);
    onOpenFilePicker();
  }, [onOpenFilePicker]);

  const pickLink = useCallback(() => {
    setMenuOpen(false);
    onAddLink();
  }, [onAddLink]);

  return (
    <div
      className={rootClass}
      data-typing={typing ? "true" : "false"}
      data-menu-open={menuOpen ? "true" : "false"}
    >
      <div className="composer-shell__plus-wrap">
        <button
          ref={plusRef}
          type="button"
          className={plusClass}
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={plusAriaLabel ?? t("composer.plus_menu_aria")}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-controls={menuOpen ? menuId : undefined}
          disabled={disabled}
        >
          <span aria-hidden="true">+</span>
        </button>
        {menuOpen ? (
          <div
            ref={menuRef}
            id={menuId}
            role="menu"
            aria-label={t("composer.plus_menu_aria")}
            className="composer-shell__menu"
          >
            <button
              type="button"
              role="menuitem"
              className="composer-shell__menu-item"
              onClick={pickAttach}
              disabled={disabled}
            >
              <span aria-hidden="true" className="composer-shell__menu-glyph">
                +
              </span>
              <span>{attachAriaLabel}</span>
            </button>
            <button
              type="button"
              role="menuitem"
              className="composer-shell__menu-item"
              onClick={pickLink}
              disabled={disabled || addLinkDisabled}
            >
              <span aria-hidden="true" className="composer-shell__menu-glyph">
                ↗
              </span>
              <span>{addLinkLabel ?? linkAriaLabel}</span>
            </button>
          </div>
        ) : null}
      </div>
      {inputSlot}
      {trailingSlot ?? null}
      {sendSlot}
    </div>
  );
}
