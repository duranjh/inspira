// Category chip — color-coded per the v5 design tokens.
//
// Bug → rust, Feature → sage, Complaint → gold, Praise → sage,
// Question → ink-wash, Noise → ink-wash. The chip is purely
// visual; clicking happens on parent rows / dropdowns.

import { ReactElement } from "react";

import type { FeedbackCategory } from "./types";

const CHIP_VARIANT: Record<FeedbackCategory, string> = {
  bug: "rust",
  feature: "sage",
  complaint: "gold",
  praise: "sage",
  question: "ghost",
  noise: "ghost",
};

const CHIP_LABEL: Record<FeedbackCategory, string> = {
  bug: "Bug",
  feature: "Feature",
  complaint: "Complaint",
  praise: "Praise",
  question: "Question",
  noise: "Noise",
};

export function CategoryChip({
  category,
}: {
  category: FeedbackCategory;
}): ReactElement {
  return (
    <span
      className={`chip chip--${CHIP_VARIANT[category]} inbox-chip`}
      data-category={category}
    >
      {CHIP_LABEL[category]}
    </span>
  );
}

export function isFeedbackCategory(value: unknown): value is FeedbackCategory {
  return (
    typeof value === "string" &&
    (value === "bug" ||
      value === "feature" ||
      value === "complaint" ||
      value === "praise" ||
      value === "question" ||
      value === "noise")
  );
}
