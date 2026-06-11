import { diffLines, type Change } from "diff";
import {
  useEffect,
  useMemo,
  useState,
  type ReactElement,
} from "react";

import { Dialog } from "../../../components/dialogs/Dialog";
import type {
  RefreshDecision,
  RefreshDecisionKind,
  RefreshDiffFile,
  RefreshDiffResponse,
  RefreshResolveResponse,
} from "../api";

type RefreshReviewModalProps = {
  open: boolean;
  diff: RefreshDiffResponse | null;
  refreshing: boolean;
  error: Error | null;
  onSubmit: (
    decisions: Record<string, RefreshDecision>,
  ) => Promise<RefreshResolveResponse | null>;
  onClose: () => void;
};

type DecisionMap = Record<string, RefreshDecision>;

/**
 * Wave F.6 — review modal that opens after a successful "Refresh PR
 * with Inspira" run. Lets the partner pick file-by-file whether to
 * accept Inspira's redraft, keep their edit, or hand-merge.
 *
 * Composes the shared ``Dialog`` shell for focus trap + Esc + backdrop
 * dismiss. Inside the body the layout is a two-column split: file
 * picker on the left (~30% width), diff panes on the right.
 *
 * Diff rendering uses the ``diff`` library's ``diffLines`` to compute
 * line-level changes; render is plain CSS (no react-diff-viewer
 * dependency) to keep the bundle delta small.
 */
