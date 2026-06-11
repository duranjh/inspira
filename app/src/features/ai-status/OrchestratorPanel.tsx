// Pure presentational atom — renders the panel anatomy from B4.5.
// All state and label derivation live in the parent (AIStatus.tsx); the
// panel takes string props so its rendering paths are deterministic.

import type { ReactElement } from "react";

import type {
  AIStatusState,
  OrchestratorConflict,
  SubAgent,
  SubAgentStatus,
} from "./types";

export interface OrchestratorPanelProps {
  state: AIStatusState;
  agents: SubAgent[];
  conflict: OrchestratorConflict | null;
  /** Subtitle shown when state is running/conflict, e.g.
   * "Running 4 sub-agents · 4 topics · started 2 min ago". */
  subtitle: string;
  /** Body text shown when state is idle/failed (replaces sub-agent list). */
  idleText: string;
  configOpen: boolean;
  onConfigToggle: () => void;
  costPreview: boolean;
  onCostPreviewToggle: () => void;
  onClose: () => void;
  panelId: string;
  /** Triggered by the Idle CTA inside the panel. */
  onTriggerRun: () => void;
}

const STATUS_LABEL: Record<SubAgentStatus, string> = {
  working: "Working",
  done: "✓ Done",
  conflict: "⚠ Conflict pending",
  failed: "✗ Failed",
};

export function OrchestratorPanel({
  state,
  agents,
  conflict,
  subtitle,
  idleText,
  configOpen,
  onConfigToggle,
  costPreview,
  onCostPreviewToggle,
  onClose,
  panelId,
  onTriggerRun,
}: OrchestratorPanelProps): ReactElement {
  const showSubAgents = state === "running" || state === "conflict";

  return (
    <div
      id={panelId}
      className="os-panel"
      role="dialog"
      aria-labelledby={`${panelId}-title`}
    >
      <div className="os-panel__hd">
        <h2 id={`${panelId}-title`} className="os-panel__title">
          Inspira agent
        </h2>
        <button
          type="button"
          className="os-panel__close"
          onClick={onClose}
          aria-label="Close orchestrator panel"
        >
          ×
        </button>
      </div>
      {showSubAgents && subtitle ? (
        <div className="os-panel__subtitle">{subtitle}</div>
      ) : null}

      {showSubAgents && conflict ? (
        <div className="os-conflict-row">
          <span>
            Orchestrator resolving 1 conflict — {conflict.description}
          </span>
          <button type="button" className="os-conflict-row__link">
            View resolution →
          </button>
        </div>
      ) : null}

      {showSubAgents ? (
        <div className="os-agents">
          {agents.map((agent) => (
            <div
              key={agent.id}
              className={`os-agent${agent.status === "done" ? " done" : ""}`}
            >
              <div className="os-agent__top">
                <span className="os-agent__name">
                  Sub-agent · {agent.name}
                </span>
                <span
                  className={`os-agent__status os-agent__status--${agent.status}`}
                >
                  {agent.status === "working" ? (
                    <span className="os-agent__dot" aria-hidden="true" />
                  ) : null}
                  {STATUS_LABEL[agent.status]}
                </span>
              </div>
              <div className="os-agent__activity">{agent.activity}</div>
              <button type="button" className="os-agent__view">
                View →
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="os-idle">
          <div className="os-idle__text">{idleText}</div>
          <button
            type="button"
            className="os-idle__cta"
            onClick={onTriggerRun}
          >
            ↻ Trigger a new run now
          </button>
        </div>
      )}

      <div className="os-config">
        <button
          type="button"
          className="os-config__trigger"
          onClick={onConfigToggle}
          aria-expanded={configOpen}
        >
          {configOpen ? "▾ Configure" : "▸ Configure"}
        </button>
        {configOpen ? (
          <div className="os-config__body">
            <button
              type="button"
              className="os-config__toggle"
              onClick={onCostPreviewToggle}
              aria-pressed={costPreview}
            >
              <span
                className={`os-config__switch os-config__switch--${costPreview ? "on" : "off"}`}
                aria-hidden="true"
              />
              <span className="os-config__label">
                Show cost preview on every regeneration
              </span>
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
