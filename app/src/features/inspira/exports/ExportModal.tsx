// Shared base modal for Send-to-Linear and Send-to-GitHub. Composes the
// app's standard Dialog shell + the export-specific cards (destination,
// issue preview, options) + footer with priority dropdown + Send button.
//
// Provider-specific labels (title, destination chip, primary CTA, success
// toast) come in via props from the per-provider wrappers; everything
// else — fetching project data and destination, options state, error
// handling — is shared.

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import { Dialog } from "../../../components/dialogs/Dialog";
import { toast } from "../../../components/ToastProvider";
import { api, type Decision, type Topic, type V2Project } from "../api";
import { IssuePreview } from "./IssuePreview";
import {
  DEFAULT_OPTIONS,
  type ConnectorDestination,
  type ExportProjectOptions,
  type ExportProvider,
  type ExportSuccess,
  type PriorityLabel,
} from "./types";
import "./exports.css";

export type ExportModalProps = {
  provider: ExportProvider;
  projectId: string | null;
  open: boolean;
  onClose: () => void;
};

type LoadState =
  | { status: "loading" }
  | {
      status: "loaded";
      project: V2Project;
      topics: Topic[];
      decisions: Decision[];
      destination: ConnectorDestination;
    }
  | { status: "error"; message: string };

const PROVIDER_COPY: Record<
  ExportProvider,
  {
    title: string;
    cta: string;
    sentToast: (s: ExportSuccess) => string;
    destinationLabel: string;
  }
> = {
  linear: {
    title: "Send to Linear",
    cta: "Send to Linear →",
    sentToast: (s) =>
      s.identifier ? `Created ${s.identifier} in Linear` : "Created in Linear",
    destinationLabel: "Linear team",
  },
  github: {
    title: "Push to GitHub",
    cta: "Push to GitHub →",
    sentToast: (s) =>
      typeof s.issue_number === "number"
        ? `Created issue #${s.issue_number} on GitHub`
        : "Created issue on GitHub",
    destinationLabel: "GitHub repo",
  },
};

