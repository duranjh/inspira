// Wizard Step 3 — connect feedback (real wiring, 3 paths).
//
// Path A — Linear API key paste: Connect Linear → expand inline
// form → POST /api/v2/connectors/linear/connect → on 200 mark
// linearConnected. Linear is API-key based (NOT OAuth) per the
// existing /linear/connect endpoint.
//
// Path B — CSV / JSON file upload: drag-drop or click. Reuse the
// existing parseFeedbackPaste() (handles quoted commas + JSON
// arrays — addresses audit concern #3 better than a DIY split).
// POST /api/v2/connectors/csv/import.
//
// Path C — Sample / Skip: client-only state flags + advance.

import {
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";

import { connectLinear, importCsvRows } from "../../connectors/api";
import {
  parseFeedbackPaste,
  type ParsedFeedback,
} from "../../connectors/CsvPasteDialog";
import type { WizardState, WizardStep } from "./OnboardingWizard";

type Step3Props = {
  state: WizardState;
  onNext: (step: WizardStep, patch?: Partial<WizardState>) => void;
  onBack: () => void;
};

export function Step3ConnectFeedback({
  state: _state,
  onNext,
  onBack,
}: Step3Props) {
  const [linearOpen, setLinearOpen] = useState(false);
  const [linearKey, setLinearKey] = useState("");
  const [linearSubmitting, setLinearSubmitting] = useState(false);
  const [linearError, setLinearError] = useState<string | null>(null);
  const [linearOk, setLinearOk] = useState(false);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [parsedRows, setParsedRows] = useState<ParsedFeedback[] | null>(null);
  const [parsedFilename, setParsedFilename] = useState<string>("");
  const [csvSubmitting, setCsvSubmitting] = useState(false);
  const [csvError, setCsvError] = useState<string | null>(null);
  const [csvImportedCount, setCsvImportedCount] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState(false);

  async function handleConnectLinear() {
    setLinearError(null);
    if (!linearKey.trim()) {
      setLinearError("Paste your Linear API key.");
      return;
    }
    setLinearSubmitting(true);
    try {
      await connectLinear(linearKey.trim());
      setLinearSubmitting(false);
      setLinearOk(true);
      setLinearOpen(false);
      setLinearKey("");
    } catch (err) {
      setLinearSubmitting(false);
      const message = err instanceof Error ? err.message : "";
      if (message.includes("401") || message.includes("linear_auth_failed")) {
        setLinearError("Invalid Linear API key.");
      } else if (message.includes("429")) {
        setLinearError("Linear rate-limited the request. Try again shortly.");
      } else {
        setLinearError("Couldn't reach Linear. Try again.");
      }
    }
  }

  async function handleFileSelected(file: File) {
    setCsvError(null);
    setParsedRows(null);
    setParsedFilename(file.name);
    try {
      const text = await file.text();
      const result = parseFeedbackPaste(text);
      if (result.error) {
        setCsvError(result.error);
        return;
      }
      if (result.rows.length === 0) {
        setCsvError("No rows in that file.");
        return;
      }
      setParsedRows(result.rows);
    } catch {
      setCsvError("Couldn't read the file. Try again.");
    }
  }

  async function handleImport() {
    if (!parsedRows) return;
    setCsvSubmitting(true);
    setCsvError(null);
    try {
      const result = await importCsvRows(parsedRows);
      setCsvSubmitting(false);
      setCsvImportedCount(result.inserted);
      // Hold the success state briefly so the partner sees the import
      // landed instead of an instant phase swap to Step 4.
      window.setTimeout(() => {
        onNext(4, {
          csvImported: true,
          csvImportedRows: result.inserted,
        });
      }, 800);
    } catch (err) {
      setCsvSubmitting(false);
      const message = err instanceof Error ? err.message : "";
      if (message.includes("413") || message.includes("too_many_rows")) {
        setCsvError("That file has too many rows. Try a smaller import.");
      } else if (message.includes("422") || message.includes("no_rows")) {
        setCsvError("No rows to import.");
      } else {
        setCsvError("Import failed. Try again.");
      }
    }
  }

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) void handleFileSelected(f);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void handleFileSelected(f);
  }

  // Continue button visible once Linear connected OR CSV ready to import.
  const linearReady = linearOk && !parsedRows;

  return (
    <>
      <div className="ob-center ob-center--wide">
        <h1 className="ob-headline">
          Where does customer feedback live for you?
        </h1>
        <p className="ob-subtitle">
          Inspira reads, clusters, and prioritizes — so your team comes in
          after the triage.
        </p>
        <div className="ob-above-tiles">
          Today, Linear and CSV / JSON. Intercom, Productboard, Salesforce,
          Help Scout in the next 4 weeks.
        </div>
        <div className="ob-tiles">
          {/* Linear tile */}
          <div className="ob-tile">
            <div className="ob-tile__logo">Li</div>
            <div className="ob-tile__name">Linear</div>
            <p className="ob-tile__desc">
              Sync your issue tracker. Inspira reads tickets, priorities, and
              team assignments.
            </p>
            {linearOk ? (
              <div className="ob-tile__check">✓ Linear connected</div>
            ) : linearOpen ? (
              <div className="ob-tile__form">
                <input
                  type="password"
                  placeholder="lin_api_..."
                  value={linearKey}
                  onChange={(e) => setLinearKey(e.target.value)}
                  disabled={linearSubmitting}
                  autoFocus
                  aria-label="Linear API key"
                />
                <div className="ob-tile__form-actions">
                  <button
                    type="button"
                    className="ob-tile__cta"
                    onClick={handleConnectLinear}
                    disabled={linearSubmitting || !linearKey.trim()}
                  >
                    {linearSubmitting ? "Connecting…" : "Connect"}
                  </button>
                  <button
                    type="button"
                    className="ob-tile__cta ob-tile__cta--secondary"
                    onClick={() => {
                      setLinearOpen(false);
                      setLinearKey("");
                      setLinearError(null);
                    }}
                    disabled={linearSubmitting}
                  >
                    Cancel
                  </button>
                </div>
                {linearError ? (
                  <div className="ob-tile__error">{linearError}</div>
                ) : null}
              </div>
            ) : (
              <button
                type="button"
                className="ob-tile__cta"
                onClick={() => setLinearOpen(true)}
                disabled={csvSubmitting}
              >
                Connect Linear →
              </button>
            )}
          </div>

          {/* CSV / JSON tile */}
          <div
            className={`ob-tile ob-tile--dropzone ${dragOver ? "ob-tile--dragover" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
          >
            <div className="ob-tile__logo">CSV</div>
            <div className="ob-tile__name">CSV / JSON paste-in</div>
            <p className="ob-tile__desc">
              Drop a file or click to upload. Inspira will parse, cluster, and
              prioritize.
            </p>
            {csvImportedCount !== null ? (
              <div className="ob-tile__check">
                ✓ Imported {csvImportedCount} row
                {csvImportedCount === 1 ? "" : "s"} — moving on…
              </div>
            ) : parsedRows ? (
              <>
                <div className="ob-tile__progress">
                  Loaded {parsedRows.length} rows from {parsedFilename}
                </div>
                <button
                  type="button"
                  className="ob-tile__cta"
                  onClick={handleImport}
                  disabled={csvSubmitting}
                >
                  {csvSubmitting
                    ? "Importing…"
                    : `Import ${parsedRows.length} rows →`}
                </button>
              </>
            ) : (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv,.json,text/csv,application/json"
                  style={{ display: "none" }}
                  onChange={onFileChange}
                />
                <button
                  type="button"
                  className="ob-tile__cta ob-tile__cta--secondary"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={csvSubmitting}
                >
                  Drop in feedback →
                </button>
                {/* Founder direction 2026-05-06: partners without
                    real feedback to hand should be able to grab a
                    10-row sample CSV and see the flow end-to-end. */}
                <a
                  href="/inspira-sample-feedback.csv"
                  download="inspira-sample-feedback.csv"
                  className="ob-tile__sample"
                  onClick={(e) => e.stopPropagation()}
                >
                  Use sample issues →
                </a>
              </>
            )}
            {csvError ? (
              <div className="ob-tile__error">{csvError}</div>
            ) : null}
          </div>
        </div>

        {linearReady ? (
          <button
            type="button"
            className="ob-cta"
            onClick={() => onNext(4, { linearConnected: true })}
          >
            Continue →
          </button>
        ) : null}

        <div className="ob-below-tiles">
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              onNext(4, { skippedFeedback: true });
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
