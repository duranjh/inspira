// Inspira — v4 Onboarding Wizard at /onboarding.
//
// 4-step fullscreen surface mirroring the internal v5-pivot hi-fi
// (design files not included in this repo):
//   1. Workspace name (real workspace creation)
//   2. Connect repo (GitHub OAuth · folder upload · sample/skip)
//   3. Connect feedback (Linear API key · CSV upload · sample/skip)
//   4. Clustering animation (mock, navigates to /workspaces)
//
// Bootstrap effect on mount:
// - is_system → /  (anon redirect)
// - URL ?step=N hydrates step (handles GitHub OAuth round-trip)
// - localStorage state hydrates same-browser resume
// - default_workspace_id present + step >= 2 → trust URL
// - default_workspace_id present + no URL step → resume from
//   localStorage onboarding_step or jump past Step 1
//
// State shape lives in localStorage under inspira_onboarding_state
// for resume-on-refresh. Cleared on Step 4 completion.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";

import { api } from "../../inspira/api";
import { API_BASE_URL } from "../../../lib/httpClient";
import { Step1WorkspaceName } from "./Step1WorkspaceName";
import { Step2ConnectRepo } from "./Step2ConnectRepo";
import { Step3ConnectFeedback } from "./Step3ConnectFeedback";
import { Step4Clustering } from "./Step4Clustering";
import "./wizard.css";

export type WizardStep = 1 | 2 | 3 | 4;

export type WizardState = {
  step: WizardStep;
  workspaceId: string | null;
  workspaceName: string;
  workspaceSlug: string;
  // Step 2 outcomes — exactly one set per session.
  githubConnected: boolean;
  localRepoUploaded: boolean;
  skippedRepo: boolean;
  // Step 3 outcomes — exactly one set per session.
  linearConnected: boolean;
  csvImported: boolean;
  csvImportedRows: number;
  skippedFeedback: boolean;
};

const STORAGE_KEY = "inspira_onboarding_state";

const DEFAULT_STATE: WizardState = {
  step: 1,
  workspaceId: null,
  workspaceName: "",
  workspaceSlug: "",
  githubConnected: false,
  localRepoUploaded: false,
  skippedRepo: false,
  linearConnected: false,
  csvImported: false,
  csvImportedRows: 0,
  skippedFeedback: false,
};

const STEP_LABELS: Record<WizardStep, string> = {
  1: "Name workspace",
  2: "Connect repo",
  3: "Connect feedback",
  4: "Inspira thinking",
};

function loadStored(): Partial<WizardState> | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") return parsed;
    return null;
  } catch {
    return null;
  }
}

function persist(state: WizardState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage disabled — non-fatal */
  }
}

function clearStored(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage disabled */
  }
}

