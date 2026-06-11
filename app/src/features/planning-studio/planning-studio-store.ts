import { useEffect, useMemo, useRef, useState } from "react";
import type { PlanningStudioWorkspace } from "../../mock-data";
import { buildCoverageChecklist, buildScenarioMatrix, buildTonightFocus } from "./planning-studio-derived";

const STORAGE_KEY = "planning-studio.workspace.v1";

function cloneWorkspace(workspace: PlanningStudioWorkspace): PlanningStudioWorkspace {
  return JSON.parse(JSON.stringify(workspace)) as PlanningStudioWorkspace;
}

function parsePersistedWorkspace(raw: string): PlanningStudioWorkspace {
  const parsed = JSON.parse(raw) as unknown;

  if (!parsed || typeof parsed !== "object") {
    throw new Error("Saved workspace JSON is not an object.");
  }

  if ("workspace" in parsed && parsed.workspace && typeof parsed.workspace === "object") {
    return (parsed as { workspace: PlanningStudioWorkspace }).workspace;
  }

  if ("planning_studio" in parsed && parsed.planning_studio && typeof parsed.planning_studio === "object") {
    const planningStudio = parsed as { planning_studio: { planning_studio?: PlanningStudioWorkspace; workspace?: PlanningStudioWorkspace } };
    return planningStudio.planning_studio.workspace ?? planningStudio.planning_studio.planning_studio ?? (planningStudio.planning_studio as PlanningStudioWorkspace);
  }

  throw new Error("Saved workspace JSON did not contain a Planning Studio workspace.");
}

function savePersistedWorkspace(workspace: PlanningStudioWorkspace) {
  const envelope = {
    version: 1,
    saved_at: new Date().toISOString(),
    workspace
  };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(envelope));
}

function formatListBlock(values: string[]) {
  return values.length ? values.map((value) => `- ${value}`).join("\n") : "- None";
}

