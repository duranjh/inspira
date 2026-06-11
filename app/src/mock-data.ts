export type PlanningStudioSnapshot = {
  planning_studio: PlanningStudioWorkspace;
};

export type PlanningStudioWorkspace = {
  enabled: boolean;
  error: PlanningStudioError | null;
  updated_at: string;
  summary: string;
  project: PlanningProject;
  intake: PlanningIntake;
  interview_flow: PlanningInterviewFlow;
  template_pack: PlanningTemplatePack;
  prd_outline: PlanningPrdOutline;
  execution_breakdown: PlanningExecutionBreakdown;
  exports: PlanningExports;
  readiness: PlanningReadiness;
};

export type PlanningStudioError = {
  code: string;
  title: string;
  detail: string;
  severity: "warning" | "danger";
};

export type PlanningProject = {
  product_name: string;
  owner: string;
  stage: string;
  target_release: string;
  goal: string;
  summary: string;
  requested_by: string;
};

export type PlanningBrief = {
  id: string;
  title: string;
  owner: string;
  department: string;
  priority: string;
  stage: string;
  status: string;
  summary: string;
  scope: string;
};

export type PlanningQuestion = {
  id: string;
  question: string;
  owner: string;
  decision_owner: string;
  status: string;
  answer: string | null;
};

export type PlanningIntake = {
  status: string;
  briefs: PlanningBrief[];
  open_questions: PlanningQuestion[];
  decision_log: string[];
};

export type InterviewStage = {
  id: string;
  title: string;
  owner: string;
  status: string;
  duration: string;
  objective: string;
  output: string;
};

export type PlanningInterviewSession = {
  id: string;
  title: string;
  interviewer: string;
  participant: string;
  status: string;
  updated_at: string;
  notes: string;
  tags: string[];
};

export type PlanningBlocker = {
  id: string;
  title: string;
  owner: string;
  status: string;
  severity: "warning" | "danger";
  detail: string;
};

export type PlanningInterviewFlow = {
  status: string;
  current_stage: string;
  stages: InterviewStage[];
  sessions: PlanningInterviewSession[];
  blockers: PlanningBlocker[];
};

export type PlanningTemplate = {
  id: string;
  title: string;
  kind: string;
  owner: string;
  status: string;
  updated_at: string;
  summary: string;
  notes: string[];
};

export type PlanningTemplatePack = {
  status: string;
  selected_template_id: string;
  last_synced_at: string;
  items: PlanningTemplate[];
};

export type PlanningPrdSection = {
  id: string;
  title: string;
  owner: string;
  status: string;
  summary: string;
  details: string;
  acceptance: string[];
  dependencies: string[];
};

export type PlanningPrdOutline = {
  status: string;
  sections: PlanningPrdSection[];
  open_decisions: string[];
  success_criteria: string[];
};

export type PlanningWorkstream = {
  id: string;
  title: string;
  owner: string;
  status: string;
  estimate: string;
  summary: string;
  dependencies: string[];
  slices: string[];
};

export type PlanningMilestone = {
  id: string;
  title: string;
  status: string;
  due_at: string;
  note: string;
};

export type PlanningExecutionBreakdown = {
  status: string;
  workstreams: PlanningWorkstream[];
  milestones: PlanningMilestone[];
  dependency_chain: string[];
};

export type PlanningExportArtifact = {
  id: string;
  title: string;
  target: string;
  owner: string;
  status: string;
  format: string;
  updated_at: string;
  summary: string;
  blocker: string | null;
};

export type PlanningExports = {
  status: string;
  items: PlanningExportArtifact[];
  disabled_actions: string[];
};

export type PlanningReadinessGate = {
  id: string;
  title: string;
  owner: string;
  status: string;
  note: string;
};

export type PlanningReadiness = {
  status: string;
  score: number;
  next_actions: string[];
  blockers: string[];
  gates: PlanningReadinessGate[];
};

function minutesAgo(base: number, minutes: number) {
  return new Date(base - minutes * 60_000).toISOString();
}

