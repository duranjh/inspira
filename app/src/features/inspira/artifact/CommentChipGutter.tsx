import {
  type ReactElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { EditorView } from "@codemirror/view";

import { type ArtifactComment, type ArtifactCommentCategory } from "../api";
import {
  type UseArtifactCommentsReturn,
  hashArtifactCommentLine,
} from "./useArtifactComments";

/**
 * Wave F.4 — inline IDE-style comment chip gutter.
 *
 * Absolutely-positioned overlay sibling to the CodeMirror view. For each
 * comment thread anchored to a (file, line) it renders a chip at the
 * line's vertical offset (sage outline → gold filled when saved). Hover
 * over an empty gutter row → a ghost "+" chip fades in; clicking it
 * opens a small popover for body + category. Saved chips expand into
 * an inline thread lozenge with reply + resolve affordances.
 *
 * The overlay reuses the parent CodeMirror viewport — it doesn't
 * scroll on its own. ``viewTick`` increments whenever the editor
 * fires an update (doc edit, scroll, geometry change) so the chip
 * positions stay anchored.
 */
type ThreadGroup = {
  parent: ArtifactComment;
  replies: ArtifactComment[];
};

type ChipState = "saved" | "resolved" | "stale";

export interface CommentChipGutterProps {
  view: EditorView | null;
  /** Increments whenever the editor view updates; triggers a re-anchor
   *  of all chips. The owner passes a counter that gets bumped via
   *  ``CodeMirrorEditor.onViewUpdate``. */
  viewTick: number;
  filePath: string | null;
  comments: ArtifactComment[];
  loading: boolean;
  createComment: UseArtifactCommentsReturn["createComment"];
  updateComment: UseArtifactCommentsReturn["updateComment"];
  /** When the editor file content changes between renders, we need
   *  fresh line strings to detect staleness. Comes from CodeEditor's
   *  draft buffer. */
  fileContent: string;
}

const CATEGORY_OPTIONS: { value: ArtifactCommentCategory; label: string }[] = [
  { value: "question", label: "Question" },
  { value: "concern", label: "Concern" },
  { value: "suggest_fix", label: "Suggest fix" },
];

function groupThreads(comments: ArtifactComment[]): Map<number, ThreadGroup[]> {
  const byParent = new Map<string, ArtifactComment[]>();
  const tops: ArtifactComment[] = [];
  for (const c of comments) {
    if (c.parent_comment_id) {
      const arr = byParent.get(c.parent_comment_id) ?? [];
      arr.push(c);
      byParent.set(c.parent_comment_id, arr);
    } else {
      tops.push(c);
    }
  }
  const byLine = new Map<number, ThreadGroup[]>();
  for (const parent of tops) {
    const replies = (byParent.get(parent.comment_id) ?? []).slice().sort(
      (a, b) => a.created_at.localeCompare(b.created_at),
    );
    const arr = byLine.get(parent.line_number) ?? [];
    arr.push({ parent, replies });
    byLine.set(parent.line_number, arr);
  }
  return byLine;
}

function relativeTimeAgo(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const s = Math.max(0, Math.round((now - then) / 1000));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  } catch {
    return "";
  }
}

