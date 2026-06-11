// Inspira — first-run workspace tour for v4 partners.
//
// Triggered on the partner's first /workspaces visit after the
// onboarding wizard. Two phases:
//   1. Welcome modal — "Want a 60-second tour?" with Show me / Skip.
//   2. Six-step coachmark sequence walking the rail, the Kanban,
//      the tag filter, the drag-spawn affordance, the in-review
//      column, and the approved column.
//
// Persistence: localStorage[`inspira_workspace_tour_completed`] flips
// to "true" on completion (or skip), so the flow runs at most once
// per browser. The user menu's "Show tour again" item clears the
// flag and routes back to /workspaces (see UserMenu.tsx).
//
// Empty-state behaviour: the existing Coachmark engine silently
// skips a step whose `targetSelector` doesn't resolve, so a partner
// who skipped feedback at Step 3 of the wizard (no Queue cards,
// no in-review cards) still sees the rail + Kanban + tag-filter
// steps and quietly skips the rest.

import { useEffect, useState } from "react";
import { Coachmark, type CoachmarkStep } from "../../components/Coachmark";
import "./WorkspaceTour.css";

const STORAGE_KEY = "inspira_workspace_tour_completed";

const WORKSPACE_TOUR_STEPS: CoachmarkStep[] = [
  {
    id: "rail",
    targetSelector: ".app-rail",
    title: "Three rooms.",
    body:
      "Workspaces is your Kanban. Connectors is where feedback comes in. Inbox holds raw items.",
    placement: "right",
  },
  {
    id: "kanban",
    targetSelector: ".kb-board",
    title: "Issues, sifted from feedback.",
    body:
      "Cards move left → right as Inspira drafts, you review, and code ships.",
    placement: "bottom",
  },
  {
    id: "tag-filter",
    targetSelector: ".kb-tag-filter",
    title: "Filter by triage type.",
    body:
      "Bugs, complaints, features. Praise + questions live in the Inbox — they don't need shipping.",
    placement: "bottom",
  },
  {
    id: "drag-spawn",
    targetSelector: '.kb-col[data-column-id="queue"] .kb-card:first-child',
    title: "Drag an issue to put AI on it.",
    body:
      "Drop a card into In Progress and Inspira drafts the topics + decisions. Or click in to watch.",
    placement: "right",
  },
  {
    id: "review",
    targetSelector: '.kb-col[data-column-id="in_review"]',
    title: "Review the AI's draft.",
    body:
      "Once Inspira finishes drafting, you review on the Canvas — every topic, every decision.",
    placement: "bottom",
  },
  {
    id: "ship",
    targetSelector: '.kb-col[data-column-id="approved"]',
    title: "Approve → Push.",
    body:
      "Approved issues open a PR back to your repo. From feedback to features.",
    placement: "bottom",
  },
];

type Phase = "checking" | "welcome" | "running" | "done";

function readCompleted(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return true; // pessimistic: skip the tour rather than spam
  }
}

function markCompleted(): void {
  try {
    localStorage.setItem(STORAGE_KEY, "true");
  } catch {
    /* storage disabled — non-fatal */
  }
}

export function WorkspaceTour() {
  const [phase, setPhase] = useState<Phase>("checking");

  useEffect(() => {
    if (readCompleted()) {
      setPhase("done");
    } else {
      setPhase("welcome");
    }
  }, []);

  if (phase === "checking" || phase === "done") return null;

  if (phase === "welcome") {
    return (
      <div
        className="wt-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wt-welcome-title"
        onClick={(e) => {
          // Click outside the card = dismiss as if Skip
          if (e.target === e.currentTarget) {
            markCompleted();
            setPhase("done");
          }
        }}
      >
        <div className="wt-card">
          <h2 id="wt-welcome-title" className="wt-title">
            Welcome to Inspira.
          </h2>
          <p className="wt-body">
            Want a 60-second tour of how feedback becomes shipped code?
          </p>
          <div className="wt-actions">
            <button
              type="button"
              className="wt-btn wt-btn--ghost"
              onClick={() => {
                markCompleted();
                setPhase("done");
              }}
            >
              Skip — I'll explore on my own
            </button>
            <button
              type="button"
              className="wt-btn wt-btn--primary"
              onClick={() => setPhase("running")}
              autoFocus
            >
              Show me →
            </button>
          </div>
        </div>
      </div>
    );
  }

  // phase === "running"
  return (
    <Coachmark
      steps={WORKSPACE_TOUR_STEPS}
      storageKey={STORAGE_KEY}
      active={true}
      onDone={() => setPhase("done")}
    />
  );
}
