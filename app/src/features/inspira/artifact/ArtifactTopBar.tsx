import { ReactElement, ReactNode, useEffect, useRef, useState } from "react";

import { t } from "../../../i18n";

export type KebabAction = "regenerate" | "copy_all";

export type ArtifactTopBarProps = {
  title: string;
  onBack: () => void;
  onSendToLinear: () => void;
  onSendToGithub: () => void;
  onKebabAction: (action: KebabAction) => void;
  /** Optional slot for the ApprovalChip — rendered between the title
   *  and the action cluster. Threaded through so ArtifactTopBar
   *  doesn't need to know about ProjectState directly. The chip is
   *  the canonical state surface for the artifact's Draft → In Review
   *  → Approved review lifecycle (product decision: code IS what gets
   *  approved, not the canvas). The legacy "✓ Approved · {age}"
   *  badge that read project.updated_at was redundant + misleading
   *  (showed "Approved" for any in_review project whose orchestrator
   *  finished hours ago) — removed to match the chip. */
  approvalSlot?: ReactNode;
};

export function ArtifactTopBar({
  title,
  onBack,
  onSendToLinear,
  onSendToGithub,
  onKebabAction,
  approvalSlot,
}: ArtifactTopBarProps): ReactElement {
  const [menuOpen, setMenuOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Click-outside closes the kebab.
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const node = wrapRef.current;
      if (!node) return;
      if (e.target instanceof Node && !node.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  return (
    <header className="av-top">
      <button
        type="button"
        className="av-top__back"
        onClick={onBack}
      >
        {t("artifact.back_to_canvas")}
      </button>
      <div className="av-top__title-wrap">
        <h1 className="av-top__title">{title}</h1>
      </div>
      {approvalSlot ? (
        <div className="av-top__approval">{approvalSlot}</div>
      ) : null}
      <div className="av-top__actions">
        <button
          type="button"
          className="av-top__btn av-top__btn--primary"
          onClick={onSendToLinear}
        >
          {t("artifact.send_to_linear")}
        </button>
        <button
          type="button"
          className="av-top__btn av-top__btn--ghost"
          onClick={onSendToGithub}
        >
          {t("artifact.send_to_github")}
        </button>
        <div className="av-top__kebab-wrap" ref={wrapRef}>
          <button
            type="button"
            className="av-top__kebab"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="More actions"
            onClick={() => setMenuOpen((v) => !v)}
          >
            ⋮
          </button>
          {menuOpen ? (
            <ul className="av-top__menu" role="menu">
              <li role="none">
                <button
                  type="button"
                  role="menuitem"
                  className="av-top__menu-item"
                  onClick={() => {
                    setMenuOpen(false);
                    onKebabAction("regenerate");
                  }}
                >
                  {t("artifact.kebab.regenerate")}
                </button>
              </li>
              <li role="none">
                <button
                  type="button"
                  role="menuitem"
                  className="av-top__menu-item"
                  onClick={() => {
                    setMenuOpen(false);
                    onKebabAction("copy_all");
                  }}
                >
                  {t("artifact.kebab.copy_all")}
                </button>
              </li>
            </ul>
          ) : null}
        </div>
      </div>
    </header>
  );
}
