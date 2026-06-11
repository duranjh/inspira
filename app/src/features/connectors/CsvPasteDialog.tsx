// CSV / JSON paste-in dialog.
//
// C6 ships a UI-only stub: textarea + parse + preview. The
// "Import" button is wired but disabled — F4 lands the backend
// /api/v2/connectors/csv/import endpoint that persists the rows
// into feedback_items. This stub already validates the parse
// path so a partner can sanity-check their export shape before
// the import lands.
//
// Recognized shapes:
//   1. CSV with a header row containing at least `title` (or
//      `subject`, or `summary`). Optional columns: body / text /
//      message, author / name, author_email / email, source,
//      received_at / created_at / timestamp.
//   2. JSON array of objects with the same keys.
//
// Max paste size: 256 KB. Anything larger is rejected with a
// hint to chunk the import. (F4 backend will enforce its own
// per-row + per-batch limits.)

import { ReactElement, useState } from "react";

const MAX_PASTE_BYTES = 256 * 1024;

const TITLE_KEYS = ["title", "subject", "summary"];
const BODY_KEYS = ["body", "text", "message", "description"];

export interface ParsedFeedback {
  title: string;
  body: string;
  author: string;
  author_email: string;
  source: string;
  received_at: string;
  type_hint: string;
}

interface ParseResult {
  rows: ParsedFeedback[];
  format: "csv" | "json";
  error?: string;
}

function pick(
  obj: Record<string, unknown>,
  keys: readonly string[],
  defaultValue = "",
): string {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "string" && v.trim()) return v.trim();
    if (typeof v === "number") return String(v);
  }
  return defaultValue;
}

function normalizeRow(obj: Record<string, unknown>): ParsedFeedback | null {
  const title = pick(obj, TITLE_KEYS);
  if (!title) return null;
  return {
    title,
    body: pick(obj, BODY_KEYS),
    author: pick(obj, ["author", "name", "user"]),
    author_email: pick(obj, ["author_email", "email"]),
    source: pick(obj, ["source", "channel"]) || "csv-import",
    received_at: pick(obj, ["received_at", "created_at", "timestamp", "date"]),
    type_hint: pick(obj, ["type_hint", "type", "category"]),
  };
}

function parseCsv(input: string): ParseResult {
  const lines = input.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length < 2) {
    return { rows: [], format: "csv", error: "Need at least a header row + one data row." };
  }
  const header = parseCsvRow(lines[0]).map((c) => c.toLowerCase().trim());
  const titleIdx = header.findIndex((c) => TITLE_KEYS.includes(c));
  if (titleIdx === -1) {
    return {
      rows: [],
      format: "csv",
      error: `CSV needs a column named one of: ${TITLE_KEYS.join(", ")}.`,
    };
  }
  const rows: ParsedFeedback[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = parseCsvRow(lines[i]);
    const obj: Record<string, string> = {};
    header.forEach((h, idx) => {
      if (idx < cells.length) obj[h] = cells[idx];
    });
    const norm = normalizeRow(obj);
    if (norm) rows.push(norm);
  }
  return { rows, format: "csv" };
}

/** Minimal RFC-4180-ish CSV row parser. Handles quoted fields with
 *  escaped quotes ("She said ""hi""") but not embedded newlines —
 *  the row split above breaks on \n which is fine for the paste-
 *  in shape (multi-line bodies should be quoted+escaped, which
 *  the typical export format does). */
export function parseCsvRow(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      out.push(cur);
      cur = "";
    } else {
      cur += c;
    }
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

function parseJson(input: string): ParseResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(input);
  } catch (exc) {
    return {
      rows: [],
      format: "json",
      error: exc instanceof Error ? exc.message : "Invalid JSON.",
    };
  }
  if (!Array.isArray(parsed)) {
    return {
      rows: [],
      format: "json",
      error: "JSON must be an array of feedback objects.",
    };
  }
  const rows: ParsedFeedback[] = [];
  for (const item of parsed) {
    if (typeof item === "object" && item !== null) {
      const norm = normalizeRow(item as Record<string, unknown>);
      if (norm) rows.push(norm);
    }
  }
  return { rows, format: "json" };
}

