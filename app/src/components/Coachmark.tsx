// Inspira — Coachmark in-situ onboarding system.
//
// Renders a soft-spotlight + paper card beside a target DOM element.
// Each flow is defined as a list of CoachmarkSteps. The flow is skipped
// entirely if localStorage[storageKey] === "true".
//
// Design:
//   - Dim overlay (rgba 0,0,0,0.35) with a box-shadow "cut-out" around
//     the target so it reads as spotlit. Target remains fully clickable.
//   - Small serif paper card with eyebrow (Step N of M), title, italic
//     body, Skip link, and Next / Got-it button.
//   - Keyboard: Esc skips; ArrowRight next; ArrowLeft back.
//   - Target not found → step silently skipped, flow advances.
//   - Initial position computed after one rAF so React Flow nodes have
//     fully laid out before we measure them.

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { t } from "../i18n";
import "./Coachmark.css";

export type CoachmarkStep = {
  id: string;
  targetSelector: string;
  title: string;
  body: string;
  placement?: "top" | "bottom" | "left" | "right";
};

export type CoachmarkFlowProps = {
  steps: CoachmarkStep[];
  storageKey: string;
  active: boolean;
  onDone?: () => void;
};

const GAP = 16; // px between target edge and card

// Returns a target element, or null if not found or outside viewport.
function resolveTarget(selector: string): Element | null {
  try {
    return document.querySelector(selector);
  } catch {
    return null;
  }
}

type Rect = { top: number; left: number; width: number; height: number };
type CardPos = { top: number; left: number };

function computeCardPosition(
  targetRect: DOMRect,
  cardWidth: number,
  cardHeight: number,
  placement: "top" | "bottom" | "left" | "right" | undefined,
): CardPos {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Candidate placements in priority order. If a specific placement is
  // requested we try it first; otherwise we auto-pick the direction with
  // the most space.
  const auto: Array<"bottom" | "top" | "right" | "left"> =
    placement
      ? [placement as "bottom" | "top" | "right" | "left", "bottom", "top", "right", "left"]
      : (() => {
          const spaceBelow = vh - targetRect.bottom;
          const spaceAbove = targetRect.top;
          const spaceRight = vw - targetRect.right;
          const spaceLeft = targetRect.left;
          const ordered = (
            [
              ["bottom", spaceBelow],
              ["top", spaceAbove],
              ["right", spaceRight],
              ["left", spaceLeft],
            ] as Array<["bottom" | "top" | "right" | "left", number]>
          ).sort((a, b) => b[1] - a[1]);
          return ordered.map((x) => x[0]);
        })();

  for (const dir of auto) {
    let top = 0;
    let left = 0;
    if (dir === "bottom") {
      top = targetRect.bottom + GAP;
      left = targetRect.left + targetRect.width / 2 - cardWidth / 2;
    } else if (dir === "top") {
      top = targetRect.top - cardHeight - GAP;
      left = targetRect.left + targetRect.width / 2 - cardWidth / 2;
    } else if (dir === "right") {
      top = targetRect.top + targetRect.height / 2 - cardHeight / 2;
      left = targetRect.right + GAP;
    } else {
      top = targetRect.top + targetRect.height / 2 - cardHeight / 2;
      left = targetRect.left - cardWidth - GAP;
    }

    // Clamp to viewport with 8px margin.
    const m = 8;
    left = Math.max(m, Math.min(vw - cardWidth - m, left));
    top = Math.max(m, Math.min(vh - cardHeight - m, top));

    // Accept the first placement that fits (card doesn't overlap target).
    const overlapsH =
      left < targetRect.right + GAP / 2 &&
      left + cardWidth > targetRect.left - GAP / 2;
    const overlapsV =
      top < targetRect.bottom + GAP / 2 &&
      top + cardHeight > targetRect.top - GAP / 2;
    if (!overlapsH || !overlapsV) {
      return { top, left };
    }
  }

  // Fallback: bottom-centre
  return {
    top: Math.min(targetRect.bottom + GAP, vh - cardHeight - 8),
    left: Math.max(8, vw / 2 - cardWidth / 2),
  };
}

