// B2.3 — content-aware topic-seed fixtures for the
// Promote-to-Project dialog.
//
// Product decision: the original seeds were bug-shaped only
// ("Reproduce the bug" / "Identify root cause" / etc.) — that's
// nonsense for feature requests, praise, questions, or general
// chatter. Now we pick a seed list based on the source feedback's
// `type_hint` (or the cluster's most-common category).
//
// HONEST COPY: these are still NOT LLM-clustered outputs — they're
// deterministic per-category fixtures. The dialog's section heading
// reads "Suggested topic seeds (you can edit)" — never "Inspira
// drafted these" — to avoid claiming AI authorship the system isn't
// performing yet. Replace with a real LLM-driven seed call when the
// backend cluster-summarizer lands.

export interface TopicSeed {
  id: string;
  name: string;
  desc: string;
  removed: boolean;
  added: boolean;
}

/** The category buckets that drive seed selection. Mirrors the
 *  type_hint values F4/F5 classifier emits + a sensible default. */
export type FeedbackCategory =
  | "bug"
  | "feature"
  | "complaint"
  | "praise"
  | "question"
  | "general";

let seq = 0;
function nextId(): string {
  seq += 1;
  return `seed-${seq}`;
}

function seed(name: string, desc: string): TopicSeed {
  return { id: nextId(), name, desc, removed: false, added: false };
}

const BUG_SEEDS = (): TopicSeed[] => [
  seed(
    "Reproduce the bug",
    "Set up a deterministic environment that triggers the issue.",
  ),
  seed(
    "Identify root cause",
    "Trace the failing path through the affected code surface.",
  ),
  seed(
    "Decide on the fix layer",
    "Pick where the fix lands — UI, API, infrastructure, or process.",
  ),
  seed(
    "Patch and verify",
    "Land the change behind a flag; confirm the original report no longer reproduces.",
  ),
  seed(
    "Customer comms",
    "Tell the affected customers what happened and what's changed.",
  ),
];

const FEATURE_SEEDS = (): TopicSeed[] => [
  seed(
    "Define the user need",
    "Pin down the job-to-be-done. Whose problem are we solving, and how do they describe it today?",
  ),
  seed(
    "Sketch the user flow",
    "Walk through the happy path end-to-end. Note where the user enters, decides, and exits.",
  ),
  seed(
    "Draft the surface area",
    "API, UI, data model. Describe the smallest shippable cut that delivers the value.",
  ),
  seed(
    "Build + test",
    "Implement behind a flag; cover the happy path with tests; smoke the edges.",
  ),
  seed(
    "Roll out",
    "Internal preview → design partners → general availability. Define the gate criteria for each.",
  ),
];

const COMPLAINT_SEEDS = (): TopicSeed[] => [
  seed(
    "Map the impact",
    "Who's affected, how often, and how blocking is it for their workflow?",
  ),
  seed(
    "Find the underlying cause",
    "Is this a UX, a docs, a performance, or a fairness issue? Different roots, different fixes.",
  ),
  seed(
    "Propose the fix",
    "Could be code, copy, or a process change. Pick the lightest one that resolves the complaint.",
  ),
  seed(
    "Close the loop",
    "Reach back to the customers who reported it; confirm the fix actually lands their concern.",
  ),
];

const QUESTION_SEEDS = (): TopicSeed[] => [
  seed(
    "Identify the gap",
    "What's the customer trying to do, and what made them stop and ask?",
  ),
  seed(
    "Write the answer",
    "Draft a clear, source-backed response. Include the steps and the why.",
  ),
  seed(
    "Surface in product",
    "Should this answer live in docs, a tooltip, an empty-state, or a help link? Pick one.",
  ),
  seed(
    "Notify the asker",
    "Send the answer to the customer who asked, plus anyone else with the same question.",
  ),
];

const PRAISE_SEEDS = (): TopicSeed[] => [
  seed(
    "Why it landed",
    "What about the experience clicked for them? Capture the verbatim signal.",
  ),
  seed(
    "Who else benefits",
    "Identify other customers or segments where this kind of moment would also resonate.",
  ),
  seed(
    "Amplify",
    "Tighten the path that produced this praise. Make the moment more discoverable.",
  ),
];

const GENERAL_SEEDS = (): TopicSeed[] => [
  seed(
    "Frame the problem",
    "Restate the feedback in the customer's words. What's actually being asked for?",
  ),
  seed(
    "Map the options",
    "Sketch 2-3 plausible directions. Note tradeoffs (cost, scope, risk) for each.",
  ),
  seed(
    "Pick a direction",
    "Decide which option to pursue and why. Document what you're explicitly NOT doing.",
  ),
  seed(
    "Plan the build",
    "Break the chosen direction into shippable slices. Identify the first one.",
  ),
  seed(
    "Communicate",
    "Tell the customer what you decided and when they can expect it.",
  ),
];

const SEED_PICKER: Record<FeedbackCategory, () => TopicSeed[]> = {
  bug: BUG_SEEDS,
  feature: FEATURE_SEEDS,
  complaint: COMPLAINT_SEEDS,
  question: QUESTION_SEEDS,
  praise: PRAISE_SEEDS,
  general: GENERAL_SEEDS,
};

/** Pick a seed list keyed by category. Unknown categories fall through
 *  to the general / generic deliberation flow so the dialog never
 *  renders with zero seeds. */
export function defaultTopicSeeds(category?: string | null): TopicSeed[] {
  const key = (category ?? "").toLowerCase().trim();
  const factory =
    (SEED_PICKER as Record<string, () => TopicSeed[]>)[key] ?? GENERAL_SEEDS;
  return factory();
}

/** Build a fresh, user-added topic seed (for the "+ Add another topic" button). */
export function makeBlankSeed(): TopicSeed {
  return {
    id: nextId(),
    name: "New topic",
    desc: "",
    removed: false,
    added: true,
  };
}
