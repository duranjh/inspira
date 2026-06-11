// /code/:projectId? — IDE as a top-level route + rail tab.
//
// Product decision: lift the Code screen out of the
// per-project artifact phase inside InspiraApp.tsx and make it its
// own top-level surface (like Workspaces / Connectors / Inbox).
// Clicking Code on a canvas navigates here with the project id
// preloaded; the partner can also click the Code rail item directly
// to land here without project context.
//
// LAYER 1 (this file): just lifts the existing ArtifactViewerPage
// into a route. The 3-pane layout (file tree + editor + chat) is
// reused as-is, scoped to the project from the URL. When no
// projectId is in the URL, renders an empty state pointing the
// partner back to a project they can pick.
//
// LAYER 2 (never built): the file tree was planned to become a real
// repo file system with main/ + PRs/ folders.

import { ReactElement, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { type ProjectState } from "../../components/ProjectStateBadge";
import { useSSE } from "../../hooks/useSSE";
import { api } from "../inspira/api";
import { ArtifactViewerPage } from "../inspira/artifact/ArtifactViewerPage";
import { OrchestratorChip } from "../inspira/chrome/OrchestratorChip";
import { AuthedShell } from "../shared/AuthedShell";

interface ProjectMeta {
  title: string;
  state: ProjectState;
}

export function CodeRoute(): ReactElement {
  const params = useParams<{ projectId?: string }>();
  const projectId = params.projectId ?? null;
  return (
    <AuthedShell
      rightSlot={projectId ? <OrchestratorChip /> : undefined}
    >
      <CodeBody />
    </AuthedShell>
  );
}

function CodeBody(): ReactElement {
  const params = useParams<{ projectId?: string }>();
  const navigate = useNavigate();
  const projectId = params.projectId ?? null;

  // Project-scoped SSE — the same window-event stream the canvas
  // subscribes to (via ProjectCanvas → useSSE). Without this, the
  // OrchestratorChip in the rail would never animate on /code/:id
  // because no EventSource is open. Safe when projectId is null
  // (useSSE short-circuits).
  useSSE(projectId);

  // Fetch the project's title + state when we have a projectId.
  // ArtifactViewerPage requires both as initial props (it self-
  // hydrates state via getV2Project too, but the title is needed
  // for the page header during the loading window).
  const [meta, setMeta] = useState<ProjectMeta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) {
      setMeta(null);
      return;
    }
    let cancelled = false;
    setMeta(null);
    setMetaError(null);
    api
      .getV2Project(projectId)
      .then((res) => {
        if (cancelled) return;
        const project = res.project;
        if (!project) {
          setMetaError("Project not found.");
          return;
        }
        setMeta({
          title: project.title ?? "Untitled project",
          state:
            (project.project_state as ProjectState | undefined) ??
            "pending_review",
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setMetaError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // No projectId — show the empty IDE landing.  Layer 2 replaces
  // this with the repo file-system browser.
  if (!projectId) {
    return (
      <div className="code-route__empty">
        <div className="code-route__empty-card">
          <p className="eyebrow">Code</p>
          <h1 className="display">Pick a project to see its code.</h1>
          <p className="meta">
            Click <strong>Code</strong> on any canvas to land here with
            that project's PR open. A full repo file-system browser
            (main + PR folders) ships next.
          </p>
          <Link to="/workspaces" className="btn btn--primary">
            Go to Workspaces →
          </Link>
        </div>
      </div>
    );
  }

  if (metaError) {
    return (
      <div className="code-route__empty">
        <div className="code-route__empty-card">
          <p className="eyebrow">Code</p>
          <h1 className="display">Couldn&rsquo;t load that project.</h1>
          <p className="meta">{metaError}</p>
          <Link to="/workspaces" className="btn btn--primary">
            Back to Workspaces
          </Link>
        </div>
      </div>
    );
  }

  if (!meta) {
    // Title fetch in flight — render ArtifactViewerPage anyway with
    // a placeholder title; the chip-ready gate inside the page hides
    // the approval pill until the real state lands.
    return (
      <ArtifactViewerPage
        projectId={projectId}
        projectTitle="Loading…"
        initialState="pending_review"
        onBack={() => navigate("/workspaces")}
      />
    );
  }

  return (
    <ArtifactViewerPage
      projectId={projectId}
      projectTitle={meta.title}
      initialState={meta.state}
      onBack={() => navigate(-1)}
    />
  );
}
