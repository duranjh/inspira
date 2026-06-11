// Pure presentational atom — renders the four chip variants from B4.5.
// All state lives in the parent (AIStatus.tsx). The chip is a real
// <button> so keyboard users can activate it; the dashed double-border
// ::before keeps the hand-drawn feel (see ai-status.css).
//
// Accepts a forwarded ref so the parent can restore focus to the chip
// when the orchestrator panel closes.

import { forwardRef } from "react";
import type { ForwardedRef } from "react";

import type { AIStatusState } from "./types";

const DOT_COLOR: Record<Exclude<AIStatusState, "conflict">, "sage" | "gold" | "rust"> = {
  idle: "sage",
  running: "gold",
  failed: "rust",
};

export interface AIStatusChipProps {
  state: AIStatusState;
  label: string;
  onClick: () => void;
  ariaExpanded: boolean;
  ariaControls: string;
}

export const AIStatusChip = forwardRef(function AIStatusChip(
  { state, label, onClick, ariaExpanded, ariaControls }: AIStatusChipProps,
  ref: ForwardedRef<HTMLButtonElement>,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={`os-chip os-chip--${state}`}
      aria-haspopup="true"
      aria-expanded={ariaExpanded}
      aria-controls={ariaControls}
      onClick={onClick}
    >
      {state === "conflict" ? (
        <span className="os-chip__warn" aria-hidden="true">
          ⚠
        </span>
      ) : (
        <span
          className={`os-chip__dot os-chip__dot--${DOT_COLOR[state]}`}
          aria-hidden="true"
        />
      )}
      {label}
    </button>
  );
});
