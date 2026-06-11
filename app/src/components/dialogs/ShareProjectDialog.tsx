// Inspira — share-project dialog.
//
// Two shapes depending on whether the project already has a read-only link:
//   * No link yet → a "Generate link" call-to-action inside a dashed-border
//     empty-state card. Clicking it calls `onGenerateLink()`, which is
//     expected to return the freshly-generated URL.
//   * Link exists → a monospace input showing the URL + a "Copy" button
//     that writes to the clipboard. Below, a small "Revoke link" affordance
//     calls `onRevoke()`.
//
// The caller owns backend wiring. These endpoints don't exist yet at the
// time of writing — callers can pass handlers that reject with "Coming
// soon" and we'll surface the error inline without breaking the dialog.

import { useCallback, useEffect, useRef, useState } from "react";

import { Dialog } from "./Dialog";

import { t } from "../../i18n";

export type ShareProjectDialogProps = {
  open: boolean;
  currentLink?: string | null;
  onGenerateLink: () => Promise<string>;
  onRevoke: () => Promise<void>;
  onClose: () => void;
};

export function ShareProjectDialog({
  open,
  currentLink,
  onGenerateLink,
  onRevoke,
  onClose,
}: ShareProjectDialogProps) {
  const linkInputRef = useRef<HTMLInputElement | null>(null);
  // Local link mirrors `currentLink` but updates immediately after a
  // successful `onGenerateLink` call so the UI reflects the new state
  // without waiting for a parent re-render.
  const [localLink, setLocalLink] = useState<string | null>(
    currentLink ?? null,
  );
  const [generating, setGenerating] = useState<boolean>(false);
  const [revoking, setRevoking] = useState<boolean>(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);

  // Keep local state in sync with prop changes across openings.
  useEffect(() => {
    if (open) {
      setLocalLink(currentLink ?? null);
      setGenerating(false);
      setRevoking(false);
      setCopyState("idle");
      setError(null);
    }
  }, [open, currentLink]);

  const handleGenerate = useCallback(async () => {
    if (generating) return;
    setGenerating(true);
    setError(null);
    try {
      const link = await onGenerateLink();
      setLocalLink(link);
    } catch (err) {
      console.error("[Inspira] share link generation failed", err);
      setError(t("errors.share_link_generate_failed"));
    } finally {
      setGenerating(false);
    }
  }, [generating, onGenerateLink]);

  const handleRevoke = useCallback(async () => {
    if (revoking) return;
    setRevoking(true);
    setError(null);
    try {
      await onRevoke();
      setLocalLink(null);
      setCopyState("idle");
    } catch (err) {
      console.error("[Inspira] share link revoke failed", err);
      setError(t("errors.share_link_revoke_failed"));
    } finally {
      setRevoking(false);
    }
  }, [revoking, onRevoke]);

  const handleCopy = useCallback(async () => {
    if (!localLink) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(localLink);
      } else {
        // Fallback — select the input and use execCommand. Best-effort only;
        // modern browsers should always have the Clipboard API.
        linkInputRef.current?.select();
        const ok = document.execCommand?.("copy");
        if (!ok) throw new Error("Clipboard unavailable");
      }
      setCopyState("copied");
      window.setTimeout(() => setCopyState("idle"), 1500);
    } catch {
      setCopyState("error");
      window.setTimeout(() => setCopyState("idle"), 1800);
    }
  }, [localLink]);

  const hasLink = typeof localLink === "string" && localLink.length > 0;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("share_dialog.title")}
      secondaryAction={{
        label: t("share_dialog.action_done"),
        onClick: onClose,
      }}
    >
      <p className="dlg__share-description">
        {t("share_dialog.description")}
      </p>

      {hasLink ? (
        <div className="dlg__field">
          <label className="dlg__label" htmlFor="dlg-share-link-input">
            {t("share_dialog.link_label")}
          </label>
          <div className="dlg__share-link-row">
            <input
              id="dlg-share-link-input"
              ref={linkInputRef}
              className="dlg__input dlg__input--mono"
              type="text"
              value={localLink ?? ""}
              readOnly
              onFocus={(e) => e.currentTarget.select()}
            />
            <button
              type="button"
              className="dlg__copy-btn"
              onClick={() => void handleCopy()}
              data-state={copyState}
              aria-label={t("share_dialog.copy")}
            >
              {copyState === "copied"
                ? t("share_dialog.copied")
                : copyState === "error"
                  ? t("share_dialog.copy_failed")
                  : t("share_dialog.copy")}
            </button>
          </div>
        </div>
      ) : (
        <div className="dlg__share-empty">
          <button
            type="button"
            className="dlg__share-generate-btn"
            onClick={() => void handleGenerate()}
            disabled={generating}
          >
            {generating ? t("share_dialog.generating") : t("share_dialog.generate")}
          </button>
        </div>
      )}

      {hasLink && (
        <div className="dlg__share-revoke-row">
          <button
            type="button"
            className="dlg__share-revoke-btn"
            onClick={() => void handleRevoke()}
            disabled={revoking}
          >
            {t("share_dialog.revoke")}
          </button>
        </div>
      )}

      {error && <div className="dlg__share-error">{error}</div>}
    </Dialog>
  );
}
