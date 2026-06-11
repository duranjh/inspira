// Inspira — export-options dialog.
//
// Four formats, presented as warm-editorial radio cards:
//   - PDF       (default) — printable snapshot (html2pdf.js path)
//   - Markdown  — edit anywhere (Obsidian, Notion, plain text)
//   - JSON      — raw structure (for devs and re-imports)
//   - CSV       — spreadsheet-friendly (topics + relationships as tables)
//
// A "What you'll get" preview section below the cards updates with the
// selection so the user knows the shape of the output before downloading.
//
// Callback behavior: the dialog delegates the actual export to its parent
// via `onExport(format)`. The parent is responsible for fetching any
// additional data (turns, decisions), assembling it, and calling the
// matching export.ts helper. The dialog itself never touches the canvas
// state — it's purely a picker.

import {
  useCallback,
  useEffect,
  useState,
  type ChangeEvent,
} from "react";

import { Dialog } from "./Dialog";
import { t } from "../../i18n";

// Extended format type. `"share"` and `"print"` linger in the union for
// backward compatibility with callers (and with the existing handler in
// InspiraApp) even though the dialog no longer shows them as options —
// they're handled via separate entry points (Share button, browser print).
//
// The active picker only exposes: pdf, markdown, json, csv. If a caller
// programmatically selects one of the legacy values, onExport will still
// be invoked with that value, but the radio group will silently fall back
// to `pdf` as the default.
export type ExportFormat =
  | "pdf"
  | "markdown"
  | "json"
  | "csv"
  // --- legacy / external entry points (not rendered as radio cards) ---
  | "share"
  | "docx"
  | "txt"
  | "print";

export type ExportOptionsDialogProps = {
  open: boolean;
  onExport: (format: ExportFormat) => Promise<void>;
  onClose: () => void;
  /** Present for backward compat; unused by the current four-format picker. */
  onOpenShare?: () => void;
};

type PickerFormat = "pdf" | "markdown" | "json" | "csv";

type OptionConfig = {
  value: PickerFormat;
  labelKey: string;
  descKey: string;
  previewKey: string;
};

const OPTIONS: OptionConfig[] = [
  {
    value: "pdf",
    labelKey: "export.card.pdf.label",
    descKey: "export.card.pdf.description",
    previewKey: "export.preview.pdf",
  },
  {
    value: "markdown",
    labelKey: "export.card.markdown.label",
    descKey: "export.card.markdown.description",
    previewKey: "export.preview.markdown",
  },
  {
    value: "json",
    labelKey: "export.card.json.label",
    descKey: "export.card.json.description",
    previewKey: "export.preview.json",
  },
  {
    value: "csv",
    labelKey: "export.card.csv.label",
    descKey: "export.card.csv.description",
    previewKey: "export.preview.csv",
  },
];

function RadioCard({
  config,
  selected,
  busy,
  onChange,
}: {
  config: OptionConfig;
  selected: boolean;
  busy: boolean;
  onChange: (e: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <label
      className={
        "dlg__radio-option" +
        (selected ? " dlg__radio-option--selected" : "")
      }
    >
      <input
        type="radio"
        name="dlg-export-format"
        value={config.value}
        checked={selected}
        onChange={onChange}
        disabled={busy}
      />
      <span className="dlg__radio-text">
        <span className="dlg__radio-title">{t(config.labelKey)}</span>
        <span className="dlg__radio-desc">{t(config.descKey)}</span>
      </span>
    </label>
  );
}

export function ExportOptionsDialog({
  open,
  onExport,
  onClose,
}: ExportOptionsDialogProps) {
  const [format, setFormat] = useState<PickerFormat>("pdf");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Reset to PDF (safest default) each time the dialog opens.
  useEffect(() => {
    if (open) {
      setFormat("pdf");
      setBusy(false);
      setError(null);
    }
  }, [open]);

  const handleChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setFormat(e.target.value as PickerFormat);
  }, []);

  const handleSubmit = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onExport(format);
    } catch (err) {
      console.error("[Inspira] export failed", err);
      setError(t("export_dialog.error_fallback"));
      setBusy(false);
    }
  }, [busy, format, onExport]);

  // Find the currently selected option so we can show its preview.
  const selectedOption =
    OPTIONS.find((o) => o.value === format) ?? OPTIONS[0];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("export_dialog.title")}
      width={540}
      primaryAction={{
        label: t("export.button_download"),
        onClick: handleSubmit,
        busy,
      }}
      secondaryAction={{
        label: t("export_dialog.action_cancel"),
        onClick: onClose,
      }}
    >
      <div
        className="dlg__radio-group"
        role="radiogroup"
        aria-label={t("export_dialog.format_aria")}
      >
        {OPTIONS.map((cfg) => (
          <RadioCard
            key={cfg.value}
            config={cfg}
            selected={format === cfg.value}
            busy={busy}
            onChange={handleChange}
          />
        ))}
      </div>

      <section className="dlg__export-preview" aria-live="polite">
        <p className="dlg__export-preview-eyebrow">
          {t("export.preview_heading")}
        </p>
        <p className="dlg__export-preview-body">
          {t(selectedOption.previewKey)}
        </p>
      </section>

      {error && <div className="dlg__share-error">{error}</div>}

      <style>{`
        .dlg__export-preview {
          margin-top: 20px;
          padding: 14px 16px;
          background: var(--paper-2);
          border: 1px solid var(--paper-edge);
          border-radius: 4px;
        }
        .dlg__export-preview-eyebrow {
          font-family: var(--ff-mono);
          font-size: 10px;
          letter-spacing: 0.18em;
          text-transform: uppercase;
          color: var(--ink-3);
          margin: 0 0 6px 0;
        }
        .dlg__export-preview-body {
          font-family: var(--ff-serif);
          font-size: 14px;
          line-height: 1.5;
          color: var(--ink-2);
          margin: 0;
        }
      `}</style>
    </Dialog>
  );
}
