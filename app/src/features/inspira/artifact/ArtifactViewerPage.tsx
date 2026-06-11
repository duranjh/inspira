import {
  ReactElement,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";

import { toast } from "../../../components/ToastProvider";
import { t } from "../../../i18n";
import { HttpError } from "../../../lib/httpClient";
import { getActiveWorkspaceId } from "../../workspaces/WorkspaceContext";
import { api, type ProjectState } from "../api";
import {
  ApprovalChip,
  projectStateToUx,
} from "./ApprovalChip";
import { ArtifactTopBar, type KebabAction } from "./ArtifactTopBar";
import { ChatSidebar } from "./ChatSidebar";
import { DualFolderExplorer } from "./DualFolderExplorer";
import { FileTree } from "./FileTree";
import { type PrFolderSelection } from "./PrFolderExplorer";
import { type RepoFileExplorerSelection } from "./RepoFileExplorer";
import { RefreshReviewModal } from "./RefreshReviewModal";
import { StaleEditConfirmModal } from "./StaleEditConfirmModal";
import "./artifact.css";
import { useArtifact } from "./useArtifact";
import { useArtifactComments } from "./useArtifactComments";
import { useRefreshPr } from "./useRefreshPr";
import { useSoftEditBlock } from "./useSoftEditBlock";
import { useStaleness } from "./useStaleness";

type LeftRailTab = "scaffold" | "repo";

type PrVerification = Awaited<ReturnType<typeof api.getPrVerification>>;

// Lazy-load the editor (and its react-syntax-highlighter dep) so the
// shared bundle stays small. Partners only pay for it when they
// actually open the artifact viewer.
const CodeEditor = lazy(() =>
  import("./CodeEditor").then((m) => ({ default: m.CodeEditor })),
);

export type ArtifactViewerPageProps = {
  projectId: string;
  projectTitle: string;
  /** Project state at phase entry. Drives the ApprovalChip's initial
   *  render and whether the StackBlitz embed is read-only. The
   *  ApprovalChip is the canonical state surface — the legacy
   *  `approvedAtIso` "✓ Approved · {age}" badge was redundant +
   *  misleading (read project.updated_at regardless of project_state)
   *  and was removed. */
  initialState: ProjectState;
  onBack: () => void;
};

export function ArtifactViewerPage({
  projectId,
  projectTitle,
  initialState,
  onBack,
}: ArtifactViewerPageProps): ReactElement {
  const navigate = useNavigate();
  const [projectState, setProjectState] = useState<ProjectState>(initialState);
  // Hide the ApprovalChip until we've hydrated the *true* project_state
  // from the backend. `initialState` reads from InspiraApp's in-memory
  // `projects` array which can be empty/stale when partners click Code
  // on a card the list hasn't refetched yet. Without the gate the chip
  // flashes "DRAFT · Request review" for an in_review project (the
  // pending_review fallback maps to "draft" in projectStateToUx) for
  // 1-2s before the fetch resolves, looking broken.
  const [chipReady, setChipReady] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api
      .getV2Project(projectId)
      .then((res) => {
        if (cancelled) return;
        const fetched = res.project?.project_state as ProjectState | undefined;
        if (fetched && fetched !== projectState) {
          setProjectState(fetched);
        }
      })
      .catch(() => {
        // Non-fatal — fall back to the prop-supplied initialState.
      })
      .finally(() => {
        if (!cancelled) setChipReady(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);
  const ux = projectStateToUx(projectState);
  const codeReadOnly = ux !== "draft";

  // PR-verification polling state. Kicks off when Send-to-GitHub
  // succeeds + reads the project's pr metadata; polls every 8s
  // until the status is terminal (passed / failed /
  // no_ci_configured) or until 5 min elapses.
  const [prVerification, setPrVerification] = useState<PrVerification | null>(
    null,
  );
  const verifyTimerRef = useRef<number | null>(null);
  const verifyDeadlineRef = useRef<number>(0);

  const stopVerifyPoll = useCallback(() => {
    if (verifyTimerRef.current !== null) {
      window.clearInterval(verifyTimerRef.current);
      verifyTimerRef.current = null;
    }
  }, []);

  const startVerifyPoll = useCallback(() => {
    stopVerifyPoll();
    verifyDeadlineRef.current = Date.now() + 5 * 60 * 1000;
    const tick = async () => {
      try {
        const result = await api.getPrVerification(projectId);
        setPrVerification(result);
        const terminal =
          result.status === "passed" ||
          result.status === "failed" ||
          result.status === "no_ci_configured" ||
          result.status === "pr_not_open";
        if (terminal || Date.now() >= verifyDeadlineRef.current) {
          stopVerifyPoll();
        }
      } catch {
        // Network blip — keep polling. Errors surface terminally
        // when the deadline expires.
      }
    };
    void tick();
    verifyTimerRef.current = window.setInterval(() => {
      void tick();
    }, 8000);
  }, [projectId, stopVerifyPoll]);

  useEffect(() => {
    return () => stopVerifyPoll();
  }, [stopVerifyPoll]);
  const {
    state,
    status,
    thinkingLabel,
    thinkingElapsedS,
    selectedPath,
    selectPath,
    generate,
    sendMessage,
    refetch: refetchArtifact,
  } = useArtifact(projectId);
  // Wave F.4 — inline IDE-style comments on the generated scaffold.
  // Mounted at the page level so both <FileTree> (dot indicators)
  // and <CodeEditor> (gutter overlay) share one fetch + state.
  const commentsHook = useArtifactComments(projectId);
  // Wave F.5 — multi-PR staleness signal for this project's overlay.
  // Mounted alongside commentsHook so the badge/banner/edit-gate all
  // share one fetch + auto-refresh interval. Soft-block dispatcher
  // tracks per-file dismissals across the session.
  const { staleness, refresh: refreshStaleness } = useStaleness(projectId);
  const softBlock = useSoftEditBlock();
  // Wave F.6 — "Refresh PR with Inspira" + 3-way diff. Hook owns the
  // refresh state machine: idle → refreshing → ready → resolving →
  // resolved/error. The banner + stale-edit-confirm modal both call
  // startRefresh; the resolve handler clears the staleness cache by
  // calling refreshStaleness() so the banner unmounts immediately.
  const refreshPr = useRefreshPr(projectId);
  // Session-scoped set of scaffold paths the partner has unlocked via
  // "Edit anyway". Once unlocked, the explicit "Edit" toggle no longer
  // renders on that file — the partner can keep editing freely. Resets
  // on page reload (drift signals are transient; re-prompt after a
  // refresh keeps the partner honest).
  const [unlockedStalePaths, setUnlockedStalePaths] = useState<Set<string>>(
    () => new Set(),
  );
  const [viewMode, setViewMode] = useState<"code" | "preview">("code");

  // Wave F.2 — left-rail tab + selected repo file. Repo tab is opt-in:
  // Scaffold stays the default so partners who haven't connected a
  // repo (or aren't browsing one) see the same UX as before. Selecting
  // a repo file is independent of the scaffold's selectedPath so
  // switching tabs preserves both contexts.
  const [activeTab, setActiveTab] = useState<LeftRailTab>("scaffold");
  const [repoSelected, setRepoSelected] =
    useState<RepoFileExplorerSelection | null>(null);
  // Wave F.3 — dual-folder explorer adds a second selection slot for
  // files inside ``PRs/<category>/<slug>/``. The repo-tab editor pane
  // shows whichever was clicked most recently.
  const [prSelected, setPrSelected] =
    useState<PrFolderSelection | null>(null);
  // Module-level getter — returns null when no provider has mounted
  // (e.g. existing ArtifactViewerPage tests, which don't wire the
  // workspace context). Workspace switches unmount this page through
  // the router, so reactivity isn't required here.
  const workspaceId = getActiveWorkspaceId();

  const handleKebab = useCallback(
    (action: KebabAction) => {
      if (action === "regenerate") {
        if (
          window.confirm(
            "Discard the current scaffold and draft a new one from the canvas?",
          )
        ) {
          // force=true so the BE re-runs the LLM. Without this the
          // cached-manifest early-return (#187) would replay the
          // existing scaffold and the user's "regenerate" would be a
          // no-op.
          generate({ force: true });
        }
        return;
      }
      if (action === "copy_all") {
        if (state.kind !== "ready") return;
        const blocks = state.artifact.files
          .map((f) => `\`\`\`${f.path}\n${f.content}\n\`\`\``)
          .join("\n\n");
        void navigator.clipboard.writeText(blocks);
        return;
      }
    },
    [generate, state],
  );

  const sendToLinear = useCallback(() => {
    window.dispatchEvent(
      new CustomEvent("inspira:export-to-linear", { detail: { projectId } }),
    );
  }, [projectId]);
  const sendToGithub = useCallback(async () => {
    // The artifact viewer surfaces only after a scaffold is generated,
    // so "Send to GitHub" here pushes CODE as a PR (branch + commits +
    // PR open). The canvas chrome's same-named button still files an
    // Issue — different pathways for different surfaces.
    toast.info("Pushing scaffold to GitHub…");
    try {
      const result = await api.exportScaffoldAsGithubPr(projectId);
      toast.success(
        `PR opened: #${result.pr_number} on ‘${result.branch_name}’ (${result.files_pushed} files).`,
      );
      try {
        window.open(result.pr_url, "_blank", "noopener,noreferrer");
      } catch {
        // Popup blocked — toast already shows the PR number.
      }
      // Start polling GitHub Actions status now that the PR exists.
      // The banner at the top of av-shell renders the running result.
      startVerifyPoll();
    } catch (err) {
      const code =
        err instanceof HttpError
          ? (err.detail as { code?: string } | null)?.code ?? null
          : null;
      const message =
        code === "scaffold_not_ready"
          ? "Generate the code first — the scaffold is empty."
          : code === "connector_not_configured"
            ? "Connect GitHub on the Connectors page first."
            : code === "destination_not_configured"
              ? "Pick a default repo for GitHub on the Connectors page."
              : code === "github_app_not_configured"
                ? "GitHub App isn't configured on this deploy."
                : code === "upstream_transient"
                  ? "GitHub returned a transient error — your previous PR may already exist. Check your repo, or wait a moment and retry."
                  : code === "upstream_rate_limited"
                    ? "GitHub rate-limited the request — wait a moment and retry."
                    : err instanceof Error
                      ? err.message
                      : "Couldn't push to GitHub.";
      // For Connectors-page errors, surface a one-tap action so the
      // partner doesn't have to hunt for the right page.
      const needsConnectors =
        code === "connector_not_configured" ||
        code === "destination_not_configured";
      toast.error(
        message,
        needsConnectors
          ? {
              actionLabel: "Open Connectors",
              onAction: () => navigate("/connectors"),
            }
          : undefined,
      );
    }
    // Note: previously also dispatched `inspira:export-to-github` to
    // open the Issue-export modal as a side effect. That confused
    // users — the artifact viewer's Push button creates a PR (the
    // scaffold is the deliverable), and the Issue modal is a
    // different surface. Removed the legacy dispatch; the canvas
    // chrome's Send-to-GitHub button still opens the Issue modal
    // via its own dispatch.
  }, [projectId, startVerifyPoll]);

  let body: ReactElement;
  if (state.kind === "loading" || (state.kind === "empty" && status === "thinking")) {
    const baseLabel = thinkingLabel || t("artifact.chat.thinking");
    const elapsedLabel =
      thinkingElapsedS > 0
        ? thinkingElapsedS < 60
          ? ` · ${thinkingElapsedS}s`
          : ` · ${Math.floor(thinkingElapsedS / 60)}m ${
              thinkingElapsedS % 60
            }s`
        : "";
    body = (
      <div className="av-empty">
        <div className="av-empty__pulse" />
        <div className="av-empty__line">
          {baseLabel}
          {elapsedLabel}
        </div>
      </div>
    );
  } else if (state.kind === "error") {
    body = (
      <div className="av-empty">
        <div className="av-empty__line av-empty__line--error">
          {state.message}
        </div>
        <button
          type="button"
          className="av-empty__cta"
          onClick={() => generate({ force: false })}
        >
          Try again
        </button>
      </div>
    );
  } else if (state.kind === "empty") {
    body = (
      <div className="av-empty">
        <div className="av-empty__line">
          No code yet — click below to draft it from the canvas.
        </div>
        <button
          type="button"
          className="av-empty__cta"
          onClick={() => generate({ force: false })}
        >
          Generate Code
        </button>
      </div>
    );
  } else {
    const leftRail = (
      <div className="av-left-rail" data-active-tab={activeTab}>
        <div
          className="av-rail-tabs"
          role="tablist"
          aria-label="Left rail panels"
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "scaffold"}
            tabIndex={activeTab === "scaffold" ? 0 : -1}
            className={
              "av-rail-tabs__btn" +
              (activeTab === "scaffold" ? " av-rail-tabs__btn--active" : "")
            }
            onClick={() => setActiveTab("scaffold")}
          >
            Scaffold
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "repo"}
            tabIndex={activeTab === "repo" ? 0 : -1}
            className={
              "av-rail-tabs__btn" +
              (activeTab === "repo" ? " av-rail-tabs__btn--active" : "")
            }
            onClick={() => setActiveTab("repo")}
          >
            Repo
          </button>
        </div>
        {activeTab === "scaffold" ? (
          <FileTree
            files={state.artifact.files}
            selectedPath={selectedPath}
            onSelect={selectPath}
            canManageFiles={!codeReadOnly}
            onCreateFile={async (path) => {
              try {
                await api.createArtifactFile(projectId, path);
                await refetchArtifact();
                selectPath(path);
                toast.success(`Created ${path}`);
              } catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                toast.error(
                  msg.includes("file_exists")
                    ? `${path} already exists`
                    : `Couldn't create ${path}`,
                );
              }
            }}
            onRenameFile={async (oldPath, newPath) => {
              try {
                await api.renameArtifactFile(projectId, oldPath, newPath);
                await refetchArtifact();
                selectPath(newPath);
                toast.success(`Renamed to ${newPath}`);
              } catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                toast.error(
                  msg.includes("file_exists")
                    ? `${newPath} already exists`
                    : `Couldn't rename ${oldPath}`,
                );
              }
            }}
            onDeleteFile={async (path) => {
              try {
                await api.deleteArtifactFile(projectId, path);
                await refetchArtifact();
                toast.success(`Deleted ${path}`);
              } catch {
                toast.error(`Couldn't delete ${path}`);
              }
            }}
            unresolvedCommentCounts={commentsHook.unresolvedCountByFile}
          />
        ) : workspaceId !== null ? (
          <DualFolderExplorer
            workspaceId={workspaceId}
            currentProjectId={projectId}
            selectedRepoPath={repoSelected?.path ?? null}
            selectedPrProjectId={prSelected?.projectId ?? null}
            selectedPrPath={prSelected?.path ?? null}
            onSelectRepo={(sel) => {
              setRepoSelected(sel);
              setPrSelected(null);
            }}
            onSelectPr={(sel) => {
              setPrSelected(sel);
              setRepoSelected(null);
            }}
            activeStaleness={staleness}
            onRefreshClick={() => {
              void refreshPr.startRefresh();
            }}
            refreshing={refreshPr.state === "refreshing"}
          />
        ) : (
          <div className="av-repo-empty" role="status">
            <p className="av-repo-empty__line">
              Switch to a workspace to browse files.
            </p>
          </div>
        )}
      </div>
    );

    let editorPane: ReactElement;
    if (activeTab === "repo") {
      // PR-folder selection takes precedence — clicking a PR file
      // resets repoSelected to null and vice versa, so at most one is
      // non-null. Effectively this branch evaluates "whichever was
      // clicked most recently."
      const activeSelection: {
        breadcrumb: string;
        path: string;
        content: string | null;
        binary: boolean;
      } | null = prSelected
        ? {
            breadcrumb: `PRs/${prSelected.repoFullName} › ${prSelected.path}`,
            path: prSelected.path,
            content: prSelected.content,
            binary: prSelected.binary,
          }
        : repoSelected
          ? {
              breadcrumb: `${repoSelected.repoFullName} › ${repoSelected.path}`,
              path: repoSelected.path,
              content: repoSelected.content,
              binary: repoSelected.binary,
            }
          : null;
      if (activeSelection === null) {
        editorPane = (
          <section className="av-artifact" aria-label="Repo file viewer">
            <div className="av-artifact__bar">
              <div className="av-artifact__breadcrumb" aria-hidden>
                Select a file from the Repo tab to preview.
              </div>
            </div>
            <div className="av-code">
              <div className="av-code__preview-empty">
                No file selected.
              </div>
            </div>
          </section>
        );
      } else if (activeSelection.binary) {
        editorPane = (
          <section className="av-artifact" aria-label="Repo file viewer">
            <div className="av-artifact__bar">
              <div className="av-artifact__breadcrumb">
                {activeSelection.breadcrumb}
              </div>
            </div>
            <div className="av-code">
              <div className="av-code__preview-empty">
                Binary file (cannot preview).
              </div>
            </div>
          </section>
        );
      } else {
        editorPane = (
          <Suspense
            fallback={<div className="av-code__preview-empty">…</div>}
          >
            <CodeEditor
              files={[
                {
                  path: activeSelection.path,
                  content: activeSelection.content ?? "",
                },
              ]}
              selectedPath={activeSelection.path}
              onSelectTab={() => {}}
              viewMode="code"
              onChangeViewMode={() => {}}
              readOnly
              breadcrumbSlot={<>{activeSelection.breadcrumb}</>}
              hidePreview
            />
          </Suspense>
        );
      }
    } else {
      // Wave F.5 — when the project's overlay is stale AND the partner
      // is in draft state (i.e. editing would otherwise be unlocked)
      // AND this specific file hasn't been unlocked yet this session,
      // hand the editor a ``staleEditGate``. CodeEditor forces
      // read-only + renders the explicit "Edit" toggle in its top bar,
      // and the click flows through ``handleStaleEditRequest`` below
      // to open the StaleEditConfirmModal.
      const shouldGateActiveFile =
        !codeReadOnly
        && staleness !== null
        && !staleness.legacy
        && staleness.is_stale
        && selectedPath !== null
        && !unlockedStalePaths.has(selectedPath);
      editorPane = (
        <Suspense
          fallback={<div className="av-code__preview-empty">…</div>}
        >
          <CodeEditor
            files={state.artifact.files}
            selectedPath={selectedPath}
            onSelectTab={selectPath}
            viewMode={viewMode}
            onChangeViewMode={setViewMode}
            scaffoldId={state.artifact.latest_scaffold_id ?? null}
            framework={state.artifact.framework ?? null}
            readOnly={codeReadOnly}
            projectId={projectId}
            commentsHook={commentsHook}
            staleEditGate={
              shouldGateActiveFile && selectedPath
                ? {
                    onRequestEdit: () => {
                      const decision = softBlock.requestEdit(
                        { projectId, filePath: selectedPath },
                        staleness,
                      );
                      // proceed=true means "not stale / already
                      // dismissed" — flip the unlock so future renders
                      // skip the gate. proceed=false means the modal
                      // opened; the wired confirmEdit handler will
                      // unlock on user confirmation.
                      if (decision.proceed) {
                        setUnlockedStalePaths((prev) => {
                          const next = new Set(prev);
                          next.add(selectedPath);
                          return next;
                        });
                      }
                    },
                  }
                : undefined
            }
          />
        </Suspense>
      );
    }

    body = (
      <div className="av-grid">
        {leftRail}
        {editorPane}
        <ChatSidebar
          messages={state.artifact.messages}
          status={status}
          thinkingLabel={thinkingLabel}
          onSend={sendMessage}
        />
      </div>
    );
  }

  return (
    <div className="av-shell">
      <ArtifactTopBar
        title={projectTitle || t("artifact.title_fallback")}
        onBack={onBack}
        onSendToLinear={sendToLinear}
        onSendToGithub={sendToGithub}
        onKebabAction={handleKebab}
        approvalSlot={
          chipReady ? (
            <ApprovalChip
              projectId={projectId}
              state={projectState}
              onStateChange={setProjectState}
            />
          ) : (
            <div className="av-chip-skeleton" aria-hidden="true" />
          )
        }
      />
      {prVerification ? (
        <PrVerificationBanner
          verification={prVerification}
          onDismiss={() => {
            stopVerifyPoll();
            setPrVerification(null);
          }}
        />
      ) : null}
      {body}
      <StaleEditConfirmModal
        open={softBlock.pendingEditTarget !== null}
        staleness={staleness}
        filePath={softBlock.pendingEditTarget?.filePath ?? null}
        onConfirm={() => {
          const target = softBlock.confirmEdit();
          if (target) {
            setUnlockedStalePaths((prev) => {
              const next = new Set(prev);
              next.add(target.filePath);
              return next;
            });
          }
        }}
        onCancel={() => softBlock.cancelEdit()}
        onRefreshClick={() => {
          // Close the soft-block modal first, then kick off the
          // refresh — the partner picked "redraft" over "edit anyway".
          softBlock.cancelEdit();
          void refreshPr.startRefresh();
        }}
        refreshing={refreshPr.state === "refreshing"}
      />
      <RefreshReviewModal
        open={
          refreshPr.state === "refreshing"
          || refreshPr.state === "ready"
          || refreshPr.state === "resolving"
          || refreshPr.state === "error"
        }
        diff={refreshPr.diff}
        refreshing={refreshPr.state === "refreshing"}
        error={refreshPr.error}
        onSubmit={async (decisions) => {
          const result = await refreshPr.submitResolutions(decisions);
          if (result) {
            // Refetch staleness immediately so the banner unmounts
            // — the BE invalidated its cache; this fetch returns the
            // post-refresh "not stale" payload.
            void refreshStaleness();
            // Reset the hook state so the modal closes cleanly.
            refreshPr.reset();
          }
          return result;
        }}
        onClose={() => {
          refreshPr.reset();
        }}
      />
    </div>
  );
}