export function parseFeedbackPaste(input: string): ParseResult {
  const trimmed = input.trim();
  if (!trimmed) {
    return { rows: [], format: "csv", error: "Paste a CSV or JSON export." };
  }
  // JSON dispatch: covers both arrays and bare objects. parseJson
  // surfaces the "must be an array" error for non-array shapes
  // instead of letting them fall through to the CSV parser
  // (where the error message wouldn't match what the partner
  // actually pasted).
  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    return parseJson(trimmed);
  }
  return parseCsv(trimmed);
}

export interface CsvPasteDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called when the user confirms import. C6 wires this to a
   *  no-op + toast; F4 wires the backend POST. */
  onImport: (rows: ParsedFeedback[]) => Promise<void> | void;
  /** Disable the import button (e.g., F4 backend not yet
   *  available). Parse + preview still works. */
  importDisabledReason?: string;
}

export function CsvPasteDialog({
  open,
  onClose,
  onImport,
  importDisabledReason,
}: CsvPasteDialogProps): ReactElement | null {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Brief success banner shown after a successful import; the BE
  // returns once items are persisted + auto-promoted, while the
  // LLM merge pass keeps running server-side. Surface that to the
  // partner so the dialog doesn't just blink shut.
  const [importedCount, setImportedCount] = useState<number | null>(null);

  if (!open) return null;

  const sizeBytes = new Blob([text]).size;
  const tooBig = sizeBytes > MAX_PASTE_BYTES;

  let parse: ParseResult | null = null;
  if (text.trim() && !tooBig) {
    parse = parseFeedbackPaste(text);
  }

  const canImport =
    !!parse && parse.rows.length > 0 && !parse.error && !importDisabledReason;

  const submit = async () => {
    if (!parse || parse.rows.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const count = parse.rows.length;
      await onImport(parse.rows);
      setImportedCount(count);
      setText("");
      window.setTimeout(() => {
        setImportedCount(null);
        onClose();
      }, 1200);
    } catch (exc) {
      setError(
        exc instanceof Error ? exc.message : "Import failed — try again.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="cw-dialog__backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="csv-paste-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="card cw-dialog cw-dialog--wide">
        <header className="cw-dialog__header">
          <h2 id="csv-paste-title" className="section-title">
            Drop in a feedback export
          </h2>
          <button
            type="button"
            className="btn btn--icon btn--ghost"
            onClick={onClose}
            aria-label="Close dialog"
            disabled={submitting}
          >
            ×
          </button>
        </header>
        <p className="meta cw-dialog__intro">
          Paste a CSV with at least a <code>title</code> column, or a
          JSON array of feedback objects. Inspira parses, dedupes, and
          classifies on import.
        </p>

        <textarea
          className="csv-paste__textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            'received_at,source,author,title,body\n2026-04-12,...,"Beta tester","Login fails on Safari","..."'
          }
          rows={10}
          spellCheck={false}
          disabled={submitting}
        />

        <div className="csv-paste__meta">
          {tooBig ? (
            <span className="csv-paste__error">
              Paste exceeds 256 KB — split into smaller chunks.
            </span>
          ) : parse?.error ? (
            <span className="csv-paste__error">{parse.error}</span>
          ) : parse ? (
            <span className="csv-paste__ok">
              {parse.rows.length} {parse.format.toUpperCase()} row
              {parse.rows.length === 1 ? "" : "s"} parsed
              {parse.rows.length > 0
                ? ` — preview: "${parse.rows[0].title.slice(0, 60)}${
                    parse.rows[0].title.length > 60 ? "…" : ""
                  }"`
                : ""}
            </span>
          ) : (
            <span className="csv-paste__hint">
              Paste rows above to preview the parsed shape.
            </span>
          )}
        </div>

        {importDisabledReason ? (
          <div className="cw-dialog__hint" role="status">
            {importDisabledReason}
          </div>
        ) : null}
        {error ? (
          <div className="cw-dialog__error" role="alert">
            {error}
          </div>
        ) : null}

        <div className="cw-dialog__actions">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={submit}
            disabled={!canImport || submitting}
          >
            {submitting
              ? "Importing…"
              : parse?.rows
                ? `Import ${parse.rows.length} item${
                    parse.rows.length === 1 ? "" : "s"
                  }`
                : "Import"}
          </button>
        </div>
      </div>
    </div>
  );
}
