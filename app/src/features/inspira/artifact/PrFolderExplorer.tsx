import {
  ReactElement,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  api,
  parseRepoBrowseError,
  type PrOverlayFileResponse,
  type PrOverlayStalenessResponse,
  type PrOverlayTreeEntry,
  type PrOverlayTreeResponse,
  type RepoFileResponse,
  type V2Project,
} from "../api";
import { slugifyForFilename } from "../export";
import { StalenessBanner } from "./StalenessBanner";

export type PrFolderSelection = {
  projectId: string;
  /** Synthetic GitHub-style label so the right-pane breadcrumb has
   *  something readable. Falls back to the project title when the
   *  overlay response didn't include a ``repo_full_name``. */
  repoFullName: string;
  path: string;
  content: string | null;
  binary: boolean;
  /** ``source: "base"`` rows come from F.2's /repo/file fallback —
   *  the field is preserved so consumers can render different chrome
   *  for scaffolded vs base files in the right-pane viewer. */
  source: "base" | "scaffold" | "modified";
};

export type PrFolderExplorerProps = {
  workspaceId: string;
  /** Project whose PR folder should auto-expand on mount. Set when the
   *  user enters the Code IDE from a canvas (``projectId`` in scope);
   *  ``undefined`` keeps every project collapsed. */
  autoExpandProjectId?: string | null;
  selectedProjectId: string | null;
  selectedPath: string | null;
  onSelectFile: (selection: PrFolderSelection) => void;
  /** Wave F.5 — staleness payload for the auto-expanded (active)
   *  project's PR overlay. Drives the rust "behind main" pill on the
   *  active folder row, the top-of-body banner, and the per-file
   *  chevrons. The component only renders these affordances for the
   *  ``autoExpandProjectId`` project — other projects in the workspace
   *  don't get a staleness signal in F.5 (one fetch per page render,
   *  not N). Defaults to ``null`` when the parent isn't tracking
   *  staleness yet. */
  activeStaleness?: PrOverlayStalenessResponse | null;
  /** Wave F.6 — wired to ``useRefreshPr.startRefresh`` so the banner's
   *  enabled "Refresh PR with Inspira" CTA can fire. Threaded down to
   *  the active project's folder body, then into ``StalenessBanner``. */
  onRefreshClick?: () => void;
  /** Wave F.6 — true while the refresh is in flight; banner shows
   *  "Refreshing…" and disables the CTA. */
  refreshing?: boolean;
};

// Singular storage value -> plural folder label. Per locked decision
// (plan: Folder labels), the FE owns the singular -> plural mapping so
// the design brief's verbatim plural folder names ("PRs/features/",
// "PRs/bugs/", ...) stay in sync with the stored ``dominant_category``
// values ("feature", "bug", ...). Unknown categories fall through to
// "general" — same fallback the BE applies when ``metadata`` is missing
// or malformed.
const CATEGORY_TO_LABEL: Record<string, string> = {
  bug: "bugs",
  feature: "features",
  complaint: "complaints",
  praise: "praises",
  question: "questions",
  general: "general",
};

const CATEGORY_ORDER = [
  "feature",
  "bug",
  "complaint",
  "question",
  "praise",
  "general",
];

function normalizeCategory(raw: unknown): string {
  if (typeof raw === "string" && raw in CATEGORY_TO_LABEL) {
    return raw;
  }
  return "general";
}

// ---- Tree model (overlay-flavored) ------------------------------------

type OverlayNode =
  | {
      kind: "file";
      path: string;
      name: string;
      size?: number;
      source: "base" | "scaffold" | "modified";
    }
  | {
      kind: "dir";
      path: string;
      name: string;
      children: OverlayNode[];
    };

/**
 * Build a nested directory tree from the overlay's flat blob list.
 *
 * Mirrors F.2's ``buildTree`` shape but preserves the per-entry
 * ``source`` so file rows can render a ``modified`` badge.
 */
