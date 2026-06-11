/**
 * LlmModesPanel — full-viewport host for the Summary planner view.
 *
 * Single-tab modal: only the Summary view is rendered. Earlier this
 * was a four-tabbed panel (Summary / Outline / Duplicates / Timeline),
 * but we collapsed those views over the 2026-04-26 cleanup:
 *
 *  - TΛ.1: Outline tab removed (backend route + adapter kept).
 *  - TΛ.2: Timeline tab removed; decisions surface inline in the
 *    topic drawer instead.
 *  - TΛ.3: Duplicates tab removed; replaced by a proactive popup
 *    rendered at the canvas level (DuplicateConflictDialog).
 *  - TΛ.4: Summary export reuses the canvas-level Export dialog.
 *
 * The panel still sits above the canvas at z-index 90; it does NOT
 * cover the topic-detail drawer (which is z-index 100) so a topic
 * the user had open stays reachable. Close via the × in the top bar
 * or Esc.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";

import { t } from "../../i18n";
import { toast } from "../../components/ToastProvider";
import { safeStorage } from "../../lib/safeStorage";
import { formatRelativeTime } from "../../lib/relativeTime";
import {
  api,
  type DocType,
  type DocumentSectionPatchBody,
  type DocumentView as DocumentViewData,
} from "../inspira/api";
import { DocumentView } from "./DocumentView";
import {
  ScaffoldButton,
  ScaffoldProgress,
  ScaffoldResult,
} from "../scaffold";
import {
  type MergeProposal,
  type TopicStub,
} from "./DedupeView";
import {
  SummaryView,
  SummaryViewError,
  SummaryViewLoading,
  isSoftwareDomain,
} from "./SummaryView";
import "./llm-modes.css";

// One-step intro fires the first time a user opens the Planner Views
// panel. Points at the tab row to explain the four views at a glance.

export type LlmModesTab = "summary";

export type LlmModesPanelProps = {
  open: boolean;
  projectId: string;
  /** Project / feedback / issue title rendered as the Summary card
   *  heading. Threaded down to SummaryView's `cardTitle` prop. */
  projectTitle?: string;
  onClose: () => void;
  topicsById: Map<string, TopicStub>;
  /**
   * Background-prefetched data from the parent. When present and keyed to
   * the current project revision, the panel seeds its caches on mount
   * so the user lands on "ready" content instead of a spinner. Missing
   * entries fall through to the panel's own on-click fetch, so prefetch
   * failure is invisible.
   */
  prefetch?: LlmModesPrefetch | null;
  /**
   * Tab to show on first open. Lets InspiraApp deep-link the panel into
   * the Next Steps tab when the user clicks "View" on the completion
   * toast. Default is "summary"; the panel still owns the active-tab
   * state for in-panel switching.
   */
  /**
   * Fired when the Next Steps tab needs to start a fresh generation.
   * InspiraApp owns the poller (so it survives panel close) and the
   * optimistic prefetch update — the panel just signals the start.
   * Errors (cap reached, plan-required, in-flight) are surfaced via
   * toasts by the parent; the panel doesn't need to know about them
   * beyond clearing its local generating-spinner.
   */
  /**
   * #094: resolved doc-type for the active project (null for career
   * / personal / unmapped). Drives the doc-type-aware tab label +
   * the unmapped-domain fallback.
   */
  docType?: DocType | null;
  /**
   * #094: best-effort cap usage for the document feature. Used by
   * DocumentView's cap pill + cap-aware Generate button disable.
   */
  documentCapUsed?: number;
  /**
   * #094: monthly cap (Pro 1, Frontier 100). Founder lock-in:
   * never says "unlimited".
   */
  documentCapLimit?: number;
  /**
   * #094: fired when the user clicks Generate / Regenerate on the
   * document tab. InspiraApp owns the BE POST + poller + optimistic
   * stub. Errors (402 / 422 / 429 / 409) surface via typed toasts.
   *
   * Optional `docTypeOverride` (#094 follow-up): when present, sent
   * on POST so the BE generates as that doc-type instead of the
   * project-domain-derived value. The empty-state picker in
   * DocumentView uses this to let the user correct a misidentified
   * domain. Persistent override is tracked as #097.
   */
  onDocumentGenerate?: (docTypeOverride?: DocType) => Promise<void>;
  /**
   * #094: fired when the user saves an inline section edit. No LLM
   * call; InspiraApp does the optimistic splice + revert-on-4xx.
   */
  onPatchDocumentSection?: (
    sectionId: string,
    body: DocumentSectionPatchBody,
  ) => Promise<void>;
};

