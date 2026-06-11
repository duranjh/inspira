// Floating chip that re-opens the Decision Summary drawer after the
// user has dismissed it. Mounted inside InspiraApp (not ProjectCanvas)
// so the canvas component stays untouched; visually adjacent to the
// canvas top action bar via position:fixed.

import { ReactElement } from "react";

export interface DecisionSummaryShowChipProps {
  visible: boolean;
  onClick: () => void;
}

export function DecisionSummaryShowChip({
  visible,
  onClick,
}: DecisionSummaryShowChipProps): ReactElement | null {
  if (!visible) return null;
  return (
    <button
      type="button"
      className="decision-summary-show-chip"
      onClick={onClick}
      aria-label="Open Inspira's decision summary"
    >
      Show summary
    </button>
  );
}
