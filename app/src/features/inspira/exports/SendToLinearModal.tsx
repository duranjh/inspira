// Per-provider wrapper around the shared ExportModal.

import { ExportModal } from "./ExportModal";

export type SendToLinearModalProps = {
  projectId: string | null;
  open: boolean;
  onClose: () => void;
};

export function SendToLinearModal(props: SendToLinearModalProps) {
  return <ExportModal provider="linear" {...props} />;
}