function buildOverlayTree(entries: PrOverlayTreeEntry[]): OverlayNode[] {
  const sorted = [...entries]
    .filter((e) => e.type === "blob" && typeof e.path === "string")
    .sort((a, b) => a.path.localeCompare(b.path));

  const root: OverlayNode[] = [];
  const dirByPath = new Map<string, OverlayNode & { kind: "dir" }>();

  for (const entry of sorted) {
    const parts = entry.path.split("/");
    const fileName = parts[parts.length - 1];

    let parentChildren = root;
    let accPath = "";
    for (let i = 0; i < parts.length - 1; i += 1) {
      const segment = parts[i];
      accPath = accPath ? `${accPath}/${segment}` : segment;
      let dir = dirByPath.get(accPath);
      if (!dir) {
        dir = {
          kind: "dir",
          name: segment,
          path: accPath,
          children: [],
        };
        dirByPath.set(accPath, dir);
        parentChildren.push(dir);
      }
      parentChildren = dir.children;
    }
    parentChildren.push({
      kind: "file",
      path: entry.path,
      name: fileName,
      size: entry.size,
      source: entry.source,
    });
  }

  const sortLevel = (nodes: OverlayNode[]): void => {
    nodes.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const node of nodes) {
      if (node.kind === "dir") sortLevel(node.children);
    }
  };
  sortLevel(root);
  return root;
}

// ---- Subviews ---------------------------------------------------------

function OverlayTreeRow({
  node,
  depth,
  selectedPath,
  loadingPath,
  affectedPaths,
  onFileClick,
}: {
  node: OverlayNode;
  depth: number;
  selectedPath: string | null;
  loadingPath: string | null;
  affectedPaths: Set<string>;
  onFileClick: (
    path: string,
    source: "base" | "scaffold" | "modified" | null,
  ) => void;
}): ReactElement {
  if (node.kind === "dir") {
    return (
      <details className="av-repo-tree__dir" open={depth < 2}>
        <summary
          className="av-repo-tree__dir-summary"
          style={{ paddingLeft: 8 + depth * 14 }}
        >
          <span className="av-repo-tree__dir-chevron" aria-hidden>
            ▸
          </span>
          {node.name}
        </summary>
        <div className="av-repo-tree__dir-body">
          {node.children.map((child) => (
            <OverlayTreeRow
              key={child.kind === "dir" ? `d:${child.path}` : `f:${child.path}`}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              loadingPath={loadingPath}
              affectedPaths={affectedPaths}
              onFileClick={onFileClick}
            />
          ))}
        </div>
      </details>
    );
  }
  const isActive = selectedPath === node.path;
  const isLoading = loadingPath === node.path;
  const showModifiedBadge = node.source !== "base";
  const isAffected = affectedPaths.has(node.path);
  return (
    <button
      type="button"
      className={
        "av-repo-tree__file" +
        (isActive ? " av-repo-tree__file--active" : "")
      }
      style={{ paddingLeft: 22 + depth * 14 }}
      onClick={() => onFileClick(node.path, node.source)}
      aria-current={isActive ? "true" : undefined}
      data-source={node.source}
    >
      <span className="av-repo-tree__file-name">{node.name}</span>
      {showModifiedBadge ? (
        <span
          className="av-repo-tree__badge av-repo-tree__badge--modified"
          aria-label="Modified by Inspira"
        >
          modified
        </span>
      ) : null}
      {isAffected ? (
        <span
          className="av-repo-tree__chevron-affected"
          aria-label="Affected by drift on main"
          title="Main has moved and a change touches this file"
        >
          ⌄
        </span>
      ) : null}
      {isLoading ? (
        <span
          className="av-repo-tree__file-spinner"
          aria-label="Loading file"
        >
          ↻
        </span>
      ) : null}
    </button>
  );
}

