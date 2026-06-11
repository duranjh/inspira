// Surface-agnostic mount point for the W2 κ export modals.
//
// Listens for two window CustomEvents:
//   inspira:export-to-linear  { detail: { projectId } }
//   inspira:export-to-github  { detail: { projectId } }
// and opens the matching modal. Callers (Artifact Viewer top bar in ι,
// any future surface) dispatch the event — no prop wiring, no provider
// context.
//
// Defensive against double-mounts: each event sets state on this host's
// closure, so multiple <ExportModalsHost /> instances would each open
// their own modal. App-level mount lives in InspiraApp.tsx; don't add
// peer mounts.

import { useEffect, useState } from "react";

import { SendToLinearModal } from "./SendToLinearModal";
import { SendToGitHubModal } from "./SendToGitHubModal";
import {
  EXPORT_TO_LINEAR_EVENT,
  EXPORT_TO_GITHUB_EVENT,
} from "./index";

type OpenState =
  | { open: false }
  | { open: true; provider: "linear" | "github"; projectId: string };

function readDetailProjectId(detail: unknown): string | null {
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const pid = (detail as { projectId?: unknown }).projectId;
  return typeof pid === "string" && pid.length > 0 ? pid : null;
}

export function ExportModalsHost() {
  const [state, setState] = useState<OpenState>({ open: false });

  useEffect(() => {
    function handle(provider: "linear" | "github") {
      return (ev: Event) => {
        const projectId = readDetailProjectId(
          (ev as CustomEvent).detail,
        );
        if (!projectId) {
          // Silent no-op on malformed dispatches — surfacing an error
          // here would be louder than the bug deserves.
          return;
        }
        setState({ open: true, provider, projectId });
      };
    }

    const linearHandler = handle("linear");
    const githubHandler = handle("github");
    window.addEventListener(EXPORT_TO_LINEAR_EVENT, linearHandler);
    window.addEventListener(EXPORT_TO_GITHUB_EVENT, githubHandler);
    return () => {
      window.removeEventListener(EXPORT_TO_LINEAR_EVENT, linearHandler);
      window.removeEventListener(EXPORT_TO_GITHUB_EVENT, githubHandler);
    };
  }, []);

  const onClose = () => setState({ open: false });

  return (
    <>
      <SendToLinearModal
        projectId={state.open && state.provider === "linear" ? state.projectId : null}
        open={state.open && state.provider === "linear"}
        onClose={onClose}
      />
      <SendToGitHubModal
        projectId={state.open && state.provider === "github" ? state.projectId : null}
        open={state.open && state.provider === "github"}
        onClose={onClose}
      />
    </>
  );
}