export function CommentChipGutter(
  props: CommentChipGutterProps,
): ReactElement | null {
  const {
    view,
    viewTick,
    filePath,
    comments,
    createComment,
    updateComment,
    fileContent,
  } = props;
  // Anchored line state shared by the resting chips + the ghost-add
  // chip + the open popover. ``hoverLine`` is the line under the
  // mouse (0 = none); ``popoverLine`` is non-null when the popover is
  // open for that line; ``threadParentId`` is the parent_comment_id
  // whose thread is currently expanded inline.
  const [hoverLine, setHoverLine] = useState<number>(0);
  const [popoverLine, setPopoverLine] = useState<number | null>(null);
  const [threadParentId, setThreadParentId] = useState<string | null>(null);
  const [popoverBody, setPopoverBody] = useState<string>("");
  const [popoverCategory, setPopoverCategory] =
    useState<ArtifactCommentCategory>("question");
  const [replyBody, setReplyBody] = useState<string>("");
  const [staleSet, setStaleSet] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);

  // Recompute stale set whenever comments or fileContent change.
  useEffect(() => {
    if (!view) {
      setStaleSet(new Set());
      return;
    }
    let cancelled = false;
    (async () => {
      const stale = new Set<string>();
      for (const c of comments) {
        if (filePath !== c.file_path) continue;
        const doc = view.state.doc;
        if (c.line_number < 1 || c.line_number > doc.lines) {
          // Off-doc line → mark stale.
          stale.add(c.comment_id);
          continue;
        }
        const line = doc.line(c.line_number);
        const hash = await hashArtifactCommentLine(line.text);
        if (hash !== c.line_content_hash && c.line_content_hash !== "") {
          stale.add(c.comment_id);
        }
      }
      if (!cancelled) setStaleSet(stale);
    })();
    return () => {
      cancelled = true;
    };
    // viewTick + fileContent included so a doc edit re-runs the
    // staleness sweep even if comments + view identity are unchanged.
  }, [view, comments, filePath, viewTick, fileContent]);

  const threadsByLine = useMemo(() => {
    if (!filePath) return new Map<number, ThreadGroup[]>();
    return groupThreads(
      comments.filter(
        (c) => c.file_path === filePath && c.resolved_at === null,
      ),
    );
  }, [comments, filePath]);

  const resolvedByLine = useMemo(() => {
    if (!filePath) return new Map<number, ThreadGroup[]>();
    return groupThreads(
      comments.filter(
        (c) => c.file_path === filePath && c.resolved_at !== null,
      ),
    );
  }, [comments, filePath]);

  // Compute Y offset for a given 1-indexed line number.
  const yForLine = useCallback(
    (lineNumber: number): number | null => {
      if (!view) return null;
      const doc = view.state.doc;
      if (lineNumber < 1 || lineNumber > doc.lines) return null;
      const pos = doc.line(lineNumber).from;
      const coords = view.coordsAtPos(pos);
      if (!coords) return null;
      const hostRect = hostRef.current?.getBoundingClientRect();
      if (!hostRect) return null;
      return coords.top - hostRect.top;
    },
    // viewTick keeps callers from caching a stale closure across
    // scroll/edit updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [view, viewTick],
  );

  const handleHostMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!view) return;
      const hostRect = hostRef.current?.getBoundingClientRect();
      if (!hostRect) return;
      // Use the editor's viewport, not the host's, so posAtCoords
      // matches whichever line the cursor is over.
      const pos = view.posAtCoords({ x: hostRect.left + 4, y: e.clientY });
      if (pos === null) {
        setHoverLine(0);
        return;
      }
      const line = view.state.doc.lineAt(pos);
      setHoverLine(line.number);
    },
    [view],
  );

  const handleHostMouseLeave = useCallback(() => {
    setHoverLine(0);
  }, []);

  const openPopover = useCallback((lineNumber: number) => {
    setPopoverLine(lineNumber);
    setPopoverBody("");
    setPopoverCategory("question");
    setSubmitError(null);
    setThreadParentId(null);
  }, []);

  const closePopover = useCallback(() => {
    setPopoverLine(null);
    setPopoverBody("");
    setSubmitError(null);
  }, []);

  const submitNewComment = useCallback(async () => {
    if (!filePath || popoverLine === null || !view) return;
    if (!popoverBody.trim()) {
      setSubmitError("Add a comment first");
      return;
    }
    const doc = view.state.doc;
    if (popoverLine < 1 || popoverLine > doc.lines) {
      setSubmitError("Line not found in the editor");
      return;
    }
    const lineContent = doc.line(popoverLine).text;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await createComment({
        filePath,
        lineNumber: popoverLine,
        lineContent,
        category: popoverCategory,
        body: popoverBody.trim(),
      });
      closePopover();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [
    filePath,
    popoverLine,
    popoverBody,
    popoverCategory,
    createComment,
    closePopover,
    view,
  ]);

  const submitReply = useCallback(
    async (parent: ArtifactComment) => {
      if (!filePath || !view) return;
      if (!replyBody.trim()) return;
      const doc = view.state.doc;
      const lineNumber = parent.line_number;
      if (lineNumber < 1 || lineNumber > doc.lines) return;
      const lineContent = doc.line(lineNumber).text;
      setSubmitting(true);
      try {
        await createComment({
          filePath,
          lineNumber,
          lineContent,
          category: parent.category,
          body: replyBody.trim(),
          parentCommentId: parent.comment_id,
        });
        setReplyBody("");
      } catch (err) {
        setSubmitError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [filePath, replyBody, createComment, view],
  );

  const toggleResolve = useCallback(
    async (parent: ArtifactComment) => {
      try {
        await updateComment(parent.comment_id, {
          resolved: parent.resolved_at === null,
        });
      } catch (err) {
        setSubmitError(err instanceof Error ? err.message : String(err));
      }
    },
    [updateComment],
  );

  if (!view || !filePath) return null;

  const chipForLine = (
    lineNumber: number,
    group: ThreadGroup,
    state: ChipState,
  ): ReactElement | null => {
    const y = yForLine(lineNumber);
    if (y === null) return null;
    const isStale = state === "stale";
    const isResolved = state === "resolved";
    const expanded = threadParentId === group.parent.comment_id;
    return (
      <div
        key={group.parent.comment_id}
        className={`av-comment-chip av-comment-chip--saved${
          isStale ? " av-comment-chip--stale" : ""
        }${isResolved ? " av-comment-chip--resolved" : ""}`}
        style={{ top: y }}
        title={
          isStale
            ? "Line content changed since comment"
            : `${group.parent.category} · ${relativeTimeAgo(group.parent.created_at)}`
        }
      >
        <button
          type="button"
          className="av-comment-chip__btn"
          onClick={() => {
            setPopoverLine(null);
            setThreadParentId(expanded ? null : group.parent.comment_id);
          }}
          aria-label={`Open thread on line ${lineNumber}`}
          aria-expanded={expanded}
        >
          <span className="av-comment-chip__dot" />
        </button>
        {expanded ? (
          <div
            className="av-comment-thread"
            role="dialog"
            aria-label={`Comment thread on line ${lineNumber}`}
          >
            <div className="av-comment-thread__body">
              <div className="av-comment-thread__meta">
                <span className="av-comment-thread__category">
                  {group.parent.category}
                </span>
                <span className="av-comment-thread__time">
                  {relativeTimeAgo(group.parent.created_at)}
                </span>
              </div>
              <p className="av-comment-thread__text">{group.parent.body}</p>
            </div>
            {group.replies.map((r) => (
              <div key={r.comment_id} className="av-comment-thread__reply">
                <div className="av-comment-thread__meta">
                  <span className="av-comment-thread__time">
                    {relativeTimeAgo(r.created_at)}
                  </span>
                </div>
                <p className="av-comment-thread__text">{r.body}</p>
              </div>
            ))}
            {isResolved ? (
              <div className="av-comment-thread__resolved-badge">
                Resolved {relativeTimeAgo(group.parent.resolved_at ?? "")}
                <button
                  type="button"
                  className="av-comment-thread__reopen"
                  onClick={() => toggleResolve(group.parent)}
                >
                  Reopen
                </button>
              </div>
            ) : (
              <>
                <textarea
                  className="av-comment-thread__reply-input"
                  placeholder="Reply…"
                  value={replyBody}
                  onChange={(e) => setReplyBody(e.target.value)}
                  rows={2}
                />
                <div className="av-comment-thread__actions">
                  <button
                    type="button"
                    className="av-comment-thread__resolve"
                    onClick={() => toggleResolve(group.parent)}
                    disabled={submitting}
                  >
                    Resolve
                  </button>
                  <button
                    type="button"
                    className="av-comment-thread__send"
                    onClick={() => submitReply(group.parent)}
                    disabled={submitting || !replyBody.trim()}
                  >
                    Reply
                  </button>
                </div>
              </>
            )}
            {submitError ? (
              <p className="av-comment-thread__error">{submitError}</p>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  };

  const popoverY =
    popoverLine !== null ? yForLine(popoverLine) : null;
  const hoverY = hoverLine > 0 ? yForLine(hoverLine) : null;
  const hoverHasThread =
    hoverLine > 0 && (threadsByLine.get(hoverLine)?.length ?? 0) > 0;

  return (
    <div
      ref={hostRef}
      className="av-comment-gutter"
      onMouseMove={handleHostMouseMove}
      onMouseLeave={handleHostMouseLeave}
    >
      {/* Resting chips (open threads). */}
      {Array.from(threadsByLine.entries()).flatMap(([line, groups]) =>
        groups.map((g) => {
          const isStale = staleSet.has(g.parent.comment_id);
          return chipForLine(line, g, isStale ? "stale" : "saved");
        }),
      )}

      {/* Resolved chips — hollow gold, expand to show "Resolved" badge. */}
      {Array.from(resolvedByLine.entries()).flatMap(([line, groups]) =>
        groups.map((g) => chipForLine(line, g, "resolved")),
      )}

      {/* Ghost-add chip on hover (only when no open thread on that line). */}
      {hoverY !== null && !hoverHasThread && popoverLine === null ? (
        <button
          type="button"
          className="av-comment-chip av-comment-chip--ghost"
          style={{ top: hoverY }}
          onClick={() => openPopover(hoverLine)}
          aria-label={`Add comment to line ${hoverLine}`}
        >
          +
        </button>
      ) : null}

      {/* Popover for a new comment. */}
      {popoverLine !== null && popoverY !== null ? (
        <div
          className="av-comment-popover"
          style={{ top: popoverY }}
          role="dialog"
          aria-label={`Add comment to line ${popoverLine}`}
        >
          <textarea
            className="av-comment-popover__input"
            placeholder="Add a comment…"
            value={popoverBody}
            onChange={(e) => setPopoverBody(e.target.value)}
            rows={3}
            autoFocus
          />
          <fieldset className="av-comment-popover__categories">
            <legend className="av-comment-popover__legend">Category</legend>
            {CATEGORY_OPTIONS.map((opt) => (
              <label key={opt.value} className="av-comment-popover__radio">
                <input
                  type="radio"
                  name="av-comment-category"
                  value={opt.value}
                  checked={popoverCategory === opt.value}
                  onChange={() => setPopoverCategory(opt.value)}
                />
                <span>{opt.label}</span>
              </label>
            ))}
          </fieldset>
          {submitError ? (
            <p className="av-comment-popover__error">{submitError}</p>
          ) : null}
          <div className="av-comment-popover__actions">
            <button
              type="button"
              className="av-comment-popover__cancel"
              onClick={closePopover}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="button"
              className="av-comment-popover__save"
              onClick={submitNewComment}
              disabled={submitting || !popoverBody.trim()}
            >
              Save
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
