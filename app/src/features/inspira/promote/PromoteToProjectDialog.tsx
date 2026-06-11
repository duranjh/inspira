// B2.3 / W3 δ — Promote-to-Project dialog.
//
// 720×640 cream-paper modal launched from the inbox FeedbackItemDrawer's
// new "Promote to project" button. Shows the AI-suggested project shape
// — title, 5 topic seeds, decisions preview — and lets the user
// edit/reorder/remove before promoting. On Promote: spawning overlay
// → backend POST → close → navigate to canvas in pending_review.
//
// Send-back button is visible but DISABLED in this slice (no backend
// redraft endpoint yet) — see SendBackPanel.tsx for the rationale.
//
// Section heading "Suggested topic seeds (you can edit)" — never
// "Inspira drafted these" — to honor the capability-vs-usage rule.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";

import { Dialog } from "../../../components/dialogs/Dialog";
import type { FeedbackItem } from "../../inbox/types";
import { api } from "../api";
import { DecisionsPreviewExpander } from "./DecisionsPreviewExpander";
import { SendBackPanel } from "./SendBackPanel";
import { SpawningOverlay } from "./SpawningOverlay";
import { TopicSeedRow, type SeedReorderKey } from "./TopicSeedRow";
import {
  defaultTopicSeeds,
  makeBlankSeed,
  type TopicSeed,
} from "./seedsFixture";
import "./promote.css";

export interface ClusterSummary {
  cluster_id?: string | null;
  theme?: string | null;
  item_count?: number | null;
  severity?: number | null;
}

export interface PromoteToProjectDialogProps {
  open: boolean;
  feedbackItem: FeedbackItem | null;
  cluster?: ClusterSummary | null;
  onClose: () => void;
  /** Called with the new project_id when Promote succeeds. Parent navigates. */
  onPromoted: (projectId: string) => void;
}

function defaultTitleFor(item: FeedbackItem | null): string {
  if (!item) return "";
  return item.title.trim() || "Untitled cluster";
}

function clusterChipText(
  item: FeedbackItem | null,
  cluster: ClusterSummary | null | undefined,
): string {
  if (cluster) {
    const count = cluster.item_count ?? 1;
    const severity = cluster.severity;
    const theme = cluster.theme ?? item?.title ?? "";
    const sevPart = severity != null ? `severity ${severity} · ` : "";
    return `${count} item${count === 1 ? "" : "s"} · ${sevPart}cluster: '${theme}'`;
  }
  const theme = item?.title.slice(0, 60) ?? "";
  return `1 item · cluster: '${theme}'`;
}