function ProjectOverlayBody({
  projectId,
  selectedPath,
  loadingPath,
  staleness,
  onRefreshClick,
  refreshing,
  onFileClick,
}: {
  projectId: string;
  selectedPath: string | null;
  loadingPath: string | null;
  /** Wave F.5 — when non-null the body renders ``StalenessBanner`` at
   *  the top + threads ``affected_paths_sample`` down to file rows so
   *  affected files get a chevron indicator. Only the active project
   *  (autoExpandProjectId) receives a non-null value from above. */
  staleness: PrOverlayStalenessResponse | null;
  /** Wave F.6 — wires the banner's "Refresh PR with Inspira" CTA to
   *  the page-level ``useRefreshPr.startRefresh``. Only set for the
   *  active project. */
  onRefreshClick?: () => void;
  refreshing?: boolean;
  onFileClick: (
    path: string,
    source: "base" | "scaffold" | "modified" | null,
  ) => void;
}): ReactElement {
  type LoadState =
    | { kind: "loading" }
    | { kind: "ok"; data: PrOverlayTreeResponse }
    | { kind: "error"; message: string };

  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [fetchNonce, setFetchNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    api
      .getPrOverlayTree(projectId)
      .then((data) => {
        if (cancelled) return;
        setState({ kind: "ok", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const parsed = parseRepoBrowseError(err);
        const fallback =
          err instanceof Error ? err.message : "Failed to load PR overlay.";
        setState({ kind: "error", message: parsed?.message ?? fallback });
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, fetchNonce]);

  const tree = useMemo(
    () => (state.kind === "ok" ? buildOverlayTree(state.data.tree) : []),
    [state],
  );

  const affectedPaths = useMemo<Set<string>>(
    () => new Set(staleness?.affected_paths_sample ?? []),
    [staleness],
  );

  if (state.kind === "loading") {
    return (
      <ul className="av-repo-tree__skeleton" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <li key={i} className="av-repo-tree__skeleton-row" />
        ))}
      </ul>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="av-repo-error" role="alert">
        <p className="av-repo-error__line">{state.message}</p>
        <button
          type="button"
          className="av-repo-error__retry"
          onClick={() => setFetchNonce((n) => n + 1)}
        >
          Retry
        </button>
      </div>
    );
  }
  if (state.data.tree.length === 0) {
    return (
      <p
        className="av-repo-empty__line"
        style={{ padding: "8px 18px" }}
      >
        No files in this PR yet.
      </p>
    );
  }
  return (
    <div className="av-repo-tree">
      {state.data.warnings && state.data.warnings.length > 0 ? (
        <div className="av-repo-truncated" role="note">
          {state.data.warnings.length} case-folded path collision(s) —
          some entries may shadow each other on case-insensitive systems.
        </div>
      ) : null}
      <StalenessBanner
        staleness={staleness}
        onRefreshClick={onRefreshClick}
        refreshing={refreshing}
      />
      {tree.map((node) => (
        <OverlayTreeRow
          key={node.kind === "dir" ? `d:${node.path}` : `f:${node.path}`}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          loadingPath={loadingPath}
          affectedPaths={affectedPaths}
          onFileClick={onFileClick}
        />
      ))}
    </div>
  );
}

