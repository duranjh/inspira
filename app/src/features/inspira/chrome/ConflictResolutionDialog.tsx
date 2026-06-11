// B1.2 — Conflict resolution modal.
//
// Opened from ConflictBanner. Renders the two conflicting topics side
// by side with their competing decision statements, plus the
// orchestrator's resolution. "Override" link is a placeholder slot —
// the override flow lives in a sibling slice (which owns project-state
// transitions). Composes the base Dialog at width 520.

import { Dialog } from "../../../components/dialogs/Dialog";

export interface ConflictTopicEntry {
  topic_id: string;
  title: string;
  statement: string;
}

export interface ConflictResolutionDialogProps {
  open: boolean;
  onClose: () => void;
  topics: ConflictTopicEntry[];
  resolution?: string;
  onOverride?: () => void;
}

export function ConflictResolutionDialog({
  open,
  onClose,
  topics,
  resolution,
  onOverride,
}: ConflictResolutionDialogProps) {
  const left = topics[0];
  const right = topics[1];

  return (
    <Dialog open={open} onClose={onClose} title="Conflict resolution" width={520}>
      <div className="conflict-modal__body">
        <div className="conflict-modal__topic">
          <div className="conflict-modal__label">TOPIC A</div>
          <div className="conflict-modal__topic-title">
            {left?.title ?? "—"}
          </div>
          <p className="conflict-modal__statement">
            {left?.statement ?? "No statement recorded."}
          </p>
        </div>
        <div className="conflict-modal__vs" aria-hidden="true">
          VS
        </div>
        <div className="conflict-modal__topic">
          <div className="conflict-modal__label">TOPIC B</div>
          <div className="conflict-modal__topic-title">
            {right?.title ?? "—"}
          </div>
          <p className="conflict-modal__statement">
            {right?.statement ?? "No statement recorded."}
          </p>
        </div>
      </div>
      <div className="conflict-modal__resolution">
        <div className="conflict-modal__label conflict-modal__label--gold">
          ORCHESTRATOR RESOLUTION
        </div>
        <p className="conflict-modal__resolution-text">
          {resolution ?? "Resolution not yet emitted."}
        </p>
      </div>
      {onOverride ? (
        <div className="conflict-modal__footer">
          <button
            type="button"
            className="conflict-modal__override"
            onClick={onOverride}
          >
            Override
          </button>
        </div>
      ) : null}
    </Dialog>
  );
}