export function PromoteToProjectDialog({
  open,
  feedbackItem,
  cluster,
  onClose,
  onPromoted,
}: PromoteToProjectDialogProps) {
  const initialTitle = defaultTitleFor(feedbackItem);
  const [titleText, setTitleText] = useState(initialTitle);
  const [titleModified, setTitleModified] = useState(false);
  const [topicSeeds, setTopicSeeds] = useState<TopicSeed[]>([]);
  const [decisionsPreviewOpen, setDecisionsPreviewOpen] = useState(false);
  const [sendBackPanelOpen, setSendBackPanelOpen] = useState(false);
  const [sendBackText, setSendBackText] = useState("");
  const [spawning, setSpawning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Drag-reorder state. dragOverId stores the row currently under the
  // pointer plus whether the drop would land before or after it.
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<{
    id: string;
    before: boolean;
  } | null>(null);

  // Keyboard-reorder state (closes #132). announceText feeds the
  // aria-live region; handle refs let us refocus the moved seed's
  // handle so a partner can chain ArrowUp/Down presses.
  const [announceText, setAnnounceText] = useState("");
  const handleRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const registerHandleRef = useCallback(
    (id: string, el: HTMLButtonElement | null) => {
      if (el === null) delete handleRefs.current[id];
      else handleRefs.current[id] = el;
    },
    [],
  );

  // Reset all dialog state whenever it re-opens with a different item.
  // The seedsFixture call also reinitializes the seed-id sequence range
  // so the "5 default" baseline is fresh each time. Mirrors
  // RenameProjectDialog.tsx:53.
  const lastItemId = useRef<string | null>(null);
  useEffect(() => {
    if (!open) return;
    const id = feedbackItem?.item_id ?? null;
    if (id === lastItemId.current && topicSeeds.length > 0) return;
    lastItemId.current = id;
    setTitleText(defaultTitleFor(feedbackItem));
    setTitleModified(false);
    // Content-aware seeds: pick the seed list that matches this
    // feedback's category (bug → bug-shaped seeds, feature → feature-
    // shaped, etc.). Falls through to general/deliberation seeds when
    // the type_hint is missing or unrecognized.
    setTopicSeeds(defaultTopicSeeds(feedbackItem?.type_hint));
    setDecisionsPreviewOpen(false);
    setSendBackPanelOpen(false);
    setSendBackText("");
    setSpawning(false);
    setError(null);
    setDraggingId(null);
    setDragOverId(null);
    setAnnounceText("");
    // Intentionally exclude topicSeeds.length from deps — it's only read
    // to detect "first open after a fresh feedbackItem".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, feedbackItem?.item_id]);

  const activeSeeds = useMemo(
    () => topicSeeds.filter((s) => !s.removed),
    [topicSeeds],
  );
  const activeSeedCount = activeSeeds.length;

  // Decisions-preview list reads off the active topic names.
  const previewTopicNames = useMemo(
    () => activeSeeds.map((s) => s.name).filter(Boolean),
    [activeSeeds],
  );

  // ---- handlers --------------------------------------------------------

  const handleTitleChange = useCallback(
    (next: string) => {
      setTitleText(next);
      const isModified = next.trim() !== initialTitle.trim();
      setTitleModified(isModified);
    },
    [initialTitle],
  );

  const setSeeds = useCallback(
    (
      updater: (prev: TopicSeed[]) => TopicSeed[],
    ) => {
      setTopicSeeds((prev) => updater(prev));
    },
    [],
  );

  const handleSeedNameChange = useCallback(
    (id: string, name: string) => {
      setSeeds((prev) =>
        prev.map((s) => (s.id === id ? { ...s, name } : s)),
      );
    },
    [setSeeds],
  );

  const handleSeedDescChange = useCallback(
    (id: string, desc: string) => {
      setSeeds((prev) =>
        prev.map((s) => (s.id === id ? { ...s, desc } : s)),
      );
    },
    [setSeeds],
  );

  const handleSeedToggleRemove = useCallback(
    (id: string) => {
      setSeeds((prev) =>
        prev.map((s) =>
          s.id === id ? { ...s, removed: !s.removed } : s,
        ),
      );
    },
    [setSeeds],
  );

  const handleAddSeed = useCallback(() => {
    setSeeds((prev) => [...prev, makeBlankSeed()]);
  }, [setSeeds]);

  // ---- Keyboard reorder (closes #132) ---------------------------------
  // Acts on the ACTIVE seed sequence (removed seeds stay anchored at
  // their absolute positions). After moving, schedules a focus on the
  // moved seed's handle so the user can chain Arrow presses; emits an
  // aria-live announcement with the new position.
  const handleSeedReorderKey = useCallback(
    (id: string, direction: SeedReorderKey) => {
      const srcIdx = topicSeeds.findIndex((s) => s.id === id);
      if (srcIdx === -1) return;
      const moved = topicSeeds[srcIdx];
      if (moved.removed) return;

      const activeIndices = topicSeeds
        .map((s, i) => (!s.removed ? i : -1))
        .filter((i) => i !== -1);
      const srcActiveIdx = activeIndices.indexOf(srcIdx);
      if (srcActiveIdx === -1) return;
      const totalActive = activeIndices.length;

      let dstActiveIdx: number;
      switch (direction) {
        case "up":
          dstActiveIdx = Math.max(0, srcActiveIdx - 1);
          break;
        case "down":
          dstActiveIdx = Math.min(totalActive - 1, srcActiveIdx + 1);
          break;
        case "first":
          dstActiveIdx = 0;
          break;
        case "last":
          dstActiveIdx = totalActive - 1;
          break;
      }

      if (dstActiveIdx === srcActiveIdx) {
        const edge =
          direction === "up" || direction === "first" ? "top" : "bottom";
        setAnnounceText(
          `Topic seed ${moved.name ? `'${moved.name}'` : `at position ${srcActiveIdx + 1}`} is already at the ${edge}.`,
        );
        return;
      }

      const dstAbsIdx = activeIndices[dstActiveIdx];
      const next = topicSeeds.slice();
      next.splice(srcIdx, 1);
      const adjustedDst = srcIdx < dstAbsIdx ? dstAbsIdx - 1 : dstAbsIdx;
      const insertAt =
        direction === "down" || direction === "last"
          ? adjustedDst + 1
          : adjustedDst;
      next.splice(insertAt, 0, moved);
      setTopicSeeds(next);

      const newPosition = dstActiveIdx + 1;
      const verb =
        direction === "first"
          ? "moved to the top"
          : direction === "last"
            ? "moved to the bottom"
            : direction === "up"
              ? "moved up"
              : "moved down";
      setAnnounceText(
        `Topic seed ${moved.name ? `'${moved.name}'` : ""} ${verb}. Now at position ${newPosition} of ${totalActive}.`,
      );

      // Refocus the handle of the moved seed once React commits the
      // new order, so ArrowUp/Down can be chained without re-Tabbing.
      requestAnimationFrame(() => {
        handleRefs.current[id]?.focus();
      });
    },
    [topicSeeds],
  );

  // ---- Drag-reorder ----------------------------------------------------

  const handleDragStart = useCallback(
    (e: DragEvent<HTMLLIElement>, id: string) => {
      // Idempotency: ignore a second dragstart while one is still in flight.
      if (draggingId) {
        e.preventDefault();
        return;
      }
      setDraggingId(id);
      e.dataTransfer.effectAllowed = "move";
      // Firefox refuses to start the drag without setData.
      e.dataTransfer.setData("text/plain", id);
    },
    [draggingId],
  );

  const handleDragOver = useCallback(
    (e: DragEvent<HTMLLIElement>, id: string) => {
      e.preventDefault();
      if (!draggingId) return;
      if (draggingId === id) return;
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      const before = e.clientY < rect.top + rect.height / 2;
      setDragOverId((cur) =>
        cur && cur.id === id && cur.before === before
          ? cur
          : { id, before },
      );
    },
    [draggingId],
  );

  const handleDragLeave = useCallback(
    (e: DragEvent<HTMLLIElement>, id: string) => {
      const next = e.relatedTarget as Node | null;
      if (next && (e.currentTarget as HTMLElement).contains(next)) return;
      setDragOverId((cur) => (cur && cur.id === id ? null : cur));
    },
    [],
  );

  const handleDrop = useCallback(
    (e: DragEvent<HTMLLIElement>, id: string) => {
      e.preventDefault();
      if (!draggingId || draggingId === id) {
        setDraggingId(null);
        setDragOverId(null);
        return;
      }
      const before = dragOverId?.before ?? false;
      setSeeds((prev) => {
        const fromIdx = prev.findIndex((s) => s.id === draggingId);
        const toIdx = prev.findIndex((s) => s.id === id);
        if (fromIdx === -1 || toIdx === -1) return prev;
        const next = prev.slice();
        const [moved] = next.splice(fromIdx, 1);
        const targetIdx = next.findIndex((s) => s.id === id);
        const insertAt = before ? targetIdx : targetIdx + 1;
        next.splice(insertAt, 0, moved);
        return next;
      });
      setDraggingId(null);
      setDragOverId(null);
    },
    [draggingId, dragOverId, setSeeds],
  );

  const handleDragEnd = useCallback(() => {
    setDraggingId(null);
    setDragOverId(null);
  }, []);

  // ---- Promote ---------------------------------------------------------

  const handlePromote = useCallback(async () => {
    if (spawning) return;
    if (titleText.trim().length === 0) {
      setError("Project title is required.");
      return;
    }
    if (activeSeedCount === 0) {
      setError("At least one topic seed is required.");
      return;
    }
    setSpawning(true);
    setError(null);
    try {
      const { project } = await api.promoteToProject({
        cluster_id: feedbackItem?.cluster_id ?? null,
        project_title: titleText.trim(),
        topic_seeds: activeSeeds.map((s) => ({ name: s.name, desc: s.desc })),
        feedback_item_id: feedbackItem?.item_id ?? null,
      });
      // Notify the inbox so the row's status flips to "promoted".
      if (typeof window !== "undefined" && feedbackItem) {
        window.dispatchEvent(
          new CustomEvent("inspira:feedback-item-promoted", {
            detail: { itemId: feedbackItem.item_id, projectId: project.project_id },
          }),
        );
      }
      onPromoted(project.project_id);
    } catch (err) {
      console.error("[Inspira] promoteToProject failed", err);
      const msg =
        err instanceof Error ? err.message : "Couldn't promote — try again.";
      setError(msg);
      setSpawning(false);
    }
  }, [
    spawning,
    titleText,
    activeSeedCount,
    activeSeeds,
    feedbackItem,
    onPromoted,
  ]);

  const handleClose = useCallback(() => {
    if (spawning) return;
    onClose();
  }, [spawning, onClose]);

  // ---- Render ----------------------------------------------------------

  return (
    <Dialog open={open} onClose={handleClose} title="Promote to project" width={720}>
      <div className="pm-dialog">
        <div className="pm-dialog__chip">
          <span className="pm-dialog__chip-dot" aria-hidden="true" />
          <span className="pm-dialog__chip-text">
            {clusterChipText(feedbackItem, cluster)}
          </span>
        </div>
        <p className="pm-dialog__subtitle">Review, modify, or send back.</p>

        <section className="pm-block">
          <label className="pm-block__label" htmlFor="pm-title-input">
            PROJECT TITLE
            {titleModified ? (
              <span className="pm-modified">Modified</span>
            ) : null}
          </label>
          <input
            id="pm-title-input"
            type="text"
            className="pm-title-input"
            value={titleText}
            onChange={(e) => handleTitleChange(e.target.value)}
            placeholder="Project title"
          />
          <p className="pm-block__hint">
            Inspira generated this from the cluster theme. Edit if you want.
          </p>
        </section>

        <section className="pm-block">
          <div className="pm-block__label">
            SUGGESTED TOPIC SEEDS — YOU CAN EDIT ({activeSeedCount})
          </div>
          <ul className="pm-topic-list">
            {topicSeeds.map((seed) => {
              const activePosition = seed.removed
                ? null
                : activeSeeds.findIndex((s) => s.id === seed.id) + 1;
              return (
                <TopicSeedRow
                  key={seed.id}
                  seed={seed}
                  isDragging={draggingId === seed.id}
                  dragOverBefore={
                    dragOverId && dragOverId.id === seed.id
                      ? dragOverId.before
                      : null
                  }
                  position={activePosition}
                  totalActive={activeSeedCount}
                  onNameChange={handleSeedNameChange}
                  onDescChange={handleSeedDescChange}
                  onToggleRemove={handleSeedToggleRemove}
                  onDragStart={handleDragStart}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  onDragEnd={handleDragEnd}
                  onReorderKey={handleSeedReorderKey}
                  registerHandleRef={registerHandleRef}
                />
              );
            })}
          </ul>
          <div
            className="pm-topic-list__status"
            role="status"
            aria-live="polite"
            aria-atomic="true"
            style={{
              position: "absolute",
              width: 1,
              height: 1,
              padding: 0,
              margin: -1,
              overflow: "hidden",
              clip: "rect(0,0,0,0)",
              whiteSpace: "nowrap",
              border: 0,
            }}
          >
            {announceText}
          </div>
          <button
            type="button"
            className="pm-add-topic"
            onClick={handleAddSeed}
          >
            + Add another topic
          </button>
        </section>

        <DecisionsPreviewExpander topicNames={previewTopicNames} />

        {error ? <div className="pm-dialog__error">{error}</div> : null}

        <div className="pm-footer">
          <button
            type="button"
            className="pm-footer__cancel"
            onClick={handleClose}
            disabled={spawning}
          >
            Cancel
          </button>
          <span className="pm-footer__spacer" />
          <button
            type="button"
            className="pm-footer__sendback-toggle"
            onClick={() => setSendBackPanelOpen((v) => !v)}
            disabled={spawning}
          >
            Send back to AI
          </button>
          <button
            type="button"
            className="pm-footer__promote"
            onClick={() => void handlePromote()}
            disabled={spawning}
            aria-busy={spawning || undefined}
          >
            Promote — Inspira drafts the canvas
          </button>
        </div>

        <SendBackPanel
          open={sendBackPanelOpen}
          value={sendBackText}
          onChange={setSendBackText}
          onCancel={() => {
            setSendBackPanelOpen(false);
            setSendBackText("");
          }}
        />

        {/* Decisions-preview render is part of the body above; the
            spawning overlay covers the whole dialog body when
            spawning=true. It sits at z-index 5 — high enough to mask
            content, but the base Dialog backdrop (3000) still sits
            above the overlay so ESC + backdrop close still propagate
            visually. The handleClose guard short-circuits the actual
            close while spawning. */}
        {spawning ? <SpawningOverlay /> : null}
      </div>
    </Dialog>
  );
}
