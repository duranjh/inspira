import type { PlanningQuestion, PlanningStudioWorkspace } from "../../mock-data";

export type CoverageStatus = "complete" | "partial" | "missing";

export type PlanningCoverageItem = {
  id: string;
  title: string;
  status: CoverageStatus;
  summary: string;
  detail: string;
};

export type PlanningScenarioRow = {
  id: string;
  type: string;
  actor: string;
  trigger: string;
  expected: string;
  status: string;
  note: string;
};

export type PlanningPhaseStatus = "ready" | "active" | "watch" | "blocked";

export type PlanningPhaseItem = {
  id: string;
  title: string;
  status: PlanningPhaseStatus;
  metric: string;
  summary: string;
  detail: string;
};

export type PlanningOperatorGuidance = {
  title: string;
  status: PlanningPhaseStatus;
  detail: string;
  cues: string[];
};

export type PlanningConfidenceCheck = {
  id: string;
  title: string;
  status: PlanningPhaseStatus;
  detail: string;
};

function hasContent(value: string | null | undefined) {
  return String(value ?? "").trim().length > 0;
}

function answeredQuestions(workspace: PlanningStudioWorkspace) {
  return workspace.intake.open_questions.filter((question) => question.status === "answered" || hasContent(question.answer));
}

function unresolvedQuestions(workspace: PlanningStudioWorkspace) {
  return workspace.intake.open_questions.filter((question) => question.status !== "answered");
}

function completedSections(workspace: PlanningStudioWorkspace) {
  return workspace.prd_outline.sections.filter((section) => section.status === "complete");
}

function staffedWorkstreams(workspace: PlanningStudioWorkspace) {
  return workspace.execution_breakdown.workstreams.filter((workstream) => workstream.status !== "draft");
}

function readyExports(workspace: PlanningStudioWorkspace) {
  return workspace.exports.items.filter((item) => item.status === "ready");
}

function blockerItems(workspace: PlanningStudioWorkspace) {
  return [...workspace.interview_flow.blockers.map((blocker) => blocker.detail), ...workspace.readiness.blockers];
}

export function coverageTone(status: CoverageStatus): "success" | "warning" | "danger" {
  if (status === "complete") return "success";
  if (status === "partial") return "warning";
  return "danger";
}