function ProjectFolderRow({
  projectId,
  slug,
  title,
  openByDefault,
  selectedPath,
  loadingPath,
  staleness,
  onRefreshClick,
  refreshing,
  onFileClick,
}: {
  projectId: string;
  slug: string;
  title: string;
  openByDefault: boolean;
  selectedPath: string | null;
  loadingPath: string | null;
  /** Wave F.5 — non-null only on the active project; drives the rust
   *  "behind main" pill on this folder's summary AND the body's banner
   *  + chevrons. Pre-F.5 / legacy / non-stale projects get null here
   *  and render normally. */
  staleness: PrOverlayStalenessResponse | null;
  /** Wave F.6 — fires the "Refresh PR with Inspira" CTA inside the
   *  body's banner. Threaded from the page-level useRefreshPr hook. */
  onRefreshClick?: () => void;
  refreshing?: boolean;
  onFileClick: (path: string) => void;
}): ReactElement {
  // Lazy expansion: the overlay tree fetch only fires after the user
  // (or the auto-expand prop) opens this folder. Without the gate, a
  // workspace with N projects would trigger N backend calls on mount,
  // burning the GitHub installation rate budget for tabs the user
  // never opens.
  const [hasOpened, setHasOpened] = useState(openByDefault);
  const showBehindMainBadge = Boolean(
    staleness && !staleness.legacy && staleness.is_stale,
  );
  return (
    <details
      className="av-repo-tree__dir"
      open={openByDefault}
      data-project-id={projectId}
      data-project-slug={slug}
      data-stale={showBehindMainBadge ? "true" : undefined}
      onToggle={(e) => {
        if ((e.target as HTMLDetailsElement).open) {
          setHasOpened(true);
        }
      }}
    >
      <summary
        className="av-repo-tree__dir-summary"
        style={{ paddingLeft: 8 + 28 }}
        title={title}
      >
        <span className="av-repo-tree__dir-chevron" aria-hidden>
          ▸
        </span>
        {slug}/
        {showBehindMainBadge ? (
          <span
            className="av-repo-tree__badge av-repo-tree__badge--behind-main"
            aria-label="Behind main"
            title="Main has moved since this PR was drafted"
          >
            behind main
          </span>
        ) : null}
      </summary>
      <div className="av-repo-tree__dir-body">
        {hasOpened ? (
          <ProjectOverlayBody
            projectId={projectId}
            selectedPath={selectedPath}
            loadingPath={loadingPath}
            staleness={staleness}
            onRefreshClick={onRefreshClick}
            refreshing={refreshing}
            onFileClick={(path) => onFileClick(path)}
          />
        ) : null}
      </div>
    </details>
  );
}

// ---- Main component ---------------------------------------------------

type ProjectsLoadState =
  | { kind: "loading" }
  | { kind: "ok"; projects: V2Project[] }
  | { kind: "error"; message: string };

