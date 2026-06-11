// Inspira — /members route gate.
//
// Mirrors BillingRoute: on mount we call /api/auth/me to decide between
// rendering the Members list or bouncing anonymous visitors back to the
// landing page for sign-in. The `?role=` query param is a design-review
// escape hatch so reviewers can exercise owner / admin / planner /
// reviewer / viewer states without touching the backend.

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, type AuthedUser } from "../inspira/api";
import { t } from "../../i18n";

import { MembersListPage } from "./MembersListPage";
import "../billing/billing.css";
import "./members.css";

type GateState =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "ready"; user: AuthedUser };

export function MembersRoute() {
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

  useEffect(() => {
    if (state.kind === "anon") {
      navigate("/?signin=1&redirect=%2Fmembers", { replace: true });
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
          {t("members.page.loading")}
        </p>
      </div>
    );
  }

  if (state.kind === "anon") {
    return null;
  }

  return (
    <MembersListPage
      user={state.user}
      onClose={() => navigate("/")}
      forcedRole={params.get("role")}
      forcedPlan={params.get("plan")}
    />
  );
}