export function OnboardingWizard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [state, setState] = useState<WizardState>(DEFAULT_STATE);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [redirectTo, setRedirectTo] = useState<string | null>(null);

  // Bootstrap: resolve the user, hydrate step from URL + localStorage.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let me;
      try {
        me = await api.me();
      } catch {
        if (cancelled) return;
        setRedirectTo("/");
        setBootstrapping(false);
        return;
      }
      if (cancelled) return;
      if (me.is_system) {
        setRedirectTo("/");
        setBootstrapping(false);
        return;
      }

      const stored = loadStored();
      const urlStep = parseInt(searchParams.get("step") || "0", 10);
      const status = searchParams.get("status");
      const reason = searchParams.get("reason");
      const initial: WizardState = {
        ...DEFAULT_STATE,
        ...(stored || {}),
      };

      // If user already has a workspace, advance past Step 1.
      if (me.default_workspace_id) {
        initial.workspaceId = me.default_workspace_id;
        if (initial.step < 2) initial.step = 2;
      }

      // URL step takes precedence (handles OAuth round-trip back
      // to /onboarding?step=2&status=connected).
      if (urlStep >= 1 && urlStep <= 4) {
        initial.step = urlStep as WizardStep;
      }

      // Step 2 — re-verify connector state via /api/v2/connectors.
      // Runs whenever the partner lands on step 2 (after OAuth
      // callback, after back-nav, on reload), not just when the
      // URL carries ?status=connected — that param is unsigned
      // (audit concern #5) and is also missing when the partner
      // returns via Cancel or by reloading. Without this check,
      // a partner who already installed the GitHub App sees a
      // stale "Connect with GitHub" CTA.
      if (initial.step === 2 && me.default_workspace_id) {
        try {
          const resp = await fetch(`${API_BASE_URL}/api/v2/connectors`, {
            credentials: "include",
            headers: { "X-Workspace-Id": me.default_workspace_id },
          });
          if (resp.ok) {
            const body = await resp.json();
            const github = (body.live as Array<{
              provider: string;
              state?: { status?: string };
            }>).find((e) => e.provider === "github");
            if (github?.state?.status === "connected") {
              initial.githubConnected = true;
              initial.step = 3;
            }
          }
        } catch {
          /* fall through — step stays at 2, partner can retry */
        }
      }

      // Strip status/reason from URL once consumed.
      if (status || reason) {
        const next = new URLSearchParams(searchParams);
        next.delete("status");
        next.delete("reason");
        setSearchParams(next, { replace: true });
      }

      if (cancelled) return;
      setState(initial);
      persist(initial);
      setBootstrapping(false);
    })();
    return () => {
      cancelled = true;
    };
    // Run once on mount; subsequent step changes go through advance().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const advance = useCallback(
    (patch: Partial<WizardState>) => {
      setState((prev) => {
        const next: WizardState = { ...prev, ...patch };
        persist(next);
        return next;
      });
    },
    [],
  );

  const goToStep = useCallback(
    (step: WizardStep, patch: Partial<WizardState> = {}) => {
      advance({ ...patch, step });
      // Mirror to URL so refresh lands on the right step.
      const params = new URLSearchParams(searchParams);
      params.set("step", String(step));
      setSearchParams(params, { replace: true });
    },
    [advance, searchParams, setSearchParams],
  );

  const finish = useCallback(() => {
    clearStored();
  }, []);

  const headerLabel = useMemo(
    () => `Step ${state.step} · ${STEP_LABELS[state.step]}`,
    [state.step],
  );

  if (redirectTo) {
    return <Navigate replace to={redirectTo} />;
  }
  if (bootstrapping) {
    return (
      <div
        className="ob-screen"
        aria-busy="true"
        aria-live="polite"
      />
    );
  }

  return (
    <div className="ob-screen">
      <header className="ob-top">
        <span className="ob-wordmark">Inspira</span>
        <a
          className="ob-skip"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            // Skipping the wizard entirely → go to Kanban (or
            // Inbox if no workspace was created).
            if (state.workspaceId) {
              finish();
              window.location.assign("/workspaces");
            } else {
              finish();
              window.location.assign("/");
            }
          }}
        >
          Skip setup
        </a>
      </header>

      {state.step === 1 ? (
        <Step1WorkspaceName state={state} onNext={goToStep} />
      ) : null}
      {state.step === 2 ? (
        <Step2ConnectRepo state={state} onNext={goToStep} onBack={() => goToStep(1)} />
      ) : null}
      {state.step === 3 ? (
        <Step3ConnectFeedback
          state={state}
          onNext={goToStep}
          onBack={() => goToStep(2)}
        />
      ) : null}
      {state.step === 4 ? (
        <Step4Clustering state={state} onFinish={finish} />
      ) : null}

      <footer className="ob-dots" aria-label={headerLabel}>
        <div className="ob-dots__row">
          {[1, 2, 3, 4].map((i) => (
            <span
              key={i}
              className={`ob-dot ${i === state.step ? "ob-dot--active" : ""}`}
              aria-hidden="true"
            />
          ))}
        </div>
        <span className="ob-dots__label">{headerLabel}</span>
      </footer>
    </div>
  );
}