export function buildPlanningStudioMarkdown(workspace: PlanningStudioWorkspace) {
  const readyExportCount = workspace.exports.items.filter((item) => item.status === "ready").length;
  const blockedExportCount = workspace.exports.items.filter((item) => item.status === "blocked").length;
  const openQuestionCount = workspace.intake.open_questions.filter((question) => question.status !== "answered").length;

  const coverageLines = buildCoverageChecklist(workspace).map(
    (item) => `- ${item.title} (${item.status}) | ${item.summary} | ${item.detail}`
  );
  const scenarioLines = buildScenarioMatrix(workspace).map(
    (scenario) =>
      `- ${scenario.type} | actor: ${scenario.actor} | trigger: ${scenario.trigger} | expected: ${scenario.expected} | status: ${scenario.status} | note: ${scenario.note}`
  );
  const focusLines = buildTonightFocus(workspace);
  const briefLines = workspace.intake.briefs.map(
    (brief) => `- ${brief.title} (${brief.status}) | owner: ${brief.owner} | dept: ${brief.department} | scope: ${brief.scope}`
  );
  const questionLines = workspace.intake.open_questions.map(
    (question) => `- ${question.question} (${question.status}) | owner: ${question.owner} | decision: ${question.decision_owner} | answer: ${
      question.answer ?? "Pending"
    }`
  );
  const stageLines = workspace.interview_flow.stages.map(
    (stage) => `- ${stage.title} (${stage.status}) | owner: ${stage.owner} | duration: ${stage.duration} | output: ${stage.output}`
  );
  const sectionLines = workspace.prd_outline.sections.map(
    (section) => `- ${section.title} (${section.status}) | owner: ${section.owner}\n  - summary: ${section.summary}\n  - acceptance: ${section.acceptance.join("; ")}\n  - dependencies: ${section.dependencies.join("; ")}`
  );
  const workstreamLines = workspace.execution_breakdown.workstreams.map(
    (workstream) =>
      `- ${workstream.title} (${workstream.status}) | owner: ${workstream.owner} | estimate: ${workstream.estimate}\n  - summary: ${workstream.summary}\n  - dependencies: ${workstream.dependencies.join("; ")}\n  - slices: ${workstream.slices.join("; ")}`
  );
  const exportLines = workspace.exports.items.map(
    (item) => `- ${item.title} (${item.status}) | target: ${item.target} | format: ${item.format} | blocker: ${item.blocker ?? "None"}`
  );

  return [
    "---",
    `title: ${workspace.project.product_name}`,
    `owner: ${workspace.project.owner}`,
    `stage: ${workspace.project.stage}`,
    `readiness: ${workspace.readiness.score}`,
    `exports_ready: ${readyExportCount}`,
    `exports_blocked: ${blockedExportCount}`,
    `generated_at: ${new Date().toISOString()}`,
    "storage_mode: local-only",
    "---",
    "",
    `# ${workspace.project.product_name}`,
    "",
    "## Operator summary",
    `- Current stage: ${workspace.interview_flow.current_stage}`,
    `- Readiness score: ${workspace.readiness.score}`,
    `- Open questions: ${openQuestionCount}`,
    `- Ready exports: ${readyExportCount}`,
    `- Blocked exports: ${blockedExportCount}`,
    `- Local persistence: JSON import/export plus browser localStorage`,
    "",
    "## Project",
    `- Owner: ${workspace.project.owner}`,
    `- Requested by: ${workspace.project.requested_by}`,
    `- Stage: ${workspace.project.stage}`,
    `- Target release: ${workspace.project.target_release}`,
    `- Goal: ${workspace.project.goal}`,
    `- Summary: ${workspace.project.summary}`,
    "",
    "## Intake",
    `- Status: ${workspace.intake.status}`,
    "- Briefs:",
    formatListBlock(briefLines),
    "- Open questions:",
    formatListBlock(questionLines),
    "- Decision log:",
    formatListBlock(workspace.intake.decision_log),
    "",
    "## Interview flow",
    `- Status: ${workspace.interview_flow.status}`,
    `- Current stage: ${workspace.interview_flow.current_stage}`,
    "- Stages:",
    formatListBlock(stageLines),
    "- Blockers:",
    formatListBlock(workspace.interview_flow.blockers.map((blocker) => `${blocker.title}: ${blocker.detail}`)),
    "",
    "## Coverage checklist",
    formatListBlock(coverageLines),
    "",
    "## Scenario matrix",
    formatListBlock(scenarioLines),
    "",
    "## Template pack",
    `- Status: ${workspace.template_pack.status}`,
    `- Selected template: ${workspace.template_pack.selected_template_id}`,
    `- Last synced: ${workspace.template_pack.last_synced_at}`,
    "",
    "## PRD outline",
    `- Status: ${workspace.prd_outline.status}`,
    "- Sections:",
    formatListBlock(sectionLines),
    "- Open decisions:",
    formatListBlock(workspace.prd_outline.open_decisions),
    "- Success criteria:",
    formatListBlock(workspace.prd_outline.success_criteria),
    "",
    "## Execution breakdown",
    `- Status: ${workspace.execution_breakdown.status}`,
    "- Workstreams:",
    formatListBlock(workstreamLines),
    "- Milestones:",
    formatListBlock(workspace.execution_breakdown.milestones.map((milestone) => `${milestone.title} (${milestone.status}) due ${milestone.due_at} - ${milestone.note}`)),
    "- Dependency chain:",
    formatListBlock(workspace.execution_breakdown.dependency_chain),
    "",
    "## Exports",
    `- Status: ${workspace.exports.status}`,
    "- Export artifacts:",
    formatListBlock(exportLines),
    "- Disabled actions:",
    formatListBlock(workspace.exports.disabled_actions),
    "",
    "## Export confidence",
    `- Ready artifacts: ${readyExportCount}`,
    `- Blocked artifacts: ${blockedExportCount}`,
    `- Open questions still visible: ${openQuestionCount}`,
    "- Recovery path: JSON snapshot restore plus local persistence",
    "",
    "## Readiness",
    `- Status: ${workspace.readiness.status}`,
    `- Score: ${workspace.readiness.score}`,
    "- Tonight focus:",
    formatListBlock(focusLines),
    "- Next actions:",
    formatListBlock(workspace.readiness.next_actions),
    "- Blockers:",
    formatListBlock(workspace.readiness.blockers),
    "- Gates:",
    formatListBlock(workspace.readiness.gates.map((gate) => `${gate.title} (${gate.status}) - ${gate.owner}: ${gate.note}`))
  ].join("\n");
}

