// Inspira — Billing API client.
//
// Thin wrapper matching the shape of ../inspira/api.ts so callers feel at
// home. Split into two tiers:
//
//   LIVE — backend routes that already exist:
//     GET  /api/v2/billing/plans
//     GET  /api/v2/billing/subscription
//     POST /api/v2/billing/checkout    → { url } or 501
//     POST /api/v2/billing/portal      → { url } or 501
//
//   STUBS — backend routes not wired up yet. Each returns a
//   realistic fixture so the UI is reviewable today. Every stub is
//   flagged with TODO(backend) so a grep finds them all at wire-up time.
//
// The stub signatures and TODO(backend) markers in this file are the
// in-repo contract for the endpoint list + shapes the backend
// implements against.

const DEFAULT_BASE_URL =
  (import.meta.env.VITE_INSPIRA_API_URL as string | undefined) ??
  "http://127.0.0.1:4174";

// ---------------------------------------------------------------------------
// Error helpers
// ---------------------------------------------------------------------------

/** Thrown when an endpoint returns 501 because the Stripe provider isn't
 *  configured on this deployment. The UI shows a warm "write to hello@" card
 *  rather than silently breaking. */
export class BillingNotConfiguredError extends Error {
  constructor(message = "billing_not_configured") {
    super(message);
    this.name = "BillingNotConfiguredError";
  }
}

export { parseStatus } from "../../lib/httpStatus";

