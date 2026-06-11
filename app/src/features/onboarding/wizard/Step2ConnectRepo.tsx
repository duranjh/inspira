// Wizard Step 2 — connect repo (real wiring, 3 paths).
//
// Path A — GitHub OAuth: real round-trip. POST /github/oauth/start
// with redirect_to="/onboarding?step=2", full-page nav to GitHub,
// callback redirects back to /onboarding?step=2&status=connected.
// Wizard's bootstrap effect re-verifies via GET /api/v2/connectors
// (audit concern #5 — ?status=connected is unsigned URL state) before
// advancing.
//
// Path B — Upload local folder: webkitdirectory file picker (or
// drag-drop). Builds FormData with each file's webkitRelativePath
// as the filename, POSTs to /api/v2/connectors/local-repo/upload.
// FE-side filter excludes .git/, node_modules/, dist/, build/,
// .venv/, __pycache__/. 50 MB total cap (FE pre-check + BE
// enforcement).
//
// Path C — Sample / Skip: client-only state flags + advance.

import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";

import {
  startGitHubOAuth,
  uploadLocalRepo,
} from "../../connectors/api";
import { API_BASE_URL } from "../../../lib/httpClient";
import type { WizardState, WizardStep } from "./OnboardingWizard";

type Step2Props = {
  state: WizardState;
  onNext: (step: WizardStep, patch?: Partial<WizardState>) => void;
  onBack: () => void;
};

const FE_EXCLUDE_DIR_PATTERNS = [
  "/.git/",
  "/node_modules/",
  "/dist/",
  "/build/",
  "/.venv/",
  "/venv/",
  "/__pycache__/",
  "/.next/",
  "/.cache/",
  "/.idea/",
  "/.vscode/",
  "/target/",
];

const FE_TOTAL_BYTES_CAP = 50 * 1024 * 1024;

function shouldKeepFile(relPath: string): boolean {
  // Insert a leading "/" so the substring match catches segments at
  // the very start of the path too.
  const probe = `/${relPath.replace(/\\/g, "/")}`;
  for (const pattern of FE_EXCLUDE_DIR_PATTERNS) {
    if (probe.includes(pattern)) return false;
  }
  // Skip lockfiles + binary placeholder files quickly.
  const base = probe.split("/").pop() || "";
  if (
    base === "package-lock.json" ||
    base === "yarn.lock" ||
    base === "pnpm-lock.yaml" ||
    base === "poetry.lock" ||
    base === "Cargo.lock" ||
    base === ".DS_Store"
  ) {
    return false;
  }
  return true;
}

declare module "react" {
  interface InputHTMLAttributes<T> {
    webkitdirectory?: string;
    directory?: string;
  }
}