export function buildCoverageChecklist(workspace: PlanningStudioWorkspace): PlanningCoverageItem[] {
  const openQuestions = unresolvedQuestions(workspace);
  const answered = answeredQuestions(workspace);
  const completeSections = completedSections(workspace);
  const activeWorkstreams = staffedWorkstreams(workspace);
  const exportsReady = readyExports(workspace);
  const blockers = blockerItems(workspace);

  return [
    {
      id: "problem-frame",
      title: "Problem framing",
      status: hasContent(workspace.project.goal) && hasContent(workspace.project.summary) ? "complete" : hasContent(workspace.project.goal) || hasContent(workspace.project.summary) ? "partial" : "missing",
      summary: workspace.project.goal || "Add a concrete goal and summary before pushing the plan forward.",
      detail: `Stage: ${workspace.project.stage || "unset"} / target: ${workspace.project.target_release || "unset"}`
    },
    {
      id: "decision-coverage",
      title: "Decision coverage",
      status: openQuestions.length === 0 && answered.length > 0 ? "complete" : answered.length > 0 || openQuestions.length > 0 ? "partial" : "missing",
      summary: openQuestions.length === 0 ? "All current planning questions have recorded answers." : `${openQuestions.length} open question(s) still block a clean handoff.`,
      detail: `${answered.length} answered / ${workspace.intake.open_questions.length} total`
    },
    {
      id: "interview-trace",
      title: "Interview trace",
      status:
        workspace.interview_flow.sessions.length > 0 && workspace.interview_flow.stages.some((stage) => stage.status === "complete")
          ? "complete"
          : workspace.interview_flow.sessions.length > 0 || workspace.interview_flow.stages.length > 0
            ? "partial"
            : "missing",
      summary: workspace.interview_flow.current_stage || "Capture the current interview stage and supporting notes.",
      detail: `${workspace.interview_flow.sessions.length} session(s) / ${workspace.interview_flow.stages.filter((stage) => stage.status === "complete").length} completed stage(s)`
    },
    {
      id: "prd-outline",
      title: "PRD outline",
      status:
        workspace.prd_outline.sections.length > 0 && completeSections.length === workspace.prd_outline.sections.length
          ? "complete"
          : workspace.prd_outline.sections.length > 0
            ? "partial"
            : "missing",
      summary:
        completeSections.length === workspace.prd_outline.sections.length
          ? "Every outline section is marked complete."
          : `${completeSections.length} of ${workspace.prd_outline.sections.length} section(s) are marked complete.`,
      detail: workspace.prd_outline.open_decisions.length ? `${workspace.prd_outline.open_decisions.length} open decision(s) remain visible.` : "No explicit open decisions are listed."
    },
    {
      id: "execution-slices",
      title: "Execution slices",
      status:
        workspace.execution_breakdown.workstreams.length > 0 && activeWorkstreams.length === workspace.execution_breakdown.workstreams.length
          ? "complete"
          : activeWorkstreams.length > 0 || workspace.execution_breakdown.workstreams.length > 0
            ? "partial"
            : "missing",
      summary:
        activeWorkstreams.length === workspace.execution_breakdown.workstreams.length
          ? "Every workstream is staffed with a non-draft status."
          : `${activeWorkstreams.length} of ${workspace.execution_breakdown.workstreams.length} workstream(s) are beyond draft.`,
      detail: workspace.execution_breakdown.dependency_chain.length ? workspace.execution_breakdown.dependency_chain.join(" / ") : "No dependency chain is listed."
    },
    {
      id: "risk-review",
      title: "Risk review",
      status: blockers.length > 0 || workspace.prd_outline.open_decisions.length > 0 ? "complete" : workspace.readiness.gates.length > 0 ? "partial" : "missing",
      summary:
        blockers.length > 0
          ? `${blockers.length} explicit blocker(s) or risk notes are captured.`
          : workspace.prd_outline.open_decisions.length > 0
            ? "Open decisions are visible, but blockers still need sharper operator notes."
            : "Capture blockers, failure modes, or recovery notes before signoff.",
      detail: `${workspace.readiness.gates.length} readiness gate(s) tracked`
    },
    {
      id: "handoff",
      title: "Handoff readiness",
      status: exportsReady.length > 0 ? "complete" : workspace.exports.items.length > 0 ? "partial" : "missing",
      summary:
        exportsReady.length > 0
          ? `${exportsReady.length} export artifact(s) are marked ready.`
          : workspace.exports.items.length > 0
            ? "Artifacts exist, but none are marked ready for handoff."
            : "No handoff artifact exists yet.",
      detail: `${workspace.readiness.score}% readiness / ${workspace.exports.disabled_actions.length} disabled action(s)`
    }
  ];
}

function questionScenario(question: PlanningQuestion): PlanningScenarioRow {
  return {
    id: `question-${question.id}`,
    type: "Open decision",
    actor: question.decision_owner,
    trigger: question.question,
    expected: question.answer ?? "Record an explicit answer before approval.",
    status: question.status,
    note: question.owner
  };
}

export function buildScenarioMatrix(workspace: PlanningStudioWorkspace): PlanningScenarioRow[] {
  const rows: PlanningScenarioRow[] = [
    ...workspace.interview_flow.blockers.map((blocker) => ({
      id: `blocker-${blocker.id}`,
      type: "Failure mode",
      actor: blocker.owner,
      trigger: blocker.title,
      expected: "Fallback or human recovery path is visible before execution.",
      status: blocker.status,
      note: blocker.detail
    })),
    ...unresolvedQuestions(workspace).map(questionScenario),
    ...workspace.execution_breakdown.workstreams.map((workstream) => ({
      id: `workstream-${workstream.id}`,
      type: workstream.status === "blocked" ? "Blocked path" : "Delivery slice",
      actor: workstream.owner,
      trigger: workstream.title,
      expected: workstream.summary,
      status: workstream.status,
      note: workstream.slices.join(" / ") || "No slices recorded yet."
    })),
    ...workspace.exports.items
      .filter((item) => item.status !== "ready")
      .map((item) => ({
        id: `export-${item.id}`,
        type: "Handoff gate",
        actor: item.owner,
        trigger: item.title,
        expected: "Artifact is reviewable and ready for the next owner.",
        status: item.status,
        note: item.blocker ?? item.summary
      }))
  ];

  return rows.slice(0, 12);
}

