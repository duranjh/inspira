// AIStatus — top-bar chip + slide-down Orchestrator panel.
//
// State sourcing:
//
//   - Real path: useOrchestratorState polls
//     /api/v2/orchestrator/runs every 3s, scoped to the active
//     workspace from WorkspaceContext.
//   - Fixture path: tests + storybook pass `initialState` directly.
//     The hook is still called (with null wsId) to keep React's
//     hook-call invariant, but it returns idle without polling.
//   - DEV demo cycle: when the chip mounts with no active workspace
//     (fresh worktree, no signup) AND DEV mode is on, the Re-run
//     button toggles a local idle ↔ running visual cycle so the
//     surface stays demoable without a backend.
//
// Mounted by AuthedShell (visible on /workspaces, /connectors,
// /inbox) AND inside InspiraApp's canvas top-bar (covers both the
// legacy ProjectCanvas and the B1.1 WorkspaceKanban surface — same
// header, single mount).

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactElement } from "react";

import { useDismissOn } from "../../hooks/useDismissOn";
import { useWorkspaceContext } from "../workspaces/WorkspaceContext";
import { AIStatusChip } from "./AIStatusChip";
import { OrchestratorPanel } from "./OrchestratorPanel";
import {
  makeIdleState,
  makeRunningState,
} from "./mockOrchestratorState";
import type { OrchestratorState } from "./types";
import { useOrchestratorState } from "./useOrchestratorState";

import "./ai-status.css";

const CONFIG_OPEN_STORAGE_KEY = "inspira.ai-status.configure-open";

