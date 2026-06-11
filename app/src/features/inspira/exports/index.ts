// W2 κ — Send-to-Linear / Send-to-GitHub export modal surface.
//
// Standalone, surface-agnostic: anyone can open these modals by
// dispatching a window CustomEvent, no prop wiring required. Default
// mount lives in InspiraApp; ι's Artifact Viewer Export buttons fire
// the same events when they ship.
//
// Window event contract:
//   window.dispatchEvent(new CustomEvent('inspira:export-to-linear', {
//     detail: { projectId },
//   }));
//   window.dispatchEvent(new CustomEvent('inspira:export-to-github', {
//     detail: { projectId },
//   }));

export { ExportModalsHost } from "./ExportModalsHost";
export { SendToLinearModal } from "./SendToLinearModal";
export { SendToGitHubModal } from "./SendToGitHubModal";
export type { ExportProvider } from "./types";

export const EXPORT_TO_LINEAR_EVENT = "inspira:export-to-linear" as const;
export const EXPORT_TO_GITHUB_EVENT = "inspira:export-to-github" as const;
