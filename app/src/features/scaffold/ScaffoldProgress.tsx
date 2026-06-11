/**
 * ScaffoldProgress — fake-but-helpful progress surface for the
 * scaffold-generation round-trip.
 *
 * The real generation is one LLM call, so there's no interim data. The
 * intent here is perceptual pacing: instead of a static "loading…"
 * spinner, we walk through a sequence of plausible phase labels while
 * the call is in flight. This is purely visual — the component never
 * drives the request, it only observes the parent's ``running`` flag.
 *
 * Respects prefers-reduced-motion: when set, we drop the shimmer
 * animation and hold a single static status string so the user isn't
 * subjected to a perpetual moving placeholder.
 */

import { useEffect, useState, type ReactElement } from "react";

import { t } from "../../i18n";

// Phase keys — labels are retrieved at render time via t() so locale
// changes mid-session are reflected immediately.
const PHASE_KEYS = [
  "scaffold.progress.phase_structure",
  "scaffold.progress.phase_manifest",
  "scaffold.progress.phase_readme",
  "scaffold.progress.phase_sources",
  "scaffold.progress.phase_sealing",
] as const;

const PHASE_INTERVAL_MS = 2200;

// Rough skeleton of a file tree — six rows of shimmer bars with varying
// widths so the layout doesn't look mechanical. The widths are hand-
// chosen to echo a real scaffold: long path (README), shorter paths
// (package.json, tsconfig), etc.
const SKELETON_WIDTHS = ["48%", "62%", "72%", "56%", "68%", "44%"];

export type ScaffoldProgressProps = {
  /** True while the generation request is in flight. */
  running: boolean;
};

export function ScaffoldProgress(
  { running }: ScaffoldProgressProps,
): ReactElement | null {
  const [phaseIndex, setPhaseIndex] = useState(0);

  useEffect(() => {
    if (!running) {
      setPhaseIndex(0);
      return;
    }
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      // One static phase; no timer so the DOM is quiet.
      setPhaseIndex(0);
      return;
    }
    const timer = window.setInterval(() => {
      setPhaseIndex((prev) => Math.min(prev + 1, PHASE_KEYS.length - 1));
    }, PHASE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!running) return null;

  const phaseKey = PHASE_KEYS[phaseIndex] ?? PHASE_KEYS[0];
  const status = t(phaseKey);

  return (
    <div
      className="scaffold-progress"
      role="status"
      aria-live="polite"
      aria-label={t("scaffold.progress.aria")}
    >
      <p className="scaffold-progress__status">{status}</p>
      <ul className="scaffold-progress__tree" aria-hidden="true">
        {SKELETON_WIDTHS.map((w, i) => (
          <li
            key={i}
            className="scaffold-progress__file"
            style={{ width: w }}
          />
        ))}
      </ul>
    </div>
  );
}