export function Step2ConnectRepo({ state, onNext, onBack }: Step2Props) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [submitting, setSubmitting] = useState<"github" | "upload" | null>(
    null,
  );
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  // Polling: while submitting==="github", poll /api/v2/connectors
  // every 3s for the install to land — the OAuth dance happens in
  // a new tab so the original tab can't rely on a redirect.
  const pollTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current) {
        window.clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
    };
  }, []);

  async function handleConnectGithub() {
    setError(null);
    setSubmitting("github");
    try {
      const { install_url } = await startGitHubOAuth({
        redirect_to: "/onboarding?step=2",
      });
      // Open in a new tab so the wizard stays put. The OAuth callback
      // hits api.tryinspira.com inside the new tab; the original tab
      // detects the install via polling /api/v2/connectors and
      // auto-advances.
      window.open(install_url, "_blank", "noopener,noreferrer");
      startPollingForInstall();
    } catch (err) {
      setSubmitting(null);
      const message = err instanceof Error ? err.message : "";
      if (message.includes("503") || message.includes("github_not_configured")) {
        setError(
          "GitHub isn't configured on this deployment. Skip or upload a folder for now.",
        );
      } else {
        setError("Couldn't reach GitHub. Try again.");
      }
    }
  }

  function startPollingForInstall() {
    if (pollTimer.current) window.clearInterval(pollTimer.current);
    // Cap the wait at 5 minutes — if the partner closed the GitHub
    // install tab without finishing, we'd otherwise poll forever and
    // the wizard would be visibly stuck on the "Waiting for GitHub
    // install…" state with no way out short of refresh.
    const startedAt = Date.now();
    const TIMEOUT_MS = 5 * 60_000;
    pollTimer.current = window.setInterval(async () => {
      if (Date.now() - startedAt > TIMEOUT_MS) {
        cancelPolling();
        setError(
          "Looks like the GitHub install didn't finish. Try again or skip for now.",
        );
        return;
      }
      try {
        const headers: Record<string, string> = {};
        if (state.workspaceId) headers["X-Workspace-Id"] = state.workspaceId;
        const resp = await fetch(`${API_BASE_URL}/api/v2/connectors`, {
          credentials: "include",
          headers,
        });
        if (!resp.ok) return;
        const body = await resp.json();
        const github = (body.live as Array<{
          provider: string;
          state?: { status?: string };
        }>).find((e) => e.provider === "github");
        if (github?.state?.status === "connected") {
          if (pollTimer.current) {
            window.clearInterval(pollTimer.current);
            pollTimer.current = null;
          }
          onNext(3, { githubConnected: true });
        }
      } catch {
        /* swallow — keep polling */
      }
    }, 3000);
  }

  function cancelPolling() {
    if (pollTimer.current) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
    setSubmitting(null);
  }

  async function handleFolderSelected(files: FileList) {
    setError(null);
    setSubmitting("upload");
    setUploadProgress("Reading files…");

    const formData = new FormData();
    let kept = 0;
    let totalBytes = 0;

    for (let i = 0; i < files.length; i += 1) {
      const f = files[i];
      // webkitRelativePath is populated by the browser on directory
      // upload; for drag-drop of a folder we may need to fall back.
      const rel =
        (f as File & { webkitRelativePath?: string }).webkitRelativePath ||
        f.name;
      if (!shouldKeepFile(rel)) continue;
      totalBytes += f.size;
      if (totalBytes > FE_TOTAL_BYTES_CAP) {
        setSubmitting(null);
        setUploadProgress(null);
        setError(
          `Folder is too large (>${Math.round(FE_TOTAL_BYTES_CAP / (1024 * 1024))} MB). Trim it down and try again.`,
        );
        return;
      }
      formData.append("files", f, rel);
      kept += 1;
    }

    if (kept === 0) {
      setSubmitting(null);
      setUploadProgress(null);
      setError("No source files in that folder.");
      return;
    }

    setUploadProgress(`Uploading ${kept} files…`);
    try {
      const result = await uploadLocalRepo(formData);
      setSubmitting(null);
      setUploadProgress(null);
      onNext(3, {
        localRepoUploaded: true,
      });
      void result;
    } catch (err) {
      setSubmitting(null);
      setUploadProgress(null);
      const message = err instanceof Error ? err.message : "";
      if (message.includes("413")) {
        setError("Folder is too large for upload. Trim it down and try again.");
      } else if (message.includes("422")) {
        setError("Couldn't find any source files in the folder.");
      } else {
        setError("Upload failed. Try again, or skip and connect later.");
      }
    }
  }

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    if (e.target.files && e.target.files.length > 0) {
      void handleFolderSelected(e.target.files);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      void handleFolderSelected(e.dataTransfer.files);
    }
  }

  return (
    <>
      <div className="ob-center">
        <h1 className="ob-headline">Plug Inspira into your codebase.</h1>
        <p className="ob-subtitle">
          The AI reads your repo to ground its prioritization in your actual
          code structure.
        </p>
        <div className="ob-above-tiles">
          Today, GitHub. Linear, Intercom, and others ship in the next 4 weeks.
        </div>

        <div
          className="ob-tiles"
          style={{
            justifyContent: "center",
            flexWrap: "wrap",
          }}
        >
          {/* GitHub OAuth tile */}
          <div className="ob-tile ob-tile--single">
            <div className="ob-tile__logo">GH</div>
            <div className="ob-tile__name">GitHub</div>
            <p className="ob-tile__desc">
              Connect your repo. Inspira reads issues, code structure, and
              recent commits.
            </p>
            {submitting === "github" ? (
              <div className="ob-tile__oauth">
                <span className="ob-tile__spinner" aria-hidden="true" />
                Waiting for GitHub install…
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    cancelPolling();
                  }}
                  style={{ display: "block", marginTop: 8, fontSize: 12 }}
                >
                  Cancel
                </a>
              </div>
            ) : (
              <button
                type="button"
                className="ob-tile__cta"
                onClick={handleConnectGithub}
                disabled={submitting !== null}
              >
                Connect with GitHub →
              </button>
            )}
          </div>

          {/* Local folder upload tile */}
          <div
            className={`ob-tile ob-tile--single ${dragOver ? "ob-tile--dragover" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
          >
            <div className="ob-tile__logo">📁</div>
            <div className="ob-tile__name">Upload local folder</div>
            <p className="ob-tile__desc">
              Pick a folder from your machine. Inspira reads the file
              structure — same as GitHub, no OAuth needed.
            </p>
            {submitting === "upload" ? (
              <div className="ob-tile__progress">{uploadProgress}</div>
            ) : (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  webkitdirectory=""
                  directory=""
                  multiple
                  style={{ display: "none" }}
                  onChange={onFileChange}
                />
                <button
                  type="button"
                  className="ob-tile__cta"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={submitting !== null}
                >
                  Choose a folder →
                </button>
              </>
            )}
          </div>
        </div>

        {error ? (
          <div className="ob-inline-error" role="alert">
            {error}
          </div>
        ) : null}

        <div className="ob-below-tiles">
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              if (submitting) return;
              onNext(3, { skippedRepo: true });
            }}
          >
            Skip — I'll do this later →
          </a>
        </div>
      </div>
      <div className="ob-bottom">
        <a
          className="ob-back"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            if (submitting) return;
            onBack();
          }}
        >
          ← Back
        </a>
        <span />
      </div>
    </>
  );
}