export function buildTonightFocus(workspace: PlanningStudioWorkspace) {
  const items = [
    ...workspace.readiness.next_actions,
    ...unresolvedQuestions(workspace).slice(0, 3).map((question) => `Answer: ${question.question}`),
    ...workspace.interview_flow.blockers.slice(0, 2).map((blocker) => `Resolve blocker: ${blocker.title}`),
    ...workspace.exports.items
      .filter((item) => item.status !== "ready")
      .slice(0, 2)
      .map((item) => `Unblock export: ${item.title}`)
  ];

  return [...new Set(items)].slice(0, 6);
}

export function buildPhaseOverview(workspace: PlanningStudioWorkspace): PlanningPhaseItem[] {
  const openQuestions = unresolvedQuestions(workspace);
  const completeSections = completedSections(workspace);
  const activeWorkstreams = staffedWorkstreams(workspace);
  const readyHandoffs = readyExports(workspace);
  const blockedHandoffs = workspace.exports.items.filter((item) => item.status === "blocked");
  const activeStage = workspace.interview_flow.stages.find((stage) => stage.status === "active") ?? workspace.interview_flow.stages[0];

  return [
    {
      id: "intake",
      title: "Intake",
      status: workspace.intake.briefs.length > 0 && hasContent(workspace.project.goal) && hasContent(workspace.project.summary) ? "ready" : workspace.intake.briefs.length > 0 ? "active" : "watch",
      metric: `${workspace.intake.briefs.length} brief${workspace.intake.briefs.length === 1 ? "" : "s"}`,
      summary: workspace.project.goal || "Set the problem frame before the interview starts moving on rails.",
      detail: workspace.intake.briefs[0]?.title ?? "No active brief yet."
    },
    {
      id: "interview",
      title: "Interview",
      status: workspace.interview_flow.blockers.length > 0 ? "blocked" : openQuestions.length === 0 && workspace.interview_flow.sessions.length > 0 ? "ready" : workspace.interview_flow.sessions.length > 0 ? "active" : "watch",
      metric: activeStage?.title ?? "No stage",
      summary: openQuestions.length ? `${openQuestions.length} unresolved decision${openQuestions.length === 1 ? "" : "s"} still shape the plan.` : "Interview coverage is coherent enough to move into artifact review.",
      detail: workspace.interview_flow.current_stage || "No current interview stage."
    },
    {
      id: "outline",
      title: "Outline",
      status:
        workspace.prd_outline.sections.length > 0 && completeSections.length === workspace.prd_outline.sections.length
          ? "ready"
          : completeSections.length > 0
            ? "active"
            : "watch",
      metric: `${completeSections.length}/${workspace.prd_outline.sections.length}`,
      summary: workspace.prd_outline.open_decisions.length ? `${workspace.prd_outline.open_decisions.length} open decision${workspace.prd_outline.open_decisions.length === 1 ? "" : "s"} remain visible.` : "Outline sections and success criteria are aligned.",
      detail: workspace.prd_outline.sections[0]?.title ?? "No PRD outline sections yet."
    },
    {
      id: "execution",
      title: "Execution",
      status:
        workspace.execution_breakdown.workstreams.some((workstream) => workstream.status === "blocked")
          ? "blocked"
          : activeWorkstreams.length === workspace.execution_breakdown.workstreams.length && workspace.execution_breakdown.workstreams.length > 0
            ? "ready"
            : activeWorkstreams.length > 0
              ? "active"
              : "watch",
      metric: `${activeWorkstreams.length}/${workspace.execution_breakdown.workstreams.length}`,
      summary: workspace.execution_breakdown.dependency_chain[0] ?? "Define the first dependency before assigning delivery slices.",
      detail: workspace.execution_breakdown.milestones[0]?.title ?? "No milestone staged yet."
    },
    {
      id: "handoff",
      title: "Handoff",
      status:
        blockedHandoffs.length > 0 ? "blocked" : readyHandoffs.length === workspace.exports.items.length && workspace.exports.items.length > 0 ? "ready" : workspace.exports.items.length > 0 ? "active" : "watch",
      metric: `${readyHandoffs.length}/${workspace.exports.items.length}`,
      summary:
        blockedHandoffs.length > 0
          ? `${blockedHandoffs.length} handoff artifact${blockedHandoffs.length === 1 ? "" : "s"} still need named ownership or evidence.`
          : readyHandoffs.length > 0
            ? "The export bundle is partially staged and reviewable."
            : "No export artifact is confidently staged yet.",
      detail: workspace.exports.items[0]?.title ?? "No export artifact staged."
    }
  ];
}