export function Coachmark({
  steps,
  storageKey,
  active,
  onDone,
}: CoachmarkFlowProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [spotlightRect, setSpotlightRect] = useState<Rect | null>(null);
  const [cardPos, setCardPos] = useState<CardPos | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);

  // Already completed — render nothing.
  if (typeof window !== "undefined" && localStorage.getItem(storageKey) === "true") {
    return null;
  }

  // Not yet triggered.
  if (!active) return null;

  // All steps exhausted (shouldn't normally reach here, but guard it).
  if (stepIndex >= steps.length) return null;

  return (
    <CoachmarkInner
      steps={steps}
      storageKey={storageKey}
      stepIndex={stepIndex}
      setStepIndex={setStepIndex}
      spotlightRect={spotlightRect}
      setSpotlightRect={setSpotlightRect}
      cardPos={cardPos}
      setCardPos={setCardPos}
      cardRef={cardRef}
      onDone={onDone}
    />
  );
}

type InnerProps = {
  steps: CoachmarkStep[];
  storageKey: string;
  stepIndex: number;
  setStepIndex: (n: number) => void;
  spotlightRect: Rect | null;
  setSpotlightRect: (r: Rect | null) => void;
  cardPos: CardPos | null;
  setCardPos: (p: CardPos | null) => void;
  cardRef: React.RefObject<HTMLDivElement | null>;
  onDone?: () => void;
};

