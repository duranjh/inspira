// Top-level Inspira flow.
//
// Flow:
// 1. Mount: fetch the current user's projects. If any exist, land on the
//    `projects_list` phase so the user sees an overview grid. If none
//    exist, show the kickoff form so they can seed a new one.
// 2. From the projects list, clicking a project opens its canvas.
// 3. Kickoff: creates a NEW v2 project (POST /api/v2/projects), then runs
//    the planner's kickoff against it. The canvas opens on that project.
// 4. Canvas: the active project's topics + relationships + decisions.
//    The top bar carries a project switcher (list all user projects, pick
//    another, rename, delete, or start a fresh one), a "Projects" button
//    back to the grid, and a user menu with Account settings + Log out.
//
// Also mounted at app root:
//   - OfflineBanner (self-gating via useOnlineStatus)
//   - CommandPalette (Cmd/Ctrl+K)
//   - SearchOverlay (`/` on canvas, or via palette)
//   - SessionExpiredModal (fired by a global `inspira:unauthorized` event)
//   - Dialogs for rename / delete / share / export
//   - ShortcutHelpOverlay
//   - AccountSettingsPage (full-viewport, z-90)

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { t, formatDate } from "../../i18n";

// AnonymousSaveBanner removed from render after reverting to signup-before-
// canvas gate; keeping the file in the repo (it's imported nowhere now) in
// case we bring anon-canvas back as a paid feature.
import { DecisionSummaryDrawer } from "./DecisionSummaryDrawer";
import { ArtifactViewerPage } from "./artifact/ArtifactViewerPage";
import { DecisionSummaryShowChip } from "./DecisionSummaryShowChip";
import { ExampleBanner } from "./ExampleBanner";
import { OrchestratorChip } from "./chrome/OrchestratorChip";
import { KickoffForm } from "./KickoffForm";
import { ProjectCanvas } from "./ProjectCanvas";
import { TopicDetail } from "./TopicDetail";
import { useDecisionSummary } from "./useDecisionSummary";
import {
  api,
  DocumentCapReachedError,
  DocumentDomainNotMappedError,
  DocumentInFlightError,
  DocumentInvalidDocTypeError,
  DocumentPlanRequiredError,
  ProjectNotFoundError,
  type AttachedSource,
  type AuthedUser,
  type Decision,
  type DocType,
  type DocumentSection,
  type DocumentSectionPatchBody,
  type DocumentView as DocumentViewData,
  type KickoffEnvelope,
  type ProjectState,
  type QnaTurn,
  type Shelf,
  type Topic,
  type TopicDeletionSuggestion,
  type V2Project,
} from "./api";
import { docTypeForDomain, documentCapForPlan } from "./docTypeMap";
import { applyLayout, computeTopicLayout, ensureNoOverlaps } from "./layout";
import {
  exportToCsv,
  exportToJson,
  projectToHtml,
  projectToPlainText,
  slugifyForFilename,
  topicToMarkdown,
} from "./export";
// AccountSettingsPage is only rendered on the `account_settings` phase
// (full-viewport overlay). Lazy-loaded so it stays out of the initial bundle.
const AccountSettingsPage = lazy(() =>
  import("../account").then((m) => ({ default: m.AccountSettingsPage })),
);
import { AuthPanel } from "../../components/AuthPanel";
import {
  DeleteConfirmDialog,
  ExportOptionsDialog,
  RenameProjectDialog,
  ShareProjectDialog,
  type ExportFormat,
} from "../../components/dialogs";
import { ExportModalsHost } from "./exports";
import { DuplicateConflictDialog } from "./DuplicateConflictDialog";
// JSON import is a low-traffic path (most users never open it); split it out
// of the initial bundle.
const ImportFromJsonDialog = lazy(() =>
  import("../../components/dialogs").then((m) => ({
    default: m.ImportFromJsonDialog,
  })),
);
import {
  NotFoundPage,
  OfflineBanner,
  ServerErrorPage,
  SessionExpiredModal,
} from "../errors";
import {
  LlmModesPanel,
  type LlmModesPrefetch,
  type MergeProposal,
} from "../llm-modes";
import { CommandPalette, SearchOverlay, type Command } from "../palette";
// ProjectsListPage + WorkspaceKanban — both removed from InspiraApp's
// render tree on 2026-05-13 when the v3 .projects-list-shell fallback
// was deleted (see comment in the `projects_list` phase branch below).
// They still mount via the v4 routes:
//   • /workspaces       → WorkspaceKanbanRoute  (AuthedShell + AppRail)
//   • /workspaces/:id/projects → ProjectsListRoute (AuthedShell + AppRail)
// Both routes import the components directly; InspiraApp no longer needs
// them as siblings of the canvas.
import { AppRail } from "../shared/AppRail";
// PR 2: voice + credits scrapped. The VoiceSession lazy-import,
// UpgradeDialog, BuyCreditsDialog, CreditMeter, and CreditPack type
// were all removed. Plan-tier gating now lives in the scaffold flow
// via the upgrade-CTA modal mounted on 402 responses.
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { LegalOverlay, type LegalOverlayKind } from "../../components/LegalOverlay";
import { LocalePicker } from "../../components/LocalePicker";
import { ShortcutHelpOverlay } from "../../components/ShortcutHelpOverlay";
import { toast } from "../../components/ToastProvider";
import {
  useKeyboardShortcuts,
  type ShortcutBinding,
} from "../../hooks/useKeyboardShortcuts";
import { ShortcutsProvider } from "../../hooks/ShortcutsProvider";
import { setSentryUser } from "../../observability/sentry";

type Phase =
  | { kind: "bootstrapping" }
  // When the user has zero projects (or explicitly started a new one), we
  // show the kickoff form. On submit we create a new project and kick off.
  | { kind: "kickoff"; error: string | null; initialIdea?: string }
  | { kind: "loading"; idea: string }
  // The all-projects grid. Landing page for any user who has at least one
  // project.
  | { kind: "projects_list" }
  | {
      kind: "canvas";
      projectId: string;
      envelope: KickoffEnvelope;
      openTopicId: string | null;
      openOriginRect: DOMRect | null;
    }
  // Three-panel artifact viewer surface. Entered from the
  // Decision Summary drawer via the `inspira:open-artifact` window
  // event. ``fromPhase`` carries the phase to restore on Back.
  | {
      kind: "artifact";
      projectId: string;
      projectTitle: string;
      /** Project state at phase entry — drives the ApprovalChip's
       *  initial render. The chip manages its own state machine
       *  thereafter; this is just the seed. */
      initialState: ProjectState | null;
      fromPhase: Phase;
    }
  // Account settings overlay. `previous` lets us restore the phase the user
  // came from when they close the overlay.
  | { kind: "account_settings"; previous: Phase }
  | { kind: "error"; message: string };

// Historical localStorage keys retained ONLY as comments so future-me
// doesn't re-add them. The original flow stashed a pending idea /
// template slug / markdown so anonymous visitors could close the tab,
// finish signing up, and come back to their work. That flow was
// replaced by giving every anonymous visitor a real per-session
// ``user-anon-<hex>`` id on the server — their canvas now exists in
// the database from the moment they click "Map it", so nothing needs
// to be stashed client-side any more. Keys were:
//   - inspira_pending_kickoff_idea
//   - inspira_pending_template_slug
//   - inspira_pending_markdown

// Window event the Decision Summary drawer dispatches when the user
// taps "Open →" on an approved canvas. Listening here keeps the
// drawer's `onGenerateArtifact` callback decoupled from the artifact
// surface — we just transition phases on the broadcast.
const ARTIFACT_OPEN_EVENT = "inspira:open-artifact";