export function buildOperatorGuidance(workspace: PlanningStudioWorkspace): PlanningOperatorGuidance {
  const openQuestions = unresolvedQuestions(workspace);
  const blockers = workspace.interview_flow.blockers;
  const blockedHandoffs = workspace.exports.items.filter((item) => item.status === "blocked");
  const incompleteSections = workspace.prd_outline.sections.filter((section) => section.status !== "complete");

  if (openQuestions.length > 0) {
    return {
      title: "Close the decision inbox",
      status: "active",
      detail: "Interview clarity is still the fastest way to raise sellable polish because unresolved decisions make every later artifact look provisional.",
      cues: openQuestions.slice(0, 3).map((question) => `${question.decision_owner}: ${question.question}`)
    };
  }

  if (blockers.length > 0) {
    return {
      title: "Resolve the visible blockers",
      status: "blocked",
      detail: "The plan already reads coherently, but the blocked operator paths need explicit recovery notes before the export looks trustworthy.",
      cues: blockers.slice(0, 3).map((blocker) => `${blocker.title}: ${blocker.detail}`)
    };
  }

  if (incompleteSections.length > 0) {
    return {
      title: "Finish the thin outline sections",
      status: "active",
      detail: "The brief and interview are far enough along that the remaining sellable lift is making the PRD read intentionally complete rather than seed-like.",
      cues: incompleteSections.slice(0, 3).map((section) => `${section.title} (${section.status})`)
    };
  }

  if (blockedHandoffs.length > 0) {
    return {
      title: "Unblock the export bundle",
      status: "blocked",
      detail: "The handoff needs one last pass on ownership and evidence so the operator can export without reading around missing context.",
      cues: blockedHandoffs.slice(0, 3).map((item) => `${item.title}: ${item.blocker ?? item.summary}`)
    };
  }

  return {
    title: "Package the handoff with confidence",
    status: "ready",
    detail: "Interview coverage, outline shape, and execution slices are aligned enough to behave like a real planning product rather than a raw editor seed.",
    cues: [
      "Review the markdown export preview",
      "Confirm the active template matches the session",
      "Hand off the ready artifact bundle"
    ]
  };
}

export function buildExportConfidence(workspace: PlanningStudioWorkspace): PlanningConfidenceCheck[] {
  const ready = readyExports(workspace);
  const blocked = workspace.exports.items.filter((item) => item.status === "blocked");
  const openQuestions = unresolvedQuestions(workspace);

  return [
    {
      id: "artifacts",
      title: "Artifacts staged",
      status: ready.length > 0 ? (blocked.length ? "active" : "ready") : workspace.exports.items.length > 0 ? "active" : "watch",
      detail: ready.length > 0 ? `${ready.length} export artifact(s) are marked ready for operator review.` : "Export artifacts exist, but none are clearly marked ready."
    },
    {
      id: "decision-visibility",
      title: "Decision visibility",
      status: openQuestions.length === 0 ? "ready" : "active",
      detail: openQuestions.length === 0 ? "No open interview questions are hiding behind the export." : `${openQuestions.length} open interview question(s) still sit behind the handoff.`
    },
    {
      id: "risk-signals",
      title: "Risk signals surfaced",
      status: blocked.length > 0 ? "blocked" : workspace.readiness.blockers.length > 0 || workspace.interview_flow.blockers.length > 0 ? "active" : "ready",
      detail:
        blocked.length > 0
          ? blocked.map((item) => item.blocker ?? item.title).join(" / ")
          : workspace.readiness.blockers.length > 0 || workspace.interview_flow.blockers.length > 0
            ? "Known blockers are visible in the workspace and should be referenced in the export review."
            : "No blocked handoff artifacts are hidden."
    },
    {
      id: "restore-path",
      title: "Recovery path",
      status: workspace.exports.items.length > 0 ? "ready" : "watch",
      detail: "JSON import/export keeps the workspace portable, and local persistence preserves the current session between refreshes."
    }
  ];
}
