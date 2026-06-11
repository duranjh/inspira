// Wizard Step 1 — workspace name + auto-derived slug.
//
// On Continue, calls POST /api/v2/workspaces. The BE auto-sets
// default_workspace_id on first creation. 409 conflict retries
// with -2 / -3 / -4 suffixes (up to 3 attempts) before surfacing
// inline. Reserved-prefix `personal-*` rejected up-front.
//
// Slug fallback (audit concern #10): unicode-only / spaces-only
// input collapses to empty string after slugify; surface inline
// error rather than POSTing an invalid slug.

import { useMemo, useState } from "react";

import { createWorkspace } from "../../workspaces/api";
import type { WizardState, WizardStep } from "./OnboardingWizard";

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

type Step1Props = {
  state: WizardState;
  onNext: (step: WizardStep, patch?: Partial<WizardState>) => void;
};

export function Step1WorkspaceName({ state, onNext }: Step1Props) {
  const [name, setName] = useState(state.workspaceName);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const slug = useMemo(() => slugify(name), [name]);
  const slugInvalid = name.trim().length > 0 && slug.length === 0;
  const slugTooShort = slug.length > 0 && slug.length < 3;
  const reservedPrefix = slug.startsWith("personal-");

  async function handleContinue() {
    setError(null);
    if (slugInvalid) {
      setError("Workspace name needs at least one letter or number.");
      return;
    }
    if (slugTooShort) {
      setError("Workspace name needs at least 3 characters.");
      return;
    }
    if (reservedPrefix) {
      setError("Workspace name can't start with 'Personal'.");
      return;
    }
    if (!slug) return;

    setSubmitting(true);
    let candidate = slug;
    let attempts = 0;
    while (attempts < 4) {
      try {
        const { workspace } = await createWorkspace({
          slug: candidate,
          name: name.trim(),
        });
        onNext(2, {
          workspaceId: workspace.workspace_id,
          workspaceName: name.trim(),
          workspaceSlug: workspace.slug,
        });
        return;
      } catch (err) {
        // Detect 409 from the error message produced by httpClient's
        // postJson helper. Shape: "POST /api/v2/workspaces failed: 409 ...".
        const message = err instanceof Error ? err.message : "";
        const detail = (err as { detail?: { error?: string; message?: string } })
          .detail;
        console.error("[Step1] workspace create failed", err);
        if (message.includes("409") && attempts < 3) {
          attempts += 1;
          candidate = `${slug}-${attempts + 1}`;
          continue;
        }
        if (
          detail?.error === "workspace_slug_taken" ||
          message.includes("workspace_slug_taken")
        ) {
          setError("That workspace URL is taken. Try a different name.");
        } else if (detail?.message) {
          setError(detail.message);
        } else if (message.includes("422")) {
          setError(
            "That name doesn't fit our URL rules. Try 3+ letters, digits, or hyphens.",
          );
        } else if (err instanceof Error && err.message) {
          setError(err.message);
        } else {
          setError("Couldn't create the workspace. Try again.");
        }
        setSubmitting(false);
        return;
      }
    }
    setSubmitting(false);
  }

  return (
    <div className="ob-center">
      <h1 className="ob-headline">What should we call your workspace?</h1>
      <p className="ob-subtitle">
        This is where your team will scope, decide, and ship.
      </p>
      <div className="ob-input-wrap">
        <input
          className="ob-input"
          placeholder="e.g., Acme Engineering"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          disabled={submitting}
          aria-label="Workspace name"
        />
        <div className="ob-slug">
          {slugTooShort
            ? "Needs at least 3 characters."
            : slug
              ? `Your URL will be ${slug}.inspira.com`
              : slugInvalid
                ? "Needs at least one letter or number."
                : ""}
        </div>
      </div>
      {error ? (
        <div className="ob-inline-error" role="alert">
          {error}
        </div>
      ) : null}
      <button
        type="button"
        className="ob-cta"
        disabled={
          !slug || slugInvalid || slugTooShort || reservedPrefix || submitting
        }
        onClick={handleContinue}
      >
        {submitting ? "Creating…" : "Continue →"}
      </button>
      <div className="ob-ghost">
        You can rename and change the URL anytime from settings.
      </div>
    </div>
  );
}
