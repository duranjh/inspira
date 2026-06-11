// Inspira — Members list page (Tier 3, B15).
//
// Full-viewport overlay styled like BillingOverviewPage — same sticky top
// bar, same centred paper inner column — but scoped to member + seat
// management. Rows are one-per-person with role select, join date, and a
// quiet actions column. Planner / Reviewer / Viewer land on a soft
// "Contact billing owner" card instead of the list.
//
// The third-paid-member cap (on Free + Pro) defers to the existing quota
// modal via `showQuotaModal("third_paid_member")` so we never rebuild
// that surface twice.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import type { AuthedUser } from "../inspira/api";
import {
  billingApi,
  BillingOwnerTransferModal,
  showQuotaModal,
  type MemberRole,
  type PlanSlug,
  type Subscription,
  type WorkspaceMember,
} from "../billing";
import { formatDate } from "../../i18n/format";
import { t } from "../../i18n";

import { BulkInviteDropzone } from "./BulkInviteDropzone";
import { MembersSeatMeter } from "./MembersSeatMeter";

export type MembersListPageProps = {
  user: AuthedUser;
  onClose: () => void;
  forcedRole?: string | null;
  forcedPlan?: string | null;
};

const VIEWABLE_ROLES: ReadonlyArray<MemberRole> = [
  "admin",
  "planner",
  "reviewer",
  "viewer",
];

const ALL_ROLES: ReadonlyArray<MemberRole> = [
  "billing_owner",
  "admin",
  "planner",
  "reviewer",
  "viewer",
];

function coerceRole(value: string | null | undefined): MemberRole | null {
  if (!value) return null;
  return (ALL_ROLES as ReadonlyArray<string>).includes(value)
    ? (value as MemberRole)
    : null;
}

function coercePlan(value: string | null | undefined): PlanSlug | null {
  if (!value) return null;
  return value === "free" || value === "pro" || value === "team"
    ? (value as PlanSlug)
    : null;
}

function seatLimitForPlan(slug: PlanSlug): number | null {
  switch (slug) {
    case "free":
      return 2;
    case "pro":
      return 5;
    case "team":
      return 10;
    default:
      return null;
  }
}

function planLabel(slug: PlanSlug): string {
  switch (slug) {
    case "free":
      return t("members.plan.free");
    case "pro":
      return t("members.plan.pro");
    case "team":
      return t("members.plan.team");
    default:
      return slug;
  }
}

function isPaidRole(role: MemberRole): boolean {
  return role !== "viewer";
}

