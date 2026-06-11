// Decision Summary drawer (B2.5) — right-rail surface that auto-opens
// when the orchestrator finishes a multi-agent run. Mirrors
// FeedbackItemDrawer's backdrop+aside+ESC shape; layout and tokens
// follow the B2.5 mockup at /tmp/inspira-v12/Decision Summary.html.

import { ReactElement, useEffect, useRef, useState } from "react";

const COPY_STATE_RESET_MS = 1500;

import { useDismissOn } from "../../hooks/useDismissOn";
import { useFocusTrap } from "../../hooks/useFocusTrap";
import {
  CtaState,
  DecisionSummary,
  relativeTimeFrom,
  serializeDecisionSummaryToMarkdown,
  themeLabelFor,
  totalDecisionCount,
} from "./decisionSummary";

const TITLE_ID = "decision-summary-drawer-title";
const ARTIFACT_OPEN_EVENT = "inspira:open-artifact";

export interface DecisionSummaryDrawerProps {
  summary: DecisionSummary;
  open: boolean;
  ctaState?: CtaState;
  projectId: string;
  onClose: () => void;
  onGenerateArtifact?: () => void;
  onSendBackForRevision?: () => void;
  onRejectPlan?: () => void;
  onRerunSummary?: () => void;
}

export function DecisionSummaryDrawer({
  summary,
  open,
  ctaState = "default",
  projectId,
  onClose,
  onGenerateArtifact,
  onSendBackForRevision,
  onRejectPlan,
  onRerunSummary,
}: DecisionSummaryDrawerProps): ReactElement | null {
  const asideRef = useRef<HTMLElement | null>(null);
  const kebabWrapRef = useRef<HTMLDivElement | null>(null);
  const copyResetRef = useRef<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [reasoningExpanded, setReasoningExpanded] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">(
    "idle",
  );

  // ESC closes the drawer; autofocus is conditional below so we leave
  // initial focus to the bespoke effect.
  useDismissOn({ enabled: open, onDismiss: onClose });
  const { onKeyDown: drawerKeyDown } = useFocusTrap(asideRef, {
    enabled: open,
    autoFocus: false,
    restoreFocus: true,
  });

  // Focus the drawer on auto-open so screen readers announce it.
  // Guard: don't hijack focus if the user is mid-typing in an editable.
  useEffect(() => {
    if (!open) return;
    const active = document.activeElement as HTMLElement | null;
    const isEditable =
      !!active &&
      (active.isContentEditable ||
        active.tagName === "INPUT" ||
        active.tagName === "TEXTAREA" ||
        active.tagName === "SELECT");
    if (!isEditable) {
      asideRef.current?.focus();
    }
  }, [open]);

  // Click-outside closes the kebab dropdown (Esc on the drawer already
  // dismisses the whole surface; menu-only Esc would be redundant).
  useDismissOn({
    enabled: menuOpen,
    onDismiss: () => setMenuOpen(false),
    esc: false,
    clickOutsideRef: kebabWrapRef,
  });

  // Reset transient state when the drawer fully closes.
  useEffect(() => {
    if (!open) {
      setMenuOpen(false);
      setReasoningExpanded(false);
      setCopyState("idle");
    }
  }, [open]);

  // Cancel any pending copy-state reset on unmount.
  useEffect(() => {
    return () => {
      if (copyResetRef.current !== null) {
        window.clearTimeout(copyResetRef.current);
      }
    };
  }, []);

  if (!open) return null;

  const total = totalDecisionCount(summary);
  const relTime = relativeTimeFrom(summary.finishedAt, new Date());
  const dimmed = ctaState === "approved";

  const handleCopyAsMarkdown = async () => {
    const md = serializeDecisionSummaryToMarkdown(summary);
    const ok = await copyToClipboard(md);
    setCopyState(ok ? "copied" : "error");
    setMenuOpen(false);
    if (copyResetRef.current !== null) {
      window.clearTimeout(copyResetRef.current);
    }
    copyResetRef.current = window.setTimeout(() => {
      setCopyState("idle");
      copyResetRef.current = null;
    }, COPY_STATE_RESET_MS);
  };

  const handlePrint = () => {
    setMenuOpen(false);
    window.print();
  };

  const handleOpenArtifact = () => {
    window.dispatchEvent(
      new CustomEvent(ARTIFACT_OPEN_EVENT, {
        detail: { projectId },
      }),
    );
  };

  return (
    <>
      <div
        className="decision-summary-drawer__backdrop"
        onClick={onClose}
        aria-hidden
      />
      <aside
        ref={asideRef}
        className={
          "decision-summary-drawer" +
          (dimmed ? " decision-summary-drawer--dimmed" : "")
        }
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
        tabIndex={-1}
        onKeyDown={drawerKeyDown}
      >
        <header className="decision-summary-drawer__header">
          <div className="decision-summary-drawer__header-top">
            <button
              type="button"
              className="decision-summary-drawer__back"
              onClick={onClose}
            >
              ← Canvas
            </button>
            <h2
              id={TITLE_ID}
              className="decision-summary-drawer__title"
            >
              Inspira's summary
            </h2>
            <div
              className="decision-summary-drawer__kebab-wrap"
              ref={kebabWrapRef}
            >
              <button
                type="button"
                className="decision-summary-drawer__kebab"
                onClick={() => setMenuOpen((v) => !v)}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                aria-label="Summary actions"
              >
                ⋮
              </button>
              {menuOpen ? (
                <div
                  className="decision-summary-drawer__menu"
                  role="menu"
                >
                  <button
                    type="button"
                    role="menuitem"
                    className="decision-summary-drawer__menu-item"
                    onClick={() => {
                      if (!onRerunSummary) return;
                      setMenuOpen(false);
                      onRerunSummary();
                    }}
                    aria-disabled={!onRerunSummary || undefined}
                    title={
                      onRerunSummary
                        ? undefined
                        : "Available when the orchestrator backend ships"
                    }
                  >
                    Re-run summary
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="decision-summary-drawer__menu-item"
                    onClick={() => void handleCopyAsMarkdown()}
                  >
                    {copyState === "copied"
                      ? "Copied ✓"
                      : copyState === "error"
                        ? "Copy failed"
                        : "Copy as markdown"}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="decision-summary-drawer__menu-item"
                    onClick={handlePrint}
                  >
                    Print
                  </button>
                </div>
              ) : null}
            </div>
          </div>
          <div className="decision-summary-drawer__attribution">
            Orchestrator finished · {relTime} · {summary.subAgentCount}{" "}
            sub-agents contributed.
          </div>
        </header>

        <div
          className="decision-summary-drawer__body"
          aria-live="polite"
        >
          <SectionAddresses summary={summary} />
          <SectionDecisions summary={summary} total={total} />
          <SectionProvenance
            summary={summary}
            expanded={reasoningExpanded}
            onToggle={() => setReasoningExpanded((v) => !v)}
          />
          <SectionTradeoffs summary={summary} />
          <SectionCta
            ctaState={ctaState}
            onGenerateArtifact={onGenerateArtifact}
            onSendBackForRevision={onSendBackForRevision}
            onRejectPlan={onRejectPlan}
            onOpenArtifact={handleOpenArtifact}
          />
        </div>
      </aside>
    </>
  );
}

