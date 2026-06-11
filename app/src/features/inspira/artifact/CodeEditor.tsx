import { ReactElement, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import type { EditorView } from "@codemirror/view";

import { ErrorBoundary } from "../../../components/ErrorBoundary";
import { useRovingTablist } from "../../../hooks/useRovingTablist";
import { api } from "../api";
import { CodeMirrorEditor } from "./CodeMirrorEditor";
import { CommentChipGutter } from "./CommentChipGutter";
import { StackBlitzPreview } from "./StackBlitzPreview";
import { type UseArtifactCommentsReturn } from "./useArtifactComments";

import { t } from "../../../i18n";
import type { ArtifactFile } from "../api";

export type CodeEditorViewMode = "code" | "preview";

export type CodeEditorProps = {
  files: ArtifactFile[];
  selectedPath: string | null;
  onSelectTab: (path: string) => void;
  viewMode: CodeEditorViewMode;
  onChangeViewMode: (mode: CodeEditorViewMode) => void;
  /** Optional: scaffold ID and framework slug threaded through to the
   *  StackBlitz embed so the Preview tab can run + edit live. */
  scaffoldId?: string | null;
  framework?: string | null;
  /** When true, the StackBlitz embed renders in preview-only mode
   *  (no editor pane). Wired by ArtifactViewerPage based on the
   *  project's approval state — Draft is editable, In Review and
   *  Approved are read-only. */
  readOnly?: boolean;
  /** Project ID — required for the autosave PATCH path on the
   *  editable Code tab. When absent, edits stay session-local. */
  projectId?: string;
  /** Wave F.2 — when provided, replaces the file-tabs row in the bar
   *  with this slot (e.g. `acme/demo › src/app.tsx` for the Repo tab).
   *  Used by ArtifactViewerPage when rendering a single GitHub repo
   *  file in read-only mode; scaffold mode leaves the tabs intact. */
  breadcrumbSlot?: ReactNode;
  /** Wave F.2 — when true, the Preview button is hidden. StackBlitz
   *  doesn't make sense for a single read-only repo file, so the Repo
   *  tab path hides the toggle. */
  hidePreview?: boolean;
  /** Wave F.4 — shared ``useArtifactComments`` instance from
   *  ArtifactViewerPage. When provided, CodeEditor mounts the
   *  inline comment-chip gutter overlay. When omitted, the overlay
   *  is skipped (e.g. for the standalone CodeEditor test). */
  commentsHook?: UseArtifactCommentsReturn;
  /** Wave F.5 — when set, force the editor to read-only regardless of
   *  ``readOnly`` and render an explicit "Edit" toggle in the top bar.
   *  Used when the active file lives in a stale PR overlay so the
   *  partner sees the friction signal BEFORE typing rather than mid-
   *  keystroke. ``onRequestEdit`` fires when the partner clicks the
   *  toggle; the parent opens the StaleEditConfirmModal and, on
   *  confirm, drops the prop for that file (session-scoped unlock). */
  staleEditGate?: {
    onRequestEdit: () => void;
  };
};

// Monaco's built-in language ids. Anything not in this map renders
// as plain text — Monaco still draws line numbers + handles editing.
const EXTENSION_TO_LANG: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  json: "json",
  css: "css",
  scss: "scss",
  html: "html",
  md: "markdown",
  markdown: "markdown",
  sh: "shell",
  bash: "shell",
  yml: "yaml",
  yaml: "yaml",
  xml: "xml",
};

function detectLanguage(path: string): string {
  const lower = path.toLowerCase();
  const dotIdx = lower.lastIndexOf(".");
  if (dotIdx === -1) return "plaintext";
  const ext = lower.slice(dotIdx + 1);
  return EXTENSION_TO_LANG[ext] ?? "plaintext";
}