/** Shape of the background-prefetched bundle. Every field is optional so
 *  the parent can fill them as they arrive without blocking the panel.
 *
 *  The `*Pending` flags let the panel distinguish "prefetch is still in
 *  flight" (ride it, show loading) from "prefetch never ran / failed"
 *  (fall through to on-click fetch). Without these, opening a tab mid-
 *  prefetch would kick off a duplicate request that races the
 *  background one. */
export type LlmModesPrefetch = {
  /** Stable revision key — when this changes the panel rebinds its
   *  caches. Parent should compose it from the topics list so a topic
   *  add/remove invalidates stale data. */
  revisionKey: string;
  summary?: SummaryData | null;
  summaryPending?: boolean;
  /** TΛ.3: dedupe proposals are still prefetched, but consumed by
   *  InspiraApp's DuplicateConflictDialog (proactive popup) — not by
   *  this panel. The fields stay so InspiraApp can keep its existing
   *  prefetch wiring. */
  dedupe?: MergeProposal[] | null;
  dedupePending?: boolean;
  /** #094: latest Document state for the (project, doc_type) pair.
   *  Null before the prefetch warm-up has landed or when no doc has
   *  ever been generated for this project's doc_type. */
  document?: DocumentViewData | null;
  /** True while the parent's tab-open warm-up GET is in flight. */
  documentPending?: boolean;
  /** True when a topic / decision change happened since the document
   *  was generated. Renders the warm-editorial stale banner. */
  documentStale?: boolean;
};

type SummaryData = {
  summary_markdown: string;
  suggested_title: string;
  domain_framing: string;
};

// Render the summary as a single markdown document that mirrors what
// was on screen. We add a heading from suggested_title when present.
function summaryToMarkdown(d: SummaryData): string {
  const title = (d.suggested_title || "").trim();
  const body = (d.summary_markdown || "").trim();
  if (title) return `# ${title}\n\n${body}\n`;
  return `${body}\n`;
}

// Best-effort clipboard copy. Tries the modern async API first;
// falls back to the legacy synchronous execCommand path when it
// fails (which happens when the tab is unfocused — Safari and Chrome
// both reject navigator.clipboard.writeText with a security error
// in that case). The legacy path uses a temp textarea + selection
// + document.execCommand('copy'), which DOES work in unfocused
// contexts. Returns true if either path succeeded.
//
// T3.7: previously this only tried the async API and failed silently
// on unfocused tabs, leaving the user with a "Copy failed" toast and
// no copy in the clipboard. The legacy fallback rescues that case.
async function copyToClipboard(text: string): Promise<boolean> {
  // 1. Modern path: navigator.clipboard.writeText.
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to legacy path */
  }
  // 2. Legacy path: tempTextarea + execCommand('copy'). Works even
  //    when the tab is unfocused. Off-screen positioning + readOnly
  //    so it doesn't briefly flash on the page.
  if (typeof document === "undefined") return false;
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  try {
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    return ok;
  } catch {
    return false;
  } finally {
    document.body.removeChild(ta);
  }
}

