import { ReactElement, useEffect, useMemo, useRef, useState } from "react";

import type { ArtifactFile } from "../api";

/**
 * Per-file 3-dot menu. Hidden by default; the parent <li> reveals it
 * via `.av-nav__file:hover .av-row-menu { opacity: 1 }` (App.css).
 * When the menu is open it stays visible regardless of hover so the
 * user can mouse over to a Rename/Delete entry without the menu
 * collapsing on them.
 */
function FileRowMenu({
  path,
  onRename,
  onDelete,
}: {
  path: string;
  onRename: () => void;
  onDelete: () => void;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const node = wrapRef.current;
      if (!node) return;
      if (e.target instanceof Node && !node.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  return (
    <div
      ref={wrapRef}
      className={"av-row-menu" + (open ? " av-row-menu--open" : "")}
      style={{
        position: "absolute",
        right: 6,
        top: "50%",
        transform: "translateY(-50%)",
        opacity: open ? 1 : undefined,
      }}
    >
      <button
        type="button"
        aria-label={`More actions for ${path}`}
        title="More actions"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        style={{
          background: "transparent",
          border: "none",
          color: "var(--ink-3)",
          cursor: "pointer",
          padding: "2px 4px",
          fontSize: 14,
          lineHeight: 1,
        }}
      >
        ⋯
      </button>
      {open ? (
        <ul
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "100%",
            marginTop: 4,
            // Solid white fallback — `var(--paper)` resolves to
            // alpha-having colors in some themes which made the
            // dropdown see-through.
            background: "var(--paper, #ffffff)",
            backgroundColor: "#ffffff",
            border: "1px solid var(--paper-edge, #e5e0d4)",
            borderRadius: 4,
            listStyle: "none",
            padding: "4px 0",
            margin: 0,
            minWidth: 120,
            boxShadow: "0 4px 16px rgba(0, 0, 0, 0.12)",
            zIndex: 100,
          }}
        >
          <li role="none">
            <button
              type="button"
              role="menuitem"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                onRename();
              }}
              style={{
                width: "100%",
                background: "transparent",
                border: "none",
                padding: "6px 12px",
                textAlign: "left",
                fontSize: 12,
                cursor: "pointer",
                color: "var(--ink-2)",
              }}
            >
              Rename
            </button>
          </li>
          <li role="none">
            <button
              type="button"
              role="menuitem"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                if (
                  window.confirm(
                    `Delete ${path}? This cannot be undone.`,
                  )
                ) {
                  onDelete();
                }
              }}
              style={{
                width: "100%",
                background: "transparent",
                border: "none",
                padding: "6px 12px",
                textAlign: "left",
                fontSize: 12,
                cursor: "pointer",
                color: "var(--rust)",
              }}
            >
              Delete
            </button>
          </li>
        </ul>
      ) : null}
    </div>
  );
}

export type FileEntryStatus = "MOD" | "NEW" | "DIFF_READY" | "THINKING";

export type FileEntry = {
  path: string;
  status?: FileEntryStatus;
  lineCount: number;
};

