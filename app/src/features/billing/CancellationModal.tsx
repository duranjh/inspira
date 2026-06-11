// Inspira — Cancellation modal (Tier 2, B6).
//
// Three sub-states: entry → survey → success. Kept warm and low-chrome; the
// only danger-weight action is the final "Cancel my plan" button, and even
// that is sentence case. On success we fire `onCanceled` so the parent can
// refresh its subscription view.

import {
  useCallback,
  useEffect,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import { billingApi, type CancelReason, type PlanSlug } from "./api";
import { formatDate } from "../../i18n/format";
import { t } from "../../i18n";

export type CancellationModalProps = {
  open: boolean;
  plan: PlanSlug;
  periodEnd: string;
  onClose: () => void;
  onCanceled?: () => void;
};

type Stage = "entry" | "survey" | "success";

const REASONS: CancelReason[] = [
  "too_expensive",
  "missing_feature",
  "switching_tool",
  "not_using",
  "other",
];

function planLabel(plan: PlanSlug): string {
  switch (plan) {
    case "free":
      return "Free";
    case "pro":
      return "Pro";
    case "team":
      // Slug stays "team" for backward-compat; user-facing label is "Frontier" post-2026-04-28 rebrand.
      return "Frontier";
    default:
      return plan;
  }
}

export function CancellationModal({
  open,
  plan,
  periodEnd,
  onClose,
  onCanceled,
}: CancellationModalProps) {
  const [stage, setStage] = useState<Stage>("entry");
  const [reason, setReason] = useState<CancelReason>("too_expensive");
  const [feedback, setFeedback] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      // Reset on close.
      setStage("entry");
      setReason("too_expensive");
      setFeedback("");
      setSubmitting(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (submitting) return;
      setSubmitting(true);
      try {
        await billingApi.cancelSubscription({
          reason,
          feedback: feedback.trim() || undefined,
        });
        setStage("success");
        onCanceled?.();
      } catch {
        // Keep user on survey so they can retry; backend error surfaces as
        // a disabled-submit blip. Broader toast plumbing is outside scope.
      } finally {
        setSubmitting(false);
      }
    },
    [feedback, onCanceled, reason, submitting],
  );

  if (!open) return null;

  const pLabel = planLabel(plan);
  const endDate = formatDate(periodEnd);

  return (
    <div
      className="billing-modal-scrim"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="billing-modal billing-modal--sm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="billing-cancel-title"
      >
        <button
          type="button"
          className="billing-modal__x"
          aria-label={t("billing.modal.close")}
          onClick={onClose}
        >
          ×
        </button>

        {stage === "entry" ? (
          <>
            <p className="billing-eyebrow">
              {t("billing.action.cancel_plan")}
            </p>
            <h3
              id="billing-cancel-title"
              className="billing-display billing-display--lg"
            >
              {t("billing.cancel.entry.title", { plan: pLabel })}
            </h3>
            <p className="billing-serif" style={{ marginTop: "10px" }}>
              {t("billing.cancel.entry.body", { date: endDate })}
            </p>
            <div
              className="billing-pm__actions"
              style={{
                marginTop: "24px",
                justifyContent: "flex-end",
              }}
            >
              <button
                type="button"
                className="billing-btn billing-btn--ghost"
                onClick={onClose}
              >
                {t("billing.cancel.entry.cta_keep", { plan: pLabel })}
              </button>
              <button
                type="button"
                className="billing-btn billing-btn--sage"
                onClick={() => setStage("survey")}
              >
                {t("billing.cancel.entry.cta_continue")}
              </button>
            </div>
          </>
        ) : stage === "survey" ? (
          <form onSubmit={handleSubmit}>
            <p className="billing-eyebrow">
              {t("billing.action.cancel_plan")}
            </p>
            <h3
              id="billing-cancel-title"
              className="billing-display billing-display--lg"
            >
              {t("billing.cancel.survey.title")}
            </h3>
            <p className="billing-serif" style={{ marginTop: "10px" }}>
              {t("billing.cancel.survey.body")}
            </p>

            <div
              role="radiogroup"
              aria-label={t("billing.cancel.survey.title")}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "10px",
                margin: "18px 0 14px",
              }}
            >
              {REASONS.map((r) => (
                <label
                  key={r}
                  className="billing-serif"
                  style={{
                    display: "flex",
                    gap: "10px",
                    alignItems: "center",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="radio"
                    name="cancel-reason"
                    value={r}
                    checked={reason === r}
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setReason(e.target.value as CancelReason)
                    }
                    disabled={submitting}
                  />
                  <span>{t(`billing.cancel.reason.${r}`)}</span>
                </label>
              ))}
            </div>

            <label className="billing-field">
              <span className="billing-field__label">
                {t("billing.cancel.feedback_label")}
              </span>
              <textarea
                className="billing-field__input"
                rows={3}
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                disabled={submitting}
              />
            </label>

            <div
              className="billing-pm__actions"
              style={{
                marginTop: "20px",
                justifyContent: "flex-end",
              }}
            >
              <button
                type="button"
                className="billing-btn billing-btn--ghost"
                onClick={() => setStage("entry")}
                disabled={submitting}
              >
                {t("billing.pm_modal.cancel")}
              </button>
              <button
                type="submit"
                className="billing-btn billing-btn--danger"
                disabled={submitting}
              >
                {submitting
                  ? t("billing.cancel.submitting")
                  : t("billing.cancel.submit")}
              </button>
            </div>
          </form>
        ) : (
          <>
            <p className="billing-eyebrow">
              {t("billing.action.cancel_plan")}
            </p>
            <h3
              id="billing-cancel-title"
              className="billing-display billing-display--lg"
            >
              {t("billing.cancel.success.title")}
            </h3>
            <p className="billing-serif" style={{ marginTop: "10px" }}>
              {t("billing.cancel.success.body", {
                plan: pLabel,
                date: endDate,
              })}
            </p>
            <div
              className="billing-pm__actions"
              style={{
                marginTop: "22px",
                justifyContent: "flex-end",
              }}
            >
              <button
                type="button"
                className="billing-btn billing-btn--sage"
                onClick={onClose}
              >
                {t("billing.cancel.success.cta")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