export function buildMockSnapshot(revision: number): PlanningStudioSnapshot {
  const baseTime = Date.now() - revision * 13 * 60_000;
  const templateId = revision % 2 === 0 ? "prd-outline" : "interview-guide";

  return {
    planning_studio: {
      enabled: true,
      error: null,
      updated_at: minutesAgo(baseTime, 3),
      summary:
        "Interview-first PRD planner for the dropshipping research agent system. The seed workspace is framed around OpenClaw candidate handling, human review, live source checks, and learning that survives host migration.",
      project: {
        product_name: "Dropshipping Research Agent",
        owner: "Project Manager",
        stage: "interview framing",
        target_release: "V1 agent launch",
        goal: "Turn owner-approved product screening rules into a continuous, review-first dropshipping pipeline that can learn from human decisions.",
        summary:
          "The planner keeps intake, interviews, templates, planning artifacts, and export readiness together so the PM can move from discovery to delivery without jumping surfaces.",
        requested_by: "Owner"
      },
      intake: {
        status: "review",
        briefs: [
          {
            id: "brief-001",
            title: "Dropshipping research agent",
            owner: "Owner",
            department: "Operations",
            priority: "critical",
            stage: "discovery",
            status: "review",
            summary: "Screen Kalodata products, run support checks, and move only the strongest candidates into human review.",
            scope: "Candidate screening and review"
          },
          {
            id: "brief-002",
            title: "Ads and website intelligence",
            owner: "Owner",
            department: "Research",
            priority: "medium",
            stage: "research",
            status: "review",
            summary: "Split candidate findings from reusable lessons while capturing ad angles, copy, saturation, and site quality.",
            scope: "Ads research and website research lanes"
          },
          {
            id: "brief-003",
            title: "Review Shadow lane",
            owner: "Project Manager",
            department: "Engineering",
            priority: "high",
            stage: "learn",
            status: "draft",
            summary: "Record human review decisions, overrides, notes, and score changes so the review agent can learn over time.",
            scope: "Human review memory and calibration"
          }
        ],
        open_questions: [
          {
            id: "q-001",
            question: "Should a candidate ever bypass human review after instant-killer screening?",
            owner: "PM",
            decision_owner: "Owner",
            status: "open",
            answer: null
          },
          {
            id: "q-002",
            question: "What support checks are required alongside Kalodata before a candidate can be reviewed?",
            owner: "PM",
            decision_owner: "Research",
            status: "answered",
            answer: "Google Trends, Meta Ad Library, and direct competitor or storefront pages are required. Unknowns can still be submitted if flagged clearly."
          },
          {
            id: "q-003",
            question: "How should the Review Shadow lane learn from human decisions without becoming the workflow of record?",
            owner: "PM",
            decision_owner: "Engineering",
            status: "answered",
            answer: "Keep the candidate record in OpenClaw, then promote curated lessons into Knowledge Exchange after each human-reviewed outcome."
          }
        ],
        decision_log: [
          "OpenClaw owns the candidate record first.",
          "Human review stays mandatory after instant-killer screening.",
          "Learning state stays in OpenClaw and must be exportable/importable.",
          "Checkpoint notifications are scheduled for 6:00 AM, 12:00 PM, and 5:00 PM local time."
        ]
      },
      interview_flow: {
        status: "active",
        current_stage: "Screening rules interview",
        stages: [
          {
            id: "stage-001",
            title: "Intake triage",
            owner: "Project Manager",
            status: "complete",
            duration: "20 min",
            objective: "Separate real product requests from background ideas and route them into the right agent lane.",
            output: "Briefs accepted into the planning queue."
          },
          {
            id: "stage-002",
            title: "Screening rules interview",
            owner: "Owner",
            status: "active",
            duration: "35 min",
            objective: "Lock down the instant-killer, support-check, and review rules that govern candidate flow.",
            output: "Open questions and screening packets."
          },
          {
            id: "stage-003",
            title: "Research lanes review",
            owner: "Research",
            status: "queued",
            duration: "25 min",
            objective: "Split candidate-specific findings from reusable lessons and keep ads and website research in parallel.",
            output: "Research lane contracts."
          },
          {
            id: "stage-004",
            title: "Review Shadow calibration",
            owner: "Engineering",
            status: "queued",
            duration: "15 min",
            objective: "Record human approve/reject/hold decisions and translate them into learning state that survives export.",
            output: "Review memory calibration."
          }
        ],
        sessions: [
          {
            id: "sess-001",
            title: "Dropshipping agent discovery session",
            interviewer: "Project Manager",
            participant: "Owner",
            status: "active",
            updated_at: minutesAgo(baseTime, 24),
            notes: "Keep OpenClaw as the candidate record, force human review after instant-killer screening, and make learning exportable.",
            tags: ["screening", "openclaw", "human review"]
          },
          {
            id: "sess-002",
            title: "Ads and website intelligence review",
            interviewer: "Research",
            participant: "Owner",
            status: "queued",
            updated_at: minutesAgo(baseTime, 72),
            notes: "Candidate findings stay in OpenClaw while reusable patterns move into Knowledge Exchange for later memory.",
            tags: ["ads", "website", "lessons"]
          }
        ],
        blockers: [
          {
            id: "blocker-001",
            title: "CAPTCHA handling needs a human fallback",
            owner: "Owner",
            status: "blocked",
            severity: "danger",
            detail: "The plan is human clears once, agent resumes. No full automation path should rely on bypassing anti-bot controls."
          }
        ]
      },
      template_pack: {
        status: "loaded",
        selected_template_id: templateId,
        last_synced_at: minutesAgo(baseTime, 11),
        items: [
          {
            id: "interview-guide",
            title: "Interview Guide",
            kind: "kickoff",
            owner: "Ops",
            status: "ready",
            updated_at: minutesAgo(baseTime, 180),
            summary: "A structured intake prompt that keeps discovery focused on ownership, constraints, and goals for the dropshipping agent system.",
            notes: ["Start with the instant-killer rules", "Ask for review actions and learning rules"]
          },
          {
            id: "scenario-matrix",
            title: "Scenario Matrix",
            kind: "analysis",
            owner: "Ops",
            status: "ready",
            updated_at: minutesAgo(baseTime, 140),
            summary: "Capture happy paths, edge cases, failure handling, and support questions before execution begins.",
            notes: ["Happy path", "Failure path", "CAPTCHA fallback"]
          },
          {
            id: "prd-outline",
            title: "PRD Outline",
            kind: "artifact",
            owner: "Ops",
            status: "active",
            updated_at: minutesAgo(baseTime, 98),
            summary: "Convert interview output into a build-ready outline with scope, goals, and acceptance criteria for V1 dropshipping operations.",
            notes: ["OpenClaw record first", "Exportable learning state", "Checkpoint notifications"]
          },
          {
            id: "handoff-bundle",
            title: "Handoff Bundle",
            kind: "export",
            owner: "Ops",
            status: "draft",
            updated_at: minutesAgo(baseTime, 40),
            summary: "Package the PRD, interview notes, and execution slices into a PM-ready delivery packet.",
            notes: ["Markdown export", "JSON import/export", "Review Shadow lane"]
          }
        ]
      },
      prd_outline: {
        status: "draft",
        sections: [
          {
            id: "sec-001",
            title: "Candidate workflow and ownership",
            owner: "PM",
            status: "complete",
            summary: "Keep the candidate record in OpenClaw and separate candidate findings from reusable lessons.",
            details: "The workflow starts with a candidate record in OpenClaw. Candidate findings, review states, scores, and learning state stay in that system so the queue remains authoritative.",
            acceptance: ["OpenClaw candidate record first", "Candidate origins are recorded", "Review states persist"],
            dependencies: ["intake review"]
          },
          {
            id: "sec-002",
            title: "Instant killers and support checks",
            owner: "Owner",
            status: "review",
            summary: "Use Kalodata plus required support checks before human review.",
            details: "Screening uses Kalodata first, then required support checks such as Google Trends, Meta Ad Library, and competitor or storefront pages. Unknowns stay visible and still go to human review.",
            acceptance: ["Kalodata + required support checks", "Unknowns still submit", "US general market is the default"],
            dependencies: ["scope interview"]
          },
          {
            id: "sec-003",
            title: "Human review and learning",
            owner: "Engineering",
            status: "active",
            summary: "Require human review after screening and learn from every human-reviewed outcome.",
            details: "Human review actions include approve, reject, temporary hold, notes, and score or data updates. Review Shadow records the decision path and the learning state remains exportable and importable.",
            acceptance: ["Human review mandatory", "Review Shadow lane records decisions", "Learning survives export/import"],
            dependencies: ["scenario matrix"]
          },
          {
            id: "sec-004",
            title: "Intelligence lanes",
            owner: "Engineering",
            status: "draft",
            summary: "Split candidate findings from reusable lessons while letting ads and website research run in parallel.",
            details: "Ads and website intelligence produce candidate-specific findings in OpenClaw and reusable lessons in Knowledge Exchange. That keeps the operational queue separate from long-term memory.",
            acceptance: ["Candidate findings stay in OpenClaw", "Reusable lessons flow to Knowledge Exchange", "Ads and website lanes stay parallel"],
            dependencies: ["scenario review"]
          },
          {
            id: "sec-005",
            title: "Automation and checkpointing",
            owner: "Operations",
            status: "review",
            summary: "Keep the pipeline moving with scheduled summaries and human-in-the-loop CAPTCHA handling.",
            details: "Checkpoint notifications go out at 6:00 AM, 12:00 PM, and 5:00 PM local time. CAPTCHA handling is human clears once, agent resumes. Layout breaks should try a backup extraction path first.",
            acceptance: ["Checkpoint summaries scheduled", "Human clears CAPTCHA once", "Backup extraction path first"],
            dependencies: ["scenario review"]
          }
        ],
        open_decisions: ["Review action wording", "Knowledge promotion rule", "Source priority order"],
        success_criteria: [
          "PM can summarize scope in under two minutes",
          "Engineering can pick up the handoff without a clarification loop",
          "The export bundle includes the right support checks and learning notes"
        ]
      },
      execution_breakdown: {
        status: "review",
        workstreams: [
          {
            id: "ws-001",
            title: "Screening lane",
            owner: "Engineering",
            status: "active",
            estimate: "2 days",
            summary: "Build Kalodata-first screening with required support checks and instant-killer evaluation.",
            dependencies: ["project brief"],
            slices: ["Candidate intake", "Support checks", "Human review packet"]
          },
          {
            id: "ws-002",
            title: "Ads research lane",
            owner: "Research",
            status: "review",
            estimate: "3 days",
            summary: "Gather ad activity, creative patterns, scaling signals, and copy lessons for each candidate.",
            dependencies: ["candidate record"],
            slices: ["Ad count", "Offer analysis", "Creative notes"]
          },
          {
            id: "ws-003",
            title: "Website research lane",
            owner: "Research",
            status: "draft",
            estimate: "3 days",
            summary: "Capture competitor storefront structure, pricing, trust elements, and website copy lessons.",
            dependencies: ["candidate record"],
            slices: ["Site quality", "Offer structure", "Branding notes"]
          },
          {
            id: "ws-004",
            title: "Review Shadow lane",
            owner: "Project Manager",
            status: "complete",
            estimate: "1 day",
            summary: "Capture human review decisions, notes, and score updates so the agent can learn from outcomes.",
            dependencies: ["stakeholder signoff"],
            slices: ["Decision log", "Notes capture", "Score updates"]
          }
        ],
        milestones: [
          {
            id: "mile-001",
            title: "Seed data ready",
            status: "complete",
            due_at: minutesAgo(baseTime, 480),
            note: "The workspace now reflects the dropshipping agent V1."
          },
          {
            id: "mile-002",
            title: "Interview flow usable",
            status: "review",
            due_at: minutesAgo(baseTime, 180),
            note: "The planner exposes editable project, interview, and outline flows."
          },
          {
            id: "mile-003",
            title: "Learning loop persisted",
            status: "queued",
            due_at: minutesAgo(baseTime, 60),
            note: "JSON import/export and localStorage keep the state portable."
          }
        ],
        dependency_chain: ["OpenClaw candidate record", "Human review", "Review Shadow", "Knowledge promotion", "Checkpoint notifications"]
      },
      exports: {
        status: "draft",
        items: [
          {
            id: "export-001",
            title: "Dropshipping V1 PRD draft packet",
            target: "Engineering and Operations",
            owner: "Project Manager",
            status: "ready",
            format: "PDF + markdown",
            updated_at: minutesAgo(baseTime, 18),
            summary: "Includes workflow ownership, screening rules, learning loop, and parallel research lanes.",
            blocker: null
          },
          {
            id: "export-002",
            title: "Human review evidence bundle",
            target: "Stakeholder review",
            owner: "Owner",
            status: "review",
            format: "Markdown",
            updated_at: minutesAgo(baseTime, 52),
            summary: "Carries the transcript summary, open questions, and follow-up decisions from human review.",
            blocker: null
          },
          {
            id: "export-003",
            title: "OpenClaw candidate export bundle",
            target: "Delivery lead",
            owner: "Operations",
            status: "blocked",
            format: "ZIP",
            updated_at: minutesAgo(baseTime, 12),
            summary: "Awaiting the pricing owner response before the handoff can be marked ready.",
            blocker: "Missing named pricing owner"
          }
        ],
        disabled_actions: ["Publish to delivery", "Archive prior draft", "Sync external tracker"]
      },
      readiness: {
        status: "watch",
        score: 84,
        next_actions: ["Draft a private companion repo for criteria and methods", "Seed the review-shadow learning lane", "Document the Mac export path"],
        blockers: ["CAPTCHA handling still depends on a human clear", "Live source login failures need fallback recovery"],
        gates: [
          {
            id: "gate-001",
            title: "Candidate workflow",
            owner: "PM",
            status: "complete",
            note: "OpenClaw candidate records, review state, and learning state are defined."
          },
          {
            id: "gate-002",
            title: "Instant killers",
            owner: "Owner",
            status: "complete",
            note: "Kalodata-first support checks and instant-killer screening are clearly defined."
          },
          {
            id: "gate-003",
            title: "Review and learning",
            owner: "Engineering",
            status: "review",
            note: "Human review actions, Review Shadow, and knowledge promotion are ready to implement."
          },
          {
            id: "gate-004",
            title: "Checkpointing",
            owner: "Operations",
            status: "review",
            note: "Morning, noon, and after-work summaries are wired into the review cadence."
          }
        ]
      }
    }
  };
}