export function exportPlanningStudioJson(workspace: PlanningStudioWorkspace) {
  return JSON.stringify(
    {
      version: 1,
      exported_at: new Date().toISOString(),
      workspace
    },
    null,
    2
  );
}

export function usePlanningStudioWorkspace(seedWorkspace: PlanningStudioWorkspace) {
  const seedRef = useRef(cloneWorkspace(seedWorkspace));
  const [workspace, setWorkspace] = useState<PlanningStudioWorkspace>(() => cloneWorkspace(seedWorkspace));
  const [storageState, setStorageState] = useState<"loading" | "ready" | "error">("loading");
  const [storageError, setStorageError] = useState<string | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const hydratedRef = useRef(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const loaded = parsePersistedWorkspace(raw);
        setWorkspace(cloneWorkspace(loaded));
        const parsed = JSON.parse(raw) as { saved_at?: string };
        setLastSavedAt(parsed.saved_at ?? new Date().toISOString());
      } else {
        setLastSavedAt(null);
      }
      setStorageError(null);
      setStorageState("ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to read Planning Studio workspace.";
      setStorageError(message);
      setStorageState("error");
    } finally {
      hydratedRef.current = true;
    }
  }, []);

  useEffect(() => {
    if (!hydratedRef.current) return;
    try {
      savePersistedWorkspace(workspace);
      setLastSavedAt(new Date().toISOString());
      if (storageState !== "error") {
        setStorageError(null);
        setStorageState("ready");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to persist Planning Studio workspace.";
      setStorageError(message);
      setStorageState("error");
    }
  }, [workspace, storageState]);

  function replaceWorkspace(next: PlanningStudioWorkspace) {
    setWorkspace(cloneWorkspace(next));
  }

  function updateWorkspace(mutator: (draft: PlanningStudioWorkspace) => void) {
    setWorkspace((current) => {
      const draft = cloneWorkspace(current);
      mutator(draft);
      return draft;
    });
  }

  function resetToSeed() {
    replaceWorkspace(seedRef.current);
    setStorageError(null);
    setStorageState("ready");
  }

  function reloadFromStorage() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        resetToSeed();
        return;
      }
      const loaded = parsePersistedWorkspace(raw);
      replaceWorkspace(loaded);
      const parsed = JSON.parse(raw) as { saved_at?: string };
      setLastSavedAt(parsed.saved_at ?? new Date().toISOString());
      setStorageError(null);
      setStorageState("ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to reload Planning Studio workspace.";
      setStorageError(message);
      setStorageState("error");
    }
  }

  function importWorkspace(raw: string) {
    const loaded = parsePersistedWorkspace(raw);
    replaceWorkspace(loaded);
    setStorageError(null);
    setStorageState("ready");
  }

  const exportJson = useMemo(() => exportPlanningStudioJson(workspace), [workspace]);
  const exportMarkdown = useMemo(() => buildPlanningStudioMarkdown(workspace), [workspace]);

  return {
    workspace,
    setWorkspace: updateWorkspace,
    replaceWorkspace,
    resetToSeed,
    reloadFromStorage,
    importWorkspace,
    exportJson,
    exportMarkdown,
    storageState,
    storageError,
    lastSavedAt
  };
}
