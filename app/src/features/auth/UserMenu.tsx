// Self-contained UserMenu for AuthedShell (post-Wave-2 v4 routes).
//
// Lighter than InspiraApp.tsx's UserMenu — self-fetches /api/auth/me +
// entitlements on mount instead of taking them as props. Used by the
// /workspaces, /connectors, /inbox top-bar to give signed-in users a
// visible account chip + sign-out.
//
// "Account settings" navigates to /app (the legacy InspiraApp chrome
// owns the canonical AccountSettingsPage phase). When that surface
// gets lifted out of InspiraApp's phase machine we'll route it
// directly here.

import { ReactElement, useEffect, useRef, useState } from "react";

import { LocalePicker } from "../../components/LocalePicker";
import { api, type AuthedUser } from "../inspira/api";

// Plan-slug → user-facing label. The "team" slug stays in the DB for
// Stripe / subscription back-compat but displays as "Frontier" per
// #081's rebrand; everything else just title-cases the slug.
function formatPlanLabel(slug: string): string {
  const lower = slug.toLowerCase();
  if (lower === "team") return "Frontier";
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

export interface UserMenuProps {
  /** When true, the dropdown panel opens UPWARD + RIGHT instead of
   *  the default downward+right. Used when the avatar lives at the
   *  bottom of the AppRail — without this the panel falls below the
   *  viewport. */
  railContext?: boolean;
}

export function UserMenu({ railContext }: UserMenuProps = {}): ReactElement | null {
  const [user, setUser] = useState<AuthedUser | null>(null);
  const [planTier, setPlanTier] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const u = await api.me();
        if (cancelled) return;
        setUser(u);
        if (!u.is_system) {
          try {
            const ent = await api.getEntitlements();
            if (!cancelled) setPlanTier(ent.plan);
          } catch {
            // soft-fail — plan pill just doesn't render
          }
        }
      } catch {
        // soft-fail — chip stays hidden
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const el = rootRef.current;
      if (!el) return;
      if (el.contains(e.target as unknown as Node)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onDown, true);
    return () =>
      document.removeEventListener("pointerdown", onDown, true);
  }, [open]);

  if (!user || user.is_system) return null;

  const initials = (user.display_name || user.email || "?")
    .split(/\s+/)
    .map((w) => w.charAt(0).toUpperCase())
    .slice(0, 2)
    .join("")
    .padEnd(1, "?");

  const onLogout = async () => {
    try {
      await api.logout();
    } catch {
      // even if logout 5xxs we still navigate; cookie best-effort
      // cleared and RootGate dispatches anon traffic to SignInPage
    }
    window.location.assign("/");
  };

  return (
    <div
      className={"user-menu" + (railContext ? " user-menu--rail" : "")}
      ref={rootRef}
    >
      <button
        type="button"
        className="user-menu__avatar"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={user.email}
        aria-label="Open profile menu"
      >
        {initials}
      </button>
      {open ? (
        <div className="user-menu__panel" role="menu">
          <div className="user-menu__header">
            <div className="user-menu__name">{user.display_name}</div>
            <div className="user-menu__email" title={user.email}>
              {user.email}
            </div>
          </div>
          {planTier ? (
            <div className="user-menu__plan-row">
              <span
                className={
                  "user-menu__plan-pill" +
                  (planTier.toLowerCase() === "free"
                    ? " user-menu__plan-pill--free"
                    : "")
                }
              >
                {formatPlanLabel(planTier)}
              </span>
            </div>
          ) : null}
          {/* "Account settings" only renders OUTSIDE rail context — in
              rail context it would navigate to /app which mounts
              InspiraApp's legacy top-bar (jarring for a partner who's
              been browsing under the rail). When the v4 /settings/account
              route ships we'll wire it back. For now, the canvas
              chrome's own UserMenu still exposes Account settings. */}
          {!railContext ? (
            <button
              type="button"
              className="user-menu__action"
              onClick={() => {
                setOpen(false);
                window.location.assign("/app");
              }}
            >
              Account settings
            </button>
          ) : null}
          <button
            type="button"
            className="user-menu__action"
            onClick={() => {
              setOpen(false);
              try {
                localStorage.removeItem("inspira_workspace_tour_completed");
              } catch {
                /* storage disabled — non-fatal */
              }
              // Hard-navigate so WorkspaceTour remounts and re-reads
              // the (now-cleared) flag, even if the user was already
              // on /workspaces.
              window.location.assign("/workspaces");
            }}
          >
            Show tour again
          </button>
          <button
            type="button"
            className="user-menu__action"
            onClick={() => {
              setOpen(false);
              void onLogout();
            }}
          >
            Log out
          </button>
          <div className="user-menu__divider" role="separator" />
          <div className="user-menu__locale">
            <LocalePicker variant="inline" onPicked={() => setOpen(false)} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