export function LlmModesPanel(props: LlmModesPanelProps): ReactElement | null {
  const {
    open,
    projectId,
    projectTitle,
    onClose,
    topicsById,
    prefetch,
    docType,
    documentCapUsed,
    documentCapLimit,
    onDocumentGenerate,
    onPatchDocumentSection,
  } = props;
  void topicsById;  // referenced by future tabs; kept for now to avoid prop churn

  // ---- Session cache --------------------------------------
  // Caches live on refs so component re-mounts don't lose state.
  // `Version` state is bumped alongside ref writes so dependent renders
  // re-read the current cache.
  const summaryRef = useRef<SummaryData | null>(null);
  const [, setCacheVersion] = useState(0);
  const bumpCache = useCallback(() => setCacheVersion((v) => v + 1), []);

  // Loading / error — simple derived state.
  const [summaryState, setSummaryState] = useState<
    "idle" | "loading" | "error"
  >("idle");
  const [summaryError, setSummaryError] = useState<string>("");

  // ---- Next Steps cache + state (#089) -----------------------------
  // Mirrors the summary pattern: a ref for the latest artifact, two
  // setState slots for loading/error, and prefetch seeding effects
  // alongside the summary ones.

  // ---- Fetchers -----------------------------------------------------
  const fetchSummary = useCallback(async (): Promise<void> => {
    setSummaryState("loading");
    setSummaryError("");
    try {
      const res = await api.projectSummary(projectId);
      summaryRef.current = {
        summary_markdown: res.summary.summary_markdown,
        suggested_title: res.summary.suggested_title,
        domain_framing: res.summary.domain_framing,
      };
      setSummaryState("idle");
      bumpCache();
    } catch (err) {
      console.error("[Inspira] project summary fetch failed", err);
      setSummaryError(t("errors.summary_failed"));
      setSummaryState("error");
    }
  }, [projectId, bumpCache]);


  // ---- Prefetch seeding ---------------------------------------------
  // When the parent supplies a `prefetch` bundle (warmed up after the
  // canvas mounted), splice the results into our refs so the user lands
  // on ready content instead of a spinner. The revision key guards
  // against stale data: if topics change we drop the cache and let the
  // on-click fetch run as usual.
  //
  // Handled fields are those that actually arrived — missing entries
  // (e.g. prefetch still in flight, or it errored silently) fall through
  // to the existing lazy-fetch path so we never block the user.
  const prefetchRevisionRef = useRef<string | null>(null);
  useEffect(() => {
    if (!prefetch) return;
    // Revision change: drop any cached refs so the new revision's data
    // seeds cleanly.
    if (prefetchRevisionRef.current !== prefetch.revisionKey) {
      prefetchRevisionRef.current = prefetch.revisionKey;
      summaryRef.current = null;
    }
    if (prefetch.summary && summaryRef.current === null) {
      summaryRef.current = prefetch.summary;
      if (summaryState === "loading") setSummaryState("idle");
      bumpCache();
    }
  }, [prefetch, bumpCache, summaryState]);

  // Mirror parent-prefetch pending flag into local loading state so the
  // "writing a summary…" copy paints while the background fetch is in
  // flight, without the panel firing its own duplicate request.
  useEffect(() => {
    if (
      prefetch?.summaryPending &&
      summaryRef.current === null &&
      summaryState === "idle"
    ) {
      setSummaryState("loading");
    }
  }, [prefetch?.summaryPending, summaryState]);



  // ---- On panel mount: trigger summary fetch -------------------------
  // Single tab; trigger fetch when the panel opens unless already cached
  // or pre-fetched.
  useEffect(() => {
    if (!open) return;
    if (
      summaryRef.current === null &&
      summaryState === "idle" &&
      !prefetch?.summaryPending
    ) {
      void fetchSummary();
    }
  }, [open, summaryState, fetchSummary, prefetch?.summaryPending]);

  // ---- Scaffold (paid-tier) state ------------------------------------
  //
  // Orthogonal to the summary/outline/dedupe caches above. Only
  // rendered when the summary's domain_framing smells software-y.
  // Credits are fetched lazily — first time the summary tab shows the
  // software signal we hit /api/v2/credits. Balance is refreshed
  // after a successful generate so the regenerate button reflects the
  // debited balance.
  const [scaffoldRunning, setScaffoldRunning] = useState(false);
  const [scaffoldResult, setScaffoldResult] = useState<{
    scaffold_id: string;
    framework: string;
    language: string;
    file_count: number;
    readme_preview: string;
    post_install_steps: string[];
    truncation_note: string;
    files: Array<{ path: string; size: number }>;
  } | null>(null);
  // PR 2: scaffold gating switched from credit-balance to plan-tier
  // entitlements. ``canRunScaffold`` is true iff the user's plan
  // unlocks the "scaffold" feature (Pro / Team).
  const [canRunScaffold, setCanRunScaffold] = useState<boolean>(false);
  const entitlementsFetchedRef = useRef<boolean>(false);

  const ensureEntitlementsLoaded = useCallback(async (): Promise<void> => {
    if (entitlementsFetchedRef.current) return;
    entitlementsFetchedRef.current = true;
    try {
      const res = await api.getEntitlements();
      setCanRunScaffold(res.features.includes("scaffold"));
    } catch {
      // Entitlements fetch failure: button stays disabled until retry.
      entitlementsFetchedRef.current = false;
    }
  }, []);

  const runScaffoldGenerate = useCallback(async (): Promise<void> => {
    if (scaffoldRunning) return;
    setScaffoldRunning(true);
    try {
      const res = await api.generateScaffold(projectId);
      setScaffoldResult(res.scaffold);
      toast.success(t("llm_panel.scaffold_ready"));
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err);
      // PR 2: 402 now means "upgrade required" instead of insufficient
      // credits. The toast steers the user toward the pricing page.
      if (raw.includes("402") || raw.includes("upgrade_required")) {
        toast.error(t("llm_panel.scaffold_upgrade_required"));
      } else {
        toast.error(t("llm_panel.scaffold_failed"));
      }
    } finally {
      setScaffoldRunning(false);
    }
  }, [projectId, scaffoldRunning]);

  const runScaffoldDownload = useCallback(async (): Promise<void> => {
    if (!scaffoldResult) return;
    try {
      await api.downloadScaffold(scaffoldResult.scaffold_id);
    } catch (err) {
      console.error("[Inspira] scaffold download failed", err);
      toast.error(t("toast.generic_load_failed"));
    }
  }, [scaffoldResult]);

  // Kick the credits fetch the first time we see a software-framed
  // summary. Runs from an effect rather than inline so the fetch
  // stays off the render path.
  useEffect(() => {
    if (!open) return;
    const s = summaryRef.current;
    if (s && isSoftwareDomain(s.domain_framing)) {
      void ensureEntitlementsLoaded();
    }
  }, [open, ensureEntitlementsLoaded]);

  // ---- Reset caches when project changes -------------
  // Keep caches alive across open→close→open of the SAME session, but
  // drop them if the project itself changes underneath us.
  const prevProjectRef = useRef<string>(projectId);
  useEffect(() => {
    if (prevProjectRef.current !== projectId) {
      summaryRef.current = null;
      setSummaryState("idle");
      setSummaryError("");
      // Scaffold state is per-project too — drop it on switch.
      setScaffoldResult(null);
      setScaffoldRunning(false);
      entitlementsFetchedRef.current = false;
      // Next Steps caches are per-project too.
      // Prefetch revision is per-project; let the next prefetch seed
      // the fresh caches.
      prefetchRevisionRef.current = null;
      prevProjectRef.current = projectId;
      bumpCache();
    }
  }, [projectId, bumpCache]);

  // ---- Esc closes ----------------------------------------------------
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // ---- Body scroll lock while open ----------------------------------
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  // ---- Summary callbacks --------------------------------------------
  const onSummaryRegenerate = useCallback(async (): Promise<void> => {
    summaryRef.current = null;
    await fetchSummary();
  }, [fetchSummary]);

  const onSummaryCopyMarkdown = useCallback((): void => {
    const d = summaryRef.current;
    if (!d) {
      toast.warning(t("llm_panel.no_summary_to_copy"));
      return;
    }
    void (async () => {
      const ok = await copyToClipboard(summaryToMarkdown(d));
      if (ok) toast.success(t("llm_panel.summary_copied"));
      else toast.error(t("llm_panel.copy_failed"));
    })();
  }, []);

  // ---- Summary → Export --------------------------------------------
  // Open the canvas export dialog from the Summary tab. TΛ.4: previously
  // this was a single-click MD export (hardcoded format). Now it opens
  // the same dialog as the canvas-level Export button so the user can
  // pick PDF / Markdown / JSON / CSV. The empty detail (no format)
  // makes InspiraApp's listener fall through to setExportDialogOpen(true).
  const onSummaryExport = useCallback((): void => {
    if (typeof window === "undefined") return;
    window.dispatchEvent(
      new CustomEvent("inspira:export-request", {
        detail: { scope: "summary" },
      }),
    );
  }, []);

  // ---- Next Steps Generate handler (#089) --------------------------
  // Delegates to the parent (InspiraApp) which owns the
  // useNextStepsGenerationPoller hook so the toast + tab badge survive
  // panel close. The panel just shows the in-flight skeleton until
  // the parent's poller updates the prefetch artifact.

  // ---- Render helpers (Next Steps tab) -----------------------------


  // ---- Body renderer ------------------------------------------------
  // Three tabs (Summary | Next steps | Business plan). The active-tab
  // state above drives which renderer fires. Per-tab loading/error
  // states are independent so switching tabs doesn't cancel the other
  // tab's in-flight fetch.
  const renderBody = (): ReactElement => {
    // Founder direction (2026-05-04): only the Summary view renders
    // here now. Next-steps + Document branches kept in code (callers
    // still trigger their fetches via window events) but unreachable
    // through the panel UI — they may resurface as separate panels
    // later.
    if (summaryState === "loading") return <SummaryViewLoading />;
    if (summaryState === "error") {
      return (
        <SummaryViewError
          message={summaryError}
          onRetry={() => void fetchSummary()}
        />
      );
    }
    const d = summaryRef.current;
    if (!d) return <SummaryViewLoading />;
    const softwareFramed = isSoftwareDomain(d.domain_framing);
    // canRunScaffold is set from entitlements above; no balance math.
    // Scaffold slot composes the three sub-surfaces (button, progress,
    // result) so SummaryView can treat it as one opaque ReactNode.
    const scaffoldSlot = softwareFramed ? (
      <>
        {scaffoldResult ? (
          <ScaffoldResult
            scaffold={scaffoldResult}
            canRegen={canRunScaffold && !scaffoldRunning}
            onDownload={runScaffoldDownload}
            onRegenerate={runScaffoldGenerate}
          />
        ) : (
          <>
            <ScaffoldButton
              canRun={canRunScaffold}
              running={scaffoldRunning}
              onClick={runScaffoldGenerate}
            />
            <ScaffoldProgress running={scaffoldRunning} />
          </>
        )}
      </>
    ) : null;

    return (
      <div className="summary-wrap">
        <div className="summary-export-row">
          <button
            type="button"
            className="llm-pill summary-export-cta"
            onClick={onSummaryExport}
          >
            {t("llm_modes.summary.export_cta")}
          </button>
        </div>
        <SummaryView
          summary={d.summary_markdown}
          suggested_title={d.suggested_title}
          domain_framing={d.domain_framing}
          cardTitle={projectTitle}
          onRegenerate={onSummaryRegenerate}
          onClose={onClose}
          onCopyMarkdown={onSummaryCopyMarkdown}
          scaffoldSlot={scaffoldSlot}
        />
      </div>
    );
  };

  // Planner-views coachmark fires the first time the panel opens for
  // a user. Storage key gates it to once-only. Small delay after mount
  // gives the tab row time to paint before we try to spotlight it.

  if (!open) return null;

  return (
    <div
      className="llm-modes-panel"
      role="dialog"
      aria-modal="true"
      aria-label={t("llm_panel.aria")}
    >
      <div className="llm-modes-panel__topbar">
        <h1 className="llm-modes-panel__title">Summary</h1>
        {/* Founder direction (2026-05-04): drop the Next-steps and
            Document tabs. The panel now shows the Summary view only;
            the multi-tab implementation stays as one branch in
            renderBody() but the tab strip is removed from chrome. */}
        <div className="llm-modes-panel__spacer" />
        <button
          type="button"
          className="llm-modes-panel__close"
          onClick={onClose}
          aria-label={t("llm_panel.close_aria")}
        >
          {"\u00D7"}
        </button>
      </div>
      <div
        className="llm-modes-panel__body"
        id="llm-modes-panel-body"
        role="tabpanel"
        aria-labelledby="llm-modes-tab-summary"
      >
        {renderBody()}
      </div>
    </div>
  );
}
