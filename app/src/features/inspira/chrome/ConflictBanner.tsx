// B1.2 / W3 δ — Conflict resolution banner.
//
// Sits in the canvas top-right area. Mounts only when α emits
// `conflict.detected`; unmounts on `conflict.resolved`. Click "View
// resolution →" opens the ConflictResolutionDialog. The banner itself
// is an inline-flex pill so it doesn't displace the canvas layout when
// it appears.

import { useEffect, useState } from "react";

import {
  ConflictResolutionDialog,
  type ConflictTopicEntry,
} from "./ConflictResolutionDialog";

interface ConflictDetail {
  conflict_id?: string;
  topics?: ConflictTopicEntry[];
  resolution?: string;
}

export function ConflictBanner() {
  const [conflict, setConflict] = useState<ConflictDetail | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const onDetected = (e: Event) => {
      const detail = (e as CustomEvent<ConflictDetail>).detail;
      if (!detail) return;
      setConflict(detail);
    };
    const onResolved = (e: Event) => {
      const detail = (e as CustomEvent<ConflictDetail>).detail;
      // If the resolved id matches what we're showing (or no id is set),
      // dismiss the banner. Multiple concurrent conflicts aren't supported
      // in B1.2; a follow-up can stack them.
      setConflict((prev) => {
        if (!prev) return null;
        if (
          detail?.conflict_id &&
          prev.conflict_id &&
          detail.conflict_id !== prev.conflict_id
        ) {
          return prev;
        }
        return null;
      });
      setModalOpen(false);
    };

    window.addEventListener("inspira:sse:conflict.detected", onDetected);
    window.addEventListener("inspira:sse:conflict.resolved", onResolved);
    return () => {
      window.removeEventListener("inspira:sse:conflict.detected", onDetected);
      window.removeEventListener("inspira:sse:conflict.resolved", onResolved);
    };
  }, []);

  if (!conflict) return null;

  const topics = conflict.topics ?? [];
  const left = topics[0]?.title ?? "Topic A";
  const right = topics[1]?.title ?? "Topic B";

  return (
    <>
      <div
        className="conflict-banner"
        role="status"
        aria-live="polite"
        aria-label={`Orchestrator resolving conflict between ${left} and ${right}`}
      >
        <span className="conflict-banner__icon" aria-hidden="true">
          ⚑
        </span>
        <span className="conflict-banner__text">
          Orchestrator resolving conflict · {left} vs {right}
        </span>
        <button
          type="button"
          className="conflict-banner__link"
          onClick={() => setModalOpen(true)}
        >
          View resolution →
        </button>
      </div>
      <ConflictResolutionDialog
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        topics={topics}
        resolution={conflict.resolution}
      />
    </>
  );
}