function CoachmarkInner({
  steps,
  storageKey,
  stepIndex,
  setStepIndex,
  spotlightRect,
  setSpotlightRect,
  cardPos,
  setCardPos,
  cardRef,
  onDone,
}: InnerProps) {
  const complete = useCallback(() => {
    try {
      localStorage.setItem(storageKey, "true");
    } catch {
      /* storage disabled — ignore */
    }
    onDone?.();
  }, [storageKey, onDone]);

  // Advance to next step, skipping steps whose targets don't exist.
  const advance = useCallback(
    (fromIndex: number) => {
      let next = fromIndex + 1;
      while (next < steps.length) {
        const el = resolveTarget(steps[next].targetSelector);
        if (el) break;
        next++;
      }
      if (next >= steps.length) {
        complete();
      } else {
        setStepIndex(next);
      }
    },
    [steps, complete, setStepIndex],
  );

  const goBack = useCallback(
    (fromIndex: number) => {
      let prev = fromIndex - 1;
      while (prev >= 0) {
        const el = resolveTarget(steps[prev].targetSelector);
        if (el) break;
        prev--;
      }
      if (prev >= 0) setStepIndex(prev);
    },
    [steps, setStepIndex],
  );

  // Find the current step that has a resolvable target, skipping forward
  // silently if the initial index target is missing.
  const resolvedIndexRef = useRef<number>(stepIndex);

  // Position computation — runs after rAF so React Flow / other
  // dynamic content has finished its first paint.
  const measureAndPosition = useCallback(() => {
    const raf = requestAnimationFrame(() => {
      let idx = stepIndex;
      let el: Element | null = null;
      // Skip forward until we find a step with a real element.
      while (idx < steps.length) {
        el = resolveTarget(steps[idx].targetSelector);
        if (el) break;
        idx++;
      }
      resolvedIndexRef.current = idx;
      if (!el || idx >= steps.length) {
        // Nothing left — complete.
        complete();
        return;
      }
      const tr = (el as HTMLElement).getBoundingClientRect();
      setSpotlightRect({
        top: tr.top - 4,
        left: tr.left - 4,
        width: tr.width + 8,
        height: tr.height + 8,
      });
      // Card dimensions — estimate or read from ref.
      const cardW = cardRef.current?.offsetWidth ?? 280;
      const cardH = cardRef.current?.offsetHeight ?? 180;
      const pos = computeCardPosition(tr, cardW, cardH, steps[idx].placement);
      setCardPos(pos);
    });
    return raf;
  }, [stepIndex, steps, complete, setSpotlightRect, setCardPos, cardRef]);

  useEffect(() => {
    const raf = measureAndPosition();
    return () => cancelAnimationFrame(raf);
  }, [measureAndPosition]);

  // Re-measure on window resize. Track the rAF id from the last
  // measure so an unmount between `resize` firing and the rAF tick
  // cancels the pending callback — otherwise `setSpotlightRect` /
  // `setCardPos` fire on a gone component and React warns.
  useEffect(() => {
    let pendingRaf: number | null = null;
    const onResize = () => {
      if (pendingRaf !== null) cancelAnimationFrame(pendingRaf);
      pendingRaf = measureAndPosition();
    };
    const opts: AddEventListenerOptions = { passive: true };
    window.addEventListener("resize", onResize, opts);
    return () => {
      window.removeEventListener("resize", onResize, opts);
      if (pendingRaf !== null) cancelAnimationFrame(pendingRaf);
    };
  }, [measureAndPosition]);

  // Keyboard: Esc = skip, ArrowRight = next, ArrowLeft = back.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        complete();
      } else if (e.key === "ArrowRight") {
        advance(stepIndex);
      } else if (e.key === "ArrowLeft") {
        goBack(stepIndex);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [complete, advance, goBack, stepIndex]);

  // After stepIndex changes, if the current step's target doesn't resolve,
  // silently skip forward.
  useEffect(() => {
    const el = resolveTarget(steps[stepIndex]?.targetSelector ?? "");
    if (!el && steps[stepIndex]) {
      advance(stepIndex);
    }
  }, [stepIndex, steps, advance]);

  // Global interaction lock: while a coachmark is on screen, the user
  // should not be able to scroll the page, pan/zoom the canvas, or
  // click any background control. The CSS overlay eats pointer events
  // from its own subtree, but wheel + keyboard scroll fire on window/
  // document and need an explicit block. We also lock body overflow so
  // the outer page can't scroll.
  useEffect(() => {
    const prevBodyOverflow = document.body.style.overflow;
    const prevBodyOverscroll = document.body.style.overscrollBehavior;
    document.body.style.overflow = "hidden";
    document.body.style.overscrollBehavior = "contain";

    // Stop wheel + touchmove from scrolling the underlying page.
    // Coach card itself allows scrolling internally via overflow:auto
    // — we only block gestures that land OUTSIDE the card.
    const stopIfOutsideCard = (e: Event) => {
      const card = cardRef.current;
      if (!card) return;
      const target = e.target as Node | null;
      if (target && card.contains(target)) return;
      e.preventDefault();
      e.stopPropagation();
    };
    // Keyboard scroll keys: PageUp/Down, Space, Arrows, Home/End.
    const stopScrollKeys = (e: KeyboardEvent) => {
      const card = cardRef.current;
      const target = e.target as Node | null;
      if (card && target && card.contains(target)) return;
      const scrollKeys = new Set([
        "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        "PageUp", "PageDown", "Home", "End", " ",
      ]);
      if (scrollKeys.has(e.key)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    const wheelOpts: AddEventListenerOptions = { passive: false };
    const touchOpts: AddEventListenerOptions = { passive: false };
    const keyOpts: AddEventListenerOptions = { capture: true };
    window.addEventListener("wheel", stopIfOutsideCard, wheelOpts);
    window.addEventListener("touchmove", stopIfOutsideCard, touchOpts);
    window.addEventListener("keydown", stopScrollKeys, keyOpts);
    return () => {
      document.body.style.overflow = prevBodyOverflow;
      document.body.style.overscrollBehavior = prevBodyOverscroll;
      window.removeEventListener("wheel", stopIfOutsideCard, wheelOpts);
      window.removeEventListener("touchmove", stopIfOutsideCard, touchOpts);
      window.removeEventListener("keydown", stopScrollKeys, keyOpts);
    };
  }, [cardRef]);

  const currentStep = steps[stepIndex];
  if (!currentStep) return null;

  // Visible step number — count only steps that have resolvable targets
  // for the eyebrow count. Simpler: just use 1-based index of the logical
  // step list (including silently-skipped ones is fine for the counter).
  const displayCurrent = stepIndex + 1;
  const displayTotal = steps.length;
  const isLast = stepIndex === steps.length - 1;

  const portal = (
    <div className="coachmark-overlay" aria-label={currentStep.title}>
      {/* Spotlight element */}
      {spotlightRect ? (
        <div
          className="coachmark-spotlight"
          style={{
            top: spotlightRect.top,
            left: spotlightRect.left,
            width: spotlightRect.width,
            height: spotlightRect.height,
          }}
          aria-hidden="true"
        />
      ) : null}

      {/* Card */}
      {cardPos ? (
        <div
          ref={cardRef}
          className="coachmark-card"
          style={{ top: cardPos.top, left: cardPos.left }}
        >
          <span className="coachmark-card__eyebrow">
            {t("coachmark.step_of", { current: displayCurrent, total: displayTotal })}
          </span>
          <h2 className="coachmark-card__title">{currentStep.title}</h2>
          <p className="coachmark-card__body">{currentStep.body}</p>
          <div className="coachmark-card__footer">
            <button
              type="button"
              className="coachmark-card__skip"
              onClick={complete}
            >
              {t("coachmark.skip")}
            </button>
            <button
              type="button"
              className="coachmark-card__next"
              onClick={() => advance(stepIndex)}
              autoFocus
            >
              {isLast ? t("coachmark.done") : t("coachmark.next")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );

  return createPortal(portal, document.body);
}