interface PrVerificationBannerProps {
  verification: PrVerification;
  onDismiss: () => void;
}

function PrVerificationBanner({
  verification,
  onDismiss,
}: PrVerificationBannerProps): ReactElement | null {
  const { status, summary, pr_url, pr_number, merged } = verification;
  const isTerminal =
    status === "passed" ||
    status === "failed" ||
    status === "no_ci_configured" ||
    status === "pr_not_open";
  const variant = (() => {
    if (status === "passed") return "passed";
    if (status === "failed") return "failed";
    if (status === "pending") return "pending";
    return "neutral";
  })();
  const icon = (() => {
    if (status === "passed") return "✓";
    if (status === "failed") return "✗";
    if (status === "pending") return "↻";
    return "·";
  })();
  return (
    <div
      className={`av-pr-verify av-pr-verify--${variant}`}
      role="status"
      aria-live="polite"
    >
      <span className="av-pr-verify__icon" aria-hidden="true">
        {icon}
      </span>
      <span className="av-pr-verify__text">
        {status === "pending"
          ? "Verifying on GitHub Actions… "
          : merged
            ? "Verified on GitHub. "
            : "PR ready for review. "}
        <span className="av-pr-verify__summary">{summary}</span>
      </span>
      {pr_number && pr_url ? (
        <a
          className="av-pr-verify__link"
          href={pr_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          PR #{pr_number} →
        </a>
      ) : null}
      {isTerminal ? (
        <button
          type="button"
          className="av-pr-verify__dismiss"
          onClick={onDismiss}
          aria-label="Dismiss verification banner"
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
