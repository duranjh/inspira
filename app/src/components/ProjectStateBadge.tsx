import "./ProjectStateBadge.css";

/**
 * The five states the project state machine recognises (B3.3). The
 * fifth, ``summary_ready``, is reserved for a post-W4 feature — the
 * badge renders it for forward-compat (Session δ may show it on a
 * canvas right-rail under manual override) but the workspace Kanban
 * (W4) never surfaces it as a column today.
 */
export type ProjectState =
  | "pending_review"
  | "in_review"
  | "approved"
  | "rejected"
  | "summary_ready";

export type ProjectStateBadgeSize = "compact" | "default" | "large";

export type ProjectStateBadgeProps = {
  state: ProjectState;
  size?: ProjectStateBadgeSize;
  /**
   * Only rendered when ``size === "large"``. The Kanban card uses
   * ``size="compact"`` and never passes attribution; Session δ's
   * canvas right-rail uses ``size="large"`` and passes the latest
   * audit-row's actor + relative timestamp.
   */
  attribution?: { actorName: string; changedAt: string } | null;
};

const STATE_LABELS: Record<ProjectState, string> = {
  pending_review: "Pending review",
  in_review: "In review",
  approved: "Approved",
  rejected: "Rejected",
  summary_ready: "Summary ready",
};

const STATE_ABBR: Record<ProjectState, string> = {
  pending_review: "P",
  in_review: "R",
  approved: "A",
  rejected: "X",
  summary_ready: "S",
};

const STATE_ICON: Record<ProjectState, string> = {
  pending_review: "•",
  in_review: "\u{1F441}",
  approved: "✓",
  rejected: "—",
  summary_ready: "★",
};

/**
 * Editorial state pill — italic serif text, dotted-grid background,
 * dashed pseudo-element outline. Imported by the workspace Kanban
 * card row (W4) and Session δ's canvas review actions panel
 * (B3.3 §1). Both consumers share the same atom; never inline a
 * one-off pill — the visual contract is locked here.
 */
export function ProjectStateBadge(props: ProjectStateBadgeProps) {
  const { state, size = "default", attribution } = props;
  const cls = [
    "psb",
    `psb--${size}`,
    `psb--${stateClassName(state)}`,
  ].join(" ");
  if (size === "large") {
    return (
      <span className={cls} data-state={state} data-size={size}>
        <span className="psb__row">
          <span className="psb__icon" aria-hidden="true">
            {STATE_ICON[state]}
          </span>
          <span className="psb__label">{STATE_LABELS[state]}</span>
          {state === "summary_ready" && (
            <span className="psb__future-tag">post-W4</span>
          )}
        </span>
        {attribution && (
          <span className="psb__attribution">
            Last changed by {attribution.actorName}
            {" · "}
            {attribution.changedAt}
          </span>
        )}
      </span>
    );
  }
  if (size === "compact") {
    return (
      <span className={cls} data-state={state} data-size={size}>
        <span className="psb__icon" aria-hidden="true">
          {STATE_ICON[state]}
        </span>
        <span className="psb__abbr">{STATE_ABBR[state]}</span>
        {/* Screen readers get the full label so the abbreviation
            isn't hostile to assistive tech. */}
        <span className="psb__sr-only">{STATE_LABELS[state]}</span>
      </span>
    );
  }
  // size === "default"
  return (
    <span className={cls} data-state={state} data-size={size}>
      <span className="psb__icon" aria-hidden="true">
        {STATE_ICON[state]}
      </span>
      <span className="psb__label">{STATE_LABELS[state]}</span>
    </span>
  );
}

/**
 * Map the snake_case state to a kebab-friendly class suffix. Kept
 * as a separate helper so the test file can exercise it without
 * rendering — the CSS file's selectors must stay in sync.
 */
function stateClassName(state: ProjectState): string {
  // ``summary_ready`` is the only multi-word state; the rest already
  // have stable single-word lower-case forms.
  return state.replace(/_/g, "-");
}