function readConfigOpen(): boolean {
  try {
    return window.localStorage.getItem(CONFIG_OPEN_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function writeConfigOpen(value: boolean): void {
  try {
    window.localStorage.setItem(
      CONFIG_OPEN_STORAGE_KEY,
      value ? "true" : "false",
    );
  } catch {
    // Safari private mode + locked-down storage policies — silently
    // skip; the toggle remains visually correct for the session.
  }
}

/** Formats an ISO timestamp as "X min ago" / "X hours ago" / "just now". */
function formatRelative(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60 * 1000) return "just now";
  const minutes = Math.floor(ms / (60 * 1000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function chipLabel(s: OrchestratorState): string {
  switch (s.state) {
    case "idle":
      return s.lastFinishedAt
        ? `Idle · last run ${formatRelative(s.lastFinishedAt)}`
        : "Idle";
    case "running": {
      const active = s.agents.filter((a) => a.status !== "done").length;
      const started = formatRelative(s.startedAt);
      const startedPart = started ? ` · started ${started}` : "";
      return `Running · ${active} sub-agents${startedPart}`;
    }
    case "failed":
      return "Failed · click to retry";
    case "conflict":
      return s.conflict
        ? `Resolving conflict · ${s.conflict.description}`
        : "Resolving conflict";
  }
}

function panelSubtitle(s: OrchestratorState): string {
  if (s.state !== "running" && s.state !== "conflict") return "";
  const active = s.agents.filter((a) => a.status !== "done").length;
  const started = formatRelative(s.startedAt);
  // "4 topics" is a placeholder until Wave 2 surfaces topic count from
  // the run config. Sub-agent count and time-since-start are derived honestly.
  const startedPart = started ? ` · started ${started}` : "";
  return `Running ${active} sub-agents · 4 topics${startedPart}`;
}

function panelIdleText(s: OrchestratorState): string {
  if (s.state === "failed") {
    return "Last run failed. Click Re-run to retry.";
  }
  if (s.lastFinishedAt) {
    return `No agents running. Last run finished ${formatRelative(s.lastFinishedAt)}.`;
  }
  return "No agents running.";
}

export interface AIStatusProps {
  /** Test-only override for the initial state. Production callers should
   *  not pass this — the default is the mock fixture. */
  initialState?: OrchestratorState;
}

export function AIStatus({ initialState }: AIStatusProps = {}): ReactElement {
  // Hook is always called (React hooks rule) but
  // receives null wsId when in fixture mode → returns idle without
  // polling.
  const ctx = useWorkspaceContext();
  const realWsId = ctx.activeWorkspace?.workspace_id ?? null;
  const wsId = initialState !== undefined ? null : realWsId;
  const hook = useOrchestratorState(wsId);

  // DEV demo cycle for the case where the chip is mounted without a
  // real workspace (fresh worktree + no signup) AND no fixture state
  // is provided. Lets the surface stay demoable. Also active when
  // initialState is supplied so the existing visual tests continue
  // to exercise the idle ↔ running transition without a backend.
  const [demoCycle, setDemoCycle] = useState<OrchestratorState | null>(null);
  const isFixtureMode =
    initialState !== undefined || realWsId === null;
  const baseState = initialState ?? hook.state;
  const state = demoCycle ?? baseState;

  const [panelOpen, setPanelOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState<boolean>(readConfigOpen);
  // Local component state — Wave-3 may persist with a server pref.
  const [costPreview, setCostPreview] = useState(true);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  // Close on click-outside or Escape. Popover semantics — Tab is
  // allowed to escape the panel (intentionally NO focus trap; that
  // would be wrong for a chip-anchored popover per WAI-ARIA).
  const closePanel = useCallback(() => setPanelOpen(false), []);
  useDismissOn({
    enabled: panelOpen,
    onDismiss: closePanel,
    esc: true,
    clickOutsideRef: wrapperRef,
  });

  // Focus restoration: when the panel transitions open → closed, return
  // focus to the chip so keyboard users don't lose their place.
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (wasOpenRef.current && !panelOpen) {
      chipRef.current?.focus();
    }
    wasOpenRef.current = panelOpen;
  }, [panelOpen]);

  const handleChipClick = useCallback(() => {
    setPanelOpen((prev) => !prev);
  }, []);

  const handleClose = useCallback(() => {
    setPanelOpen(false);
  }, []);

  const handleRerun = useCallback(() => {
    if (state.state === "running") return;
    if (isFixtureMode) {
      // Test path or no-workspace DEV demo: cycle visually so the
      // chip remains interactive without a backend.
      if (import.meta.env.DEV) {
        setDemoCycle((prev) => {
          const cur = prev ?? baseState;
          return cur.state === "running"
            ? makeIdleState()
            : makeRunningState();
        });
      }
      return;
    }
    void hook.rerun();
  }, [state.state, isFixtureMode, baseState, hook]);

  const handleConfigToggle = useCallback(() => {
    setConfigOpen((prev) => {
      const next = !prev;
      writeConfigOpen(next);
      return next;
    });
  }, []);

  const handleCostPreviewToggle = useCallback(() => {
    setCostPreview((prev) => !prev);
  }, []);

  const handleTriggerRun = useCallback(() => {
    handleRerun();
    setPanelOpen(false);
  }, [handleRerun]);

  const rerunDisabled = isFixtureMode
    ? state.state === "running"
    : hook.rerunDisabled;
  const rerunTooltip = isFixtureMode ? null : hook.rerunTooltip;

  return (
    <div className="ai-status" ref={wrapperRef}>
      <AIStatusChip
        ref={chipRef}
        state={state.state}
        label={chipLabel(state)}
        onClick={handleChipClick}
        ariaExpanded={panelOpen}
        ariaControls={panelId}
      />
      <button
        type="button"
        className="os-rerun"
        onClick={handleRerun}
        disabled={rerunDisabled}
        title={rerunTooltip ?? undefined}
      >
        ↻ Re-run
      </button>
      {panelOpen ? (
        <OrchestratorPanel
          state={state.state}
          agents={state.agents}
          conflict={state.conflict}
          subtitle={panelSubtitle(state)}
          idleText={panelIdleText(state)}
          configOpen={configOpen}
          onConfigToggle={handleConfigToggle}
          costPreview={costPreview}
          onCostPreviewToggle={handleCostPreviewToggle}
          onClose={handleClose}
          panelId={panelId}
          onTriggerRun={handleTriggerRun}
        />
      ) : null}
    </div>
  );
}