// --- sections ---------------------------------------------------------

function SectionAddresses({
  summary,
}: {
  summary: DecisionSummary;
}): ReactElement {
  return (
    <section className="decision-summary-card">
      <h3 className="decision-summary-card__heading">What this addresses</h3>
      <p className="decision-summary-card__body">
        {summary.summary_json.headline}
      </p>
      <div className="decision-summary-card__chips">
        {summary.chips.map((chip, i) => (
          <span
            key={i}
            className={`decision-summary-chip decision-summary-chip--${chip.tone}`}
          >
            {chip.hasDot ? (
              <span className={`decision-summary-chip__dot decision-summary-chip__dot--${chip.tone}`} />
            ) : null}
            {chip.label}
          </span>
        ))}
      </div>
    </section>
  );
}

function SectionDecisions({
  summary,
  total,
}: {
  summary: DecisionSummary;
  total: number;
}): ReactElement {
  return (
    <section className="decision-summary-card">
      <h3 className="decision-summary-card__heading">
        Decisions made ({total})
      </h3>
      {summary.summary_json.themes.map((theme) => (
        <div key={theme.theme_id} className="decision-summary-decision-group">
          <div className="decision-summary-decision-group__topic">
            {themeLabelFor(summary, theme)}
          </div>
          {theme.highlights.map((h, hi) => (
            <div key={hi} className="decision-summary-decision">
              <span className="decision-summary-decision__dot" />
              <span className="decision-summary-decision__text">{h}</span>
              <button
                type="button"
                className="decision-summary-decision__why"
                disabled
                title="Per-decision reasoning ships in a follow-up"
              >
                Why →
              </button>
            </div>
          ))}
        </div>
      ))}
      {summary.summary_json.failed_themes.length > 0 ? (
        <div className="decision-summary-decision-group">
          <div className="decision-summary-decision-group__topic">
            Failed themes
          </div>
          {summary.summary_json.failed_themes.map((f) => (
            <div key={f.theme_id} className="decision-summary-decision">
              <span
                className="decision-summary-decision__dot"
                style={{ background: "var(--rust)" }}
              />
              <span className="decision-summary-decision__text">
                {f.theme_id}: {f.error}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SectionProvenance({
  summary,
  expanded,
  onToggle,
}: {
  summary: DecisionSummary;
  expanded: boolean;
  onToggle: () => void;
}): ReactElement {
  return (
    <section className="decision-summary-card">
      <h3 className="decision-summary-card__heading">
        How Inspira reached these decisions
      </h3>
      <div className="decision-summary-provenance">
        {summary.provenance.map((para, i) => (
          <p key={i}>{para}</p>
        ))}
      </div>
      <button
        type="button"
        className="decision-summary-card__link"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        {expanded
          ? "▾ Hide full reasoning trace"
          : "▸ View full reasoning trace →"}
      </button>
      {expanded ? (
        <div className="decision-summary-reason">
          {summary.agents.map((a, i) => (
            <div key={i} className="decision-summary-reason__agent">
              <div className="decision-summary-reason__agent-name">
                {a.name}
              </div>
              <div className="decision-summary-reason__agent-text">
                {a.text}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SectionTradeoffs({
  summary,
}: {
  summary: DecisionSummary;
}): ReactElement {
  return (
    <section className="decision-summary-card">
      <h3 className="decision-summary-card__heading">
        Trade-offs Inspira considered
      </h3>
      <ul className="decision-summary-tradeoffs">
        {summary.summary_json.conflicts.map((c) => (
          <li
            key={`${c.decision_a_id}-${c.decision_b_id}`}
            className="decision-summary-tradeoff"
          >
            {c.subject}: {c.resolution_text}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SectionCta({
  ctaState,
  onGenerateArtifact,
  onSendBackForRevision,
  onRejectPlan,
  onOpenArtifact,
}: {
  ctaState: CtaState;
  onGenerateArtifact?: () => void;
  onSendBackForRevision?: () => void;
  onRejectPlan?: () => void;
  onOpenArtifact: () => void;
}): ReactElement {
  if (ctaState === "approved") {
    return (
      <section className="decision-summary-cta">
        <button
          type="button"
          className="decision-summary-cta__btn decision-summary-cta__btn--ready"
          onClick={onOpenArtifact}
        >
          Artifact ready · Open →
        </button>
        <div className="decision-summary-cta__footer">
          The code artifact has been generated. Open it to review, edit
          inline, or export to GitHub / Linear.
        </div>
      </section>
    );
  }

  return (
    <section className="decision-summary-cta">
      <button
        type="button"
        className="decision-summary-cta__btn"
        onClick={onGenerateArtifact}
      >
        Generate the artifact (code) →
      </button>
      <div className="decision-summary-cta__links">
        <button
          type="button"
          className="decision-summary-cta__link decision-summary-cta__link--gold"
          onClick={onSendBackForRevision}
        >
          Send back to AI for revision
        </button>
        <button
          type="button"
          className="decision-summary-cta__link decision-summary-cta__link--rust"
          onClick={onRejectPlan}
        >
          Reject this plan
        </button>
      </div>
      <div className="decision-summary-cta__footer">
        Once you generate the artifact, Inspira will produce the code that
        implements these decisions. You can edit it inline or in chat.
      </div>
    </section>
  );
}

// --- clipboard helper -------------------------------------------------
// Mirrors LlmModesPanel.tsx:251 — modern async API first, legacy
// execCommand fallback for unfocused tabs / non-secure contexts.

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  if (typeof document === "undefined") return false;
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  try {
    ta.select();
    ta.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(ta);
  }
}
