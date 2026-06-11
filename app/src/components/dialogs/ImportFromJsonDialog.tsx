// Inspira — import-from-JSON dialog.
//
// Mirror of `ExportOptionsDialog` for the reverse direction. The user
// picks a `.json` file that was previously produced by `exportToJson`
// and we hand the parsed blob to a parent callback which POSTs it to
// /api/v2/projects/from-json.
//
// Flow:
//   1. Drop / pick a .json file.
//   2. We parse it client-side so any JSON error surfaces before the
//      network round-trip and we can short-circuit on schema mismatch.
//   3. An optional "Title override" field lets the user rename on
//      import (handy when re-importing their own export to experiment
//      without clobbering the original's name on the projects list).
//   4. Primary action: "Import" — calls `onSubmit(parsedBlob, title?)`.
//      The parent is responsible for actually creating the project and
//      routing to its canvas. The dialog only surfaces errors.
//
// Accessibility: the file input is a real <input type="file"> wrapped
// in a visually-styled label so it's keyboard-reachable via Tab. The
// hidden attribute stays off — browsers let screen readers announce
// it correctly that way. Drag-drop is NOT implemented in this pass
// (the file picker is enough for a first version and keeps the
// component tight); a follow-up could add it.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

import { Dialog } from "./Dialog";
import { t } from "../../i18n";

export type ImportFromJsonDialogProps = {
  open: boolean;
  /** Called with the parsed JSON blob + optional title override. */
  onSubmit: (blob: object, titleOverride?: string) => Promise<void>;
  onClose: () => void;
};

// Schema tag we recognise. Keep in sync with
// services/planning_studio_service/json_import.py :: SCHEMA_TAG and
// app/src/features/inspira/export.ts :: exportToJson.
const EXPECTED_SCHEMA = "inspira.canvas.v1";

// Hard cap on file size, purely to stop a user accidentally feeding a
// gigabyte blob through FileReader. The export path produces files
// well under a megabyte even for large canvases.
const MAX_JSON_BYTES = 10 * 1024 * 1024; // 10 MB

export function ImportFromJsonDialog({
  open,
  onSubmit,
  onClose,
}: ImportFromJsonDialogProps) {
  const [fileName, setFileName] = useState<string | null>(null);
  const [blob, setBlob] = useState<object | null>(null);
  const [titleOverride, setTitleOverride] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Reset every time the dialog opens so leftover state from a previous
  // session doesn't confuse the user.
  useEffect(() => {
    if (open) {
      setFileName(null);
      setBlob(null);
      setTitleOverride("");
      setBusy(false);
      setError(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }, [open]);

  const handleFilePicked = useCallback(
    async (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0] ?? null;
      if (!file) return;
      setError(null);
      setBlob(null);
      setFileName(file.name);

      if (file.size > MAX_JSON_BYTES) {
        setError(t("import_json_dialog.error_too_large"));
        return;
      }

      let text: string;
      try {
        text = await file.text();
      } catch {
        setError(t("import_json_dialog.error_read_failed"));
        return;
      }

      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        setError(t("import_json_dialog.error_not_json"));
        return;
      }

      if (
        !parsed ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        setError(t("import_json_dialog.error_not_json"));
        return;
      }

      // Early schema check — the server will reject mismatches too, but
      // telling the user up front is a friendlier experience and saves a
      // round-trip.
      const schemaTag = (parsed as Record<string, unknown>)["schema"];
      if (schemaTag !== EXPECTED_SCHEMA) {
        setError(
          t("import_json_dialog.error_wrong_schema", {
            expected: EXPECTED_SCHEMA,
            got: typeof schemaTag === "string" ? schemaTag : "(none)",
          }),
        );
        return;
      }

      setBlob(parsed as object);
    },
    [],
  );

  const handleSubmit = useCallback(async () => {
    if (!blob || busy) return;
    setBusy(true);
    setError(null);
    try {
      const trimmed = titleOverride.trim();
      await onSubmit(blob, trimmed.length > 0 ? trimmed : undefined);
      // Parent decides whether to close on success — typically by
      // changing the phase; we don't force it here.
    } catch (err) {
      console.error("[Inspira] JSON import failed", err);
      setError(t("import_json_dialog.error_fallback"));
      setBusy(false);
    }
  }, [blob, busy, onSubmit, titleOverride]);

  const canSubmit = blob !== null && !busy;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("import_json_dialog.title")}
      width={520}
      primaryAction={{
        label: t("import_json_dialog.action_import"),
        onClick: handleSubmit,
        disabled: !canSubmit,
        busy,
      }}
      secondaryAction={{
        label: t("import_json_dialog.action_cancel"),
        onClick: onClose,
      }}
    >
      <p className="dlg__import-intro">{t("import_json_dialog.intro")}</p>

      <div className="dlg__field">
        <label className="dlg__label" htmlFor="dlg-import-json-file">
          {t("import_json_dialog.file_label")}
        </label>
        <input
          id="dlg-import-json-file"
          ref={fileInputRef}
          className="dlg__input"
          type="file"
          accept="application/json,.json"
          onChange={(e) => {
            void handleFilePicked(e);
          }}
          disabled={busy}
        />
        {fileName ? (
          <p className="dlg__import-filename">{fileName}</p>
        ) : null}
      </div>

      <div className="dlg__field">
        <label className="dlg__label" htmlFor="dlg-import-json-title">
          {t("import_json_dialog.title_label")}
        </label>
        <input
          id="dlg-import-json-title"
          className="dlg__input"
          type="text"
          value={titleOverride}
          onChange={(e) => setTitleOverride(e.target.value)}
          placeholder={t("import_json_dialog.title_placeholder")}
          autoComplete="off"
          spellCheck={true}
          disabled={busy}
          maxLength={200}
        />
        <p className="dlg__import-hint">{t("import_json_dialog.title_hint")}</p>
      </div>

      {error && <div className="dlg__share-error">{error}</div>}

      <style>{`
        .dlg__import-intro {
          font-family: var(--ff-serif);
          font-size: 14px;
          line-height: 1.5;
          color: var(--ink-2);
          margin: 0 0 18px 0;
        }
        .dlg__import-filename {
          font-family: var(--ff-mono);
          font-size: 12px;
          color: var(--ink-3);
          margin: 6px 0 0 0;
        }
        .dlg__import-hint {
          font-family: var(--ff-sans);
          font-size: 11.5px;
          color: var(--ink-3);
          margin: 6px 0 0 0;
        }
      `}</style>
    </Dialog>
  );
}