export function ExportModal({
  provider,
  projectId,
  open,
  onClose,
}: ExportModalProps) {
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [options, setOptions] = useState<ExportProjectOptions>(DEFAULT_OPTIONS);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitErrorCode, setSubmitErrorCode] = useState<string | null>(null);
  const sourceFeedbackId = useId();

  // Reset options + error state on open / projectId change so re-opening
  // the modal doesn't leak state from the previous run.
  useEffect(() => {
    if (open) {
      setOptions(DEFAULT_OPTIONS);
      setSubmitError(null);
      setSubmitErrorCode(null);
    }
  }, [open, projectId, provider]);

  // Fetch project + destination in parallel on open.
  const fetchTokenRef = useRef(0);
  useEffect(() => {
    if (!open || !projectId) {
      return;
    }
    setLoad({ status: "loading" });
    const token = ++fetchTokenRef.current;
    (async () => {
      try {
        const [projectEnvelope, topicsEnvelope, decisionsEnvelope, destination] =
          await Promise.all([
            api.getV2Project(projectId),
            api.listTopics(projectId),
            api.listProjectDecisions(projectId),
            api.getConnectorDestination(provider),
          ]);
        if (token !== fetchTokenRef.current) {
          return; // a newer fetch superseded this one
        }
        setLoad({
          status: "loaded",
          project: projectEnvelope.project,
          topics: topicsEnvelope.topics,
          decisions: decisionsEnvelope.decisions,
          destination,
        });
      } catch (err) {
        if (token !== fetchTokenRef.current) {
          return;
        }
        setLoad({
          status: "error",
          message: err instanceof Error ? err.message : "Failed to load project",
        });
      }
    })();
  }, [open, projectId, provider]);

  const sourceFeedbackCount = useMemo(() => {
    if (load.status !== "loaded") {
      return 0;
    }
    // Count unique cited items would require a separate API; the modal
    // preview shows whether any are cited, with the exact count provided
    // by the backend at send time. For preview purposes, count distinct
    // confirmed decisions as a proxy upper bound (close enough for the
    // header chip in the preview card).
    return load.decisions.filter((d) => d.status !== "retracted").length;
  }, [load]);

  const onSubmit = useCallback(async () => {
    if (!projectId || load.status !== "loaded") {
      return;
    }
    if (!load.destination.configured) {
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    setSubmitErrorCode(null);
    try {
      const result =
        provider === "linear"
          ? await api.exportProjectToLinear(projectId, options)
          : await api.exportProjectToGitHub(projectId, options);
      const copy = PROVIDER_COPY[provider];
      const message = copy.sentToast(result);
      toast.success(message, {
        actionLabel: result.issue_url ? "Open →" : undefined,
        onAction: result.issue_url
          ? () =>
              window.open(
                result.issue_url,
                "_blank",
                "noopener,noreferrer",
              )
          : undefined,
      });
      onClose();
    } catch (err) {
      const detail = parseExportError(err);
      setSubmitError(detail.message);
      setSubmitErrorCode(detail.code);
    } finally {
      setSubmitting(false);
    }
  }, [projectId, load, provider, options, onClose]);

  const copy = PROVIDER_COPY[provider];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={copy.title}
      width={560}
      primaryAction={{
        label: copy.cta,
        onClick: onSubmit,
        busy: submitting,
        disabled:
          load.status !== "loaded" ||
          !load.destination.configured ||
          submitting,
      }}
      secondaryAction={{
        label: "Cancel",
        onClick: onClose,
      }}
    >
      <p className="exports__attribution">
        Issue body sourced from Inspira's Decision Summary.
      </p>

      {load.status === "loading" && (
        <div className="exports__state" role="status">
          Loading project details…
        </div>
      )}

      {load.status === "error" && (
        <div className="exports__state exports__state--error" role="alert">
          {load.message}
        </div>
      )}

      {load.status === "loaded" && (
        <>
          <div className="ex-card">
            <div className="ex-card__label">Destination</div>
            <div className="ex-dest">
              <div className="ex-dest__logo" aria-hidden="true">
                {provider === "github" ? "GH" : "Li"}
              </div>
              <div className="ex-dest__info">
                <div className="ex-dest__name">{copy.destinationLabel}</div>
                <div className="ex-dest__path">
                  {load.destination.configured
                    ? load.destination.display
                    : load.destination.hint ||
                      `Configure default destination for ${provider}.`}
                </div>
              </div>
              <button
                type="button"
                className="ex-dest__change"
                aria-disabled="true"
                title="Coming soon"
                disabled
              >
                Change →
              </button>
            </div>
          </div>

          <div className="ex-card">
            <div className="ex-card__label">Issue preview</div>
            <IssuePreview
              project={load.project}
              topics={load.topics}
              decisions={load.decisions}
              provider={provider}
              showCanvasLink={options.include_canvas_link}
              showSourceFeedback={options.include_source_feedback}
              sourceFeedbackCount={sourceFeedbackCount}
            />
          </div>

          <div className="ex-card">
            <div className="ex-card__label">Options</div>
            <Toggle
              label="Include link back to Inspira canvas"
              hint="So engineers can click through to the AI's full reasoning."
              checked={options.include_canvas_link}
              onChange={(v) =>
                setOptions((prev) => ({ ...prev, include_canvas_link: v }))
              }
            />
            <Toggle
              label="Include source feedback items"
              hint="Customer-impact items get linked so engineers see the user voice."
              checked={options.include_source_feedback}
              onChange={(v) =>
                setOptions((prev) => ({
                  ...prev,
                  include_source_feedback: v,
                }))
              }
              describedBy={sourceFeedbackId}
            />
            <PriorityToggle
              checked={options.apply_priority_label}
              priority={options.priority_label}
              onChangeChecked={(v) =>
                setOptions((prev) => ({ ...prev, apply_priority_label: v }))
              }
              onChangePriority={(p) =>
                setOptions((prev) => ({ ...prev, priority_label: p }))
              }
            />
          </div>

          {submitError && (
            <div
              className="exports__error"
              role="alert"
              data-code={submitErrorCode || ""}
            >
              {submitError}
            </div>
          )}

          <div className="exports__edit-note">
            <button
              type="button"
              className="exports__edit"
              aria-disabled="true"
              title="Coming soon"
              disabled
            >
              Edit issue body →
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
  describedBy,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  describedBy?: string;
}) {
  return (
    <label className="ex-opt">
      <span
        className={`ex-opt__check${checked ? " checked" : ""}`}
        aria-hidden="true"
      >
        {checked ? "✓" : ""}
      </span>
      <input
        type="checkbox"
        className="exports__visually-hidden"
        checked={checked}
        onChange={(e) => onChange(e.currentTarget.checked)}
        aria-describedby={describedBy}
      />
      <span className="ex-opt__text">
        <span>{label}</span>
        {hint && <span className="ex-opt__hint" id={describedBy}>{hint}</span>}
      </span>
    </label>
  );
}

function PriorityToggle({
  checked,
  priority,
  onChangeChecked,
  onChangePriority,
}: {
  checked: boolean;
  priority: PriorityLabel;
  onChangeChecked: (v: boolean) => void;
  onChangePriority: (p: PriorityLabel) => void;
}) {
  return (
    <label className="ex-opt">
      <span
        className={`ex-opt__check${checked ? " checked" : ""}`}
        aria-hidden="true"
      >
        {checked ? "✓" : ""}
      </span>
      <input
        type="checkbox"
        className="exports__visually-hidden"
        checked={checked}
        onChange={(e) => onChangeChecked(e.currentTarget.checked)}
      />
      <span className="ex-opt__text">
        <span>Tag with priority label</span>
        <select
          className="ex-opt__dropdown"
          disabled={!checked}
          value={priority}
          onChange={(e) => onChangePriority(e.currentTarget.value as PriorityLabel)}
        >
          <option value="P0">P0 · severity 5</option>
          <option value="P1">P1 · severity 4</option>
          <option value="P2">P2 · severity 3</option>
        </select>
      </span>
    </label>
  );
}

function parseExportError(err: unknown): { message: string; code: string | null } {
  if (!(err instanceof Error)) {
    return { message: "Unexpected error.", code: null };
  }
  // Match the `POST {path} failed: {status} {statusText} — {detail}`
  // shape produced by the api.ts postJson helper. Detail is JSON.
  const match = err.message.match(/—\s*(\{.*\})$/s);
  if (match) {
    try {
      const detailWrapper = JSON.parse(match[1]);
      const detail = detailWrapper.detail ?? detailWrapper;
      const code: string | null =
        typeof detail?.code === "string" ? detail.code : null;
      const provider: string =
        typeof detail?.provider === "string" ? detail.provider : "this provider";
      switch (code) {
        case "connector_not_configured":
          return {
            message: `Connect ${provider} first in Connectors settings, then try again.`,
            code,
          };
        case "destination_not_configured":
          return {
            message: `Configure a default destination for ${provider} before sending.`,
            code,
          };
        case "upstream_unauthorized":
          return {
            message: `${provider} rejected the request — reconnect the integration.`,
            code,
          };
        case "upstream_rate_limited":
          return {
            message: `${provider} rate-limited the request. Try again in a minute.`,
            code,
          };
        case "upstream_transient":
          return {
            message: `${provider} had a transient error. Try again.`,
            code,
          };
        case "github_app_not_configured":
          return {
            message:
              "GitHub App is not configured on this deployment — contact your admin.",
            code,
          };
        default:
          break;
      }
    } catch {
      // fall through
    }
  }
  return { message: err.message, code: null };
}
