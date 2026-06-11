// Per-provider wrapper around the shared ExportModal.

import { ExportModal } from "./ExportModal";

export type SendToGitHubModalProps = {
  projectId: string | null;
  open: boolean;
  onClose: () => void;
};

export function SendToGitHubModal(props: SendToGitHubModalProps) {
  return <ExportModal provider="github" {...props} />;
}