export function MembersListPage({
  user,
  onClose,
  forcedRole,
  forcedPlan,
}: MembersListPageProps) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [planSlug, setPlanSlug] = useState<PlanSlug>("pro");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<MemberRole>("planner");
  const [inviteStatus, setInviteStatus] = useState<string | null>(null);
  const [transferForId, setTransferForId] = useState<string | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);

  const forcedRoleValue = coerceRole(forcedRole);
  const forcedPlanValue = coercePlan(forcedPlan);

  // Load subscription (for plan) + members concurrently. Fall back to the
  // design-review defaults if the backend is unreachable so the surface
  // still renders.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    void (async () => {
      try {
        const [subRes, memRes] = await Promise.all([
          billingApi.getSubscription().catch<null>(() => null),
          billingApi.getMembers(),
        ]);
        if (!alive) return;
        const slug = forcedPlanValue
          ?? coercePlan((subRes as { subscription?: Subscription } | null)?.subscription?.plan?.slug)
          ?? "pro";
        setPlanSlug(slug);
        setMembers(memRes.members);
      } catch {
        if (!alive) return;
        setPlanSlug(forcedPlanValue ?? "pro");
        setMembers([]);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [forcedPlanValue]);

  // Esc closes, capture-phase so any inner handlers don't swallow it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  const seatLimit = seatLimitForPlan(planSlug);
  const seatsUsed = members.length;

  const currentOwner = useMemo(
    () => members.find((m) => m.role === "billing_owner") ?? null,
    [members],
  );

  // Resolve the effective role for the signed-in viewer. Default to owner
  // (parity with BillingOverviewPage) until the backend exposes a proper
  // billing-role field. `?role=` overrides for design review.
  const viewerRole: MemberRole = useMemo(() => {
    if (forcedRoleValue) return forcedRoleValue;
    const match = members.find(
      (m) => m.email.toLowerCase() === user.email.toLowerCase(),
    );
    return match?.role ?? "billing_owner";
  }, [forcedRoleValue, members, user.email]);

  const canManage =
    viewerRole === "billing_owner" || viewerRole === "admin";

  const transferTarget = useMemo(
    () => members.find((m) => m.user_id === transferForId) ?? null,
    [members, transferForId],
  );

  const handleInvite = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      const trimmed = inviteEmail.trim();
      if (!trimmed) return;
      // Third-paid-member cap: on Free (or Pro at its cap), route to the
      // shared quota modal instead of hitting the invite endpoint.
      const projectedPaid =
        members.filter((m) => isPaidRole(m.role)).length +
        (isPaidRole(inviteRole) ? 1 : 0);
      if (
        (planSlug === "free" && projectedPaid >= 3) ||
        (planSlug === "pro" && seatLimit != null && seatsUsed >= seatLimit)
      ) {
        showQuotaModal("third_paid_member");
        return;
      }
      setInviteStatus(t("members.invite.submitting"));
      try {
        await billingApi.inviteMember({
          email: trimmed,
          role: inviteRole,
        });
        setInviteEmail("");
        setInviteStatus(t("members.invite.sent"));
      } catch {
        setInviteStatus(t("members.invite.error"));
      }
    },
    [inviteEmail, inviteRole, members, planSlug, seatLimit, seatsUsed],
  );

  const handleRoleChange = useCallback(
    async (m: WorkspaceMember, role: MemberRole) => {
      if (role === m.role) return;
      setActionNote(null);
      try {
        await billingApi.updateMemberRole(m.user_id, role);
        setMembers((prev) =>
          prev.map((x) => (x.user_id === m.user_id ? { ...x, role } : x)),
        );
        setActionNote(
          t("members.actions.role_updated", {
            name: m.display_name,
          }),
        );
      } catch {
        setActionNote(t("members.actions.role_error"));
      }
    },
    [],
  );

  const handleRemove = useCallback(
    async (m: WorkspaceMember) => {
      setActionNote(null);
      try {
        await billingApi.removeMember(m.user_id);
        setMembers((prev) => prev.filter((x) => x.user_id !== m.user_id));
        setActionNote(
          t("members.actions.removed", { name: m.display_name }),
        );
      } catch {
        setActionNote(t("members.actions.remove_error"));
      }
    },
    [],
  );

  const seatNote =
    planSlug === "free"
      ? t("members.seats.note_free")
      : planSlug === "pro"
        ? t("members.seats.note_pro")
        : t("members.seats.note_team");

  if (!canManage) {
    // Planner / Reviewer / Viewer: quiet role-block card, not the list.
    return (
      <div
        className="billing-page"
        role="dialog"
        aria-modal="true"
        aria-label={t("members.page.aria")}
      >
        <header className="billing-page__topbar">
          <h1 className="billing-page__brand">Inspira</h1>
          <span className="billing-page__crumbs">
            {t("members.page.crumbs_prefix")}
            {" \u00B7 "}
            <em>{t("members.page.crumbs_self")}</em>
          </span>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className="billing-page__close"
            onClick={onClose}
            aria-label={t("members.page.close_aria")}
            title={t("members.page.close_title")}
          >
            {"\u00D7"}
          </button>
        </header>
        <div className="billing-page__inner">
          <section
            className="billing-role-block"
            aria-labelledby="members-role-block-title"
          >
            <p className="billing-eyebrow">{t("members.role_block.eyebrow")}</p>
            <h2
              id="members-role-block-title"
              className="billing-display billing-display--md"
            >
              {t("members.role_block.title")}
            </h2>
            <p className="billing-serif" style={{ marginTop: 10 }}>
              {t("members.role_block.body")}
            </p>
          </section>
        </div>
      </div>
    );
  }

  return (
    <div
      className="billing-page"
      role="dialog"
      aria-modal="true"
      aria-label={t("members.page.aria")}
      aria-busy={loading ? "true" : undefined}
    >
      <header className="billing-page__topbar">
        <h1 className="billing-page__brand">Inspira</h1>
        <span className="billing-page__crumbs">
          {t("members.page.crumbs_prefix")}
          {" \u00B7 "}
          <em>{t("members.page.crumbs_self")}</em>
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="billing-page__close"
          onClick={onClose}
          aria-label={t("members.page.close_aria")}
          title={t("members.page.close_title")}
        >
          {"\u00D7"}
        </button>
      </header>

      <div className="billing-page__inner">
        <section className="members-surface">
          <div className="members-surface__head">
            <div>
              <p className="billing-eyebrow">
                {t("members.page.eyebrow")}
              </p>
              <h2 className="billing-display billing-display--lg">
                {t("members.page.title")}
              </h2>
            </div>
          </div>

          <MembersSeatMeter
            seatsUsed={seatsUsed}
            seatsLimit={seatLimit}
            planLabel={planLabel(planSlug)}
            note={seatNote}
          />

          {/* Invite row */}
          <form
            className="members-invite-row"
            onSubmit={handleInvite}
            aria-label={t("members.invite.aria")}
          >
            <label className="billing-field" style={{ flex: 1, minWidth: 220 }}>
              <span className="billing-field__label">
                {t("members.invite.email_label")}
              </span>
              <input
                type="email"
                className="billing-field__input"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder={t("members.invite.email_placeholder")}
                autoComplete="off"
                required
              />
            </label>
            <label className="billing-field">
              <span className="billing-field__label">
                {t("members.invite.role_label")}
              </span>
              <select
                className="billing-field__input members-role-select"
                value={inviteRole}
                onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                  setInviteRole(e.target.value as MemberRole)
                }
              >
                {VIEWABLE_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {t(`members.role.${r}`)}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              className="billing-btn billing-btn--sage"
              disabled={!inviteEmail.trim()}
            >
              {t("members.invite.cta")}
            </button>
          </form>
          {inviteStatus ? (
            <p
              className="billing-status"
              role="status"
              aria-live="polite"
              style={{ marginTop: 8 }}
            >
              {inviteStatus}
            </p>
          ) : null}

          {/* Members table */}
          <div className="members-head">
            <p className="members-head__title">
              {t("members.list.title")}
            </p>
          </div>
          <div className="members-row members-row--head" role="row">
            <span />
            <span>{t("members.col.name")}</span>
            <span>{t("members.col.email")}</span>
            <span>{t("members.col.role")}</span>
            <span>{t("members.col.joined")}</span>
            <span />
          </div>

          {loading ? (
            <p
              className="billing-status"
              style={{ padding: "24px 0" }}
            >
              {t("members.list.loading")}
            </p>
          ) : members.length === 0 ? (
            <p
              className="billing-status"
              style={{ padding: "24px 0" }}
            >
              {t("members.list.empty")}
            </p>
          ) : (
            members.map((m) => {
              const isSelf =
                m.email.toLowerCase() === user.email.toLowerCase();
              const isOwnerRow = m.role === "billing_owner";
              const roleOptions = isOwnerRow
                ? (["billing_owner"] as MemberRole[])
                : (VIEWABLE_ROLES as MemberRole[]);
              return (
                <div
                  key={m.user_id}
                  className="members-row"
                  role="row"
                >
                  <span className="members-row__avatar">
                    {m.avatar_initials}
                  </span>
                  <div>
                    <div className="members-row__name">{m.display_name}</div>
                    <div className="members-row__sub">
                      {isSelf ? t("members.row.self") : null}
                      {isOwnerRow ? (
                        <span className="members-row__badge">
                          {t("members.role.billing_owner")}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <span className="members-row__email">{m.email}</span>
                  <select
                    className="members-role-select"
                    value={m.role}
                    onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                      handleRoleChange(m, e.target.value as MemberRole)
                    }
                    disabled={isOwnerRow}
                    aria-label={t("members.row.role_aria", {
                      name: m.display_name,
                    })}
                  >
                    {roleOptions.map((r) => (
                      <option key={r} value={r}>
                        {t(`members.role.${r}`)}
                      </option>
                    ))}
                  </select>
                  <span className="members-row__joined">
                    {formatDate(m.joined_at)}
                  </span>
                  <span className="members-row__actions">
                    {!isOwnerRow && viewerRole === "billing_owner" ? (
                      <button
                        type="button"
                        className="members-row__link"
                        onClick={() => setTransferForId(m.user_id)}
                      >
                        {t("members.row.transfer")}
                      </button>
                    ) : null}
                    {!isOwnerRow ? (
                      <button
                        type="button"
                        className="members-row__link members-row__link--rust"
                        onClick={() => void handleRemove(m)}
                      >
                        {t("members.row.remove")}
                      </button>
                    ) : null}
                  </span>
                </div>
              );
            })
          )}

          {actionNote ? (
            <p
              className="billing-status"
              role="status"
              aria-live="polite"
              style={{ marginTop: 12 }}
            >
              {actionNote}
            </p>
          ) : null}

          <BulkInviteDropzone planSlug={planSlug} />
        </section>
      </div>

      <BillingOwnerTransferModal
        open={transferForId != null}
        currentOwner={currentOwner}
        candidates={
          transferTarget
            ? [transferTarget]
            : members.filter((m) => m.role !== "billing_owner")
        }
        onClose={() => setTransferForId(null)}
        onTransferred={(newOwnerId) => {
          setMembers((prev) =>
            prev.map((m) => {
              if (m.user_id === newOwnerId) {
                return { ...m, role: "billing_owner" };
              }
              if (m.role === "billing_owner") {
                return { ...m, role: "admin" };
              }
              return m;
            }),
          );
          setTransferForId(null);
        }}
      />
    </div>
  );
}
