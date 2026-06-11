import {
  ReactElement,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Link } from "react-router-dom";

import {
  api,
  parseRepoBrowseError,
  type RepoFileResponse,
  type RepoTreeEntry,
  type RepoTreeResponse,
} from "../api";

export type RepoFileExplorerSelection = {
  repoFullName: string;
  path: string;
  content: string | null;
  binary: boolean;
  sha: string;
};

export type RepoFileExplorerProps = {
  /** Currently-open file path, if any. Used to highlight the active
   *  row in the tree so the UX matches the Scaffold tab. */
  selectedPath: string | null;
  /** Fired when the user clicks a file and its content has loaded.
   *  ``content`` is ``null`` and ``binary`` is ``true`` for files that
   *  failed strict UTF-8 decode — the parent should render a "cannot
   *  preview" placeholder instead of feeding the bytes to the editor. */
  onSelectFile: (selection: RepoFileExplorerSelection) => void;
};

// ---- Tree model -------------------------------------------------------

type TreeNode =
  | { kind: "file"; path: string; name: string; size?: number }
  | {
      kind: "dir";
      name: string;
      path: string;
      children: TreeNode[];
    };

/**
 * Build a nested directory tree from GitHub's flat recursive listing.
 *
 * Only ``blob`` entries are processed — we derive directory structure
 * from file paths. GitHub's ``tree`` entries describe empty directories
 * which we'd render as empty `<details>` blocks; skipping them keeps
 * the explorer focused on actual files.
 */
function buildTree(entries: RepoTreeEntry[]): TreeNode[] {
  const sorted = entries
    .filter((e) => e.type === "blob" && typeof e.path === "string")
    .sort((a, b) => a.path.localeCompare(b.path));

  const root: TreeNode[] = [];
  // Path → dir-node lookup, scoped per buildTree call.
  const dirByPath = new Map<string, TreeNode & { kind: "dir" }>();

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
    });
  }

  // Sort each level: directories first, then files alpha. We did an
  // initial alpha sort above, but inserting dirs out-of-order can leave
  // the level mixed — re-sort here for visual cleanliness.
  const sortLevel = (nodes: TreeNode[]): void => {
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

function TreeRow({
  node,
  depth,
  selectedPath,
  loadingPath,
  onFileClick,
}: {
  node: TreeNode;
  depth: number;
  selectedPath: string | null;
  loadingPath: string | null;
  onFileClick: (path: string) => void;
}): ReactElement {
  if (node.kind === "dir") {
    return (
      <details
        className="av-repo-tree__dir"
        // Auto-open the top two levels so the partner sees structure
        // immediately; deeper paths stay collapsed to keep the rail
        // scannable on first render.
        open={depth < 2}
      >
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
            <TreeRow
              key={child.kind === "dir" ? `d:${child.path}` : `f:${child.path}`}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              loadingPath={loadingPath}
              onFileClick={onFileClick}
            />
          ))}
        </div>
      </details>
    );
  }
  const isActive = selectedPath === node.path;
  const isLoading = loadingPath === node.path;
  return (
    <button
      type="button"
      className={
        "av-repo-tree__file" +
        (isActive ? " av-repo-tree__file--active" : "")
      }
      style={{ paddingLeft: 22 + depth * 14 }}
      onClick={() => onFileClick(node.path)}
      aria-current={isActive ? "true" : undefined}
    >
      <span className="av-repo-tree__file-name">{node.name}</span>
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

function LoadingSkeleton(): ReactElement {
  return (
    <ul className="av-repo-tree__skeleton" aria-hidden>
      {[0, 1, 2, 3, 4].map((i) => (
        <li key={i} className="av-repo-tree__skeleton-row" />
      ))}
    </ul>
  );
}

function EmptyState({ message }: { message?: string | null }): ReactElement {
  return (
    <div className="av-repo-empty" role="status">
      <p className="av-repo-empty__line">
        {message ?? "Connect a GitHub repo to browse files."}
      </p>
      <Link className="av-repo-empty__cta" to="/connectors">
        Open Connectors
      </Link>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): ReactElement {
  return (
    <div className="av-repo-error" role="alert">
      <p className="av-repo-error__line">{message}</p>
      <button
        type="button"
        className="av-repo-error__retry"
        onClick={onRetry}
      >
        Retry
      </button>
    </div>
  );
}

function TruncatedBanner(): ReactElement {
  return (
    <div className="av-repo-truncated" role="note">
      GitHub returned a truncated tree (repo &gt; 100k entries). Some
      files won&apos;t be visible.
    </div>
  );
}

// ---- Main component ---------------------------------------------------

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; data: RepoTreeResponse }
  | { kind: "empty"; message: string | null }
  | { kind: "error"; message: string };

export function RepoFileExplorer({
  selectedPath,
  onSelectFile,
}: RepoFileExplorerProps): ReactElement {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [loadingPath, setLoadingPath] = useState<string | null>(null);
  const [fetchNonce, setFetchNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    api
      .getRepoTree()
      .then((data) => {
        if (cancelled) return;
        setState({ kind: "ok", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const parsed = parseRepoBrowseError(err);
        if (parsed && parsed.error === "github_not_connected") {
          setState({ kind: "empty", message: parsed.message });
          return;
        }
        const fallback =
          err instanceof Error ? err.message : "Failed to load repo tree.";
        setState({ kind: "error", message: parsed?.message ?? fallback });
      });
    return () => {
      cancelled = true;
    };
  }, [fetchNonce]);

  const tree = useMemo(
    () => (state.kind === "ok" ? buildTree(state.data.tree) : []),
    [state],
  );

  const handleFileClick = useCallback(
    async (path: string) => {
      if (state.kind !== "ok") return;
      const repoFullName = state.data.repo_full_name;
      setLoadingPath(path);
      try {
        const file: RepoFileResponse = await api.getRepoFile(path);
        onSelectFile({
          repoFullName,
          path: file.path,
          content: file.content,
          binary: file.binary,
          sha: file.sha,
        });
      } catch (err) {
        const parsed = parseRepoBrowseError(err);
        // Bubble a one-line console error; the parent can decide
        // whether to toast. We avoid a toast dep here to keep the
        // component self-contained for the vitest harness.
        // eslint-disable-next-line no-console
        console.warn(
          `Repo file fetch failed for ${path}:`,
          parsed?.error ?? (err instanceof Error ? err.message : String(err)),
        );
      } finally {
        setLoadingPath(null);
      }
    },
    [state, onSelectFile],
  );

  let body: ReactNode;
  if (state.kind === "loading") {
    body = <LoadingSkeleton />;
  } else if (state.kind === "empty") {
    body = <EmptyState message={state.message} />;
  } else if (state.kind === "error") {
    body = (
      <ErrorState
        message={state.message}
        onRetry={() => setFetchNonce((n) => n + 1)}
      />
    );
  } else {
    body = (
      <>
        {state.data.truncated ? <TruncatedBanner /> : null}
        <div className="av-repo-tree">
          {tree.map((node) => (
            <TreeRow
              key={node.kind === "dir" ? `d:${node.path}` : `f:${node.path}`}
              node={node}
              depth={0}
              selectedPath={selectedPath}
              loadingPath={loadingPath}
              onFileClick={handleFileClick}
            />
          ))}
        </div>
      </>
    );
  }

  return (
    <nav
      className="av-nav av-nav--repo"
      aria-label="Repo files"
      data-state={state.kind}
    >
      {body}
    </nav>
  );
}
