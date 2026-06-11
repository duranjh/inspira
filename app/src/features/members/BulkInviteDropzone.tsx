// Inspira — Bulk invite dropzone (Tier 3, B15).
//
// Team-plan-only drop zone for CSVs with `email,role` columns. On
// non-Team plans the zone renders a quiet upgrade card that opens the
// plan comparison modal. Parsed rows are shown inline before submit so
// the owner can spot typos before a batch invite fires.

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";

import {
  billingApi,
  PlanComparisonModal,
  type MemberRole,
  type PlanSlug,
} from "../billing";
import { t } from "../../i18n";

export type BulkInviteDropzoneProps = {
  planSlug: PlanSlug;
};

type ParsedRow = {
  email: string;
  role: MemberRole;
  error?: string;
};

const VALID_ROLES: ReadonlyArray<MemberRole> = [
  "admin",
  "planner",
  "reviewer",
  "viewer",
];

function parseCsv(text: string): ParsedRow[] {
  const rows: ParsedRow[] = [];
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length === 0) return rows;

  // Detect optional header row (email,role).
  const first = lines[0]
    .split(",")
    .map((c) => c.trim().toLowerCase());
  const hasHeader = first[0] === "email" && first[1] === "role";
  const dataLines = hasHeader ? lines.slice(1) : lines;

  for (const line of dataLines) {
    const [rawEmail, rawRole] = line
      .split(",")
      .map((c) => c.trim());
    const email = (rawEmail ?? "").toLowerCase();
    const role = ((rawRole ?? "").toLowerCase() as MemberRole) || "viewer";
    const validEmail = /.+@.+\..+/.test(email);
    const validRole = (VALID_ROLES as ReadonlyArray<string>).includes(role);
    rows.push({
      email,
      role: validRole ? role : "viewer",
      error: !validEmail
        ? "invalid_email"
        : !validRole
          ? "invalid_role"
          : undefined,
    });
  }
  return rows;
}

export function BulkInviteDropzone({ planSlug }: BulkInviteDropzoneProps) {
  const [file, setFile] = useState<File | null>(null);
  const [parsed, setParsed] = useState<ParsedRow[]>([]);
  const [status, setStatus] = useState<null | "idle" | "submitting" | "done">(
    null,
  );
  const [resultNote, setResultNote] = useState<string | null>(null);
  const [planModalOpen, setPlanModalOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const isTeam = planSlug === "team";

  const readFile = useCallback(async (f: File) => {
    setFile(f);
    setStatus("idle");
    setResultNote(null);
    try {
      const text = await f.text();
      setParsed(parseCsv(text));
    } catch {
      setParsed([]);
      setResultNote(t("members.bulk.parse_error"));
    }
  }, []);

  const handleFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (f) void readFile(f);
    },
    [readFile],
  );

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      const f = e.dataTransfer.files?.[0];
      if (f) void readFile(f);
    },
    [readFile],
  );

  const handleSubmit = useCallback(async () => {
    if (!file) return;
    setStatus("submitting");
    setResultNote(null);
    try {
      const res = await billingApi.bulkInviteMembers({ csv: file });
      setStatus("done");
      setResultNote(
        t("members.bulk.done", {
          invited: res.invited,
          errors: res.errors.length,
        }),
      );
    } catch {
      setStatus("idle");
      setResultNote(t("members.bulk.submit_error"));
    }
  }, [file]);

  const validCount = useMemo(
    () => parsed.filter((r) => !r.error).length,
    [parsed],
  );

  if (!isTeam) {
    return (
      <div className="members-bulk-card members-bulk-card--locked">
        <p className="members-bulk-card__title">
          {t("members.bulk.locked_title")}
        </p>
        <p className="members-bulk-card__desc">
          {t("members.bulk.locked_desc")}
        </p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button
            type="button"
            className="billing-btn billing-btn--ghost billing-btn--sm"
            onClick={() => setPlanModalOpen(true)}
          >
            {t("members.bulk.locked_cta")}
          </button>
        </div>
        <PlanComparisonModal
          open={planModalOpen}
          onClose={() => setPlanModalOpen(false)}
        />
      </div>
    );
  }

  return (
    <div
      className="members-bulk-card"
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
    >
      <p className="members-bulk-card__title">
        {t("members.bulk.title")}
      </p>
      <p className="members-bulk-card__desc">
        {t("members.bulk.desc")}
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button
          type="button"
          className="billing-btn billing-btn--ghost billing-btn--sm"
          onClick={() => inputRef.current?.click()}
          disabled={status === "submitting"}
        >
          {file ? t("members.bulk.replace_cta") : t("members.bulk.upload_cta")}
        </button>
        {file && validCount > 0 ? (
          <button
            type="button"
            className="billing-btn billing-btn--sage billing-btn--sm"
            onClick={handleSubmit}
            disabled={status === "submitting"}
          >
            {status === "submitting"
              ? t("members.bulk.submitting")
              : t("members.bulk.submit_cta", { count: validCount })}
          </button>
        ) : null}
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          onChange={handleFileChange}
          style={{ display: "none" }}
        />
      </div>

      {parsed.length > 0 ? (
        <ul className="members-bulk-list">
          {parsed.map((row, idx) => (
            <li
              key={`${row.email}-${idx}`}
              className={
                row.error
                  ? "members-bulk-list__row members-bulk-list__row--err"
                  : "members-bulk-list__row"
              }
            >
              <span className="members-bulk-list__email">{row.email}</span>
              <span className="members-bulk-list__role">
                {t(`members.role.${row.role}`)}
              </span>
              {row.error ? (
                <span className="members-bulk-list__note">
                  {t(`members.bulk.row_error_${row.error}`)}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {resultNote ? (
        <p
          className="billing-status"
          role="status"
          aria-live="polite"
          style={{ marginTop: 10 }}
        >
          {resultNote}
        </p>
      ) : null}
    </div>
  );
}
