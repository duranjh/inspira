// Inspira — Members seat meter (Tier 3, B15).
//
// Editorial seat strip at the top of the Members list. Sage fill by
// default; switches to gold when the workspace is at its seat cap so the
// eye picks up the guardrail without a warning banner.

import { t } from "../../i18n";

export type MembersSeatMeterProps = {
  seatsUsed: number;
  seatsLimit: number | null;
  planLabel: string;
  note?: string;
};

export function MembersSeatMeter({
  seatsUsed,
  seatsLimit,
  planLabel,
  note,
}: MembersSeatMeterProps) {
  const pct =
    seatsLimit && seatsLimit > 0
      ? Math.min(100, Math.round((seatsUsed / seatsLimit) * 100))
      : Math.min(100, seatsUsed * 12);
  const atCap = seatsLimit != null && seatsUsed >= seatsLimit;

  const numText =
    seatsLimit == null
      ? t("members.seats.num_unlimited", {
          used: seatsUsed,
          plan: planLabel,
        })
      : t("members.seats.num", {
          used: seatsUsed,
          limit: seatsLimit,
          plan: planLabel,
        });

  return (
    <div className="members-seat-strip" role="group" aria-label={t("members.seats.aria")}>
      <div className="members-seat-strip__meter">
        <div className="members-seat-strip__head">
          <span className="members-seat-strip__title">
            {t("members.seats.title")}
          </span>
          <span className="members-seat-strip__num">{numText}</span>
        </div>
        <div className="members-seat-strip__track">
          <div
            className={
              atCap
                ? "members-seat-strip__fill members-seat-strip__fill--cap"
                : "members-seat-strip__fill"
            }
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      {note ? (
        <p className="members-seat-strip__note">
          <em>{note}</em>
        </p>
      ) : null}
    </div>
  );
}
