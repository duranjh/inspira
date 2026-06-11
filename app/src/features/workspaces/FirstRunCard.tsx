// First-run empty-state for users with 0 workspaces.
//
// Surfaces inside AuthedShell when the workspace context has
// resolved with an empty list. Shown on /workspaces, /connectors,
// and any other v4 route that requires a workspace context.
//
// Per the C5 watch points: this is a STUB until B1.4 (the 4-step
// onboarding wizard) ships in W7/F14. The real onboarding is name
// → connect repo → connect feedback → first prioritization run;
// the C5 stub just sends the user through CreateWorkspaceDialog
// (name + slug) and drops them on the workspace home with empty
// connectors. Not great UX but unblocks the demo flow.

import { ReactElement } from "react";

export interface FirstRunCardProps {
  onCreate: () => void;
}

export function FirstRunCard({ onCreate }: FirstRunCardProps): ReactElement {
  return (
    <div className="first-run">
      <div className="card first-run__card">
        <p className="eyebrow first-run__eyebrow">Welcome to Inspira</p>
        <h1 className="display first-run__title">
          Let&apos;s set up your <em>workspace</em>.
        </h1>
        <p className="meta first-run__intro">
          A workspace is where your repo, feedback, and decisions
          live. Create one to start connecting sources and reviewing
          AI-generated plans.
        </p>
        <div className="first-run__actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={onCreate}
          >
            Create your workspace →
          </button>
        </div>
        <p className="first-run__footnote meta">
          The full guided onboarding (connect repo, connect feedback,
          first prioritization run) is coming next. For now you can
          create the workspace and wire connectors yourself.
        </p>
      </div>
    </div>
  );
}
