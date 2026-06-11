import { ReactElement } from "react";

import type { PrOverlayStalenessResponse } from "../api";
import {
  PrFolderExplorer,
  type PrFolderSelection,
} from "./PrFolderExplorer";
import {
  RepoFileExplorer,
  type RepoFileExplorerSelection,
} from "./RepoFileExplorer";

export type DualFolderSelection =
  | { kind: "repo"; selection: RepoFileExplorerSelection }
  | { kind: "pr"; selection: PrFolderSelection };

export type DualFolderExplorerProps = {
  workspaceId: string;
  /** Optional — when set, the PR root expands by default and the
   *  project's PR folder also expands. Comes from the canvas->Code
   *  flow. ``undefined`` keeps everything collapsed. */
  currentProjectId?: string | null;
  selectedRepoPath: string | null;
  selectedPrProjectId: string | null;
  selectedPrPath: string | null;
  onSelectRepo: (selection: RepoFileExplorerSelection) => void;
  onSelectPr: (selection: PrFolderSelection) => void;
  /** Wave F.5 — staleness payload for ``currentProjectId``. Forwarded
   *  to PrFolderExplorer where it drives the rust "behind main" pill +
   *  banner + per-file chevrons on the active project only. */
  activeStaleness?: PrOverlayStalenessResponse | null;
  /** Wave F.6 — forwarded into the PrFolderExplorer's StalenessBanner.
   *  Fires the "Refresh PR with Inspira" CTA. */
  onRefreshClick?: () => void;
  refreshing?: boolean;
};

/**
 * Two-root file tree used by the artifact-viewer "Repo" tab (Wave F.3).
 *
 * - ``main/`` is the F.2 repo browser, slotted in unchanged so the
 *   existing test suite acts as the regression guard.
 * - ``PRs/`` is the new categorized list of in-flight project overlays.
 *
 * Both roots render as ``<details>`` elements so the user can collapse
 * either side independently — partners can hide ``PRs/`` to focus on
 * the base repo, or hide ``main/`` to scan their open PRs.
 */
export function DualFolderExplorer({
  workspaceId,
  currentProjectId,
  selectedRepoPath,
  selectedPrProjectId,
  selectedPrPath,
  onSelectRepo,
  onSelectPr,
  activeStaleness = null,
  onRefreshClick,
  refreshing = false,
}: DualFolderExplorerProps): ReactElement {
  const prsOpen = currentProjectId != null;
  return (
    <div className="av-repo-tree" data-variant="dual">
      <details className="av-repo-tree__dir" open>
        <summary
          className="av-repo-tree__dir-summary"
          style={{ paddingLeft: 8 }}
        >
          <span className="av-repo-tree__dir-chevron" aria-hidden>
            ▸
          </span>
          main/
        </summary>
        <div className="av-repo-tree__dir-body">
          <RepoFileExplorer
            selectedPath={selectedRepoPath}
            onSelectFile={onSelectRepo}
          />
        </div>
      </details>

      <details className="av-repo-tree__dir" open={prsOpen}>
        <summary
          className="av-repo-tree__dir-summary"
          style={{ paddingLeft: 8 }}
        >
          <span className="av-repo-tree__dir-chevron" aria-hidden>
            ▸
          </span>
          PRs/
        </summary>
        <div className="av-repo-tree__dir-body">
          <PrFolderExplorer
            workspaceId={workspaceId}
            autoExpandProjectId={currentProjectId ?? null}
            selectedProjectId={selectedPrProjectId}
            selectedPath={selectedPrPath}
            onSelectFile={onSelectPr}
            activeStaleness={activeStaleness}
            onRefreshClick={onRefreshClick}
            refreshing={refreshing}
          />
        </div>
      </details>
    </div>
  );
}