export function CodeEditor({
  files,
  selectedPath,
  onSelectTab,
  viewMode,
  onChangeViewMode,
  scaffoldId,
  framework,
  readOnly,
  projectId,
  breadcrumbSlot,
  hidePreview,
  commentsHook,
  staleEditGate,
}: CodeEditorProps): ReactElement {
  // Wave F.5 — when the parent passes a staleEditGate, force readOnly
  // regardless of the caller's intent. The gate's "Edit" toggle is the
  // only path to mutable editing for stale files; clearing the gate
  // is the parent's responsibility after the StaleEditConfirmModal's
  // "Edit anyway" confirms.
  const effectiveReadOnly = readOnly || staleEditGate !== undefined;
  const [editorView, setEditorView] = useState<EditorView | null>(null);
  const [viewTick, setViewTick] = useState<number>(0);
  const activeFile = useMemo(
    () => files.find((f) => f.path === selectedPath) ?? files[0] ?? null,
    [files, selectedPath],
  );
  // Local edit buffer — keyed by file path. Persists across tab
  // switches inside one session and feeds the autosave PATCH below.
  const [edited, setEdited] = useState<Record<string, string>>({});
  const displayContent = activeFile
    ? edited[activeFile.path] ?? activeFile.content ?? ""
    : "";

  // Autosave: debounce-PATCH to /artifact/files on each keystroke.
  // Per-path so switching files doesn't cancel a pending save on the
  // file the user just left. Status surfaced as "Saving… / Saved /
  // Save failed" near the mode toggle. Read-only mode skips the
  // effect entirely — the backend would 409 anyway on locked
  // projects (see api.py v2_artifact_patch_file).
  type SaveStatus = "idle" | "saving" | "saved" | "error";
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const saveTimers = useRef<Record<string, number>>({});
  useEffect(() => {
    if (effectiveReadOnly) return;
    if (!projectId) return;
    if (!activeFile) return;
    const buf = edited[activeFile.path];
    if (buf === undefined) return; // No edit yet for this file
    if (buf === activeFile.content) return; // No change vs server

    const path = activeFile.path;
    const content = buf;
    // Cancel any prior pending timer for this path.
    if (saveTimers.current[path]) {
      window.clearTimeout(saveTimers.current[path]);
    }
    saveTimers.current[path] = window.setTimeout(() => {
      setSaveStatus("saving");
      api
        .patchArtifactFile(projectId, path, content)
        .then(() => setSaveStatus("saved"))
        .catch(() => setSaveStatus("error"));
    }, 600);
    return () => {
      if (saveTimers.current[path]) {
        window.clearTimeout(saveTimers.current[path]);
      }
    };
  }, [
    edited,
    activeFile,
    projectId,
    effectiveReadOnly,
  ]);
  const saveLabel =
    saveStatus === "saving"
      ? "Saving…"
      : saveStatus === "saved"
        ? "Saved"
        : saveStatus === "error"
          ? "Save failed — retrying"
          : "";

  // Roving-tabindex + arrow-key wiring for both tablists (closes #133).
  const fileIds = useMemo(() => files.map((f) => f.path), [files]);
  const fileTablist = useRovingTablist<string>({
    ids: fileIds,
    activeId: activeFile?.path ?? null,
    onSelect: onSelectTab,
  });
  const modeIds = useMemo<readonly CodeEditorViewMode[]>(
    () => ["code", "preview"],
    [],
  );
  const modeTablist = useRovingTablist<CodeEditorViewMode>({
    ids: modeIds,
    activeId: viewMode,
    onSelect: onChangeViewMode,
  });

  return (
    <section className="av-artifact" aria-label="Artifact code">
      <div className="av-artifact__bar">
        {breadcrumbSlot ? (
          // Wave F.2: when ArtifactViewerPage renders a single repo
          // file (Repo tab → CodeEditor), the breadcrumb identifies
          // the source repo + path. Replaces the per-file tab strip
          // because there's only ever one open file in that path.
          <div className="av-artifact__breadcrumb" aria-label="Repo file path">
            {breadcrumbSlot}
          </div>
        ) : (
          <div className="av-artifact__tabs" role="tablist" aria-label="Open files">
            {files.map((f) => {
              const isActive = activeFile?.path === f.path;
              return (
                <button
                  key={f.path}
                  ref={fileTablist.registerRef(f.path)}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  tabIndex={fileTablist.tabIndex(f.path)}
                  className={
                    "av-artifact__tab" +
                    (isActive ? " av-artifact__tab--active" : "")
                  }
                  onClick={() => onSelectTab(f.path)}
                  onKeyDown={fileTablist.onKeyDown(f.path)}
                >
                  {f.path.split("/").pop()}
                </button>
              );
            })}
          </div>
        )}
        {saveLabel ? (
          <span
            className="av-save-status"
            aria-live="polite"
            style={{
              fontSize: 11,
              fontStyle: "italic",
              color:
                saveStatus === "error"
                  ? "var(--rust)"
                  : saveStatus === "saved"
                    ? "var(--sage)"
                    : "var(--ink-3)",
              marginRight: 12,
              whiteSpace: "nowrap",
            }}
          >
            {saveLabel}
          </span>
        ) : null}
        {staleEditGate ? (
          <button
            type="button"
            className="av-artifact__edit-stale"
            onClick={staleEditGate.onRequestEdit}
            title="Stale — review before editing"
          >
            <span className="av-artifact__edit-stale-label">Edit</span>
            <span className="av-artifact__edit-stale-hint">
              Stale — review before editing
            </span>
          </button>
        ) : null}
        {hidePreview ? null : (
          <div className="av-mode" role="tablist" aria-label="View mode">
            <button
              ref={modeTablist.registerRef("code")}
              type="button"
              role="tab"
              aria-selected={viewMode === "code"}
              tabIndex={modeTablist.tabIndex("code")}
              className={
                "av-mode__btn" +
                (viewMode === "code" ? " av-mode__btn--active" : "")
              }
              onClick={() => onChangeViewMode("code")}
              onKeyDown={modeTablist.onKeyDown("code")}
            >
              {t("artifact.tabs.code")}
            </button>
            <button
              ref={modeTablist.registerRef("preview")}
              type="button"
              role="tab"
              aria-selected={viewMode === "preview"}
              tabIndex={modeTablist.tabIndex("preview")}
              className={
                "av-mode__btn" +
                (viewMode === "preview" ? " av-mode__btn--active" : "")
              }
              onClick={() => onChangeViewMode("preview")}
              onKeyDown={modeTablist.onKeyDown("preview")}
            >
              {t("artifact.tabs.preview")}
            </button>
          </div>
        )}
      </div>
      <div className="av-code">
        {/* Inner ErrorBoundary keyed on viewMode: a render crash on
            either side (StackBlitz iframe teardown leaving stale
            postMessage handlers, or SyntaxHighlighter choking on
            unusual content) shows a local fallback instead of
            taking down the whole InspiraApp tree. The resetKey
            clears the boundary on every view-mode swap so the next
            tab click gets a fresh attempt. */}
        <ErrorBoundary
          resetKey={viewMode}
          fallback={({ reset }) => (
            <div
              className="av-code__preview-empty"
              style={{ padding: "32px", textAlign: "center" }}
            >
              <p>This view didn't render — try switching tabs.</p>
              <button
                type="button"
                className="av-empty__cta"
                onClick={reset}
                style={{ marginTop: 12 }}
              >
                Retry
              </button>
            </div>
          )}
        >
          {viewMode === "preview" ? (
            files.length > 0 ? (
              <StackBlitzPreview
                files={files}
                framework={framework}
                scaffoldId={scaffoldId}
                readOnly={effectiveReadOnly}
              />
            ) : (
              <div className="av-code__preview-empty">
                {t("artifact.preview.empty")}
              </div>
            )
          ) : activeFile ? (
            // CodeMirror 6 — full IDE-grade editor (multi-cursor,
            // search, syntax highlighting, line numbers, history,
            // bracket matching) at ~200KB instead of Monaco's 5MB.
            // Self-hosted by Vite, no CDN round-trip. readOnly is
            // wired through so In Review / Approved disable typing
            // while keeping all the read affordances.
            <div
              className="av-code__pane av-code__grid av-code__monaco"
              style={{ position: "relative" }}
            >
              <CodeMirrorEditor
                value={displayContent}
                language={detectLanguage(activeFile.path)}
                readOnly={effectiveReadOnly}
                onChange={(value) => {
                  if (effectiveReadOnly) return;
                  setEdited((prev) => ({
                    ...prev,
                    [activeFile.path]: value,
                  }));
                }}
                onViewReady={setEditorView}
                onViewUpdate={() =>
                  setViewTick((t) =>
                    // Wrap to keep the counter bounded; bumping is the
                    // signal, not the magnitude.
                    t >= Number.MAX_SAFE_INTEGER - 1 ? 1 : t + 1,
                  )
                }
              />
              {commentsHook ? (
                <CommentChipGutter
                  view={editorView}
                  viewTick={viewTick}
                  filePath={activeFile.path}
                  comments={commentsHook.comments}
                  loading={commentsHook.loading}
                  createComment={commentsHook.createComment}
                  updateComment={commentsHook.updateComment}
                  fileContent={displayContent}
                />
              ) : null}
            </div>
          ) : (
            <div className="av-code__preview-empty">No file selected.</div>
          )}
        </ErrorBoundary>
      </div>
    </section>
  );
}