export function InspiraApp() {
  // Router hooks must be bound before the bootstrap effect that
  // uses them — InspiraApp's bootstrap can redirect v4 partners to
  // /workspaces if they land on /app without a target project.
  const navigate = useNavigate();
  const location = useLocation();
  const [phase, setPhase] = useState<Phase>({ kind: "bootstrapping" });
  const [projects, setProjects] = useState<V2Project[]>([]);
  const [user, setUser] = useState<AuthedUser | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  // Shelves — named groupings of projects, user-scoped. Empty for
  // users who haven't organized yet; in that case the v4
  // ProjectsListRoute (/workspaces/:id/projects) renders the flat
  // grid + a "New shelf" button in the header.
  const [shelves, setShelves] = useState<Shelf[]>([]);

  // Overlay / modal flags
  const [helpOpen, setHelpOpen] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [authInitialMode, setAuthInitialMode] = useState<"login" | "signup">(
    "login",
  );
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [sessionExpiredOpen, setSessionExpiredOpen] = useState(false);
  const [llmModesOpen, setLlmModesOpen] = useState(false);
  // Plan slug cached for paid-feature gating in the canvas (scaffold,
  // frontier models). Fetched via api.getEntitlements; null while we
  // don't know, "free" | "pro" | "team" once resolved. PR 2 dropped
  // the credit balance from this payload.
  const [planSlug, setPlanSlug] = useState<string | null>(null);
  const isFreePlan = planSlug === "free";

  // #094: best-effort document cap usage. The BE doesn't expose a GET
  // for current_count today; we hydrate from 429 DocumentCapReachedError
  // payloads (which include current_count + cap from the BE) and bump
  // optimistically on each successful generation. Off-by-one for
  // regenerates is acceptable — the BE is the source of truth and it
  // 429s any over-cap POST attempt. Cap LIMIT is derived from planSlug
  // via documentCapForPlan (Pro 1, Frontier/team 100).
  const [documentCapUsed, setDocumentCapUsed] = useState<number>(0);
  const documentCapLimit = useMemo<number>(
    () => (planSlug ? documentCapForPlan(planSlug) : 0),
    [planSlug],
  );

  // Legal overlay — shown from the footer link on the kickoff /
  // projects-list screens. `kind` selects privacy vs terms.
  const [legalOverlay, setLegalOverlay] = useState<LegalOverlayKind | null>(
    null,
  );

  // Session-scoped "Save your work" banner dismissal. Anonymous visitors
  // see the banner above their canvas; the × dismisses it for this tab
  // session only. We intentionally DON'T persist dismissal — reloading
  // brings the banner back, which is the right behavior for a user who
  // might forget they aren't signed in.
  const [anonBannerDismissed, setAnonBannerDismissed] = useState(false);

  // Dialog flags — keyed by what they're about rather than by kind, since
  // several of them operate on the "active project" implicitly.
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [deleteProjectDialogOpen, setDeleteProjectDialogOpen] = useState(false);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  // Kickoff-screen JSON import — opened from the "Or import a JSON export"
  // link. The dialog itself owns file-pick + schema-precheck; the parent
  // (us) handles the API call and routes to the canvas on success.
  const [importJsonDialogOpen, setImportJsonDialogOpen] = useState(false);

  // TΛ.3: proactive duplicate-detection queue. The Duplicates tab
  // was deleted in favor of a popup that fires automatically when the
  // dedupe pass surfaces candidate merges. We walk the queue one at a
  // time so the user never sees a list — they answer one question,
  // move on. The "seen" key prevents re-popping the same proposal
  // batch every re-render.
  const [duplicateQueue, setDuplicateQueue] = useState<MergeProposal[]>([]);
  const [duplicateIndex, setDuplicateIndex] = useState<number>(0);
  const seenDedupeBatchRef = useRef<string | null>(null);

  // Active share link URL for the open project (null when no live link).
  // Loaded lazily when the share dialog opens; kept in sync after generate/revoke.
  const [activeShareUrl, setActiveShareUrl] = useState<string | null>(null);

  // Pending-delete tracker for topics: the canvas asks to delete, we show a
  // confirm dialog, and on confirm we call api.deleteTopic. Kept here so
  // the dialog's lifecycle is decoupled from the canvas renderer.
  const [pendingTopicDelete, setPendingTopicDelete] = useState<{
    topicId: string;
    title: string;
  } | null>(null);

  // Planner-suggested deletions — populated when the topic_turn response
  // includes a topic_deletion_suggestion. Keyed by target_topic_id so each
  // affected TopicNode can read its own suggestion independently.
  const [pendingDeletionSuggestions, setPendingDeletionSuggestions] = useState<
    Record<string, TopicDeletionSuggestion>
  >({});

  // Refresh plan_slug when the user changes. Anonymous/system users never
  // see the paywall affordance (they can't hold a paid plan anyway), so
  // we only fetch for signed-in real accounts. Soft-fails — if the
  // entitlements endpoint is down we just leave plan unset and the
  // upgrade-CTA stays hidden until the next refetch.
  useEffect(() => {
    if (!user || user.is_system) {
      setPlanSlug(user?.is_system ? "free" : null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getEntitlements();
        if (cancelled) return;
        setPlanSlug(res.plan);
      } catch {
        /* best-effort — plan stays null */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

  // Sentry user context: tag every captured event with the OPAQUE user_id
  // so on-call can correlate a crash with the rest of the user's session
  // (rate-limit logs, billing rows) without us ever shipping their email
  // or display name to Sentry. ``user_id`` is privacy-safe: it's an
  // internal opaque identifier that means nothing outside our DB.
  //
  // Runs on every change to ``user.user_id`` so it covers all three
  // identity transitions: bootstrap (anonymous → resolved), login swap
  // (anon → real user), and logout (real → null).
  useEffect(() => {
    setSentryUser(user?.user_id ?? null);
  }, [user?.user_id]);

  // P1.1 — `userRef` mirrors the latest `user` state so callbacks
  // that fire AFTER `setUser(...)` has been queued (but before the
  // closure that captured them re-runs) can read the fresh value
  // instead of a stale one. The signup → resume-kickoff path is the
  // motivating case: after a successful signup, the inline
  // onAuthenticated callback queues `setUser(authedUser)` AND
  // `setAuthOpen(false)`, then `void async () => { … resume }()`
  // schedules a microtask. The microtask awaits a network call
  // (so React renders + commits the new user state in the meantime),
  // but the resume branch then calls `handleKickoff(...)` via a
  // closure that captured the OLD `handleKickoff` — which closed
  // over the OLD anon `user`. The OLD handler's auth gate (`if
  // (user?.is_system) { setAuthOpen(true); return; }`) then re-fires
  // setAuthOpen(true) and the modal flickers back into view with
  // its password fields cleared by AuthPanel's [open, initialMode]
  // reset effect. The ref breaks that staleness — gates read
  // `userRef.current` at call time, after `setUser` has flushed.
  const userRef = useRef(user);
  useEffect(() => {
    userRef.current = user;
  }, [user]);

  // ---------------------------------------------------------------
  // Bootstrap: who am I, what projects do I own, where should I land?
  // ---------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Kick off both bootstrap calls IN PARALLEL. They're independent
      // (listV2Projects does its own auth resolution on the server) and
      // serialising them wastes the full round-trip time — ~800ms per
      // hop on high-latency connections, which made the "Your projects"
      // screen feel sluggish on every cold load. Firing both at once and
      // handling each result in order preserves the original "me wins
      // even if projects fails" semantics.
      const mePromise = api.me();
      // Wrap in a result envelope so the awaited promise never rejects
      // — we surface the error in the second await below to preserve
      // the "me wins even if projects fails" semantics.
      type ProjectsFetchResult =
        | { ok: true; value: Awaited<ReturnType<typeof api.listV2Projects>> }
        | { ok: false; err: unknown };
      const projectsPromise: Promise<ProjectsFetchResult> = api
        .listV2Projects()
        .then((value) => ({ ok: true as const, value }))
        .catch((err) => ({ ok: false as const, err }));

      let meRes: AuthedUser;
      try {
        meRes = await mePromise;
      } catch (err) {
        if (cancelled) return;
        console.error("[Inspira] bootstrap failed", err);
        setPhase({
          kind: "kickoff",
          error: t("errors.backend_unreachable"),
        });
        return;
      }
      if (cancelled) return;
      setUser(meRes);

      // Now unwrap the already-in-flight projects response. A failure
      // here is non-fatal — keep the user signed in, surface a soft
      // error on the kickoff screen, and optionally toast.
      let projectsRes;
      try {
        const awaited = await projectsPromise;
        if (!awaited.ok) {
          throw awaited.err;
        }
        projectsRes = awaited.value;
      } catch (err) {
        if (cancelled) return;
        setProjects([]);
        const msg = t("kickoff.projects_fetch_failed");
        setPhase({ kind: "error", message: msg });
        try {
          toast.error(msg);
        } catch {
          /* toast system optional */
        }
        // Suppress unused-variable lint without changing behavior
        void err;
        return;
      }
      if (cancelled) return;
      setProjects(projectsRes.projects);

      const hasProjects = projectsRes.projects.length > 0;

      // Anonymous visitors hitting /app: there's no v4 surface for
      // them — bounce to the marketing root with the signup modal
      // queued. RootGate handles the rest.
      if (meRes.is_system) {
        navigate("/?signup=1", { replace: true });
        return;
      }

      // v4 partners (anyone with a default_workspace_id) live in the
      // /workspaces Kanban surface. If they land on /app with no
      // openProject (or it failed to resolve), bounce to /workspaces.
      if (meRes.default_workspace_id) {
        // Don't redirect if there's a pending openProject in router
        // state — the dedicated effect below handles that flow.
        const routerState = (location.state as { openProject?: string } | null) ?? null;
        if (!routerState?.openProject) {
          navigate("/workspaces", { replace: true });
          return;
        }
      }

      // Signed-up user with no workspace yet → the v4 onboarding
      // wizard is the canonical entry point. The legacy kickoff
      // surface is dead code pending the dead-code sweep.
      if (!hasProjects) {
        navigate("/onboarding", { replace: true });
        return;
      }
      // 1+ projects → land on the projects list. User clicks one to
      // open its canvas.
      setPhase({ kind: "projects_list" });
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------
  // Global 401 interceptor — api.ts fires `inspira:unauthorized`
  // whenever an authenticated call returns 401 (excluding the
  // login/signup endpoints themselves). We surface the session-
  // expired modal; user can sign back in or dismiss to guest mode.
  // ---------------------------------------------------------------
  useEffect(() => {
    const onUnauthorized = () => {
      // Don't open if the user is already on the auth flow or the modal
      // is already up; avoids stacking.
      if (authOpen) return;
      setSessionExpiredOpen(true);
    };
    window.addEventListener("inspira:unauthorized", onUnauthorized);
    return () =>
      window.removeEventListener("inspira:unauthorized", onUnauthorized);
  }, [authOpen]);

  // ---------------------------------------------------------------
  // Global project-not-found handler.
  //
  // api.ts throws ProjectNotFoundError on 404 + "project_not_found" (or
  // "topic_not_found") responses. Any call site that lets it bubble past
  // its own catch will trigger this handler, which:
  //   1. Clears any localStorage keys that reference the dead project.
  //   2. Toasts a friendly message.
  //   3. Routes the user back to the projects list (or kickoff if none).
  // ---------------------------------------------------------------
  // Listen for the Decision Summary drawer's "Open →" CTA.
  // The drawer dispatches `inspira:open-artifact` with `{projectId}`;
  // we transition to the artifact phase, carrying the current phase as
  // ``fromPhase`` so the artifact viewer's Back button can restore.
  useEffect(() => {
    const onOpenArtifact = (e: Event) => {
      const projectId =
        (e as CustomEvent<{ projectId: string }>).detail?.projectId ?? "";
      if (!projectId) return;
      setPhase((prev) => {
        // Resolve title + approved-at from the projects list when
        // available. Falling back to a generic label is fine — the
        // artifact viewer's `getArtifact` call will hydrate the rest.
        const proj = projects.find((p) => p.project_id === projectId);
        const projectTitle = proj?.title ?? "";
        const initialState =
          (proj?.project_state as ProjectState | undefined) ?? null;
        return {
          kind: "artifact",
          projectId,
          projectTitle,
          initialState,
          fromPhase: prev,
        };
      });
    };
    window.addEventListener(ARTIFACT_OPEN_EVENT, onOpenArtifact);
    return () =>
      window.removeEventListener(ARTIFACT_OPEN_EVENT, onOpenArtifact);
  }, [projects]);

  useEffect(() => {
    const onProjectNotFound = (e: Event) => {
      const projectId =
        (e as CustomEvent<{ projectId: string }>).detail?.projectId ?? "";

      // Part C — flush any localStorage entries that reference this project.
      if (typeof window !== "undefined" && projectId) {
        for (const key of [
          "inspira_pending_kickoff_idea",
          "inspira_pending_template_slug",
          "inspira_pending_markdown",
        ]) {
          // Only clear if the value contains the dead project id (the
          // idea/markdown keys hold text, not ids — but template slug key
          // can hold a slug, so we check just in case).
          const val = window.localStorage.getItem(key) ?? "";
          if (val.includes(projectId)) {
            window.localStorage.removeItem(key);
          }
        }
        // Always flush a last-opened-project key that stores the id directly.
        const lastKey = `inspira_last_project_${projectId}`;
        window.localStorage.removeItem(lastKey);
      }

      toast.error(t("toast.project_gone"));

      // Clear phase back to the projects list (or kickoff for anonymous).
      setPhase((prev) => {
        if (prev.kind === "canvas" || prev.kind === "loading") {
          return { kind: "projects_list" };
        }
        return prev;
      });
      // Refresh project list so the deleted/inaccessible entry disappears.
      api.listV2Projects().then((res) => setProjects(res.projects)).catch(() => {
        /* best effort */
      });
    };

    window.addEventListener("inspira:project-not-found", onProjectNotFound);
    return () =>
      window.removeEventListener(
        "inspira:project-not-found",
        onProjectNotFound,
      );
  }, []);

  // ---------------------------------------------------------------
  // Anonymous-visitor routing: the old guard bounced anonymous users
  // OFF the canvas because they shared the legacy system-user account
  // with every other guest — showing them arbitrary canvases leaked
  // strangers' titles. Post-anonymous-refactor each guest has their
  // own ``user-anon-…`` id, so the canvas is genuinely theirs and the
  // bounce is gone. Anonymous users are still blocked from the
  // projects_list + account_settings phases (they have at most one
  // canvas by design; see handleCreateNewProject which prompts signup
  // instead of creating a second project).
  // ---------------------------------------------------------------
  useEffect(() => {
    if (!user?.is_system) return;
    if (
      phase.kind === "projects_list" ||
      phase.kind === "account_settings"
    ) {
      setPhase({ kind: "kickoff", error: null });
    }
  }, [user?.is_system, phase.kind]);

  // ---------------------------------------------------------------
  // Load the canvas for an existing project.
  // ---------------------------------------------------------------
  const openProject = useCallback(
    async (projectId: string, opts: { updateList?: boolean } = {}) => {
      try {
        // Pre-flight existence check before entering loading phase.
        // Stale/deleted IDs throw ProjectNotFoundError (dispatched
        // globally — see handler at ~503-526), so we never leave the
        // user on a stuck loading screen. (closes #039)
        await api.getV2Project(projectId);
        setPhase({ kind: "loading", idea: t("loading.loading_project") });
        const [topicsRes, relsRes] = await Promise.all([
          api.listTopics(projectId),
          api.listRelationships(projectId),
        ]);
        const cleanTopics = ensureNoOverlaps(topicsRes.topics);
        // Synthesize a minimal kickoff envelope — the opening_card body
        // only exists on the original kickoff call, so we leave it empty
        // on subsequent project loads; the ProjectCanvas just hides the
        // planner-opening card when body is falsy.
        const envelope: KickoffEnvelope = {
          kickoff: {
            domain: "personal",
            domain_confidence: "medium",
            opening_card: { body: "" },
            topics: [],
            relationships: [],
            suggested_first_topic: "",
            clarifying_question_if_too_vague: null,
          },
          topics: cleanTopics,
          relationships: relsRes.relationships,
        };
        setPhase({
          kind: "canvas",
          projectId,
          envelope,
          openTopicId: null,
          openOriginRect: null,
        });
        if (opts.updateList) {
          const refreshed = await api.listV2Projects();
          setProjects(refreshed.projects);
        }
      } catch (err) {
        // ProjectNotFoundError already dispatched globally — it will route
        // the user back to projects_list, so we don't need to set an error phase.
        if (err instanceof ProjectNotFoundError) {
          return;
        }
        console.error("[Inspira] openProject failed", err);
        setPhase({
          kind: "error",
          message: t("toast.generic_load_failed"),
        });
      }
    },
    [],
  );

  // B2.3 — consume react-router state from the Promote-to-Project
  // flow. PromoteToProjectController calls `navigate("/app", { state:
  // { openProject, pendingReview }})` after a successful promotion.
  // We read it here, fire openProject once per fresh state, and stash
  // the consumed id so a re-render doesn't loop. The pendingReview
  // marker is informational for now — project-state UI is owned by a
  // separate slice; once its pill ships, the canvas can render it
  // without this flow knowing.
  // ``location`` is bound at the top of the component now.
  const consumedOpenProjectRef = useRef<string | null>(null);
  useEffect(() => {
    // Two paths to open a project on /app:
    //   1. router state.openProject — set by KanbanCard click-through
    //      and the legacy projects-list flow.
    //   2. URL path /app/{projectId} or /app/{projectId}/artifact —
    //      partner demos refresh the page or paste links; without
    //      URL-driven open we fall back to projects_list (jarring).
    //      If the URL also ends in /artifact we additionally dispatch
    //      `inspira:open-artifact` after the project loads so the
    //      Artifact Viewer surfaces.
    const state = (location.state as { openProject?: string } | null) ?? null;
    let target: string | null = state?.openProject ?? null;
    let openArtifact = false;
    if (!target && location.pathname.startsWith("/app/")) {
      const rest = location.pathname.slice(5); // strip "/app/"
      const [pidPart, ...trailing] = rest.split("/");
      // Only project_ids match the prefix our backend mints
      // (`project-` followed by hex). Other /app/* paths (legacy
      // /app/shared, etc.) fall through to InspiraApp's own
      // path-sniffer.
      if (pidPart.startsWith("project-")) {
        target = pidPart;
        openArtifact = trailing[0] === "artifact";
      }
    }
    if (!target) return;
    if (consumedOpenProjectRef.current === target) return;
    consumedOpenProjectRef.current = target;
    void openProject(target).then(() => {
      if (openArtifact) {
        // Defer one tick so the project finishes hydrating into
        // InspiraApp's `projects` array before the artifact phase
        // tries to look it up for title/state.
        setTimeout(() => {
          window.dispatchEvent(
            new CustomEvent("inspira:open-artifact", {
              detail: { projectId: target },
            }),
          );
        }, 0);
      }
    });
  }, [location.state, location.pathname, openProject]);

  // ---------------------------------------------------------------
  // Fresh kickoff — creates a new project, then runs the planner.
  //
  // Product decision (2026-04-22 reversal): require signup BEFORE a
  // user reaches the canvas. The earlier anon-canvas flow let anon
  // visitors create projects under a ``user-anon-<hex>`` session; it's
  // still supported at the backend for transfer-on-signup, but we now
  // gate every kickoff entry point at the top. Stash what they typed
  // so signup-then-resume lands them back on the same idea.
  // ---------------------------------------------------------------
  const PENDING_KICKOFF_IDEA_KEY = "inspira_pending_kickoff_idea";
  const PENDING_KICKOFF_ATTACHMENTS_KEY = "inspira_pending_kickoff_attachments";
  const PENDING_TEMPLATE_SLUG_KEY = "inspira_pending_template_slug";
  const PENDING_MARKDOWN_KEY = "inspira_pending_markdown";

  const handleKickoff = useCallback(
    async (
      idea: string,
      attachments: AttachedSource[] = [],
    ) => {
      // Signup gate: anonymous visitors don't reach the canvas.
      // Read from `userRef` (P1.1) so post-signup resume callers
      // see the freshly authed user, not the stale anon one their
      // captured closure froze in place.
      if (userRef.current?.is_system) {
        try {
          window.localStorage.setItem(PENDING_KICKOFF_IDEA_KEY, idea);
          window.localStorage.setItem(
            PENDING_KICKOFF_ATTACHMENTS_KEY,
            JSON.stringify(attachments),
          );
        } catch {
          // localStorage full / private mode — proceed anyway.
        }
        setAuthInitialMode("signup");
        setAuthOpen(true);
        return;
      }
      // T1.6: stash the typed idea + attachments BEFORE we enter the
      // loading phase so a planner failure can restore the textarea
      // instead of dumping the user back to a blank kickoff form.
      // Stored under the same key the signup-gate flow already uses.
      try {
        window.localStorage.setItem(PENDING_KICKOFF_IDEA_KEY, idea);
        window.localStorage.setItem(
          PENDING_KICKOFF_ATTACHMENTS_KEY,
          JSON.stringify(attachments),
        );
      } catch {
        // localStorage full / private mode — proceed anyway.
      }
      setPhase({ kind: "loading", idea });
      // T4.9: track the just-created project so we can clean it up if
      // the planner errors before the canvas mounts. Without this, a
      // failed kickoff leaves the empty project lingering in the user's
      // projects list — a confusing "Goals / Users / Scope" stub they
      // didn't ask for.
      let createdProjectIdForCleanup: string | null = null;
      try {
        // Title the project after the first line of the idea, falling
        // back to the first attachment's name if the user kicked off
        // with a file and no prose. Capped at 80 chars for the top-bar chip.
        let firstLine = idea.trim().split(/\r?\n/, 1)[0];
        if (!firstLine && attachments.length > 0) {
          firstLine = attachments[0].display_name;
        }
        firstLine = firstLine || "New project";
        const title = firstLine.length > 80 ? firstLine.slice(0, 77) + "…" : firstLine;

        const created = await api.createV2Project(title);
        const newProjectId = created.project.project_id;
        createdProjectIdForCleanup = newProjectId;

        // If the user attached files with no typed idea, send a short
        // synthetic seed so the planner has something to anchor on; real
        // context travels in attached_sources.
        const ideaForPlanner =
          idea.trim() ||
          `Map the project described in the attached source${
            attachments.length === 1 ? "" : "s"
          }.`;

        // Phase 1 SSE streaming: prefer the streaming endpoint so the UI
        // can flip the loading message to "AI is thinking…" within ~50ms
        // of the request leaving (vs ~6-12s of a blank wait on the
        // non-streaming path). Falls back to the legacy `api.kickoff`
        // when the backend feature flag is off (503 streaming_disabled)
        // so we can dark-launch this safely.
        let envelope;
        try {
          envelope = await api.kickoffStream(
            newProjectId,
            ideaForPlanner,
            attachments,
            null,
            {
              onHeartbeat: (data) => {
                // Use the backend's progressive message when we get one
                // ("Reading your idea…" → "Sketching topics…" → ...), and
                // fall back to the i18n default on the first-byte tick so
                // a non-English locale still sees a translated string.
                const msg = data?.message?.trim()
                  ? data.message
                  : t("loading.ai_thinking");
                setPhase({
                  kind: "loading",
                  idea: msg,
                });
              },
            },
          );
        } catch (err) {
          // 503 streaming_disabled (or any other stream-side failure):
          // retry the synchronous endpoint so the user still lands on a
          // canvas. This makes the streaming route purely additive from
          // a behaviour standpoint.
          if (
            err instanceof Error
            && /streaming_disabled|503/.test(err.message)
          ) {
            envelope = await api.kickoff(
              newProjectId,
              ideaForPlanner,
              attachments,
            );
          } else {
            throw err;
          }
        }

        // Guardrail: the product promises "I will map your idea." If the
        // planner returns an empty topic list (e.g. the LLM decided the
        // input was too short and asked a clarifying question instead of
        // building), we DO NOT bounce the user back to the kickoff with
        // an error. Instead we drop in a universal starter scaffold so
        // the user still lands on a canvas they can work from. The
        // clarifying question, if any, becomes the first topic's seed
        // question so the signal isn't lost.
        if (envelope.topics.length === 0) {
          const clarifying = envelope.kickoff.clarifying_question_if_too_vague;
          // 2 columns x 3 rows grid. Column stride 440px, row stride 280px —
          // leaves ample gap relative to the 280x180 TopicNode footprint so
          // ensureNoOverlaps (OVERLAP_GAP=20 in layout.ts) won't nudge any
          // card on reload. openProject re-runs ensureNoOverlaps but not
          // computeTopicLayout, so these positions stick.
          const fallbackTopics = [
            { title: "Goals", icon: "🎯", position_x: 0, position_y: 0 },
            { title: "Users", icon: "👥", position_x: 440, position_y: 0 },
            { title: "Scope", icon: "🗺️", position_x: 0, position_y: 280 },
            { title: "Constraints", icon: "🔒", position_x: 440, position_y: 280 },
            { title: "Milestones", icon: "🚩", position_x: 0, position_y: 560 },
            { title: "Success", icon: "✅", position_x: 440, position_y: 560 },
          ];
          try {
            for (const seed of fallbackTopics) {
              await api.createTopic(newProjectId, {
                title: seed.title,
                icon: seed.icon,
                position_x: seed.position_x,
                position_y: seed.position_y,
              });
            }
          } catch (err) {
            console.warn("[Inspira] fallback topic seed failed", err);
          }
          if (clarifying) {
            toast.info(clarifying);
          }
          // Reload the project so we have the real topic rows.
          await openProject(newProjectId, { updateList: true });
          return;
        }

        const layout = computeTopicLayout(envelope.topics, envelope.relationships);
        const laidTopics = ensureNoOverlaps(applyLayout(envelope.topics, layout));
        for (const t of laidTopics) {
          api
            .updateTopic(t.topic_id, {
              position_x: t.position_x,
              position_y: t.position_y,
            })
            .catch((err) =>
              console.warn("[Inspira] failed to persist auto-layout", err),
            );
        }

        setPhase({
          kind: "canvas",
          projectId: newProjectId,
          envelope: { ...envelope, topics: laidTopics },
          openTopicId: null,
          openOriginRect: null,
        });

        // T1.6: kickoff succeeded — clear the recovery stash so a
        // future failed attempt doesn't pre-fill an unrelated idea.
        try {
          window.localStorage.removeItem(PENDING_KICKOFF_IDEA_KEY);
          window.localStorage.removeItem(PENDING_KICKOFF_ATTACHMENTS_KEY);
        } catch {
          /* noop */
        }

        // Refresh the project list so the new entry shows in the switcher.
        const refreshed = await api.listV2Projects();
        setProjects(refreshed.projects);
      } catch (err) {
        if (err instanceof ProjectNotFoundError) return;
        console.error("[Inspira] kickoff failed", err);
        // T4.9: clean up the empty project that was created before
        // the planner failed. Best-effort — if delete also fails the
        // worst case is a stub in the projects list, same as before.
        if (createdProjectIdForCleanup) {
          api
            .deleteV2Project(createdProjectIdForCleanup)
            .catch((deleteErr: unknown) => {
              console.warn(
                "[Inspira] failed to clean up empty kickoff project",
                deleteErr,
              );
            });
        }
        // T1.6: re-render the kickoff form with the user's typed idea
        // pre-filled so they don't have to retype it after a planner
        // crash. The stash was written above; we just thread it through
        // the phase so KickoffForm's `initialIdea` prop renders it.
        setPhase({
          kind: "kickoff",
          error: t("errors.kickoff_failed"),
          initialIdea: idea,
        });
      }
    },
    [user],
  );

  // ---------------------------------------------------------------
  // Template kickoff — bypasses the LLM planner entirely.
  // Creates a project pre-seeded with the template's topics and
  // relationships and opens the canvas directly. Anonymous users go
  // through the same path as signed-in users; their per-session
  // user-anon id scopes the resulting project.
  // ---------------------------------------------------------------
  const handleTemplateKickoff = useCallback(
    async (slug: string) => {
      // Signup gate: anonymous visitors don't reach the canvas.
      // Read from `userRef` (P1.1) so post-signup resume callers
      // see the freshly authed user, not the stale anon one their
      // captured closure froze in place.
      if (userRef.current?.is_system) {
        try {
          window.localStorage.setItem(PENDING_TEMPLATE_SLUG_KEY, slug);
        } catch {
          // ignore
        }
        setAuthInitialMode("signup");
        setAuthOpen(true);
        return;
      }
      setPhase({ kind: "loading", idea: t("loading.loading_project") });
      try {
        const result = await api.createProjectFromTemplate(slug);
        const { project, topics, relationships } = result;

        const envelope: KickoffEnvelope = {
          kickoff: {
            domain: result.template.domain_framing,
            domain_confidence: "high",
            opening_card: { body: "" },
            topics: [],
            relationships: [],
            suggested_first_topic: topics[0]?.title ?? "",
            clarifying_question_if_too_vague: null,
          },
          topics,
          relationships,
        };

        const layout = computeTopicLayout(topics, relationships);
        const laidTopics = ensureNoOverlaps(applyLayout(topics, layout));
        for (const topic of laidTopics) {
          api
            .updateTopic(topic.topic_id, {
              position_x: topic.position_x,
              position_y: topic.position_y,
            })
            .catch((err) =>
              console.warn("[Inspira] template layout persist failed", err),
            );
        }

        setPhase({
          kind: "canvas",
          projectId: project.project_id,
          envelope: { ...envelope, topics: laidTopics },
          openTopicId: null,
          openOriginRect: null,
        });

        const refreshed = await api.listV2Projects();
        setProjects(refreshed.projects);
      } catch (err) {
        if (err instanceof ProjectNotFoundError) return;
        console.error("[Inspira] template kickoff failed", err);
        setPhase({
          kind: "kickoff",
          error: t("toast.generic_load_failed"),
        });
      }
    },
    [user],
  );

  // ---------------------------------------------------------------
  // Markdown import — Notion/Obsidian migration path.
  //
  // Anonymous users go through the same backend path as signed-in
  // users; their per-session user-anon id scopes the imported project.
  // ---------------------------------------------------------------
  const handleImportMarkdown = useCallback(
    async (markdown: string) => {
      // Signup gate: anonymous visitors don't reach the canvas.
      // Read from `userRef` (P1.1) so post-signup resume callers
      // see the freshly authed user, not the stale anon one their
      // captured closure froze in place.
      if (userRef.current?.is_system) {
        try {
          window.localStorage.setItem(PENDING_MARKDOWN_KEY, markdown);
        } catch {
          // ignore
        }
        setAuthInitialMode("signup");
        setAuthOpen(true);
        return;
      }
      setPhase({ kind: "loading", idea: t("kickoff.importing") });
      try {
        const result = await api.importFromMarkdown(markdown);
        const { project, topics } = result;

        const envelope: KickoffEnvelope = {
          kickoff: {
            domain: "personal",
            domain_confidence: "medium",
            opening_card: { body: "" },
            topics: [],
            relationships: [],
            suggested_first_topic: topics[0]?.title ?? "",
            clarifying_question_if_too_vague: null,
          },
          topics,
          relationships: [],
        };

        const layout = computeTopicLayout(topics, []);
        const laidTopics = ensureNoOverlaps(applyLayout(topics, layout));
        for (const topic of laidTopics) {
          api
            .updateTopic(topic.topic_id, {
              position_x: topic.position_x,
              position_y: topic.position_y,
            })
            .catch((err) =>
              console.warn("[Inspira] markdown import layout persist failed", err),
            );
        }

        setPhase({
          kind: "canvas",
          projectId: project.project_id,
          envelope: { ...envelope, topics: laidTopics },
          openTopicId: null,
          openOriginRect: null,
        });

        const refreshed = await api.listV2Projects();
        setProjects(refreshed.projects);
      } catch (err) {
        if (err instanceof ProjectNotFoundError) return;
        console.error("[Inspira] markdown import failed", err);
        setPhase({
          kind: "kickoff",
          error: t("toast.generic_load_failed"),
        });
      }
    },
    [user],
  );

  // ---------------------------------------------------------------
  // JSON import — companion to exportToJson. Takes a parsed blob
  // from the ImportFromJsonDialog and creates a fresh project with
  // the topics, relationships, and decisions baked in. See
  // services/planning_studio_service/json_import.py for the server
  // side.
  //
  // Thrown errors propagate to the dialog (which surfaces them
  // inline) so the user sees the same helpful schema/parse messages
  // the server produces instead of a generic "something failed".
  // ---------------------------------------------------------------
  const handleImportJson = useCallback(
    async (blob: object, titleOverride?: string) => {
      setPhase({ kind: "loading", idea: t("kickoff.importing") });
      try {
        const result = await api.importFromJson(blob, titleOverride);
        const { project, topics, relationships } = result;

        // Close the dialog now that we've committed — the phase flip
        // tears it down on re-render anyway, but explicit is kinder.
        setImportJsonDialogOpen(false);

        const envelope: KickoffEnvelope = {
          kickoff: {
            domain: "personal",
            domain_confidence: "medium",
            opening_card: { body: "" },
            topics: [],
            relationships: [],
            suggested_first_topic: topics[0]?.title ?? "",
            clarifying_question_if_too_vague: null,
          },
          topics,
          relationships,
        };

        const layout = computeTopicLayout(topics, relationships);
        const laidTopics = ensureNoOverlaps(applyLayout(topics, layout));
        for (const topic of laidTopics) {
          api
            .updateTopic(topic.topic_id, {
              position_x: topic.position_x,
              position_y: topic.position_y,
            })
            .catch((err) =>
              console.warn("[Inspira] json import layout persist failed", err),
            );
        }

        setPhase({
          kind: "canvas",
          projectId: project.project_id,
          envelope: { ...envelope, topics: laidTopics },
          openTopicId: null,
          openOriginRect: null,
        });

        const refreshed = await api.listV2Projects();
        setProjects(refreshed.projects);
      } catch (err) {
        if (err instanceof ProjectNotFoundError) {
          throw err;
        }
        console.error("[Inspira] json import failed", err);
        // Re-throw so the dialog renders its inline error. We also
        // roll the phase back to the kickoff screen in case the user
        // dismisses the dialog after the failure.
        setPhase({ kind: "kickoff", error: t("toast.import_json_failed") });
        throw err;
      }
    },
    [user],
  );

  // ---------------------------------------------------------------
  // Example kickoff — bypasses the LLM planner; creates a project
  // pre-seeded with realistic example content and opens the canvas.
  // ---------------------------------------------------------------
  const handleExampleKickoff = useCallback(
    async (slug: string) => {
      setPhase({ kind: "loading", idea: t("example.loading") });
      try {
        const res = await api.createProjectFromExample(slug);
        const { project, topics } = res;

        const envelope: KickoffEnvelope = {
          kickoff: {
            domain: "personal",
            domain_confidence: "high",
            opening_card: { body: "" },
            topics: [],
            relationships: [],
            suggested_first_topic: topics[0]?.title ?? "",
            clarifying_question_if_too_vague: null,
          },
          topics,
          relationships: [],
        };

        const layout = computeTopicLayout(topics, []);
        const laidTopics = ensureNoOverlaps(applyLayout(topics, layout));
        for (const topic of laidTopics) {
          api
            .updateTopic(topic.topic_id, {
              position_x: topic.position_x,
              position_y: topic.position_y,
            })
            .catch((err) =>
              console.warn("[Inspira] example layout persist failed", err),
            );
        }

        setPhase({
          kind: "canvas",
          projectId: project.project_id,
          envelope: { ...envelope, topics: laidTopics },
          openTopicId: null,
          openOriginRect: null,
        });

        const refreshed = await api.listV2Projects();
        setProjects(refreshed.projects);
      } catch (err) {
        if (err instanceof ProjectNotFoundError) return;
        console.error("[Inspira] example kickoff failed", err);
        setPhase({
          kind: "kickoff",
          error: t("toast.generic_load_failed"),
        });
      }
    },
    [],
  );

  // ---------------------------------------------------------------
  // Decisions (per-project) for the canvas card bullets.
  // ---------------------------------------------------------------
  const fetchDecisions = useCallback(async (projectId: string) => {
    try {
      const res = await api.listProjectDecisions(projectId);
      setDecisions(res.decisions);
    } catch (err) {
      console.warn("[Inspira] failed to fetch project decisions", err);
    }
  }, []);

  const activeProjectIdForDecisions =
    phase.kind === "canvas" ? phase.projectId : null;
  useEffect(() => {
    if (activeProjectIdForDecisions)
      void fetchDecisions(activeProjectIdForDecisions);
  }, [activeProjectIdForDecisions, fetchDecisions]);

  // Tag the document body with `data-canvas-active` while the project
  // canvas is the active phase. App.css uses this to hide the Feedback
  // launcher (which otherwise covers the composer's submit arrow on
  // phones, and overlaps the React Flow minimap on desktop).
  //
  // T4.7: also tag `data-kickoff-active` while the kickoff form is
  // showing so the launcher hides above the kickoff CTA on mobile.
  // Cleanup runs on unmount and on phase transitions away.
  useEffect(() => {
    if (phase.kind === "canvas") {
      document.body.setAttribute("data-canvas-active", "true");
      return () => {
        document.body.removeAttribute("data-canvas-active");
      };
    }
    if (phase.kind === "kickoff") {
      document.body.setAttribute("data-kickoff-active", "true");
      return () => {
        document.body.removeAttribute("data-kickoff-active");
      };
    }
    return undefined;
  }, [phase.kind]);

  const decisionsByTopicId = useMemo(() => {
    const m = new Map<string, Decision[]>();
    for (const d of decisions) {
      const list = m.get(d.topic_id) ?? [];
      list.push(d);
      m.set(d.topic_id, list);
    }
    return m;
  }, [decisions]);

  // ---------------------------------------------------------------
  // LLM-modes background prefetch
  // ---------------------------------------------------------------
  //
  // The Planner Views panel (Summary / Outline / Duplicates / Timeline)
  // used to stall on first tab activation while the backend generated
  // each view. We now warm all four views in the background once the
  // canvas renders, so the user arrives at a ready tab.
  //
  // Trigger conditions:
  //   - phase.kind === "canvas" (canvas is mounted — includes when the
  //     Topic Detail drawer is open, since that's still the canvas phase)
  //   - topics.length >= 1 (even small projects benefit from warm-up;
  //     the user-perceivable latency is the same regardless of topic
  //     count, so there's no reason to gate)
  //
  // Cache-bust signal:
  //   - revision key = projectId + topic count. We deliberately DO NOT
  //     include max(updated_at) any more — doing so invalidated prefetch
  //     on every Q&A save, which meant heavy Q&A users saw constant
  //     re-fetches and the panel was usually mid-fetch when they opened
  //     it ("spinner every time I click the tab"). Topic add/remove still
  //     invalidates. The user can close+reopen the panel to force a
  //     refresh if they want the most current read.
  //
  // Non-blocking: the prefetch fires from a queueMicrotask so it never
  // blocks the canvas paint. Errors are swallowed silently — the tab's
  // own on-click fetch still works as a fallback.
  //
  // The panel consumes `prefetch` via props and seeds its own caches;
  // missing fields (still-in-flight or errored) just fall through to
  // on-click fetch as usual.
  const [llmModesPrefetch, setLlmModesPrefetch] =
    useState<LlmModesPrefetch | null>(null);

  const canvasTopicsSignature = useMemo((): string | null => {
    if (phase.kind !== "canvas") return null;
    const topics = phase.envelope.topics;
    if (topics.length < 1) return null;
    return `${phase.projectId}|${topics.length}`;
  }, [phase]);

  useEffect(() => {
    if (!canvasTopicsSignature) {
      // Either we're off-canvas, or we have <3 topics and are deliberately
      // skipping prefetch. Clear any previous bundle so a stale one doesn't
      // seed the panel on the next open.
      setLlmModesPrefetch(null);
      return;
    }
    const projectId =
      phase.kind === "canvas" ? phase.projectId : null;
    if (!projectId) return;
    const revisionKey = canvasTopicsSignature;
    let cancelled = false;

    // Seed the shared bundle with the revision key AND per-field
    // "pending" markers so the panel knows the prefetch is in flight
    // (don't fire a duplicate request) and can paint a loading state
    // while it waits. Each fetch flips its own pending flag off when
    // it lands.
    setLlmModesPrefetch({
      revisionKey,
      summaryPending: true,
      dedupePending: true,
      documentPending: true,
    });

    // queueMicrotask defers the fire-and-forget work off the render
    // path so the canvas paints first. setTimeout(0) would also work;
    // microtask is a tick faster and runs before the next paint after
    // layout settles, which is fine here — we're only issuing fetches.
    const kickoff = (): void => {
      if (cancelled) return;
      void (async () => {
        try {
          const res = await api.projectSummary(projectId);
          if (cancelled) return;
          setLlmModesPrefetch((prev) =>
            prev && prev.revisionKey === revisionKey
              ? { ...prev, summary: res.summary, summaryPending: false }
              : prev,
          );
        } catch {
          // On-click fetch will handle it. Clear pending so the panel
          // falls through to its own fetch path instead of spinning.
          setLlmModesPrefetch((prev) =>
            prev && prev.revisionKey === revisionKey
              ? { ...prev, summaryPending: false }
              : prev,
          );
        }
      })();
      void (async () => {
        try {
          const res = await api.projectDedupe(projectId);
          if (cancelled) return;
          const proposals: MergeProposal[] =
            res.dedupe.merge_proposals || [];
          setLlmModesPrefetch((prev) =>
            prev && prev.revisionKey === revisionKey
              ? { ...prev, dedupe: proposals, dedupePending: false }
              : prev,
          );
        } catch {
          setLlmModesPrefetch((prev) =>
            prev && prev.revisionKey === revisionKey
              ? { ...prev, dedupePending: false }
              : prev,
          );
        }
      })();
      // #094: warm up the latest Document state for this project. The
      // BE derives doc_type from project.metadata.domain so we don't
      // pass the doc_type query param. 404 → null (no doc generated
      // yet); 422 → DocumentDomainNotMappedError swallowed (career /
      // personal projects render the unmapped fallback in the panel).
      //
      // Optimization (post-/simplify audit): skip the network call
      // entirely when the project's domain isn't mapped. The BE would
      // 422 and we'd swallow — a wasted round-trip on every canvas
      // mount for career/personal projects.
      void (async () => {
        const projectRow = projects.find((p) => p.project_id === projectId);
        const projectDomain =
          (projectRow?.metadata?.domain as string | undefined) ?? null;
        if (!docTypeForDomain(projectDomain)) {
          if (cancelled) return;
          setLlmModesPrefetch((prev) =>
            prev && prev.revisionKey === revisionKey
              ? { ...prev, document: null, documentPending: false }
              : prev,
          );
          return;
        }
        try {
          const res = await api.getLatestDocument(projectId);
          if (cancelled) return;
          setLlmModesPrefetch((prev) =>
            prev && prev.revisionKey === revisionKey
              ? {
                  ...prev,
                  document: res,
                  documentPending: false,
                  documentStale: false,
                }
              : prev,
          );
        } catch (err) {
          if (cancelled) return;
          // Defense-in-depth: race between the FE pre-check and a
          // concurrent BE domain change. Swallow + clear pending.
          if (!(err instanceof DocumentDomainNotMappedError)) {
            console.warn("[Inspira] document warm-up failed", err);
          }
          setLlmModesPrefetch((prev) =>
            prev && prev.revisionKey === revisionKey
              ? { ...prev, document: null, documentPending: false }
              : prev,
          );
        }
      })();
    };

    if (typeof queueMicrotask === "function") {
      queueMicrotask(kickoff);
    } else {
      setTimeout(kickoff, 0);
    }

    return () => {
      cancelled = true;
    };
  }, [canvasTopicsSignature, phase]);


  // #094: same cleanup pattern for the Document poller, attached
  // after its declaration below (the poller block lives near the
  // Business Plan handlers). See the matching useEffect there.



  // ---------------------------------------------------------------
  // Document generation poller (#094 / Item 3 redesign)
  // ---------------------------------------------------------------
  // Mirrors the Next Steps poller (#089). Async one-shot generation
  // via gpt-5.5; the BG task takes ~30-60s. Polls every 2s, bails
  // after 5 min. Single-flight via inFlight ref. document_id-keyed
  // cleanup so a project switch / second generation abandons the
  // old poller cleanly.
  const documentPollerRef = useRef<{
    documentId: string;
    timer: ReturnType<typeof setInterval>;
    deadline: number;
    projectId: string;
    docType: DocType;
    inFlight: boolean;
  } | null>(null);

  const stopDocumentPoller = useCallback((): void => {
    const handle = documentPollerRef.current;
    if (!handle) return;
    clearInterval(handle.timer);
    documentPollerRef.current = null;
  }, []);

  const startDocumentPoller = useCallback(
    (projectId: string, documentId: string, docType: DocType): void => {
      if (
        documentPollerRef.current
        && documentPollerRef.current.documentId === documentId
      ) {
        return;
      }
      stopDocumentPoller();
      const deadline = Date.now() + 5 * 60 * 1000;
      const tick = async (): Promise<void> => {
        const handle = documentPollerRef.current;
        if (!handle || handle.documentId !== documentId) return;
        if (Date.now() > deadline) {
          stopDocumentPoller();
          toast.error(t("llm_modes.document.failed_toast"));
          return;
        }
        if (handle.inFlight) return;
        handle.inFlight = true;
        try {
          const doc = await api.getDocument(projectId, documentId);
          // Change-detection guard mirrors next-steps poller.
          setLlmModesPrefetch((prev) => {
            if (!prev) return prev;
            const cur = prev.document;
            if (
              cur
              && cur.document_id === doc.document_id
              && cur.status === doc.status
            ) {
              return prev;
            }
            return { ...prev, document: doc };
          });
          if (doc.status === "completed") {
            stopDocumentPoller();
            // Optimistic cap-used bump. The BE's Option C only
            // increments on first-gen-of-new-doc-type, so this can
            // over-count on regenerates — acceptable; cap-pill is
            // an FYI and the BE 429s any over-cap POST.
            setDocumentCapUsed((u) => u + 1);
            toast.success(t("llm_modes.document.completion_toast"), {
              actionLabel: t("llm_modes.document.completion_view_action"),
              onAction: () => {
                setLlmModesOpen(true);
              },
            });
            return;
          }
          if (doc.status === "failed") {
            stopDocumentPoller();
            toast.error(t("llm_modes.document.failed_toast"));
            return;
          }
          // Still in_progress — let the interval fire again.
        } catch (err) {
          console.warn("[Inspira] document poll tick failed", err);
        } finally {
          const cur = documentPollerRef.current;
          if (cur && cur.documentId === documentId) cur.inFlight = false;
        }
      };
      const timer = setInterval(() => void tick(), 2000);
      documentPollerRef.current = {
        documentId, timer, deadline, projectId, docType, inFlight: false,
      };
      // Fire one tick immediately so the panel can update faster than
      // the 2s interval on the (rare) sub-second BG path.
      void tick();
    },
    [stopDocumentPoller],
  );

  // Cleanup on unmount — tears the document poller down so it doesn't
  // tick into a removed listener.
  useEffect(() => {
    return () => stopDocumentPoller();
  }, [stopDocumentPoller]);

  // Flip documentStale only when there's a completed document AND the
  // flag isn't already set. Fired from the topic/decision-changed
  // listeners — a topic / decision change taints any derived doc.
  const markDocumentStale = useCallback((): void => {
    setLlmModesPrefetch((prev) => {
      if (!prev?.document) return prev;
      if (prev.document.status !== "completed") return prev;
      if (prev.documentStale) return prev;
      return { ...prev, documentStale: true };
    });
  }, []);

  /** Generate (or regenerate) the document for the active project's
   *  doc_type. POST 202 + optimistic in-flight stub + spawn poller.
   *  Branches on typed errors to surface the right toast. Always
   *  rethrows so the calling component can reset its local generating
   *  flag.
   *
   *  Optional `docTypeOverride` (#094 follow-up): when supplied, sent
   *  on POST so the BE generates as that type instead of the project-
   *  domain-derived value. The empty-state picker uses this to let
   *  the user correct a misidentified domain. The override applies
   *  only to this generation; project metadata is unchanged
   *  (persistent override tracked as #097). */
  const onDocumentGenerate = useCallback(async (
    docTypeOverride?: DocType,
  ): Promise<void> => {
    if (phase.kind !== "canvas") return;
    const projectId = phase.projectId;
    const project = projects.find((p) => p.project_id === projectId);
    const domain =
      (project?.metadata?.domain as string | undefined) ?? null;
    const derivedDocType = docTypeForDomain(domain);
    // Override wins when supplied; otherwise fall back to derivation.
    const docType: DocType | null = docTypeOverride ?? derivedDocType;
    if (!docType) {
      // Career / personal / unmapped AND no override — render the
      // friendly fallback. With an override the picker gets us here
      // so this branch only fires for the unhappy "no override on
      // unmapped domain" path.
      toast.error(t("llm_modes.document.unmapped_domain_body"));
      return;
    }
    try {
      const res = await api.generateDocument(projectId, docTypeOverride);
      // Optimistic stub so the panel paints "Generating…" right away.
      setLlmModesPrefetch((prev) =>
        prev
          ? {
              ...prev,
              document: {
                document_id: res.document_id,
                project_id: projectId,
                doc_type: docType,
                status: "in_progress",
                content: null,
                error_message: null,
                model_id: "gpt-5.5",
                plan_tier: planSlug ?? "pro",
                output_tokens_estimate: 0,
                generated_at: new Date().toISOString(),
                completed_at: null,
              },
              documentPending: false,
              documentStale: false,
            }
          : prev,
      );
      toast.info(t("llm_modes.document.generating_title"), {
        durationMs: 5000,
      });
      startDocumentPoller(projectId, res.document_id, docType);
    } catch (err) {
      if (err instanceof DocumentPlanRequiredError) {
        toast.error(t("llm_modes.document.upgrade_body"));
        throw err;
      }
      if (err instanceof DocumentCapReachedError) {
        // Pull current_count + cap from the typed error so the UI
        // reflects the BE truth.
        setDocumentCapUsed(err.currentCount);
        toast.error(
          t("llm_modes.document.cap_exceeded_toast", {
            used: String(err.currentCount),
            cap: String(err.cap),
          }),
        );
        throw err;
      }
      if (err instanceof DocumentInFlightError) {
        toast.info(t("llm_modes.document.errors.in_flight"));
        throw err;
      }
      if (err instanceof DocumentInvalidDocTypeError) {
        // Picker sent an unrecognized doc_type — should be unreachable
        // from the dropdown (which only offers VALID_DOC_TYPES) but
        // surface a friendly message if it ever fires.
        toast.error(t("llm_modes.document.errors.invalid_doc_type"));
        throw err;
      }
      if (err instanceof DocumentDomainNotMappedError) {
        toast.error(t("llm_modes.document.unmapped_domain_body"));
        throw err;
      }
      console.error("[Inspira] document generate failed", err);
      toast.error(t("llm_modes.document.failed_toast"));
      throw err;
    }
  }, [phase, planSlug, projects, startDocumentPoller]);

  /** PATCH a single section. Optimistic UI: snapshot the current
   *  document, splice the edit immediately into the prefetch, then
   *  fire the BE PATCH. On success replace with the canonical
   *  response. On failure restore the snapshot + show error toast. */
  const onPatchDocumentSection = useCallback(
    async (
      sectionId: string,
      body: DocumentSectionPatchBody,
    ): Promise<void> => {
      if (phase.kind !== "canvas") return;
      const projectId = phase.projectId;
      let snapshot: DocumentViewData | null = null;
      let documentId: string | null = null;
      // Capture snapshot + apply optimistic splice in one setState
      // call. The setState reducer reads the latest prev state, so
      // this is race-safe relative to the poller.
      setLlmModesPrefetch((prev) => {
        if (!prev?.document?.content) return prev;
        snapshot = prev.document;
        documentId = prev.document.document_id;
        const sections: DocumentSection[] = prev.document.content.sections;
        const updatedSections = sections.map((s) =>
          s.section_id === sectionId
            ? {
                ...s,
                ...(body.title !== undefined ? { title: body.title } : {}),
                ...(body.prose_markdown !== undefined
                  ? { prose_markdown: body.prose_markdown }
                  : {}),
              }
            : s,
        );
        return {
          ...prev,
          document: {
            ...prev.document,
            content: {
              ...prev.document.content,
              sections: updatedSections,
            },
          },
        };
      });
      if (!documentId) return; // No document to patch.

      try {
        const updated = await api.patchDocumentSection(
          projectId,
          documentId,
          sectionId,
          body,
        );
        // Replace the optimistic state with the canonical BE response.
        setLlmModesPrefetch((prev) =>
          prev?.document?.document_id === documentId
            ? { ...prev, document: updated }
            : prev,
        );
      } catch (err) {
        // Revert on failure.
        if (snapshot) {
          const restore: DocumentViewData = snapshot;
          setLlmModesPrefetch((prev) =>
            prev?.document?.document_id === documentId
              ? { ...prev, document: restore }
              : prev,
          );
        }
        console.error("[Inspira] document section patch failed", err);
        toast.error(t("llm_modes.document.errors.edit_failed"));
        throw err;
      }
    },
    [phase],
  );

  // ---------------------------------------------------------------
  // Refetch after mutations.
  // ---------------------------------------------------------------
  const refetch = useCallback(() => {
    setPhase((prev) => {
      if (prev.kind !== "canvas") return prev;
      const projectId = prev.projectId;
      void (async () => {
        try {
          const [topicsRes, relsRes] = await Promise.all([
            api.listTopics(projectId),
            api.listRelationships(projectId),
          ]);
          const cleanTopics = ensureNoOverlaps(topicsRes.topics);
          for (const cleaned of cleanTopics) {
            const original = topicsRes.topics.find(
              (o) => o.topic_id === cleaned.topic_id,
            );
            if (
              original &&
              (original.position_x !== cleaned.position_x ||
                original.position_y !== cleaned.position_y)
            ) {
              api
                .updateTopic(cleaned.topic_id, {
                  position_x: cleaned.position_x,
                  position_y: cleaned.position_y,
                })
                .catch((err) =>
                  console.warn("[Inspira] failed to persist overlap fix", err),
                );
            }
          }
          setPhase((cur) =>
            cur.kind === "canvas"
              ? {
                  ...cur,
                  envelope: {
                    kickoff: cur.envelope.kickoff,
                    topics: cleanTopics,
                    relationships: relsRes.relationships,
                  },
                }
              : cur,
          );
        } catch (err) {
          console.warn("[Inspira] refetch failed", err);
        }
      })();
      return prev;
    });
  }, []);

  // ---------------------------------------------------------------
  // Project switcher handlers — now dialog-driven, no more window.prompt
  // / window.confirm.
  // ---------------------------------------------------------------
  const activeProjectId: string | null =
    phase.kind === "canvas" ? phase.projectId : null;
  const activeProject = activeProjectId
    ? projects.find((p) => p.project_id === activeProjectId) ?? null
    : null;
  // #094: resolve the project's doc_type from its kickoff-inferred
  // domain. null for career / personal / unmapped — LlmModesPanel
  // renders the unmapped-domain fallback for those.
  const activeDocType: DocType | null = useMemo<DocType | null>(() => {
    const domain =
      (activeProject?.metadata?.domain as string | undefined) ?? null;
    return docTypeForDomain(domain);
  }, [activeProject]);

  const handleRenameSubmit = useCallback(
    async (nextTitle: string) => {
      if (!activeProjectId) return;
      try {
        await api.renameV2Project(activeProjectId, nextTitle);
        const refreshed = await api.listV2Projects();
        setProjects(refreshed.projects);
        setRenameDialogOpen(false);
        toast.success(t("toast.project_renamed"));
      } catch {
        toast.error(t("toast.rename_failed"));
        setRenameDialogOpen(false);
      }
    },
    [activeProjectId],
  );

  const handleDeleteProjectConfirm = useCallback(async () => {
    if (!activeProjectId) return;
    await api.deleteV2Project(activeProjectId);
    const refreshed = await api.listV2Projects();
    setProjects(refreshed.projects);
    setDeleteProjectDialogOpen(false);
    toast.success(t("toast.project_deleted"));
    // Anonymous users can never see projects_list (it'd leak the shared
    // system account's other projects); always route them to kickoff.
    if (user?.is_system || refreshed.projects.length === 0) {
      setPhase({ kind: "kickoff", error: null });
    } else {
      setPhase({ kind: "projects_list" });
    }
  }, [activeProjectId, user]);

  // Variant used from the projects list (deletes a project that isn't
  // currently open).
  const handleListRename = useCallback(
    async (projectId: string, newTitle: string) => {
      await api.renameV2Project(projectId, newTitle);
      const refreshed = await api.listV2Projects();
      setProjects(refreshed.projects);
      toast.success(t("toast.project_renamed"));
    },
    [],
  );

  const handleListDelete = useCallback(async (projectId: string) => {
    await api.deleteV2Project(projectId);
    const refreshed = await api.listV2Projects();
    setProjects(refreshed.projects);
    // Toast is owned by ProjectsListPage so it can include the Undo
    // action (Recently Deleted recovery, §7.19). Don't double-toast here.
  }, []);

  // ---------------------------------------------------------------
  // Shelves — CRUD handlers for the projects list. All routes are
  // user-scoped on the backend; anonymous users never reach this code
  // path (the anonymous-visitor guard redirects them off projects_list
  // before this component branch renders). Every handler refetches
  // shelves + projects on success so drag-drop + list views stay in
  // sync without manual state plumbing.
  // ---------------------------------------------------------------
  const refreshShelves = useCallback(async (): Promise<void> => {
    if (!user || user.is_system) {
      setShelves([]);
      return;
    }
    try {
      const res = await api.listShelves();
      setShelves(res.shelves);
    } catch (err) {
      console.warn("[Inspira] listShelves failed", err);
    }
  }, [user]);

  useEffect(() => {
    void refreshShelves();
  }, [refreshShelves]);

  const handleCreateShelf = useCallback(
    async (name: string): Promise<void> => {
      await api.createShelf(name);
      await refreshShelves();
    },
    [refreshShelves],
  );

  const handleRenameShelf = useCallback(
    async (shelfId: string, nextName: string): Promise<void> => {
      await api.renameShelf(shelfId, nextName);
      await refreshShelves();
    },
    [refreshShelves],
  );

  const handleDeleteShelf = useCallback(
    async (shelfId: string): Promise<void> => {
      await api.deleteShelf(shelfId);
      // Projects on the deleted shelf fall back to "Unfiled"; refresh
      // both so the grid reflects the reassignment immediately.
      await Promise.all([refreshShelves(), api.listV2Projects()]).then(
        ([, proj]) => setProjects(proj.projects),
      );
    },
    [refreshShelves],
  );

  const handleMoveProjectToShelf = useCallback(
    async (projectId: string, shelfIdOrNull: string | null): Promise<void> => {
      await api.moveProjectToShelf(projectId, shelfIdOrNull);
      const refreshed = await api.listV2Projects();
      setProjects(refreshed.projects);
    },
    [],
  );

  // ---------------------------------------------------------------
  // Topic delete flow — ProjectCanvas raises an `inspira:topic-delete`
  // event with the topic id + title when the user presses Delete on a
  // selected node. We show the confirm dialog here and the canvas
  // refetches when we're done.
  // ---------------------------------------------------------------
  useEffect(() => {
    const onTopicDeleteRequest = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as
        | { topicId?: string; title?: string }
        | undefined;
      if (!detail?.topicId) return;
      setPendingTopicDelete({
        topicId: detail.topicId,
        title: detail.title ?? "this topic",
      });
    };
    window.addEventListener(
      "inspira:topic-delete-request",
      onTopicDeleteRequest as EventListener,
    );
    return () =>
      window.removeEventListener(
        "inspira:topic-delete-request",
        onTopicDeleteRequest as EventListener,
      );
  }, []);

  // ProjectCanvas's "Export…" canvas-action button raises this event so
  // it stays decoupled from the dialog / navigation wiring that lives here.
  // ShortcutsProvider also dispatches it in response to Mod+E.
  //
  // Extended detail: callers can include a `format` in the event detail
  // to short-circuit straight to that export and bypass the dialog. If
  // no format is present, the picker opens so the user picks PDF / MD /
  // JSON / CSV. The ref indirection keeps the listener bound once while
  // still reading the latest handleExport closure.
  //
  // 2026-04-26 (TΛ.4): the Summary tab no longer hardcodes markdown —
  // it dispatches `{ scope: "summary" }` with no format, which falls
  // through to the dialog so the user picks any of the four formats.
  // The `scope` detail is currently informational only; the export
  // logic still produces a whole-project bundle either way.
  const handleExportRef = useRef<((f: ExportFormat) => Promise<void>) | null>(
    null,
  );
  useEffect(() => {
    const onExportRequest = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as
        | { format?: ExportFormat; scope?: string }
        | undefined;
      if (detail?.format && handleExportRef.current) {
        void handleExportRef.current(detail.format);
        return;
      }
      setExportDialogOpen(true);
    };
    window.addEventListener(
      "inspira:export-request",
      onExportRequest as EventListener,
    );
    return () =>
      window.removeEventListener(
        "inspira:export-request",
        onExportRequest as EventListener,
      );
  }, []);

  // ShortcutsProvider dispatches this in response to Mod+Shift+E. We route
  // it straight to the existing share dialog opener.
  useEffect(() => {
    const onShareRequest = () => setShareDialogOpen(true);
    window.addEventListener("inspira:share-request", onShareRequest);
    return () =>
      window.removeEventListener("inspira:share-request", onShareRequest);
  }, []);

  // TΛ.3: when the canvas-mount prefetch surfaces dedupe proposals,
  // pop them as a popup queue. The "seen" key is the revision key + a
  // hash of proposal-pair-ids so we don't re-pop the same batch on
  // every re-render. A new batch (kickoff or future post-turn dedupe)
  // gets a new key and replaces the queue.
  //
  // Skip `keep_both_but_note` proposals — those are informational FYI
  // flags from the planner ("these overlap but you should keep both
  // anyway"), not decisions that require user input. Surfacing them
  // as a popup with only a "Got it" button confused users into
  // thinking the popup was asking a question with no real options.
  // Only `merge` proposals actually need a user decision.
  useEffect(() => {
    if (!llmModesPrefetch) return;
    const allProposals = llmModesPrefetch.dedupe;
    if (!allProposals || allProposals.length === 0) return;
    const proposals = allProposals.filter(
      (p) => p.suggested_action === "merge",
    );
    if (proposals.length === 0) return;
    const batchKey =
      llmModesPrefetch.revisionKey +
      "::" +
      proposals
        .map((p) => `${p.topic_a_id}|${p.topic_b_id}`)
        .sort()
        .join(",");
    if (seenDedupeBatchRef.current === batchKey) return;
    seenDedupeBatchRef.current = batchKey;
    setDuplicateQueue(proposals);
    setDuplicateIndex(0);
  }, [llmModesPrefetch]);

  // TΛ.3 (future hook): listen for `inspira:duplicate-detected` events
  // so a post-turn dedupe call (or any future caller) can push proposals
  // into the queue without coupling to InspiraApp internals. Detail
  // shape: { proposals: MergeProposal[] }. Same `merge`-only filter
  // applies — informational `keep_both_but_note` flags don't surface.
  useEffect(() => {
    const onDuplicateDetected = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as
        | { proposals?: MergeProposal[] }
        | undefined;
      const incoming = (detail?.proposals ?? []).filter(
        (p) => p.suggested_action === "merge",
      );
      if (incoming.length === 0) return;
      setDuplicateQueue((prev) => [...prev, ...incoming]);
    };
    window.addEventListener(
      "inspira:duplicate-detected",
      onDuplicateDetected as EventListener,
    );
    return () =>
      window.removeEventListener(
        "inspira:duplicate-detected",
        onDuplicateDetected as EventListener,
      );
  }, []);

  // Stubs for shortcuts whose canvas-side behaviour hasn't landed yet.
  // ShortcutsProvider advertises these bindings (Mod+D, arrow keys) in
  // the help overlay, so we listen for them here purely to keep the
  // console trail honest if someone presses them before the real
  // handlers ship. Replacing these with the real hooks is a follow-up.
  useEffect(() => {
    const onDuplicateSelected = () => {
      // No-op until ProjectCanvas implements duplicate-of-selected.
      console.debug(
        "[Inspira] inspira:topic-duplicate-selected received (not yet implemented)",
      );
    };
    const onFocusMove = (ev: Event) => {
      // No-op until ProjectCanvas implements arrow-key navigation.
      const detail = (ev as CustomEvent).detail as
        | { direction?: string }
        | undefined;
      console.debug(
        "[Inspira] inspira:canvas-focus-move received (not yet implemented)",
        detail?.direction,
      );
    };
    window.addEventListener(
      "inspira:topic-duplicate-selected",
      onDuplicateSelected,
    );
    window.addEventListener(
      "inspira:canvas-focus-move",
      onFocusMove as EventListener,
    );
    return () => {
      window.removeEventListener(
        "inspira:topic-duplicate-selected",
        onDuplicateSelected,
      );
      window.removeEventListener(
        "inspira:canvas-focus-move",
        onFocusMove as EventListener,
      );
    };
  }, []);

  // TΛ.3 handlers: advance the queue after the user resolves a
  // proposal. `onMerge` calls the merge endpoint; `onKeepBoth` is a
  // pure dismissal. Closing the dialog skips the rest of the queue.
  const advanceDuplicateQueue = useCallback(() => {
    setDuplicateIndex((prev) => {
      const next = prev + 1;
      // If we ran past the end, clear the queue so the dialog unmounts.
      if (next >= duplicateQueue.length) {
        setDuplicateQueue([]);
        return 0;
      }
      return next;
    });
  }, [duplicateQueue.length]);

  const handleDuplicateMerge = useCallback(
    async (p: MergeProposal): Promise<void> => {
      if (phase.kind !== "canvas") {
        advanceDuplicateQueue();
        return;
      }
      try {
        await api.mergeTopics(phase.projectId, p.topic_a_id, p.topic_b_id);
        toast.success(t("llm_panel.merge_success"));
        if (typeof window !== "undefined") {
          window.dispatchEvent(new CustomEvent("inspira:topics-changed"));
          window.dispatchEvent(new CustomEvent("inspira:decisions-changed"));
        }
      } catch (err) {
        if (err instanceof Error && err.name === "ProjectNotFoundError") {
          // Existing global handler routes the user away; just bail.
          throw err;
        }
        console.error("[Inspira] proactive merge failed", err);
        toast.error(t("toast.generic_save_failed"));
      } finally {
        advanceDuplicateQueue();
      }
    },
    [phase, advanceDuplicateQueue],
  );

  const handleDuplicateKeepBoth = useCallback(
    (_p: MergeProposal): void => {
      advanceDuplicateQueue();
    },
    [advanceDuplicateQueue],
  );

  const handleDuplicateClose = useCallback(() => {
    setDuplicateQueue([]);
    setDuplicateIndex(0);
  }, []);

  // ProjectCanvas's "Summary" canvas-action button opens the LlmModesPanel
  // (plan summary + outline + deduper). Event-based so the canvas doesn't
  // need a handle on InspiraApp's state.
  // phaseKindRef avoids a stale closure: the listener subscribes once and
  // reads the current phase.kind via the ref rather than recapturing it.
  const phaseKindRef = useRef(phase.kind);
  useEffect(() => {
    phaseKindRef.current = phase.kind;
  }, [phase.kind]);
  useEffect(() => {
    const onOpenLlmModes = () => {
      if (phaseKindRef.current === "canvas") setLlmModesOpen(true);
    };
    window.addEventListener("inspira:open-llm-modes", onOpenLlmModes);
    return () =>
      window.removeEventListener("inspira:open-llm-modes", onOpenLlmModes);
  }, []);

  // Cross-topic decision router (Fix 3): when the backend reroutes a
  // Q&A-proposed decision onto a DIFFERENT topic than the active one,
  // TopicDetail fires `inspira:decisions-changed`. We refetch the
  // canvas's project-level decisions so the bullet under the correct
  // topic card updates immediately instead of waiting for the next
  // canvas refresh.
  useEffect(() => {
    const onDecisionsChanged = () => {
      if (activeProjectIdForDecisions)
        void fetchDecisions(activeProjectIdForDecisions);
      // #094: surface the warm-editorial stale banner on the Document tab.
      markDocumentStale();
    };
    window.addEventListener("inspira:decisions-changed", onDecisionsChanged);
    return () =>
      window.removeEventListener(
        "inspira:decisions-changed",
        onDecisionsChanged,
      );
  }, [
    activeProjectIdForDecisions,
    fetchDecisions,
    markDocumentStale,
  ]);

  // Dedupe merge: `inspira:topics-changed` is fired by LlmModesPanel after a
  // successful merge so the canvas refetches its topic + relationship lists.
  // Follows the same pattern as `inspira:decisions-changed` above.
  useEffect(() => {
    const onTopicsChanged = () => {
      if (phase.kind === "canvas") refetch();
      // #094: same Document staleness signal.
      markDocumentStale();
    };
    window.addEventListener("inspira:topics-changed", onTopicsChanged);
    return () =>
      window.removeEventListener("inspira:topics-changed", onTopicsChanged);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase.kind, refetch, markDocumentStale]);

  // Planner deletion suggestion: add the suggestion to the pending map so the
  // affected TopicNode renders the banner.
  useEffect(() => {
    const onSuggest = (ev: Event) => {
      const sug = (ev as CustomEvent).detail as TopicDeletionSuggestion;
      if (sug?.target_topic_id) {
        setPendingDeletionSuggestions((prev) => ({
          ...prev,
          [sug.target_topic_id]: sug,
        }));
      }
    };
    window.addEventListener(
      "inspira:topic-deletion-suggested",
      onSuggest as EventListener,
    );
    return () =>
      window.removeEventListener(
        "inspira:topic-deletion-suggested",
        onSuggest as EventListener,
      );
  }, []);

  // Timeline click: `inspira:open-topic-detail` is fired by TimelineView
  // when the user clicks a decision. We open the TopicDetail drawer on the
  // given topic, mirroring how SearchOverlay opens topics.
  useEffect(() => {
    const onOpenTopicDetail = (e: Event) => {
      const topicId = (e as CustomEvent<{ topic_id: string }>).detail
        ?.topic_id;
      if (!topicId || phase.kind !== "canvas") return;
      setPhase((prev) =>
        prev.kind === "canvas"
          ? { ...prev, openTopicId: topicId, openOriginRect: null }
          : prev,
      );
    };
    window.addEventListener("inspira:open-topic-detail", onOpenTopicDetail);
    return () =>
      window.removeEventListener(
        "inspira:open-topic-detail",
        onOpenTopicDetail,
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase.kind]);

  // Completion-banner "Next topic" click: TopicDetail fires
  // `inspira:open-next-topic` after closing the current topic. We find
  // the next sibling in the project whose status is not "fleshed_out"
  // and open it via the same setOpenTopicId path the canvas uses.
  useEffect(() => {
    const onOpenNextTopic = (e: Event) => {
      const detail = (e as CustomEvent<{ from_topic_id: string; project_id: string }>)
        .detail;
      if (!detail?.from_topic_id) return;
      if (phase.kind !== "canvas") return;
      const topics = phase.envelope.topics;
      // Find the first topic in the project that isn't the one we just
      // closed and isn't already fleshed out. Server-side status may not
      // have propagated back into the envelope yet, so we defensively
      // skip the from_topic_id regardless.
      const next = topics.find(
        (t) =>
          t.topic_id !== detail.from_topic_id &&
          t.status !== "fleshed_out",
      );
      if (!next) return;
      setPhase((prev) =>
        prev.kind === "canvas"
          ? { ...prev, openTopicId: next.topic_id, openOriginRect: null }
          : prev,
      );
    };
    window.addEventListener("inspira:open-next-topic", onOpenNextTopic);
    return () =>
      window.removeEventListener(
        "inspira:open-next-topic",
        onOpenNextTopic,
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase.kind]);

  const handleTopicDeleteConfirm = useCallback(async () => {
    if (!pendingTopicDelete) return;
    try {
      await api.deleteTopic(pendingTopicDelete.topicId);
      toast.success(t("toast.topic_deleted"));
    } catch (err) {
      console.error("[Inspira] failed to delete topic", err);
      toast.error(t("toast.topic_delete_failed"));
    } finally {
      setPendingTopicDelete(null);
      refetch();
    }
  }, [pendingTopicDelete, refetch]);

  // ---------------------------------------------------------------
  // Share / Export — both operate on the currently-open project.
  // ---------------------------------------------------------------
  const handleShareGenerate = useCallback(async (): Promise<string> => {
    if (phase.kind !== "canvas") throw new Error("No project open.");
    const res = await api.generateShareLink(phase.projectId);
    // The backend returns the full https://tryinspira.com/shared/<token> URL.
    const url = res.url;
    setActiveShareUrl(url);
    return url;
  }, [phase]);

  const handleShareRevoke = useCallback(async (): Promise<void> => {
    if (phase.kind !== "canvas") throw new Error("No project open.");
    await api.revokeShareLink(phase.projectId);
    setActiveShareUrl(null);
  }, [phase]);

  // Load the active share link when the dialog opens so `currentLink`
  // is pre-populated for projects that already have a live link.
  useEffect(() => {
    if (!shareDialogOpen || phase.kind !== "canvas") return;
    let cancelled = false;
    api.getShareLink(phase.projectId)
      .then((res) => {
        if (cancelled) return;
        if (res.share_link) {
          // Reconstruct the full URL from the token.
          setActiveShareUrl(
            `https://tryinspira.com/shared/${res.share_link.token}`,
          );
        } else {
          setActiveShareUrl(null);
        }
      })
      .catch(() => {
        if (!cancelled) setActiveShareUrl(null);
      });
    return () => { cancelled = true; };
  }, [shareDialogOpen, phase]);

  const handleExport = useCallback(
    async (format: ExportFormat) => {
      if (phase.kind !== "canvas") {
        toast.warning(t("toast.open_project_first"));
        return;
      }
      const topics = phase.envelope.topics;
      const projectTitle = activeProject?.title ?? "Inspira Project";
      const slug = slugifyForFilename(projectTitle);

      if (format === "markdown") {
        // Project-level markdown: render each topic with topicToMarkdown
        // and concatenate under a cover heading. Q&A threads are fetched
        // on demand so we have the same fidelity as the single-topic
        // Copy-as-Markdown button.
        const turnEntries = await Promise.all(
          topics.map(async (t): Promise<[string, QnaTurn[]]> => {
            try {
              const res = await api.listTurns(t.topic_id);
              return [t.topic_id, res.turns];
            } catch {
              return [t.topic_id, []];
            }
          }),
        );
        const turnsByTopicId = new Map<string, QnaTurn[]>(turnEntries);
        const parts: string[] = [];
        parts.push(`# ${projectTitle}`);
        parts.push("");
        parts.push(`_Exported ${formatDate(new Date())}_`);
        parts.push("");
        for (const t of topics) {
          const tDecisions = decisionsByTopicId.get(t.topic_id) ?? [];
          const tTurns = turnsByTopicId.get(t.topic_id) ?? [];
          parts.push(topicToMarkdown(t, tTurns, tDecisions));
          parts.push("");
        }
        const md = parts.join("\n");
        downloadBlob(new Blob([md], { type: "text/markdown" }), `${slug}.md`);
        setExportDialogOpen(false);
        toast.success(t("toast.markdown_downloaded"));
        void api.logExport(phase.projectId, "markdown");
        return;
      }

      if (format === "pdf") {
        // Soft-cap PDF exports: html2canvas routinely OOMs on projects with
        // 50+ topics on low-memory devices. Warn the user before we kick off
        // the render; if they cancel, abort cleanly.
        if (topics.length > 50) {
          const proceed =
            typeof window !== "undefined" && typeof window.confirm === "function"
              ? window.confirm(
                  t("toast.pdf_large_warning", { count: topics.length }),
                )
              : true;
          if (!proceed) {
            return;
          }
        }
        try {
          const turnEntries = await Promise.all(
            topics.map(async (tp): Promise<[string, QnaTurn[]]> => {
              try {
                const res = await api.listTurns(tp.topic_id);
                return [tp.topic_id, res.turns];
              } catch (err) {
                console.warn(
                  "[Inspira] failed to fetch turns for topic during PDF export",
                  tp.topic_id,
                  err,
                );
                return [tp.topic_id, []];
              }
            }),
          );
          const turnsByTopicId = new Map<string, QnaTurn[]>(turnEntries);
          const html = projectToHtml({
            projectTitle,
            topics,
            turnsByTopicId,
            decisionsByTopicId,
            hasFullContent: true,
          });
          // Four compounding failure modes had to be untangled to stop
          // html2canvas (which html2pdf wraps) from emitting a blank PDF:
          //
          //   1. `left: -10000px` returns blank — Chromium places the
          //      layout but never paints the backing buffer offscreen.
          //   2. `visibility: hidden` short-circuits html2canvas's DOM
          //      traversal; it explicitly skips invisible nodes.
          //   3. `opacity: 0` — html2canvas DOES respect opacity when
          //      compositing, so an opacity-0 container rasters as a
          //      transparent canvas → blank PDF. (This was the regression
          //      my previous attempt introduced.)
          //   4. `container.innerHTML = <!doctype html>...</html>` strips
          //      the html/head/body wrappers AND the <style> in head
          //      often doesn't take, so content rendered unstyled.
          //
          // Working recipe: DOMParser-extract head styles + body children
          // into a fully visible container, and hide the container from
          // the user by wrapping it in a position:fixed, 0×0, overflow:
          // hidden clipper. html2canvas uses getBoundingClientRect on the
          // container itself — clipping by an ancestor doesn't affect the
          // returned rect — so it rasters at full 794px width while the
          // user sees nothing. The container is removed after capture.
          const doc = new DOMParser().parseFromString(html, "text/html");
          const container = document.createElement("div");
          doc.head.querySelectorAll("style").forEach((styleNode) => {
            container.appendChild(styleNode.cloneNode(true));
          });
          while (doc.body.firstChild) {
            container.appendChild(doc.body.firstChild);
          }
          container.style.width = "794px";
          container.style.background = "var(--paper)";
          // Parent clipper — 0×0, fixed, clips the child visually without
          // hiding it from html2canvas's render pipeline.
          const clipper = document.createElement("div");
          clipper.style.position = "fixed";
          clipper.style.top = "0";
          clipper.style.left = "0";
          clipper.style.width = "0";
          clipper.style.height = "0";
          clipper.style.overflow = "hidden";
          clipper.style.pointerEvents = "none";
          clipper.style.zIndex = "-1";
          clipper.appendChild(container);
          document.body.appendChild(clipper);
          // Force a reflow so html2canvas sees the committed layout.
          void container.getBoundingClientRect();
          try {
            const { default: html2pdf } = await import("html2pdf.js");
            await html2pdf()
              .set({
                margin: [12, 12, 12, 12],
                filename: `${slug}.pdf`,
                image: { type: "jpeg", quality: 0.96 },
                // useCORS + allowTaint keep embedded assets from failing.
                // foreignObjectRendering: false avoids a Firefox-specific
                // blank-output path in some Chromium versions too.
                html2canvas: {
                  scale: 2,
                  backgroundColor: "#F5F0E6",
                  useCORS: true,
                  allowTaint: true,
                  foreignObjectRendering: false,
                  logging: false,
                },
                jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
              })
              .from(container)
              .save();
          } finally {
            clipper.remove();
          }
          setExportDialogOpen(false);
          toast.success(t("toast.pdf_downloaded"));
          void api.logExport(phase.projectId, "pdf");
        } catch (err) {
          console.error("[Inspira] PDF export failed", err);
          toast.error(t("toast.pdf_failed"));
        }
        return;
      }

      if (format === "txt") {
        const turnEntries = await Promise.all(
          topics.map(async (tp): Promise<[string, QnaTurn[]]> => {
            try {
              const res = await api.listTurns(tp.topic_id);
              return [tp.topic_id, res.turns];
            } catch {
              return [tp.topic_id, []];
            }
          }),
        );
        const turnsByTopicId = new Map<string, QnaTurn[]>(turnEntries);
        const txt = projectToPlainText(projectTitle, topics, decisionsByTopicId, turnsByTopicId);
        downloadBlob(new Blob([txt], { type: "text/plain" }), `${slug}.txt`);
        setExportDialogOpen(false);
        toast.success(t("toast.txt_downloaded"));
        return;
      }

      if (format === "json") {
        // JSON and CSV share a need for flat decisions + turns arrays. We
        // fetch turns on demand per-topic (same pattern the markdown / pdf
        // branches use) and reuse the in-memory decisions state.
        if (!activeProject) {
          toast.warning(t("toast.open_project_first"));
          return;
        }
        const turnEntries = await Promise.all(
          topics.map(async (tp): Promise<QnaTurn[]> => {
            try {
              const res = await api.listTurns(tp.topic_id);
              return res.turns;
            } catch {
              return [];
            }
          }),
        );
        const flatTurns: QnaTurn[] = turnEntries.flat();
        exportToJson(
          activeProject,
          topics,
          phase.envelope.relationships,
          decisions,
          flatTurns,
        );
        setExportDialogOpen(false);
        toast.success(t("toast.json_downloaded"));
        void api.logExport(phase.projectId, "json");
        return;
      }

      if (format === "csv") {
        if (!activeProject) {
          toast.warning(t("toast.open_project_first"));
          return;
        }
        // CSV export intentionally skips decisions / turns (see export.ts
        // header comment), but we pass them through for parity so any
        // future column-addition inside exportToCsv picks them up.
        const turnEntries = await Promise.all(
          topics.map(async (tp): Promise<QnaTurn[]> => {
            try {
              const res = await api.listTurns(tp.topic_id);
              return res.turns;
            } catch {
              return [];
            }
          }),
        );
        const flatTurns: QnaTurn[] = turnEntries.flat();
        try {
          await exportToCsv(
            activeProject,
            topics,
            phase.envelope.relationships,
            decisions,
            flatTurns,
          );
          setExportDialogOpen(false);
          toast.success(t("toast.csv_downloaded"));
          void api.logExport(phase.projectId, "csv");
        } catch (err) {
          console.error("[Inspira] CSV export failed", err);
          toast.error(t("toast.csv_failed"));
        }
        return;
      }

      // "share" and "print" are handled inside ExportOptionsDialog itself
      // (share closes Export then opens Share; print calls window.print()).
      // They should never reach this callback, but guard defensively.
    },
    [activeProject, decisions, decisionsByTopicId, phase, t, api],
  );

  // Keep the ref used by the `inspira:export-request` listener in sync.
  // See the listener's comment for why the indirection exists.
  useEffect(() => {
    handleExportRef.current = handleExport;
  }, [handleExport]);

  // ---------------------------------------------------------------
  // Navigation helpers — used from the command palette + top bar.
  // ---------------------------------------------------------------
  //
  // The projects_list phase shows the signed-in user's project grid.
  // For an anonymous visitor, that grid is the SHARED system-fallback
  // account's grid — effectively other visitors' leftover projects.
  // Never let an anonymous user land there; route them to kickoff so
  // they hit the auth gate on submit.
  const goToProjectsList = useCallback(() => {
    if (user?.is_system) {
      setPhase({ kind: "kickoff", error: null });
      return;
    }
    setPhase({ kind: "projects_list" });
  }, [user]);

  const openAccountSettings = useCallback(() => {
    setPhase((prev) =>
      prev.kind === "account_settings"
        ? prev
        : { kind: "account_settings", previous: prev },
    );
  }, []);

  const closeAccountSettings = useCallback(() => {
    setPhase((prev) =>
      prev.kind === "account_settings" ? prev.previous : prev,
    );
  }, []);

  // Anonymous-user signup gate. When an anon visitor tries to do
  // something that implies "I want more than one canvas" or "I want to
  // share this with others" — a second project, share, export — we
  // pop the signup modal instead of running the action. Context
  // message is surfaced as a toast so the user knows WHY the modal
  // opened, rather than seeing it appear from nowhere.
  const promptSignupForAnonAction = useCallback(
    (contextMessage: string) => {
      toast.info(contextMessage);
      setAuthInitialMode("signup");
      setAuthOpen(true);
    },
    [],
  );

  const startNewProject = useCallback(() => {
    if (user?.is_system) {
      promptSignupForAnonAction(
        t("banner.anonymous.signup_context_new_project"),
      );
      return;
    }
    setPhase({ kind: "kickoff", error: null });
  }, [user, promptSignupForAnonAction]);

  const handleSuggestStart = useCallback((idea: string) => {
    setPhase({ kind: "kickoff", error: null, initialIdea: idea });
  }, []);

  const handleLogout = useCallback(async () => {
    await api.logout().catch(() => undefined);
    // Reload to reset all state cleanly.
    window.location.reload();
  }, []);

  // ---------------------------------------------------------------
  // Keyboard shortcuts
  // ---------------------------------------------------------------
  //
  // Centralized here so one place can reason about "is a topic detail
  // open?" / "is a modal on top?" before dispatching. Handlers that
  // target the canvas (Tidy, focus composer) coordinate with
  // ProjectCanvas via window events / DOM queries so the coupling
  // stays loose.

  const detailOpen = phase.kind === "canvas" && phase.openTopicId !== null;

  const shortcutBindings = useMemo<ShortcutBinding[]>(() => {
    return [
      // ---- Global --------------------------------------------------
      {
        combo: "?",
        description: "Show this keyboard-shortcut cheat sheet",
        group: "Global",
        handler: (event) => {
          event.preventDefault();
          setHelpOpen((v) => !v);
        },
      },
      {
        combo: "Esc",
        description: "Close the top-most modal (shortcut overlay, then topic detail)",
        group: "Global",
        // Let TopicDetail own its own Esc handling — only react here
        // when the shortcut overlay is on top.
        handler: () => {
          if (helpOpen) {
            setHelpOpen(false);
          }
          // When detail is open but help is closed, TopicDetail's
          // internal keydown listener takes over — we intentionally
          // do not call setPhase here to avoid double-dispatch.
        },
      },
      {
        combo: "Mod+K",
        description: "Open command palette",
        group: "Global",
        handler: (event) => {
          event.preventDefault();
          setPaletteOpen(true);
        },
      },

      // ---- Canvas --------------------------------------------------
      //
      // Canvas shortcuts are disabled while a topic detail is open.
      // The hook already skips shortcuts when focus is inside an
      // input/textarea/contenteditable; the detailOpen guard on top
      // of that makes modal behavior predictable.
      {
        combo: "n",
        description: "Start a new project",
        group: "Canvas",
        handler: (event) => {
          if (detailOpen || helpOpen) return;
          if (phase.kind !== "canvas") return;
          event.preventDefault();
          setPhase({ kind: "kickoff", error: null });
        },
      },
      {
        combo: "t",
        description: "Tidy the canvas (auto-layout)",
        group: "Canvas",
        handler: (event) => {
          if (detailOpen || helpOpen) return;
          if (phase.kind !== "canvas") return;
          event.preventDefault();
          window.dispatchEvent(new CustomEvent("inspira:canvas-tidy"));
        },
      },
      {
        combo: "/",
        description: "Open cross-project search",
        group: "Canvas",
        handler: (event) => {
          if (detailOpen || helpOpen) return;
          if (phase.kind !== "canvas") return;
          event.preventDefault();
          setSearchOpen(true);
        },
      },

      // ---- Topic detail -------------------------------------------
      //
      // The detail view owns its own Esc key binding; we advertise it
      // here so it shows up in the cheat sheet. The handler is a no-op
      // — TopicDetail's internal listener fires in parallel.
      {
        combo: "Esc",
        description: "Close the topic detail view",
        group: "Topic detail",
        handler: () => {
          /* handled inside TopicDetail */
        },
      },
    ];
  }, [detailOpen, helpOpen, phase.kind]);

  useKeyboardShortcuts(shortcutBindings);

  // ---------------------------------------------------------------
  // Command palette commands
  // ---------------------------------------------------------------
  const paletteCommands = useMemo<Command[]>(() => {
    const commands: Command[] = [];

    // Navigation
    commands.push({
      id: "nav.projects",
      label: t("inspira.palette.go_to_projects"),
      group: "Navigate",
      keywords: ["list", "home", "grid"],
      run: () => goToProjectsList(),
    });
    commands.push({
      id: "nav.account",
      label: t("inspira.palette.account_settings"),
      group: "Navigate",
      keywords: ["profile", "password", "theme"],
      run: () => openAccountSettings(),
    });
    commands.push({
      id: "nav.help",
      label: t("inspira.palette.keyboard_shortcuts"),
      group: "Navigate",
      hint: "?",
      keywords: ["help", "cheatsheet"],
      run: () => setHelpOpen(true),
    });
    // Project actions
    commands.push({
      id: "project.new",
      label: t("inspira.palette.new_project"),
      group: "Project",
      hint: "N",
      keywords: ["create", "start"],
      run: () => startNewProject(),
    });
    if (phase.kind === "canvas") {
      commands.push({
        id: "project.rename",
        label: t("inspira.palette.rename_project"),
        group: "Project",
        keywords: ["edit", "title"],
        run: () => setRenameDialogOpen(true),
      });
      commands.push({
        id: "project.delete",
        label: t("inspira.palette.delete_project"),
        group: "Project",
        keywords: ["remove", "trash"],
        run: () => setDeleteProjectDialogOpen(true),
      });
      commands.push({
        id: "project.share",
        label: t("inspira.palette.share_project"),
        group: "Project",
        keywords: ["link", "collaborate"],
        run: () => setShareDialogOpen(true),
      });
      commands.push({
        id: "project.export",
        label: t("inspira.palette.export_project"),
        group: "Project",
        keywords: ["download", "pdf", "markdown"],
        run: () => setExportDialogOpen(true),
      });
    }

    // Search
    commands.push({
      id: "search.open",
      label: t("inspira.palette.search"),
      group: "Search",
      hint: "/",
      keywords: ["find", "filter", "lookup"],
      run: () => setSearchOpen(true),
    });

    // Account
    if (user && !user.is_system) {
      commands.push({
        id: "account.logout",
        label: t("inspira.palette.log_out"),
        group: "Account",
        keywords: ["sign out"],
        run: () => void handleLogout(),
      });
    } else {
      commands.push({
        id: "account.login",
        label: t("inspira.palette.sign_in"),
        group: "Account",
        keywords: ["log in"],
        run: () => setAuthOpen(true),
      });
    }

    // Open-topic entries for the currently-active project
    if (phase.kind === "canvas") {
      for (const topic of phase.envelope.topics) {
        commands.push({
          id: `topic.open.${topic.topic_id}`,
          label: t("inspira.palette.open_topic", { title: topic.title }),
          group: "Topics",
          keywords: [topic.title],
          run: () =>
            setPhase((prev) =>
              prev.kind === "canvas"
                ? { ...prev, openTopicId: topic.topic_id, openOriginRect: null }
                : prev,
            ),
        });
      }
    }

    return commands;
  }, [
    goToProjectsList,
    openAccountSettings,
    startNewProject,
    handleLogout,
    phase,
    user,
  ]);

  // ---------------------------------------------------------------
  // Global overlays + mounts — rendered inside a fragment so every
  // phase branch can share them consistently.
  // ---------------------------------------------------------------
  const GlobalOverlays = (
    <>
      {/* Registers the app-level shortcut bindings that dispatch custom
          window events (save-intercept, export, share, duplicate,
          arrow-key canvas focus). Returns null — purely a listener. */}
      <ShortcutsProvider />
      <OfflineBanner />
<ShortcutHelpOverlay open={helpOpen} onClose={() => setHelpOpen(false)} />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={paletteCommands}
      />
      <SearchOverlay
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        activeProjectTopics={
          phase.kind === "canvas" ? phase.envelope.topics : undefined
        }
        activeProjectId={phase.kind === "canvas" ? phase.projectId : undefined}
        projects={projects}
        onOpenProject={(projectId) => {
          setSearchOpen(false);
          void openProject(projectId);
        }}
        onOpenTopic={(projectId, topicId) => {
          setSearchOpen(false);
          if (phase.kind === "canvas" && phase.projectId === projectId) {
            setPhase((prev) =>
              prev.kind === "canvas"
                ? { ...prev, openTopicId: topicId, openOriginRect: null }
                : prev,
            );
          } else {
            // Open the project first, then the topic, once it's loaded.
            void (async () => {
              await openProject(projectId);
              setPhase((prev) =>
                prev.kind === "canvas" && prev.projectId === projectId
                  ? { ...prev, openTopicId: topicId, openOriginRect: null }
                  : prev,
              );
            })();
          }
        }}
      />
      <SessionExpiredModal
        open={sessionExpiredOpen}
        onSignIn={() => {
          setSessionExpiredOpen(false);
          setAuthOpen(true);
        }}
        onDismiss={() => setSessionExpiredOpen(false)}
      />
      <AuthPanel
        open={authOpen}
        initialMode={authInitialMode}
        onClose={() => setAuthOpen(false)}
        onAuthenticated={(u) => {
          // Snapshot the caller's prior identity BEFORE we swap in the
          // signed-in user state. If the prior id was an anonymous
          // session, we'll transfer its canvases to the new account
          // below — the backend stamped ``previous_anon_user_id`` on
          // the new session cookie at signup/login, so the transfer
          // endpoint can verify the claim.
          const priorUser = user;
          setUser(u);
          setAuthOpen(false);
          void (async () => {
            try {
              // Transfer anonymous canvases FIRST so the project list
              // refetch below returns the merged view in one call.
              const priorAnonId = priorUser?.is_system
                && priorUser.user_id.startsWith("user-anon-")
                ? priorUser.user_id
                : null;
              if (priorAnonId) {
                try {
                  const transfer =
                    await api.transferAnonymousProjects(priorAnonId);
                  if (transfer.transferred > 0) {
                    toast.success(t("toast.anonymous_saved"));
                  }
                } catch (err) {
                  console.warn(
                    "[Inspira] anonymous transfer failed", err,
                  );
                }
              }
              const refreshed = await api.listV2Projects();
              setProjects(refreshed.projects);

              // Resume-after-signup: if the user tried to kickoff /
              // pick a template / import markdown BEFORE signing up,
              // we stashed their input. Now that they're authenticated,
              // run the deferred action with the stashed payload.
              let resumed = false;
              try {
                const pendingIdea = window.localStorage.getItem(
                  PENDING_KICKOFF_IDEA_KEY,
                );
                const pendingAttachmentsRaw = window.localStorage.getItem(
                  PENDING_KICKOFF_ATTACHMENTS_KEY,
                );
                const pendingTemplate = window.localStorage.getItem(
                  PENDING_TEMPLATE_SLUG_KEY,
                );
                const pendingMarkdown = window.localStorage.getItem(
                  PENDING_MARKDOWN_KEY,
                );

                if (pendingTemplate) {
                  window.localStorage.removeItem(PENDING_TEMPLATE_SLUG_KEY);
                  resumed = true;
                  void handleTemplateKickoff(pendingTemplate);
                } else if (pendingMarkdown) {
                  window.localStorage.removeItem(PENDING_MARKDOWN_KEY);
                  resumed = true;
                  void handleImportMarkdown(pendingMarkdown);
                } else if (pendingIdea && pendingIdea.trim().length > 0) {
                  window.localStorage.removeItem(PENDING_KICKOFF_IDEA_KEY);
                  window.localStorage.removeItem(
                    PENDING_KICKOFF_ATTACHMENTS_KEY,
                  );
                  let attachments: AttachedSource[] = [];
                  if (pendingAttachmentsRaw) {
                    try {
                      attachments = JSON.parse(
                        pendingAttachmentsRaw,
                      ) as AttachedSource[];
                    } catch {
                      attachments = [];
                    }
                  }
                  resumed = true;
                  void handleKickoff(pendingIdea, attachments);
                }
              } catch {
                // localStorage access failure — fall through to default.
              }

              if (!resumed) {
                setPhase(
                  refreshed.projects.length === 0
                    ? { kind: "kickoff", error: null }
                    : { kind: "projects_list" },
                );
              }
            } catch (err) {
              console.warn("[Inspira] post-auth refresh failed", err);
            }
          })();
        }}
      />
      <RenameProjectDialog
        open={renameDialogOpen}
        currentTitle={activeProject?.title ?? ""}
        onClose={() => setRenameDialogOpen(false)}
        onSubmit={handleRenameSubmit}
      />
      <DeleteConfirmDialog
        open={deleteProjectDialogOpen}
        itemType="project"
        itemName={activeProject?.title ?? "this project"}
        consequences={t("inspira.delete.project_consequences")}
        onClose={() => setDeleteProjectDialogOpen(false)}
        onConfirm={handleDeleteProjectConfirm}
      />
      <DeleteConfirmDialog
        open={pendingTopicDelete !== null}
        itemType="topic"
        itemName={pendingTopicDelete?.title ?? "this topic"}
        consequences={t("inspira.delete.topic_consequences")}
        onClose={() => setPendingTopicDelete(null)}
        onConfirm={handleTopicDeleteConfirm}
      />
      <ShareProjectDialog
        open={shareDialogOpen}
        currentLink={activeShareUrl}
        onClose={() => setShareDialogOpen(false)}
        onGenerateLink={handleShareGenerate}
        onRevoke={handleShareRevoke}
      />
      <ExportOptionsDialog
        open={exportDialogOpen}
        onClose={() => setExportDialogOpen(false)}
        onExport={handleExport}
        onOpenShare={() => { setExportDialogOpen(false); setShareDialogOpen(true); }}
      />
      <ExportModalsHost />
      {importJsonDialogOpen ? (
        <Suspense fallback={null}>
          <ImportFromJsonDialog
            open={importJsonDialogOpen}
            onClose={() => setImportJsonDialogOpen(false)}
            onSubmit={handleImportJson}
          />
        </Suspense>
      ) : null}
      <LegalOverlay
        open={legalOverlay !== null}
        kind={legalOverlay ?? "privacy"}
        onClose={() => setLegalOverlay(null)}
      />
      {phase.kind === "canvas" ? (
        <LlmModesPanel
          open={llmModesOpen}
          projectId={phase.projectId}
          projectTitle={
            projects.find((p) => p.project_id === phase.projectId)?.title
          }
          onClose={() => {
            setLlmModesOpen(false);
          }}
          topicsById={
            new Map(
              phase.envelope.topics.map((t) => [
                t.topic_id,
                { title: t.title, icon: t.icon },
              ]),
            )
          }
          prefetch={llmModesPrefetch}
          docType={activeDocType}
          documentCapUsed={documentCapUsed}
          documentCapLimit={documentCapLimit}
          onDocumentGenerate={onDocumentGenerate}
          onPatchDocumentSection={onPatchDocumentSection}
        />
      ) : null}
      {/* TΛ.3: proactive duplicate-detection popup. Replaces the old
          Planner Views > Duplicates tab — the user sees one merge
          candidate at a time and either accepts the merge or dismisses. */}
      {phase.kind === "canvas" && duplicateQueue.length > 0 ? (
        <DuplicateConflictDialog
          proposal={duplicateQueue[duplicateIndex] ?? null}
          currentIndex={duplicateIndex + 1}
          totalCount={duplicateQueue.length}
          topicsById={
            new Map(
              phase.envelope.topics.map((t) => [
                t.topic_id,
                { title: t.title, icon: t.icon },
              ]),
            )
          }
          onMerge={handleDuplicateMerge}
          onKeepBoth={handleDuplicateKeepBoth}
          onClose={handleDuplicateClose}
        />
      ) : null}
      {/* PR 2: voice session modal + upgrade dialog removed with the
          rest of the voice feature. */}
    </>
  );

  // ---------------------------------------------------------------
  // Renders
  // ---------------------------------------------------------------

  // Account settings overlays on top of whatever the user was looking at.
  // We render the previous phase underneath so the top bar / canvas
  // continue to exist (they're covered by the overlay's z-index but still
  // preserve their in-memory state).
  if (phase.kind === "account_settings") {
    return (
      <>
        {/* Render the previous phase as the underlay. We mount an
            "account_settings"-less clone so React doesn't infinitely
            recurse. */}
        <InspiraPhaseRender
          phase={phase.previous}
          projects={projects}
          user={user}
          openProject={openProject}
          onNewProject={startNewProject}
          onRenameActive={() => setRenameDialogOpen(true)}
          onDeleteActive={() => setDeleteProjectDialogOpen(true)}
          onGoToProjectsList={goToProjectsList}
          onOpenAccountSettings={openAccountSettings}
          onLogout={handleLogout}
          onOpenAuth={(mode) => {
            setAuthInitialMode(mode ?? "login");
            setAuthOpen(true);
          }}
          envelope={
            phase.previous.kind === "canvas" ? phase.previous.envelope : null
          }
          openTopicId={
            phase.previous.kind === "canvas"
              ? phase.previous.openTopicId
              : null
          }
          openOriginRect={
            phase.previous.kind === "canvas"
              ? phase.previous.openOriginRect
              : null
          }
          onOpenTopic={(topicId, rect) =>
            setPhase({
              kind: "account_settings",
              previous:
                phase.previous.kind === "canvas"
                  ? {
                      ...phase.previous,
                      openTopicId: topicId,
                      openOriginRect: rect,
                    }
                  : phase.previous,
            })
          }
          onCloseTopic={() =>
            setPhase({
              kind: "account_settings",
              previous:
                phase.previous.kind === "canvas"
                  ? {
                      ...phase.previous,
                      openTopicId: null,
                      openOriginRect: null,
                    }
                  : phase.previous,
            })
          }
          decisionsByTopicId={decisionsByTopicId}
          onRefetch={refetch}
          onKickoff={handleKickoff}
          onSelectTemplate={handleTemplateKickoff}
          onImportMarkdown={handleImportMarkdown}
          onOpenImportJson={() => setImportJsonDialogOpen(true)}
          onOpenShare={() => setShareDialogOpen(true)}
          onOpenExport={() => setExportDialogOpen(true)}
          onListRename={handleListRename}
          onListDelete={handleListDelete}
          fetchDecisions={fetchDecisions}
          phaseSetter={setPhase}
          onOpenLegal={(kind) => setLegalOverlay(kind)}
          shelves={shelves}
          onCreateShelf={handleCreateShelf}
          onRenameShelf={handleRenameShelf}
          onDeleteShelf={handleDeleteShelf}
          onMoveProjectToShelf={handleMoveProjectToShelf}
          onSuggestStart={handleSuggestStart}
          pendingDeletionSuggestions={pendingDeletionSuggestions}
          onDismissDeletionSuggestion={(topicId) =>
            setPendingDeletionSuggestions((prev) => {
              const next = { ...prev };
              delete next[topicId];
              return next;
            })
          }
          onConfirmDeletion={(topicId) => {
            const topic = (phase.kind === "account_settings" && phase.previous.kind === "canvas"
              ? phase.previous.envelope.topics
              : []).find((t) => t.topic_id === topicId);
            if (topic) setPendingTopicDelete({ topicId, title: topic.title });
            setPendingDeletionSuggestions((prev) => {
              const next = { ...prev };
              delete next[topicId];
              return next;
            });
          }}
        />
        {user ? (
          <Suspense fallback={null}>
            <AccountSettingsPage
              user={user}
              onClose={closeAccountSettings}
              onProfileUpdated={(updated) => setUser(updated)}
            />
          </Suspense>
        ) : null}
        {GlobalOverlays}
      </>
    );
  }

  return (
    <>
      <InspiraPhaseRender
        phase={phase}
        projects={projects}
        user={user}
        openProject={openProject}
        onNewProject={startNewProject}
        onRenameActive={() => setRenameDialogOpen(true)}
        onDeleteActive={() => setDeleteProjectDialogOpen(true)}
        onGoToProjectsList={goToProjectsList}
        onOpenAccountSettings={openAccountSettings}
        onLogout={handleLogout}
        onOpenAuth={() => setAuthOpen(true)}
        envelope={phase.kind === "canvas" ? phase.envelope : null}
        openTopicId={phase.kind === "canvas" ? phase.openTopicId : null}
        openOriginRect={phase.kind === "canvas" ? phase.openOriginRect : null}
        onOpenTopic={(topicId, rect) =>
          setPhase((prev) =>
            prev.kind === "canvas"
              ? { ...prev, openTopicId: topicId, openOriginRect: rect }
              : prev,
          )
        }
        onCloseTopic={() => {
          setPhase((prev) =>
            prev.kind === "canvas"
              ? { ...prev, openTopicId: null, openOriginRect: null }
              : prev,
          );
          if (phase.kind === "canvas") void fetchDecisions(phase.projectId);
        }}
        decisionsByTopicId={decisionsByTopicId}
        onRefetch={() => {
          refetch();
          if (phase.kind === "canvas") void fetchDecisions(phase.projectId);
        }}
        onKickoff={handleKickoff}
        onSelectTemplate={handleTemplateKickoff}
        onImportMarkdown={handleImportMarkdown}
        onOpenImportJson={() => setImportJsonDialogOpen(true)}
        onOpenShare={() => setShareDialogOpen(true)}
        onOpenExport={() => setExportDialogOpen(true)}
        onListRename={handleListRename}
        onListDelete={handleListDelete}
        fetchDecisions={fetchDecisions}
        phaseSetter={setPhase}
        onOpenLegal={(kind) => setLegalOverlay(kind)}
        shelves={shelves}
        onCreateShelf={handleCreateShelf}
        onRenameShelf={handleRenameShelf}
        onDeleteShelf={handleDeleteShelf}
        onMoveProjectToShelf={handleMoveProjectToShelf}
        onSuggestStart={handleSuggestStart}
        pendingDeletionSuggestions={pendingDeletionSuggestions}
        onDismissDeletionSuggestion={(topicId) =>
          setPendingDeletionSuggestions((prev) => {
            const next = { ...prev };
            delete next[topicId];
            return next;
          })
        }
        onConfirmDeletion={(topicId) => {
          const topic = (phase.kind === "canvas" ? phase.envelope.topics : []).find(
            (t) => t.topic_id === topicId,
          );
          if (topic) setPendingTopicDelete({ topicId, title: topic.title });
          setPendingDeletionSuggestions((prev) => {
            const next = { ...prev };
            delete next[topicId];
            return next;
          });
        }}
      />
      {GlobalOverlays}
    </>
  );
}

// ---------------------------------------------------------------------
// InspiraPhaseRender — the per-phase renderer. Extracted so the
// account-settings overlay can render it underneath itself.
// ---------------------------------------------------------------------

type InspiraPhaseRenderProps = {
  phase: Phase;
  projects: V2Project[];
  user: AuthedUser | null;
  openProject: (projectId: string) => Promise<void>;
  onNewProject: () => void;
  onRenameActive: () => void;
  onDeleteActive: () => void;
  onGoToProjectsList: () => void;
  onOpenAccountSettings: () => void;
  onLogout: () => Promise<void>;
  // Optional mode. If omitted, opens whatever the panel's internal state
  // was last set to — callers that care (AnonymousSaveBanner CTA, account
  // menus) pass an explicit mode to route to login vs signup deterministically.
  onOpenAuth: (mode?: "login" | "signup") => void;
  envelope: KickoffEnvelope | null;
  openTopicId: string | null;
  openOriginRect: DOMRect | null;
  onOpenTopic: (topicId: string, rect: DOMRect | null) => void;
  onCloseTopic: () => void;
  decisionsByTopicId: Map<string, Decision[]>;
  onRefetch: () => void;
  onKickoff: (idea: string, attachments: AttachedSource[]) => Promise<void>;
  onSelectTemplate: (slug: string) => void;
  onImportMarkdown: (markdown: string) => Promise<void>;
  onOpenImportJson: () => void;
  onOpenShare: () => void;
  onOpenExport: () => void;
  onListRename: (projectId: string, newTitle: string) => Promise<void>;
  onListDelete: (projectId: string) => Promise<void>;
  fetchDecisions: (projectId: string) => Promise<void>;
  phaseSetter: React.Dispatch<React.SetStateAction<Phase>>;
  onOpenLegal: (kind: LegalOverlayKind) => void;
  shelves: Shelf[];
  onCreateShelf: (name: string) => Promise<void>;
  onRenameShelf: (shelfId: string, name: string) => Promise<void>;
  onDeleteShelf: (shelfId: string) => Promise<void>;
  onMoveProjectToShelf: (
    projectId: string,
    shelfIdOrNull: string | null,
  ) => Promise<void>;
  onSuggestStart: (idea: string) => void;
  pendingDeletionSuggestions: Record<string, TopicDeletionSuggestion>;
  onDismissDeletionSuggestion: (topicId: string) => void;
  onConfirmDeletion: (topicId: string) => void;
};

function InspiraPhaseRender(props: InspiraPhaseRenderProps) {
  const {
    phase,
    projects,
    user,
    openProject,
    onNewProject,
    onRenameActive,
    onDeleteActive,
    onGoToProjectsList,
    onOpenAccountSettings,
    onLogout,
    onOpenAuth,
    envelope,
    openTopicId,
    openOriginRect,
    onOpenTopic,
    onCloseTopic,
    decisionsByTopicId,
    onRefetch,
    onKickoff,
    onSelectTemplate,
    onImportMarkdown,
    onOpenImportJson,
    onOpenShare,
    onOpenExport,
    onListRename,
    onListDelete,
    phaseSetter,
    onOpenLegal,
    shelves,
    onCreateShelf,
    onRenameShelf,
    onDeleteShelf,
    onMoveProjectToShelf,
    onSuggestStart,
    pendingDeletionSuggestions,
    onDismissDeletionSuggestion,
    onConfirmDeletion,
  } = props;
  // KanbanCard.tsx click-through hands /app a `state.openProject` payload.
  // The /app → /workspaces redirect below MUST skip when this is set,
  // otherwise the card click looks like a no-op (canvas opens then
  // bounces home). Read from the React Router hook — `window.location`
  // does not expose router state.
  const routerLocation = useLocation();
  const pendingOpenProjectId =
    (routerLocation.state as { openProject?: string } | null)?.openProject ??
    null;

  // Decision summary drawer (B2.5). Subscribes to
  // `inspira:orchestrator-completed` and exposes mock-trigger for dev.
  // Owns its own visibility state; the chip + dev button below mount
  // unconditionally on the canvas phase.
  const decisionSummary = useDecisionSummary();

  if (phase.kind === "bootstrapping") {
    return (
      <div className="loading">
        <div className="loading__inner">
          <div className="loading__eyebrow">{t("loading.waking_up")}</div>
          <div className="loading__pulse" />
        </div>
      </div>
    );
  }

  if (phase.kind === "kickoff") {
    return (
      <div className="kickoff-wrap" id="main-content" tabIndex={-1}>
        {/* Top bar with the user menu. Even on the kickoff phase a signed-in
            user needs access to Account Settings / Sign out — without this
            row, a fetch failure or zero-project landing would strand them
            here with no way to manage their account. We mirror the markup
            used by the projects_list phase so the same .top-bar styles apply. */}
        {user ? (
          <header className="top-bar">
            <div className="top-bar__brand">{t("top_bar.brand")}</div>
            <div className="top-bar__spacer" />
            <UserMenu
              user={user}
              onOpenAccountSettings={onOpenAccountSettings}
              onLogout={onLogout}
              onSignIn={onOpenAuth}
            />
          </header>
        ) : null}
        {/* Only signed-in users with at least one project see the Back
            button. Anonymous visitors (system-user fallback) can inherit
            stale project state from prior sessions — showing "Back to
            projects" to them would route them into someone else's grid,
            which the anon-visitor guard will bounce back anyway. */}
        {!user?.is_system && projects.length > 0 ? (
          <div className="kickoff-wrap__back">
            <button
              type="button"
              className="kickoff-wrap__back-btn"
              onClick={onGoToProjectsList}
            >
              {t("kickoff.back_to_projects")}
            </button>
          </div>
        ) : (
          // Anonymous / zero-project landing — tiny brand strip at the
          // top so the page isn't a blank float. Signed-in users with
          // projects see the back-to-projects chip instead.
          <div className="kickoff-wrap__brand">
            <span className="kickoff-wrap__brand-mark">Inspira</span>
            <span className="kickoff-wrap__brand-tagline">
              From feedback to features.
            </span>
          </div>
        )}
        <KickoffForm
          onSubmit={onKickoff}
          error={phase.error}
          initialIdea={phase.initialIdea}
          onSelectTemplate={onSelectTemplate}
          onImportMarkdown={onImportMarkdown}
          onOpenImportJson={onOpenImportJson}
        />
        <LegalFooter onOpenLegal={onOpenLegal} />
      </div>
    );
  }

  if (phase.kind === "loading") {
    return (
      <div className="loading" id="main-content" tabIndex={-1}>
        <div className="loading__inner">
          <p className="loading__idea">{phase.idea}</p>
          <div className="loading__pulse" />
        </div>
      </div>
    );
  }

  if (phase.kind === "error") {
    return (
      <ServerErrorPage
        message={phase.message}
        onRetry={() => phaseSetter({ kind: "bootstrapping" })}
        onGoHome={onGoToProjectsList}
      />
    );
  }

  if (phase.kind === "projects_list") {
    // v4 unified entry: there is no longer a legacy /app
    // projects-list-shell. EVERY path through this phase redirects to a
    // route that mounts the AuthedShell+AppRail (or the marketing
    // root for anon). Drops the v3 chrome regression where stale
    // `location.state.usr.openProject` (e.g. a just-deleted project
    // the user was on the canvas for) skipped the redirect and fell
    // through to the v3 fallback shell with no AppRail.
    //
    // Routing rules:
    //   • signed-in user with default_workspace_id  → /workspaces
    //     (WorkspaceKanbanRoute → AuthedShell → AppRail + Kanban)
    //   • signed-in user without workspace           → /onboarding
    //     (the wizard creates the workspace, then returns to /workspaces)
    //   • anon / system                              → /  (marketing
    //     root; RootGate redirects to signup or back to /app as needed)
    //
    // window.location.replace clears React Router state so any stale
    // ``openProject`` from a deleted-project canvas hop doesn't survive
    // the redirect. The KanbanCard click-through flow is unaffected:
    // first-mount with state.openProject set boots into ``loading``
    // phase via the effect at line ~668, never ``projects_list``.
    if (typeof window !== "undefined") {
      if (user?.default_workspace_id) {
        window.location.replace("/workspaces");
      } else if (user && !user.is_system) {
        window.location.replace("/onboarding");
      } else {
        window.location.replace("/");
      }
    }
    return <div aria-hidden="true" />;
  }

  if (phase.kind === "canvas") {
    if (!envelope) {
      // Defensive — should never happen, but keeps the compiler happy.
      return (
        <NotFoundPage onGoHome={onGoToProjectsList} pathAttempted="canvas" />
      );
    }
    const openTopic: Topic | null = openTopicId
      ? envelope.topics.find((t) => t.topic_id === openTopicId) ?? null
      : null;
    const activeProject = projects.find(
      (p) => p.project_id === phase.projectId,
    );

    return (
      <div className="app-shell app-shell--rail">
        {/* v4 canvas chrome: AppRail on the left handles workspace
            nav + user menu and now hosts the OrchestratorChip in its
            right-slot, so agent activity lives in the same rail spot
            across every project-scoped surface (canvas, Code IDE,
            artifact viewer). The canvas top-bar keeps only the
            ProjectSwitcher. */}
        <AppRail rightSlot={<OrchestratorChip />} />
        <div className="app-shell__main">
          <header className="top-bar top-bar--canvas">
            <div className="top-bar__spacer">
              {activeProject?.title ? (
                <ProjectSwitcher
                  projects={projects}
                  activeProjectId={phase.projectId}
                  activeTitle={activeProject.title}
                  variant="centered"
                  onSwitch={(id) => void openProject(id)}
                  onNew={onNewProject}
                  onRename={onRenameActive}
                  onDelete={onDeleteActive}
                  onShare={onOpenShare}
                  onExport={onOpenExport}
                />
              ) : null}
            </div>
          </header>
        {activeProject?.metadata?.is_example === true ? (
          <ExampleBanner onStartFresh={onNewProject} />
        ) : null}
        <ProjectCanvas
          projectId={phase.projectId}
          topics={envelope.topics}
          relationships={envelope.relationships}
          decisionsByTopicId={decisionsByTopicId}
          kickoff={envelope.kickoff}
          hiddenTopicId={openTopicId}
          onOpenTopic={(topicId, rect) => onOpenTopic(topicId, rect)}
          onRefetch={onRefetch}
          pendingDeletionSuggestions={pendingDeletionSuggestions}
          onDismissDeletionSuggestion={onDismissDeletionSuggestion}
          onConfirmDeletion={onConfirmDeletion}
        />
        {openTopic ? (
          <TopicDetail
            topic={openTopic}
            allTopics={envelope.topics}
            relationships={envelope.relationships}
            originRect={openOriginRect}
            onClose={onCloseTopic}
            project={activeProject ?? null}
          />
        ) : null}
        {decisionSummary.summary ? (
          <DecisionSummaryDrawer
            summary={decisionSummary.summary}
            open={decisionSummary.drawerOpen}
            projectId={phase.projectId}
            onClose={decisionSummary.close}
            onGenerateArtifact={() =>
              console.info(
                "[Inspira] Generate-artifact CTA — wires up when B4.1 lands.",
              )
            }
            onSendBackForRevision={() =>
              console.info(
                "[Inspira] Send-back-for-revision CTA — wires up with the orchestrator backend.",
              )
            }
            onRejectPlan={() =>
              console.info(
                "[Inspira] Reject CTA — wires up with the orchestrator backend.",
              )
            }
          />
        ) : null}
        {/* Re-open chip lives here, not in ProjectCanvas, so the
            canvas component stays untouched. position: fixed places it
            visually adjacent to the canvas top action bar. */}
        <DecisionSummaryShowChip
          visible={
            !!decisionSummary.summary && !decisionSummary.drawerOpen
          }
          onClick={decisionSummary.open}
        />
        {import.meta.env.DEV ? (
          <button
            type="button"
            onClick={decisionSummary.triggerMock}
            className="btn btn--ghost btn--sm"
            style={{
              position: "fixed",
              bottom: 16,
              right: 16,
              zIndex: 95,
            }}
          >
            [DEV] Trigger orchestrator complete
          </button>
        ) : null}
        </div>
      </div>
    );
  }

  // Three-panel artifact viewer.
  if (phase.kind === "artifact") {
    return (
      <div className="app-shell app-shell--rail">
        {/* OrchestratorChip in the rail's right-slot for visual
            consistency with the canvas + Code IDE. Chip stays at
            idle on the artifact phase (no useSSE here — the artifact
            viewer has its own codegen spinner). */}
        <AppRail rightSlot={<OrchestratorChip />} />
        <div className="app-shell__main">
          <ArtifactViewerPage
            projectId={phase.projectId}
            projectTitle={phase.projectTitle}
            initialState={phase.initialState ?? "pending_review"}
            onBack={() => phaseSetter(phase.fromPhase)}
          />
        </div>
      </div>
    );
  }

  // Defensive fallback — no phase matches.
  return <NotFoundPage onGoHome={onGoToProjectsList} />;
}

// ---------------------------------------------------------------------
// LegalFooter — small unobtrusive footer with Privacy / Terms links.
// Rendered on the kickoff, auth-gate, and projects-list screens so the
// legal documents are reachable from anywhere a user lands before being
// signed in. Uses tiny muted type to stay out of the way.
// ---------------------------------------------------------------------

function LegalFooter({
  onOpenLegal,
}: {
  onOpenLegal: (kind: LegalOverlayKind) => void;
}) {
  return (
    <footer className="legal-footer" aria-label={t("inspira.legal_footer.aria")}>
      <button
        type="button"
        className="legal-footer__link"
        onClick={() => onOpenLegal("privacy")}
      >
        {t("inspira.legal_footer.privacy")}
      </button>
      <span className="legal-footer__sep" aria-hidden="true">·</span>
      <button
        type="button"
        className="legal-footer__link"
        onClick={() => onOpenLegal("terms")}
      >
        {t("inspira.legal_footer.terms")}
      </button>
      <span className="legal-footer__sep" aria-hidden="true">·</span>
      <LocalePicker variant="inline" />
    </footer>
  );
}

// ---------------------------------------------------------------------
// Project switcher — minimal dropdown for switch / new / rename / delete
// / share / export.
// ---------------------------------------------------------------------

function ProjectSwitcher({
  projects,
  activeProjectId,
  activeTitle,
  variant = "pill",
  onSwitch,
  onNew,
  onRename,
  onDelete,
  onShare,
  onExport,
}: {
  projects: V2Project[];
  activeProjectId: string;
  activeTitle: string;
  /** "pill" — small bordered button (default).
   *  "centered" — h1-style serif title in the top-bar spacer (#088). */
  variant?: "pill" | "centered";
  onSwitch: (projectId: string) => void;
  onNew: () => void;
  onRename: () => void;
  onDelete: () => void;
  onShare: () => void;
  onExport: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const el = rootRef.current;
      if (!el) return;
      if (el.contains(e.target as unknown as globalThis.Node)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [open]);

  const rootClassName =
    "project-switcher" +
    (variant === "centered" ? " project-switcher--centered" : "");
  const triggerClassName =
    "project-switcher__current" +
    (variant === "centered" ? " project-switcher__current--centered" : "");

  return (
    <div className={rootClassName} ref={rootRef}>
      <button
        type="button"
        className={triggerClassName}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        title={activeTitle}
      >
        <span className="project-switcher__title">{activeTitle}</span>
        <span className="project-switcher__caret">{open ? "▾" : "▸"}</span>
      </button>
      {open ? (
        <div className="project-switcher__menu" role="menu">
          <div className="project-switcher__list">
            {projects.map((p) => (
              <button
                key={p.project_id}
                type="button"
                className={
                  "project-switcher__item" +
                  (p.project_id === activeProjectId
                    ? " project-switcher__item--active"
                    : "")
                }
                onClick={() => {
                  setOpen(false);
                  if (p.project_id !== activeProjectId) onSwitch(p.project_id);
                }}
              >
                {p.title}
              </button>
            ))}
            {projects.length === 0 ? (
              <div className="project-switcher__empty">{t("inspira.switcher.no_other_projects")}</div>
            ) : null}
          </div>
          <div className="project-switcher__actions">
            <button
              type="button"
              className="project-switcher__action"
              onClick={() => {
                setOpen(false);
                onNew();
              }}
            >
              {t("inspira.switcher.new_project")}
            </button>
            <button
              type="button"
              className="project-switcher__action"
              onClick={() => {
                setOpen(false);
                onRename();
              }}
            >
              {t("inspira.switcher.rename")}
            </button>
            <button
              type="button"
              className="project-switcher__action"
              onClick={() => {
                setOpen(false);
                onShare();
              }}
            >
              {t("inspira.switcher.share")}
            </button>
            <button
              type="button"
              className="project-switcher__action"
              onClick={() => {
                setOpen(false);
                onExport();
              }}
            >
              {t("inspira.switcher.export")}
            </button>
            <button
              type="button"
              className="project-switcher__action project-switcher__action--danger"
              onClick={() => {
                setOpen(false);
                onDelete();
              }}
            >
              {t("inspira.switcher.delete")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------
// User menu — avatar chip + account settings + logout.
// ---------------------------------------------------------------------

function UserMenu({
  user,
  onOpenAccountSettings,
  onLogout,
  onSignIn,
}: {
  user: AuthedUser;
  onOpenAccountSettings: () => void;
  onLogout: () => Promise<void>;
  onSignIn: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Plan tier — fetched once on mount for signed-in users. Used by the
  // user-menu plan-row label. Anonymous/system users don't see it.
  const [planTier, setPlanTier] = useState<string | null>(null);

  const refreshPlan = useCallback(async (): Promise<void> => {
    try {
      const res = await api.getEntitlements();
      setPlanTier(res.plan);
    } catch {
      // Soft-fail — the plan row just doesn't render.
    }
  }, []);

  useEffect(() => {
    if (user.is_system) {
      setPlanTier(null);
      return;
    }
    void refreshPlan();
  }, [user.is_system, user.user_id, refreshPlan]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const el = rootRef.current;
      if (!el) return;
      if (el.contains(e.target as unknown as globalThis.Node)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [open]);

  const initials = (user.display_name || user.email || "?")
    .split(/\s+/)
    .map((w) => w.charAt(0).toUpperCase())
    .slice(0, 2)
    .join("")
    .padEnd(1, "?");

  return (
    <div className="user-menu" ref={rootRef}>
      <button
        type="button"
        className="user-menu__avatar"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={user.is_system ? t("user_menu.not_signed_in") : user.email}
      >
        {user.is_system ? "·" : initials}
      </button>
      {open ? (
        <div className="user-menu__panel" role="menu">
          <div className="user-menu__header">
            {user.is_system ? (
              <>
                <div className="user-menu__email">{t("user_menu.not_signed_in")}</div>
                <div className="user-menu__hint">
                  {t("user_menu.sign_in_hint")}
                </div>
              </>
            ) : (
              <>
                <div className="user-menu__name">{user.display_name}</div>
                <div className="user-menu__email" title={user.email}>{user.email}</div>
              </>
            )}
          </div>
          {/* Plan-tier row — pill inside the dropdown matching the
              top-bar Free pill so the visual treatment is consistent
              between surfaces. PR 2 dropped the credit meter; this row
              just shows the plan tier label as a pill. */}
          {!user.is_system && planTier ? (
            <div className="user-menu__plan-row">
              <span
                className={
                  "user-menu__plan-pill" +
                  (planTier.toLowerCase() === "free"
                    ? " user-menu__plan-pill--free"
                    : "")
                }
              >
                {planTier.toLowerCase() === "team"
                  ? "Frontier"
                  : planTier.charAt(0).toUpperCase() + planTier.slice(1)}
              </span>
            </div>
          ) : null}
          {user.is_system ? (
            <button
              type="button"
              className="user-menu__action"
              onClick={() => {
                setOpen(false);
                onSignIn();
              }}
            >
              {t("user_menu.sign_in")}
            </button>
          ) : (
            <>
              <button
                type="button"
                className="user-menu__action"
                onClick={() => {
                  setOpen(false);
                  onOpenAccountSettings();
                }}
              >
                {t("user_menu.account_settings")}
              </button>
              <button
                type="button"
                className="user-menu__action"
                onClick={() => {
                  setOpen(false);
                  void onLogout();
                }}
              >
                {t("user_menu.log_out")}
              </button>
            </>
          )}
          <div className="user-menu__divider" role="separator" />
          <div className="user-menu__locale">
            <LocalePicker variant="inline" onPicked={() => setOpen(false)} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------
// downloadBlob — small helper that triggers a file download from an
// in-memory Blob. Used by the Markdown export path.
// ---------------------------------------------------------------------
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick so Safari has time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