export function RefreshReviewModal({
  open,
  diff,
  refreshing,
  error,
  onSubmit,
  onClose,
}: RefreshReviewModalProps): ReactElement {
  const files: RefreshDiffFile[] = diff?.files ?? [];
  const [decisions, setDecisions] = useState<DecisionMap>({});
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);

  // Reset internal state every time a fresh diff payload comes in.
  useEffect(() => {
    if (!diff) {
      setDecisions({});
      setSelectedPath(null);
      return;
    }
    const seed: DecisionMap = {};
    for (const f of diff.files) {
      // Default: accept the AI redraft for every file. The partner
      // can override per-file or via bulk actions.
      seed[f.path] = { decision: "accept_redraft" };
    }
    setDecisions(seed);
    setSelectedPath(diff.files[0]?.path ?? null);
  }, [diff]);

  const activeFile = useMemo(
    () => files.find((f) => f.path === selectedPath) ?? null,
    [files, selectedPath],
  );

  function setDecision(
    path: string, kind: RefreshDecisionKind,
    mergedContent?: string,
  ): void {
    setDecisions((prev) => ({
      ...prev,
      [path]: kind === "merged"
        ? { decision: kind, merged_content: mergedContent ?? "" }
        : { decision: kind },
    }));
  }

  function bulkAcceptAll(): void {
    setDecisions((prev) => {
      const next: DecisionMap = { ...prev };
      for (const f of files) next[f.path] = { decision: "accept_redraft" };
      return next;
    });
  }

  function bulkKeepAll(): void {
    setDecisions((prev) => {
      const next: DecisionMap = { ...prev };
      for (const f of files) {
        next[f.path] = f.partner_edit !== null
          ? { decision: "keep_partner_edit" }
          : { decision: "accept_redraft" };
      }
      return next;
    });
  }

  async function handleSubmit(): Promise<void> {
    setSubmitting(true);
    try {
      const result = await onSubmit(decisions);
      if (result) {
        onClose();
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (refreshing) {
    return (
      <Dialog
        open={open}
        onClose={onClose}
        title="Inspira is redrafting…"
      >
        <p className="av-refresh-modal__loading">
          Inspira is reworking this PR on top of the latest main and
          your edits. This usually takes 30–90 seconds.
        </p>
      </Dialog>
    );
  }

  if (error) {
    return (
      <Dialog
        open={open}
        onClose={onClose}
        title="Refresh failed"
        primaryAction={{
          label: "Close",
          onClick: onClose,
        }}
      >
        <p className="av-refresh-modal__error">{error.message}</p>
      </Dialog>
    );
  }

  if (!diff) {
    return (
      <Dialog open={open} onClose={onClose} title="">
        <p />
      </Dialog>
    );
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Review Inspira's redraft"
      primaryAction={{
        label: submitting ? "Applying…" : "Apply decisions",
        onClick: handleSubmit,
        busy: submitting,
      }}
      secondaryAction={{
        label: "Cancel",
        onClick: onClose,
      }}
    >
      <p className="av-refresh-modal__header">
        Inspira redrafted this PR on top of your edits and the new
        main. Here's everything that changed — accept all, reject all,
        or pick file-by-file.
      </p>
      <div className="av-refresh-modal__bulk">
        <button
          type="button"
          className="av-refresh-modal__bulk-btn"
          onClick={bulkAcceptAll}
        >
          Accept all AI changes
        </button>
        <button
          type="button"
          className="av-refresh-modal__bulk-btn"
          onClick={bulkKeepAll}
        >
          Keep all my edits
        </button>
      </div>
      <div className="av-refresh-modal__body">
        <div className="av-refresh-modal__filelist">
          {files.length === 0 ? (
            <p className="av-refresh-modal__empty">
              No file changes. The redraft is identical to your current
              scaffold.
            </p>
          ) : (
            <ul>
              {files.map((f) => {
                const decision = decisions[f.path]?.decision
                  ?? "accept_redraft";
                const chip = f.conflict
                  ? "Conflict"
                  : f.partner_edit !== null
                    ? "You edited"
                    : "AI changed";
                return (
                  <li
                    key={f.path}
                    className={
                      "av-refresh-modal__file-row"
                      + (selectedPath === f.path ? " is-active" : "")
                    }
                  >
                    <button
                      type="button"
                      className="av-refresh-modal__file-btn"
                      onClick={() => setSelectedPath(f.path)}
                    >
                      <span className="av-refresh-modal__file-path">
                        {f.path}
                      </span>
                      <span
                        className={
                          "av-refresh-modal__file-chip "
                          + `av-refresh-modal__file-chip--${
                            f.conflict
                              ? "conflict"
                              : f.partner_edit !== null
                                ? "you"
                                : "ai"
                          }`
                        }
                      >
                        {chip}
                      </span>
                      <span
                        className={
                          "av-refresh-modal__file-decision"
                          + ` av-refresh-modal__file-decision--${decision}`
                        }
                      >
                        {decisionLabel(decision)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="av-refresh-modal__diff">
          {activeFile ? (
            <DiffPanes
              file={activeFile}
              decision={decisions[activeFile.path]}
              onDecision={(kind, mergedContent) =>
                setDecision(activeFile.path, kind, mergedContent)
              }
            />
          ) : (
            <p className="av-refresh-modal__diff-empty">
              Select a file on the left to review the diff.
            </p>
          )}
        </div>
      </div>
    </Dialog>
  );
}

function decisionLabel(kind: RefreshDecisionKind): string {
  if (kind === "accept_redraft") return "Accept AI";
  if (kind === "keep_partner_edit") return "Keep my edit";
  return "Merged";
}

type DiffPanesProps = {
  file: RefreshDiffFile;
  decision: RefreshDecision | undefined;
  onDecision: (
    kind: RefreshDecisionKind, mergedContent?: string,
  ) => void;
};

function DiffPanes({
  file, decision, onDecision,
}: DiffPanesProps): ReactElement {
  const has3Way = file.partner_edit !== null;
  const baseToRedraft: Change[] = useMemo(
    () => diffLines(file.base ?? "", file.ai_redraft ?? ""),
    [file.base, file.ai_redraft],
  );
  const partnerToRedraft: Change[] | null = useMemo(
    () =>
      file.partner_edit !== null
        ? diffLines(file.partner_edit, file.ai_redraft ?? "")
        : null,
    [file.partner_edit, file.ai_redraft],
  );

  const decisionKind: RefreshDecisionKind =
    decision?.decision ?? "accept_redraft";
  const mergedContent =
    decision?.merged_content ?? (file.ai_redraft ?? "");

  return (
    <div className="av-refresh-diff">
      <div className="av-refresh-diff__decisions">
        <label>
          <input
            type="radio"
            name={`decision-${file.path}`}
            value="accept_redraft"
            checked={decisionKind === "accept_redraft"}
            onChange={() => onDecision("accept_redraft")}
          />
          Accept Inspira's redraft
        </label>
        <label
          className={has3Way ? "" : "av-refresh-diff__decision--hidden"}
        >
          <input
            type="radio"
            name={`decision-${file.path}`}
            value="keep_partner_edit"
            checked={decisionKind === "keep_partner_edit"}
            onChange={() => onDecision("keep_partner_edit")}
            disabled={!has3Way}
          />
          Keep my edit
        </label>
        <label>
          <input
            type="radio"
            name={`decision-${file.path}`}
            value="merged"
            checked={decisionKind === "merged"}
            onChange={() => onDecision("merged", mergedContent)}
          />
          Merged (custom)
        </label>
      </div>
      <div
        className={
          "av-refresh-diff__panes"
          + (has3Way
            ? " av-refresh-diff__panes--three"
            : " av-refresh-diff__panes--two")
        }
      >
        <DiffPane
          title={has3Way ? "Original AI" : "Previous draft"}
          changes={baseToRedraft.map(invertSides)}
        />
        {partnerToRedraft && has3Way ? (
          <DiffPane
            title="Your edit"
            changes={partnerToRedraft.map(invertSides)}
          />
        ) : null}
        <DiffPane
          title="Inspira's redraft"
          changes={baseToRedraft}
        />
      </div>
      {decisionKind === "merged" ? (
        <textarea
          className="av-refresh-diff__merged-textarea"
          value={mergedContent}
          onChange={(e) => onDecision("merged", e.target.value)}
          placeholder="Hand-merged content lands here."
          rows={10}
        />
      ) : null}
    </div>
  );
}

/** Render one column of the diff. Walks the Change[] list once and
 *  emits classed lines so CSS can highlight ``--added`` /
 *  ``--removed`` / ``--unchanged``. */
function DiffPane({
  title, changes,
}: {
  title: string; changes: Change[];
}): ReactElement {
  return (
    <div className="av-refresh-diff__pane">
      <h4 className="av-refresh-diff__pane-title">{title}</h4>
      <pre className="av-refresh-diff__pane-body">
        {changes.flatMap((c, idx) => {
          const cls = c.added
            ? "av-refresh-diff__line av-refresh-diff__line--added"
            : c.removed
              ? "av-refresh-diff__line av-refresh-diff__line--removed"
              : "av-refresh-diff__line av-refresh-diff__line--unchanged";
          const lines = c.value.split("\n");
          // diffLines emits a trailing empty string after a final \n;
          // skip it to avoid an empty highlighted row.
          if (lines.length > 0 && lines[lines.length - 1] === "") {
            lines.pop();
          }
          return lines.map((line, lineIdx) => (
            <span
              key={`${idx}-${lineIdx}`}
              className={cls}
            >
              {line || " "}
              {"\n"}
            </span>
          ));
        })}
      </pre>
    </div>
  );
}

/** Swap added/removed on a Change so the same underlying diff renders
 *  correctly when reading "left to right" vs "right to left" — we
 *  invert one of the two pane columns so the left column shows the
 *  source state and the right shows the redraft. */
function invertSides(c: Change): Change {
  if (c.added) return { ...c, added: false, removed: true };
  if (c.removed) return { ...c, added: true, removed: false };
  return c;
}
