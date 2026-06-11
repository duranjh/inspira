// Inspira — /billing route gate.
//
// Checks authentication on mount via /api/auth/me. Anonymous visitors are
// sent back to the landing page (the marketing shell handles sign-in from
// there). Signed-in users land on BillingOverviewPage, which reads its own
// subscription + usage data.
//
// The page supports a ?state= override for design review (one of the six
// hi-fi states or "free") and a ?checkout=success|canceled hand-off from
// Stripe, surfaced as sibling pages.

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, type AuthedUser } from "../inspira/api";
import { t } from "../../i18n";

import { BillingOverviewPage } from "./BillingOverviewPage";
import { CheckoutCanceledPage } from "./CheckoutCanceledPage";
import { CheckoutSuccessPage } from "./CheckoutSuccessPage";
import "./billing.css";

type GateState =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "ready"; user: AuthedUser };

export function BillingRoute() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [state, setState] = useState<GateState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.me();
        if (cancelled) return;
        if (me.is_system) {
          setState({ kind: "anon" });
        } else {
          setState({ kind: "ready", user: me });
        }
      } catch {
        if (cancelled) return;
        setState({ kind: "anon" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Anonymous visitors: bounce to landing so they can sign in. The landing
  // page's signup form already takes a ?redirect= param; we set it to
  // /billing so they land back here on success.
  useEffect(() => {
    if (state.kind === "anon") {
      navigate("/?signin=1&redirect=%2Fbilling", { replace: true });
    }
  }, [state.kind, navigate]);

  if (state.kind === "loading") {
    return (
      <div
        className="billing-page"
        aria-busy="true"
        aria-live="polite"
        style={{ justifyContent: "center", alignItems: "center" }}
      >
        <p className="billing-serif billing-serif--dim">
          {t("billing.page.loading")}
        </p>
      </div>
    );
  }

  if (state.kind === "anon") {
    // While the redirect settles, render nothing to avoid a flash of the
    // signed-in UI.
    return null;
  }

  const checkoutStatus = params.get("checkout");
  if (checkoutStatus === "success") {
    return <CheckoutSuccessPage onClose={() => navigate("/billing", { replace: true })} />;
  }
  if (checkoutStatus === "canceled") {
    return (
      <CheckoutCanceledPage
        onClose={() => navigate("/billing", { replace: true })}
        onRetry={() => navigate("/billing", { replace: true })}
      />
    );
  }

  return (
    <BillingOverviewPage
      user={state.user}
      onClose={() => navigate("/")}
      // `?state=...` is a design-review escape hatch — real data still
      // drives the default render.
      forcedStateKey={params.get("state")}
      forcedRole={params.get("role")}
    />
  );
}