async function bgetJson<T>(path: string): Promise<T> {
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    credentials: "include",
  });
  if (!res.ok) {
    const detail = await res.text();
    if (res.status === 501) throw new BillingNotConfiguredError(detail);
    throw new Error(
      `GET ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
  return res.json() as Promise<T>;
}

async function bpostJson<T>(
  path: string,
  body: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (!res.ok) {
    const detail = await res.text();
    if (res.status === 501) throw new BillingNotConfiguredError(detail);
    throw new Error(
      `POST ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PlanSlug = "free" | "pro" | "team";
export type BillingPeriod = "monthly" | "annual";

/** Public shape from /api/v2/billing/plans — mirrors Plan.to_public_dict() on
 *  the backend. Frontend adds its own i18n copy + comparison-table mapping on
 *  top of this, so the authoritative numbers stay in plans.py. */
export type PlanSummary = {
  slug: string;
  title: string;
  monthly_price_cents: number;
  annual_price_cents: number | null;
  description: string;
  features: string[];
  limits: {
    max_projects: number | null;
    daily_token_budget: number;
    max_topics_total: number | null;
    allow_share_links: boolean;
    allow_team: boolean;
    allow_export_pdf: boolean;
    allow_advanced_exports: boolean;
    priority_planner: boolean;
  };
};

/** Six distinct subscription states the Billing Overview renders. Names
 *  match the hi-fi tweaks panel. */
export type SubscriptionStatus =
  | "active-paid"
  | "trialing"
  | "trial-3d"
  | "trial-1d"
  | "trial-expired-grace"
  | "past-due"
  | "suspended"
  | "free";

/** Billing role — drives CTA visibility. "owner" sees everything;
 *  "admin" sees plan + usage but actions are replaced with Contact Billing
 *  Owner; "member" sees summary only. */
export type BillingRole = "owner" | "admin" | "member";

export type Subscription = {
  plan: PlanSummary;
  /** Server-reported status, before UI trial-window computation. Frontend
   *  may derive the richer state (trial-3d / trial-1d / past-due / ...)
   *  from `status` + `current_period_end` + `trial_end`. */
  status: SubscriptionStatus | string;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  current_period_end: string | null;
  /** Added by the backend once trial endpoints land. Safe to be absent. */
  trial_end?: string | null;
  billing_period?: BillingPeriod;
  /** When the paid plan first started. ISO 8601. Used by the
   *  Switch-to-annual offer's 30-day age gate. `null` / absent for Free
   *  tier rows — the helper gracefully skips the age check in that case. */
  started_at?: string | null;
  /** Present only while Stripe reports the subscription is trialing. */
  trial_ends_at?: string | null;
};

export type SubscriptionResponse = {
  subscription: Subscription;
  provider_configured: boolean;
};

export type CheckoutResponse = {
  checkout: { session_id: string; url: string };
};

export type PortalResponse = {
  portal: { url: string };
};

// --- Tier-2 stub shapes ------------------------------------------------

export type Invoice = {
  id: string;
  number: string; // e.g. INV-00412
  issued_at: string;
  period_label: string; // e.g. "Pro, annual renewal"
  amount_cents: number;
  currency: string;
  status: "paid" | "pending" | "failed" | "refund";
  pdf_url: string | null;
};

export type InvoiceLine = {
  description: string;
  amount_cents: number;
};

export type InvoiceDetail = Invoice & {
  lines: InvoiceLine[];
  tax_cents: number;
  total_cents: number;
  billed_to: { name: string; address_lines: string[]; vat_id?: string | null };
};

export type PaymentMethod = {
  brand: string; // e.g. VISA
  last4: string;
  exp_month: number;
  exp_year: number;
  holder_name: string | null;
};

export type BillingContacts = {
  company_name: string | null;
  vat_id: string | null;
  address_lines: string[];
  receipt_emails: string[];
};

export type WorkspaceUsage = {
  seats_used: number;
  seats_limit: number | null;
  projects_used: number;
  projects_limit: number | null;
  repos_used: number;
  repos_limit: number | null;
};

export type TrialStatus = {
  is_trialing: boolean;
  plan_slug: PlanSlug | null;
  days_remaining: number | null;
  trial_end: string | null;
};

export type CancelReason =
  | "too_expensive"
  | "missing_feature"
  | "switching_tool"
  | "not_using"
  | "other";

export type DeletionStatus = {
  pending: boolean;
  scheduled_delete_at: string | null;
  days_remaining: number | null;
  initiated_by_user_id: string | null;
};

// --- Tier-3 stub shapes ------------------------------------------------

export type MemberRole =
  | "billing_owner"
  | "admin"
  | "planner"
  | "reviewer"
  | "viewer";

export type WorkspaceMember = {
  user_id: string;
  display_name: string;
  email: string;
  role: MemberRole;
  joined_at: string;
  avatar_initials: string;
};

export type RefundReason =
  | "too_expensive"
  | "missing_feature"
  | "duplicate_charge"
  | "other";

// ---------------------------------------------------------------------------
// Fixtures — used by the stub methods. Kept trivially mockable so a tester
// can swap them from a browser console if needed.
// ---------------------------------------------------------------------------

const FIXTURE_INVOICES: Invoice[] = [
  {
    id: "in_00412",
    number: "INV-00412",
    issued_at: "2026-03-18T12:00:00Z",
    period_label: "Pro, annual renewal",
    amount_cents: 28800,
    currency: "USD",
    status: "paid",
    pdf_url: null,
  },
  {
    id: "in_00298",
    number: "INV-00298",
    issued_at: "2025-03-18T12:00:00Z",
    period_label: "Pro, annual renewal",
    amount_cents: 28800,
    currency: "USD",
    status: "paid",
    pdf_url: null,
  },
  {
    id: "in_00277",
    number: "INV-00277",
    issued_at: "2025-02-18T12:00:00Z",
    period_label: "Monthly-to-annual proration",
    amount_cents: 6800,
    currency: "USD",
    status: "paid",
    pdf_url: null,
  },
];

const FIXTURE_PAYMENT_METHOD: PaymentMethod = {
  brand: "VISA",
  last4: "4242",
  exp_month: 9,
  exp_year: 2028,
  holder_name: "Marguerite Hale",
};

const FIXTURE_CONTACTS: BillingContacts = {
  company_name: null,
  vat_id: null,
  address_lines: [],
  receipt_emails: [],
};

const FIXTURE_USAGE: WorkspaceUsage = {
  seats_used: 3,
  seats_limit: 5,
  projects_used: 4,
  projects_limit: null,
  repos_used: 2,
  repos_limit: 5,
};

const FIXTURE_MEMBERS: WorkspaceMember[] = [
  {
    user_id: "u_marguerite",
    display_name: "Marguerite Hale",
    email: "marguerite@haleandco.com",
    role: "billing_owner",
    joined_at: "2025-09-02T09:00:00Z",
    avatar_initials: "M",
  },
  {
    user_id: "u_rosa",
    display_name: "Rosa Delgado",
    email: "rosa@haleandco.com",
    role: "admin",
    joined_at: "2025-09-14T09:00:00Z",
    avatar_initials: "R",
  },
  {
    user_id: "u_ines",
    display_name: "Ines Park",
    email: "ines@haleandco.com",
    role: "planner",
    joined_at: "2025-11-02T09:00:00Z",
    avatar_initials: "I",
  },
  {
    user_id: "u_benedict",
    display_name: "Benedict Wren",
    email: "benedict@haleandco.com",
    role: "reviewer",
    joined_at: "2026-01-18T09:00:00Z",
    avatar_initials: "B",
  },
  {
    user_id: "u_lucienne",
    display_name: "Lucienne Obi",
    email: "lucienne@haleandco.com",
    role: "viewer",
    joined_at: "2026-02-20T09:00:00Z",
    avatar_initials: "L",
  },
];

function delay<T>(value: T, ms = 120): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

// ---------------------------------------------------------------------------
// Public client
// ---------------------------------------------------------------------------

export const billingApi = {
  // -- Live endpoints ----------------------------------------------------
  getPlans: (): Promise<{ plans: PlanSummary[] }> =>
    bgetJson("/api/v2/billing/plans"),

  getSubscription: (): Promise<SubscriptionResponse> =>
    bgetJson("/api/v2/billing/subscription"),

  /** Starts a Stripe Checkout session. Throws BillingNotConfiguredError on
   *  501 so the caller can render the warm fallback card. */
  startCheckout: (input: {
    plan_slug: string;
    period?: BillingPeriod;
    seats?: number;
  }): Promise<CheckoutResponse> =>
    bpostJson("/api/v2/billing/checkout", {
      plan_slug: input.plan_slug,
      // The backend body today only takes plan_slug. Extra fields are
      // forwarded so the route can pick them up once wired without a
      // client change.
      period: input.period,
      seats: input.seats,
    }),

  openPortalSession: (): Promise<PortalResponse> =>
    bpostJson("/api/v2/billing/portal", {}),

  // -- Stub endpoints (TODO: backend) -----------------------------------

  getInvoices: async (): Promise<{ invoices: Invoice[] }> => {
    // TODO(backend): GET /api/v2/workspace/invoices
    // Returns [] until the backend ships — fake invoices ($288
    // "Pro, annual renewal" for a free user) erodes trust. Existing
    // empty-state copy at billing.invoices.empty / billing.invoices_page.empty_headline
    // renders cleanly with []. (E2E 2026-04-25 #4)
    return delay({ invoices: [] });
  },

  getInvoiceDetail: async (
    id: string,
  ): Promise<{ invoice: InvoiceDetail }> => {
    // TODO(backend): GET /api/v2/workspace/invoices/{id}
    const base = FIXTURE_INVOICES.find((inv) => inv.id === id) ??
      FIXTURE_INVOICES[0];
    const lines: InvoiceLine[] = [
      { description: `${base.period_label} · 1 seat`, amount_cents: base.amount_cents },
    ];
    const tax = Math.round(base.amount_cents * 0.2);
    const detail: InvoiceDetail = {
      ...base,
      lines,
      tax_cents: tax,
      total_cents: base.amount_cents + tax,
      billed_to: {
        name: "Marguerite Hale",
        address_lines: [],
        vat_id: null,
      },
    };
    return delay({ invoice: detail });
  },

  /** Returns a URL the browser can open to download the PDF. The stub
   *  resolves to null so the UI renders "PDF available once backend
   *  lands". */
  downloadInvoicePdf: async (id: string): Promise<{ url: string | null }> => {
    // TODO(backend): GET /api/v2/workspace/invoices/{id}/pdf
    void id;
    return delay({ url: null });
  },

  getPaymentMethod: async (): Promise<{ payment_method: PaymentMethod | null }> => {
    // TODO(backend): GET /api/v2/workspace/payment-method
    // Returns null until the backend ships — shipping the FIXTURE
    // "Marguerite Hale / Visa ··4242" to a brand-new free user is a
    // first-impression killer (E2E 2026-04-25 #4). The component-level
    // FIXTURES in BillingOverviewPage still drive the design-review
    // path (forcedStateKey).
    return delay({ payment_method: null });
  },

  updatePaymentMethod: async (input: {
    token: string;
  }): Promise<{ payment_method: PaymentMethod }> => {
    // TODO(backend): POST /api/v2/workspace/payment-method
    void input;
    return delay({ payment_method: FIXTURE_PAYMENT_METHOD });
  },

  deletePaymentMethod: async (): Promise<{ deleted: true }> => {
    // TODO(backend): DELETE /api/v2/workspace/payment-method
    return delay({ deleted: true as const });
  },

  getBillingContacts: async (): Promise<{ contacts: BillingContacts }> => {
    // TODO(backend): GET /api/v2/workspace/billing-contacts
    return delay({ contacts: FIXTURE_CONTACTS });
  },

  updateBillingContacts: async (
    input: Partial<BillingContacts>,
  ): Promise<{ contacts: BillingContacts }> => {
    // TODO(backend): PATCH /api/v2/workspace/billing-contacts
    const merged: BillingContacts = { ...FIXTURE_CONTACTS, ...input };
    return delay({ contacts: merged });
  },

  getWorkspaceUsage: async (): Promise<{ usage: WorkspaceUsage | null }> => {
    // TODO(backend): GET /api/v2/workspace/usage
    // Returns null until the backend ships — the fixture "Seats 3 of 5
    // / Projects 4 of ∞ / Repos 2 of 5" is meaningless for a free user
    // (no seats/repos concept on the free tier) and actively misleads.
    // BillingOverviewPage hides the meter block entirely when null.
    // (E2E 2026-04-25 #4)
    return delay({ usage: null });
  },

  getTrialStatus: async (): Promise<{ trial: TrialStatus }> => {
    // TODO(backend): GET /api/v2/workspace/trial-status
    return delay({
      trial: {
        is_trialing: false,
        plan_slug: null,
        days_remaining: null,
        trial_end: null,
      },
    });
  },

  cancelSubscription: async (input: {
    reason: CancelReason;
    feedback?: string;
  }): Promise<{ canceled_at: string }> => {
    // TODO(backend): POST /api/v2/workspace/subscription/cancel
    void input;
    return delay({ canceled_at: new Date().toISOString() });
  },

  requestRefund: async (input: {
    invoice_id: string;
    reason: RefundReason | string;
    description?: string;
    amount_cents?: number;
  }): Promise<{ request_id: string }> => {
    // TODO(backend): POST /api/v2/workspace/refund-requests
    void input;
    return delay({ request_id: `req_${Date.now()}` });
  },

  transferBillingOwner: async (input: {
    to_user_id: string;
  }): Promise<{ transferred: true }> => {
    // TODO(backend): PATCH /api/v2/workspace/billing-owner
    void input;
    return delay({ transferred: true as const });
  },

  getMembers: async (): Promise<{ members: WorkspaceMember[] }> => {
    // TODO(backend): GET /api/v2/workspace/members
    return delay({ members: FIXTURE_MEMBERS });
  },

  inviteMember: async (input: {
    email: string;
    role: MemberRole;
  }): Promise<{ invited: true }> => {
    // TODO(backend): POST /api/v2/workspace/members/invite
    void input;
    return delay({ invited: true as const });
  },

  bulkInviteMembers: async (input: {
    csv: File;
  }): Promise<{ invited: number; errors: string[] }> => {
    // TODO(backend): POST /api/v2/workspace/members/bulk-invite
    void input;
    return delay({ invited: 0, errors: [] });
  },

  removeMember: async (userId: string): Promise<{ removed: true }> => {
    // TODO(backend): DELETE /api/v2/workspace/members/{user_id}
    void userId;
    return delay({ removed: true as const });
  },

  updateMemberRole: async (
    userId: string,
    role: MemberRole,
  ): Promise<{ updated: true }> => {
    // TODO(backend): PATCH /api/v2/workspace/members/{user_id}/role
    void userId;
    void role;
    return delay({ updated: true as const });
  },

  scheduleWorkspaceDeletion: async (input: {
    confirmation: string;
  }): Promise<DeletionStatus> => {
    // TODO(backend): POST /api/v2/workspace/delete
    void input;
    const graceDays = 30;
    const scheduledDate = new Date(Date.now() + graceDays * 864e5).toISOString();
    return delay({
      pending: true,
      scheduled_delete_at: scheduledDate,
      days_remaining: graceDays,
      initiated_by_user_id: null,
    });
  },

  restoreWorkspace: async (): Promise<{ restored: true }> => {
    // TODO(backend): POST /api/v2/workspace/restore
    return delay({ restored: true as const });
  },

  getDeletionStatus: async (): Promise<DeletionStatus> => {
    // TODO(backend): GET /api/v2/workspace/deletion-status
    return delay({
      pending: false,
      scheduled_delete_at: null,
      days_remaining: null,
      initiated_by_user_id: null,
    });
  },
};
