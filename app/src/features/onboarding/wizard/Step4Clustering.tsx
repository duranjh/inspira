// Wizard Step 4 — clustering animation + final navigate.
//
// Two variants:
// - Has feedback (sample / Linear / CSV): show the animation
//   (5 cards fade in, status feed fades in line by line) → after
//   ~3.2s, navigate to /workspaces.
// - Skipped feedback (no Linear / no CSV / no sample): show the
//   "Inspira is ready, but quiet." variant + Take-me-to-Inbox CTA.
//
// Reduced-motion path: skip the fade-in, render the static
// end-state, advance after ~500ms.
//
// The animation is mocked — F4/F5 IS running server-side for the
// real-import paths, but the wizard's animation timing is decoupled
// (product decision; SSE wire is a follow-up).

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { WizardState } from "./OnboardingWizard";

type Step4Props = {
  state: WizardState;
  onFinish: () => void;
};

const STATUS_LINES = [
  "Reading 247 feedback items…",
  "Clustering by theme — 5 themes detected: Mobile login, Search ranking, Onboarding emails, Dashboard load, Slack integration",
  "Prioritizing by ROI…",
  "Spawning 5 sub-agents…",
  "Drafting decisions…",
  "Ready.",
];

const CARD_POSITIONS = [
  { left: 30, top: 25, delay: "0s", title: "Mobile login" },
  { left: 160, top: 15, delay: "0.4s", title: "Search ranking" },
  { left: 290, top: 40, delay: "0.8s", title: "Onboarding" },
  { left: 95, top: 130, delay: "1.2s", title: "Dashboard" },
  { left: 250, top: 145, delay: "1.6s", title: "Slack" },
];

const LINES = [
  { x1: 130, y1: 50, x2: 160, y2: 40, delay: "0.6s" },
  { x1: 260, y1: 40, x2: 290, y2: 60, delay: "1.0s" },
  { x1: 130, y1: 77, x2: 130, y2: 130, delay: "1.4s" },
  { x1: 260, y1: 77, x2: 280, y2: 145, delay: "1.8s" },
];

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export function Step4Clustering({ state, onFinish }: Step4Props) {
  const navigate = useNavigate();
  const hasFeedback = state.csvImported || state.linearConnected;
  const reducedMotion = prefersReducedMotion();

  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!hasFeedback) return;
    // ~3.2s for the full animation (last status line at delay 4000ms),
    // collapsed to 500ms under reduced-motion.
    const wait = reducedMotion ? 500 : 3200;
    const timer = window.setTimeout(() => {
      setDone(true);
    }, wait);
    return () => window.clearTimeout(timer);
  }, [hasFeedback, reducedMotion]);

  useEffect(() => {
    if (done) {
      onFinish();
      navigate("/workspaces", { replace: true });
    }
  }, [done, navigate, onFinish]);

  if (!hasFeedback) {
    return (
      <div className="ob-center">
        <h1 className="ob-headline">Inspira is ready, but quiet.</h1>
        <p className="ob-subtitle">
          Connect a feedback channel from Settings to wake it up.
        </p>
        <button
          type="button"
          className="ob-cta"
          onClick={() => {
            onFinish();
            navigate("/workspaces", { replace: true });
          }}
        >
          Take me to my workspace →
        </button>
      </div>
    );
  }

  return (
    <div className="ob-center">
      <h1 className="ob-headline">Inspira is reading your data.</h1>
      <p className="ob-subtitle">
        Hang tight — first pass takes about 2 minutes.
      </p>
      <div className="ob-anim" aria-hidden="true">
        {LINES.map((l, i) => (
          <svg
            key={`line-${i}`}
            className="ob-anim__line"
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: "100%",
              height: "100%",
            }}
          >
            <line
              x1={l.x1}
              y1={l.y1}
              x2={l.x2}
              y2={l.y2}
              style={
                reducedMotion ? { opacity: 0.5 } : { animationDelay: l.delay }
              }
            />
          </svg>
        ))}
        {CARD_POSITIONS.map((c, i) => (
          <div
            key={`card-${i}`}
            className="ob-anim__card"
            style={{
              left: c.left,
              top: c.top,
              animationDelay: reducedMotion ? "0s" : c.delay,
              opacity: reducedMotion ? 1 : undefined,
            }}
          >
            <div className="ob-anim__card-title">{c.title}</div>
            <div
              className="ob-anim__card-line"
              style={{ width: "70%" }}
            />
            <div
              className="ob-anim__card-line"
              style={{ width: "50%" }}
            />
            <div
              className="ob-anim__card-line"
              style={{ width: "60%" }}
            />
          </div>
        ))}
      </div>
      <div className="ob-status-feed" role="status" aria-live="polite">
        {STATUS_LINES.map((line, i) => (
          <div
            key={i}
            className="ob-status-line"
            style={
              reducedMotion
                ? { animationDelay: "0s", opacity: 1 }
                : { animationDelay: `${i * 500}ms` }
            }
          >
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}
