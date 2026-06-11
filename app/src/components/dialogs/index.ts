// Inspira — dialog barrel.
//
// Re-exports the base `Dialog` and the four warm-editorial dialogs that
// compose it. Import from here, not from the individual files, so the
// caller API stays stable if we ever restructure internally.

export { Dialog } from "./Dialog";
export type {
  DialogProps,
  DialogPrimaryAction,
  DialogSecondaryAction,
} from "./Dialog";

export { RenameProjectDialog } from "./RenameProjectDialog";
export type { RenameProjectDialogProps } from "./RenameProjectDialog";

export { DeleteConfirmDialog } from "./DeleteConfirmDialog";
export type { DeleteConfirmDialogProps } from "./DeleteConfirmDialog";

export { ShareProjectDialog } from "./ShareProjectDialog";
export type { ShareProjectDialogProps } from "./ShareProjectDialog";

export { ExportOptionsDialog } from "./ExportOptionsDialog";
export type {
  ExportOptionsDialogProps,
  ExportFormat,
} from "./ExportOptionsDialog";

export { ImportFromJsonDialog } from "./ImportFromJsonDialog";
export type { ImportFromJsonDialogProps } from "./ImportFromJsonDialog";

export { RelationshipLabelDialog } from "./RelationshipLabelDialog";
export type { RelationshipLabelDialogProps } from "./RelationshipLabelDialog";

export {
  TopicCompletionDialog,
  isCompletionSuppressed,
  suppressCompletionDialog,
} from "./TopicCompletionDialog";
export type { TopicCompletionDialogProps } from "./TopicCompletionDialog";

export { MoveToShelfDialog } from "./MoveToShelfDialog";
export type { MoveToShelfDialogProps } from "./MoveToShelfDialog";