export type FileTreeProps = {
  files: ArtifactFile[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
  /** Optional per-file status badges (MOD/NEW/etc.). Keyed by path —
   *  paths missing from the map render without a chip. v0 does not
   *  populate this (no diff feature yet) but the chip styles are
   *  hooked up so the diff integration can fill the map later. */
  statusByPath?: Record<string, FileEntryStatus>;
  /** When false, hides the create/rename/delete affordances. Wired
   *  by ArtifactViewerPage from the project's draft state — file
   *  mgmt only allowed in Draft, matching the autosave gate. */
  canManageFiles?: boolean;
  onCreateFile?: (path: string) => Promise<void>;
  onRenameFile?: (oldPath: string, newPath: string) => Promise<void>;
  onDeleteFile?: (path: string) => Promise<void>;
  /** Wave F.4 — file paths → count of unresolved comment threads.
   *  When > 0, the file row renders a small gold dot before the line
   *  count so partners can see at a glance which files carry open
   *  threads. */
  unresolvedCommentCounts?: Map<string, number>;
};

type Entry =
  | { kind: "dir"; name: string; depth: number }
  | { kind: "file"; path: string; name: string; depth: number; lineCount: number };

/**
 * Flat-list visual tree (no expand/collapse — the v0 mockup shows two
 * folders both expanded, and our scaffolds typically have ≤14 files).
 * If/when scaffold size grows past comfortable scroll, swap this for a
 * collapsible variant.
 */
function flattenTree(files: ArtifactFile[]): Entry[] {
  // Group by directory prefix; emit a `dir` row when we cross a new
  // top-level segment, then the files under it, then the next group.
  const sorted = [...files].sort((a, b) => a.path.localeCompare(b.path));
  const out: Entry[] = [];
  const seenDirs = new Set<string>();
  for (const file of sorted) {
    const parts = file.path.split("/");
    const fileName = parts[parts.length - 1];
    const dirSegments = parts.slice(0, -1);
    let depthOffset = 0;
    let prefix = "";
    for (const seg of dirSegments) {
      prefix = prefix ? `${prefix}/${seg}` : seg;
      if (!seenDirs.has(prefix)) {
        seenDirs.add(prefix);
        out.push({ kind: "dir", name: `${seg}/`, depth: depthOffset });
      }
      depthOffset += 1;
    }
    const lineCount = (file.content.match(/\n/g) || []).length + 1;
    out.push({
      kind: "file",
      path: file.path,
      name: fileName,
      depth: dirSegments.length,
      lineCount,
    });
  }
  return out;
}

export function FileTree({
  files,
  selectedPath,
  onSelect,
  statusByPath = {},
  canManageFiles,
  onCreateFile,
  onRenameFile,
  onDeleteFile,
  unresolvedCommentCounts,
}: FileTreeProps): ReactElement {
  const entries = useMemo(() => flattenTree(files), [files]);
  // Per-row hover-actions are revealed via CSS (`.av-nav__file:hover
  // .av-nav__file-actions`); no JS hover state needed. Inline rename
  // mode is tracked here.
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [creatingPath, setCreatingPath] = useState<string | null>(null);

  const submitRename = async () => {
    const old = renaming;
    const next = renameValue.trim();
    setRenaming(null);
    if (!old || !next || next === old || !onRenameFile) return;
    try {
      await onRenameFile(old, next);
    } catch {
      // Caller surfaces an error toast; stay silent here.
    }
  };

  const submitCreate = async () => {
    const next = (creatingPath || "").trim();
    setCreatingPath(null);
    if (!next || !onCreateFile) return;
    try {
      await onCreateFile(next);
    } catch {
      // Caller toast handles user feedback.
    }
  };

  return (
    <nav className="av-nav" aria-label="Artifact files">
      {canManageFiles ? (
        <div
          style={{
            padding: "8px 14px",
            borderBottom: "1px solid var(--paper-edge)",
          }}
        >
          {creatingPath !== null ? (
            <input
              autoFocus
              value={creatingPath}
              placeholder="src/NewFile.tsx"
              onChange={(e) => setCreatingPath(e.target.value)}
              onBlur={submitCreate}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitCreate();
                else if (e.key === "Escape") setCreatingPath(null);
              }}
              style={{
                width: "100%",
                fontFamily: "var(--ff-mono)",
                fontSize: 11,
                padding: "4px 6px",
                border: "1px solid var(--paper-edge)",
                background: "var(--paper)",
                borderRadius: 3,
              }}
            />
          ) : (
            <button
              type="button"
              onClick={() => setCreatingPath("")}
              style={{
                fontSize: 11,
                color: "var(--ink-3)",
                background: "transparent",
                border: "1px dashed var(--paper-edge)",
                padding: "4px 8px",
                borderRadius: 3,
                cursor: "pointer",
                width: "100%",
              }}
            >
              + New file
            </button>
          )}
        </div>
      ) : null}
      <ul className="av-nav__list">
        {entries.map((entry, idx) => {
          if (entry.kind === "dir") {
            return (
              <li
                key={`dir-${idx}-${entry.name}`}
                className="av-nav__dir"
                style={{ paddingLeft: 14 + entry.depth * 14 }}
              >
                <span className="av-nav__chevron" aria-hidden>
                  ▸
                </span>
                {entry.name}
              </li>
            );
          }
          const isActive = selectedPath === entry.path;
          const status = statusByPath[entry.path];
          const isRenaming = renaming === entry.path;
          return (
            <li
              key={entry.path}
              className={
                "av-nav__file" + (isActive ? " av-nav__file--active" : "")
              }
              style={{
                paddingLeft: 14 + entry.depth * 14,
                position: "relative",
              }}
            >
              {isRenaming ? (
                <input
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={submitRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submitRename();
                    else if (e.key === "Escape") setRenaming(null);
                  }}
                  style={{
                    width: "calc(100% - 14px)",
                    fontFamily: "var(--ff-mono)",
                    fontSize: 11,
                    padding: "2px 4px",
                    border: "1px solid var(--paper-edge)",
                    background: "var(--paper)",
                    borderRadius: 2,
                  }}
                />
              ) : (
                <button
                  type="button"
                  className="av-nav__file-btn"
                  onClick={() => onSelect(entry.path)}
                  title={entry.path}
                >
                  <span className="av-nav__file-name">{entry.name}</span>
                  {status ? (
                    <span
                      className={`av-chip av-chip--${status.toLowerCase()}`}
                      aria-label={`status: ${status}`}
                    >
                      {status === "DIFF_READY"
                        ? "Diff ready"
                        : status === "THINKING"
                          ? "Thinking…"
                          : status}
                    </span>
                  ) : null}
                  {(unresolvedCommentCounts?.get(entry.path) ?? 0) > 0 ? (
                    <span
                      className="av-nav__comment-dot"
                      aria-label={`${unresolvedCommentCounts?.get(entry.path)} unresolved comment thread${
                        (unresolvedCommentCounts?.get(entry.path) ?? 0) === 1
                          ? ""
                          : "s"
                      }`}
                    />
                  ) : null}
                  <span className="av-nav__lines" aria-hidden>
                    {entry.lineCount}L
                  </span>
                </button>
              )}
              {canManageFiles && !isRenaming ? (
                <FileRowMenu
                  path={entry.path}
                  onRename={() => {
                    setRenameValue(entry.path);
                    setRenaming(entry.path);
                  }}
                  onDelete={() => onDeleteFile?.(entry.path)}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
