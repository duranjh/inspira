import { useCallback, useRef, useState } from "react";

import type { PrOverlayStalenessResponse } from "../api";

export type PendingEditTarget = {
  projectId: string;
  filePath: string;
};

export type SoftEditDecision =
  | { proceed: true; reason: "not_stale" | "dismissed" | "no_staleness_data" }
  | { proceed: false; reason: "modal_opened" };

export type UseSoftEditBlockReturn = {
  /** Non-null while the modal is open; identifies the file the user
   *  tried to edit. The modal mounts off this. */
  pendingEditTarget: PendingEditTarget | null;
  /** Call this at the moment the partner tries to enter edit mode on
   *  a specific file. Returns ``proceed: true`` when the edit should
   *  flow through unblocked (not stale, or already dismissed for this
   *  session); ``proceed: false`` when the modal has been opened and
   *  the parent should NOT flip ``codeReadOnly`` yet. */
  requestEdit: (
    target: PendingEditTarget,
    staleness: PrOverlayStalenessResponse | null,
  ) => SoftEditDecision;
  /** User clicked "Edit anyway". Records dismissal for this
   *  (project, file) pair so reopening doesn't re-prompt within the
   *  same session, then returns the pending target so the parent can
   *  flip ``codeReadOnly``. */
  confirmEdit: () => PendingEditTarget | null;
  /** User clicked Cancel or dismissed the modal. Closes without
   *  recording a dismissal — reopening the same file WILL prompt
   *  again, which is the right behavior: the partner explicitly
   *  backed out, so future open should still warn. */
  cancelEdit: () => void;
};

/**
 * Wave F.5 — soft edit-block dispatcher.
 *
 * Holds the in-flight ``PendingEditTarget`` and a session-scoped set of
 * ``${projectId}::${filePath}`` keys that the partner has confirmed
 * with "Edit anyway". The set lives in a ref (not state) because
 * mutating it shouldn't trigger a re-render — only the modal's open
 * state should. Reset on page reload by design: drift signals are
 * transient, and re-prompting after a refresh keeps the partner
 * honest about working against stale files.
 */
export function useSoftEditBlock(): UseSoftEditBlockReturn {
  const [pendingEditTarget, setPendingEditTarget] =
    useState<PendingEditTarget | null>(null);
  const dismissedRef = useRef<Set<string>>(new Set<string>());

  const dismissedKey = (target: PendingEditTarget): string =>
    `${target.projectId}::${target.filePath}`;

  const requestEdit = useCallback(
    (
      target: PendingEditTarget,
      staleness: PrOverlayStalenessResponse | null,
    ): SoftEditDecision => {
      if (!staleness) {
        return { proceed: true, reason: "no_staleness_data" };
      }
      if (staleness.legacy || !staleness.is_stale) {
        return { proceed: true, reason: "not_stale" };
      }
      if (dismissedRef.current.has(dismissedKey(target))) {
        return { proceed: true, reason: "dismissed" };
      }
      setPendingEditTarget(target);
      return { proceed: false, reason: "modal_opened" };
    },
    [],
  );

  const confirmEdit = useCallback((): PendingEditTarget | null => {
    const target = pendingEditTarget;
    if (target) {
      dismissedRef.current.add(dismissedKey(target));
    }
    setPendingEditTarget(null);
    return target;
  }, [pendingEditTarget]);

  const cancelEdit = useCallback((): void => {
    setPendingEditTarget(null);
  }, []);

  return {
    pendingEditTarget,
    requestEdit,
    confirmEdit,
    cancelEdit,
  };
}
