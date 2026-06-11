// Pill that appears on a regenerated decision after cascade-commit.
// Three age states drive distinct styling (fresh pulses; recent is
// static fill; stale is faded outline).

import React from "react";

import type { VersionAge } from "./types";

export type DiffBadgeProps = {
  age: VersionAge;
  lastChangedAt: string;
  changeNote?: string | null;
};

function _formatAgo(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const diffSec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  return `${Math.round(diffSec / 86400)}d ago`;
}

export function DiffBadge({
  age,
  lastChangedAt,
  changeNote,
}: DiffBadgeProps): React.JSX.Element {
  return (
    <span
      className={`cc-diff-badge cc-diff-badge--${age}`}
      title={changeNote ?? undefined}
      data-cc-no-select
    >
      <span className="cc-diff-badge__label">Changed</span>
      <span className="cc-diff-badge__time">{_formatAgo(lastChangedAt)}</span>
    </span>
  );
}