export function PrFolderExplorer({
  workspaceId,
  autoExpandProjectId,
  selectedProjectId,
  selectedPath,
  onSelectFile,
  activeStaleness = null,
  onRefreshClick,
  refreshing = false,
}: PrFolderExplorerProps): ReactElement {
  const [state, setState] = useState<ProjectsLoadState>({ kind: "loading" });
  const [loadingPath, setLoadingPath] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    api
      .listWorkspaceProjects(workspaceId)
      .then(({ projects }) => {
        if (cancelled) return;
        const eligible = projects.filter((p) => !p.archived_at);
        setState({ kind: "ok", projects: eligible });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const fallback =
          err instanceof Error ? err.message : "Failed to load projects.";
        setState({ kind: "error", message: fallback });
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  // Single shared dispatcher: any file click in any project's overlay
  // routes through here so the base -> /repo/file fallback lives in
  // one place. ``source: "base"`` from the overlay-file route means the
  // path lives in the GitHub repo, not the scaffold; F.2's /repo/file
  // is the authoritative content source for that case.
  const handleFileClick = useCallback(
    async (
      projectId: string,
      repoFullName: string,
      path: string,
    ) => {
      setLoadingPath(`${projectId}:${path}`);
      try {
        const overlayFile: PrOverlayFileResponse =
          await api.getPrOverlayFile(projectId, path);
        if (overlayFile.source === "base") {
          // Fall through to F.2's /repo/file — single source of truth
          // for base content (binary detection, 1 MiB cap, etc.).
          const repoFile: RepoFileResponse = await api.getRepoFile(path);
          onSelectFile({
            projectId,
            repoFullName,
            path: repoFile.path,
            content: repoFile.content,
            binary: repoFile.binary,
            source: "base",
          });
          return;
        }
        onSelectFile({
          projectId,
          repoFullName,
          path: overlayFile.path,
          content: overlayFile.content,
          binary: overlayFile.binary,
          source: overlayFile.source,
        });
      } catch (err) {
        const parsed = parseRepoBrowseError(err);
        // eslint-disable-next-line no-console
        console.warn(
          `PR overlay file fetch failed for ${projectId}:${path}:`,
          parsed?.error ?? (err instanceof Error ? err.message : String(err)),
        );
      } finally {
        setLoadingPath(null);
      }
    },
    [onSelectFile],
  );

  // Group eligible projects by category. Each category renders as its
  // own folder ("PRs/features/", "PRs/bugs/", ...). Unknown categories
  // collapse into "general" to match the BE's fallback semantics.
  const grouped = useMemo(() => {
    if (state.kind !== "ok") return new Map<string, V2Project[]>();
    const byCategory = new Map<string, V2Project[]>();
    for (const project of state.projects) {
      const raw = (project.metadata as Record<string, unknown> | undefined)?.[
        "dominant_category"
      ];
      const category = normalizeCategory(raw);
      const bucket = byCategory.get(category) ?? [];
      bucket.push(project);
      byCategory.set(category, bucket);
    }
    // Sort projects alphabetically within each bucket for stable
    // ordering — reviewers can scan the tree visually without needing
    // to track recency.
    for (const bucket of byCategory.values()) {
      bucket.sort((a, b) => a.title.localeCompare(b.title));
    }
    return byCategory;
  }, [state]);

  if (state.kind === "loading") {
    return (
      <ul className="av-repo-tree__skeleton" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <li key={i} className="av-repo-tree__skeleton-row" />
        ))}
      </ul>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="av-repo-error" role="alert">
        <p className="av-repo-error__line">{state.message}</p>
      </div>
    );
  }
  if (state.projects.length === 0) {
    return (
      <p
        className="av-repo-empty__line"
        style={{ padding: "12px 18px" }}
      >
        No in-flight PRs yet — projects show up here once you kick off
        code-gen from the canvas.
      </p>
    );
  }

  const nonEmptyCategories = CATEGORY_ORDER.filter(
    (c) => (grouped.get(c) ?? []).length > 0,
  );
  let body: ReactNode = nonEmptyCategories.map((category) => {
    const projects = grouped.get(category) ?? [];
    const label = CATEGORY_TO_LABEL[category] ?? category;
    const containsCurrent =
      autoExpandProjectId !== undefined &&
      autoExpandProjectId !== null &&
      projects.some((p) => p.project_id === autoExpandProjectId);
    return (
      <details
        key={category}
        className="av-repo-tree__dir"
        open={containsCurrent}
        data-category={category}
      >
        <summary
          className="av-repo-tree__dir-summary"
          style={{ paddingLeft: 8 + 14 }}
        >
          <span className="av-repo-tree__dir-chevron" aria-hidden>
            ▸
          </span>
          {label}/
        </summary>
        <div className="av-repo-tree__dir-body">
          {projects.map((project) => {
            const slug =
              slugifyForFilename(project.title) || project.project_id;
            const isCurrent =
              autoExpandProjectId === project.project_id;
            return (
              <ProjectFolderRow
                key={project.project_id}
                projectId={project.project_id}
                slug={slug}
                title={project.title}
                openByDefault={isCurrent}
                selectedPath={
                  selectedProjectId === project.project_id
                    ? selectedPath
                    : null
                }
                loadingPath={
                  loadingPath?.startsWith(`${project.project_id}:`)
                    ? loadingPath.slice(project.project_id.length + 1)
                    : null
                }
                staleness={isCurrent ? activeStaleness : null}
                onRefreshClick={isCurrent ? onRefreshClick : undefined}
                refreshing={isCurrent ? refreshing : false}
                onFileClick={(path) => {
                  void handleFileClick(project.project_id, slug, path);
                }}
              />
            );
          })}
        </div>
      </details>
    );
  });

  return (
    <div className="av-repo-tree" data-state={state.kind}>
      {body}
    </div>
  );
}
