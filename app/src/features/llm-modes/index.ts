// Public exports for the llm-modes feature. Host code (InspiraApp or
// similar) imports the panel + its types from this barrel so the rest
// of the app doesn't need to know the internal file layout.

export { LlmModesPanel } from "./LlmModesPanel";
export type {
  LlmModesPanelProps,
  LlmModesPrefetch,
  LlmModesTab,
} from "./LlmModesPanel";

export {
  SummaryView,
  SummaryViewError,
  SummaryViewLoading,
} from "./SummaryView";
export type {
  SummaryViewProps,
  SummaryViewErrorProps,
} from "./SummaryView";

export {
  DedupeView,
  DedupeViewError,
  DedupeViewLoading,
} from "./DedupeView";
export type {
  DedupeViewProps,
  DedupeViewErrorProps,
  MergeProposal,
  TopicStub,
} from "./DedupeView";
