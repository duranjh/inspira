// Left-side navigation rail for v4 authed routes (/workspaces,
// /connectors, /inbox). Replaces the prior horizontal top-bar in
// AuthedShell — matches the v5 design's `.cn-rail` / `.fb-rail`
// pattern (icon-only sidebar) and adds an expand-to-labels mode
// for partners who want to see nav text.
//
// Two visual modes:
//   collapsed  (60px) — icons + tooltips
//   expanded  (224px) — icons + labels + WorkspaceSwitcher (brand)
//
// Mode persists to localStorage (`inspira:app-rail-collapsed`).
//
// Slots (top → bottom):
//   1. Toggle:  prominent collapse/expand button (always visible)
//   2. Brand:   workspace initial (collapsed) / WorkspaceSwitcher (expanded)
//   3. Nav:     Workspaces / Connectors / Inbox / Settings
//   4. Spacer
//   5. Footer:  AIStatus (expanded only) + UserMenu (avatar + dropdown)

import { ReactElement, ReactNode, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { CreateWorkspaceDialog } from "../workspaces/CreateWorkspaceDialog";
import { WorkspaceSwitcher } from "../workspaces/WorkspaceSwitcher";
import { useWorkspaceContext } from "../workspaces/WorkspaceContext";
import { UserMenu } from "../auth/UserMenu";

const STORAGE_KEY = "inspira:app-rail-collapsed";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
}

// Inline SVG icons — stroke 1.5, currentColor. Sized 18×18 to match
// the v5 design's `.cn-rail .rb svg` spec.
const ICON_HOME = (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3 11.5L12 4l9 7.5" />
    <path d="M5.5 10v9.5h13V10" />
  </svg>
);
const ICON_PLUG = (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M9 3v5" />
    <path d="M15 3v5" />
    <path d="M7 8h10v3a5 5 0 0 1-5 5 5 5 0 0 1-5-5V8z" />
    <path d="M12 16v5" />
  </svg>
);
const ICON_INBOX = (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3 13l3-8h12l3 8" />
    <path d="M3 13v6h18v-6" />
    <path d="M3 13h5l1 2h6l1-2h5" />
  </svg>
);
const ICON_CODE = (
  <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="16 18 22 12 16 6" />
    <polyline points="8 6 2 12 8 18" />
  </svg>
);

const ICON_GEAR = (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h0a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8h0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </svg>
);

const NAV_ITEMS: readonly NavItem[] = [
  { to: "/workspaces", label: "Workspaces", icon: ICON_HOME },
  { to: "/inbox", label: "Inbox", icon: ICON_INBOX },
  { to: "/code", label: "Code", icon: ICON_CODE },
  { to: "/connectors", label: "Connectors", icon: ICON_PLUG },
  { to: "/settings/workspaces", label: "Settings", icon: ICON_GEAR },
];

export interface AppRailProps {
  /** When true, the rail mounts in zero-workspace mode: brand is a
   *  static "Inspira" wordmark and nav rows are hidden. The /workspaces
   *  route in zero state shows FirstRunCard in the main area, so the
   *  rail shouldn't tease links the user can't yet act on. */
  hideNav?: boolean;
  /** Optional surface-specific widget rendered in a slot above the
   *  footer (UserMenu). Canvas + Code IDE pass `<OrchestratorChip />`
   *  here so the agent activity indicator lives in the same place
   *  across every project-scoped surface. Undefined on non-project
   *  routes (workspaces, inbox, connectors, settings). */
  rightSlot?: ReactNode;
}

export function AppRail({ hideNav, rightSlot }: AppRailProps): ReactElement {
  const location = useLocation();
  const ctx = useWorkspaceContext();
  const [createOpen, setCreateOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return globalThis.localStorage?.getItem(STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      globalThis.localStorage?.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    } catch {
      // Soft-fail — ignore quota or privacy-mode failures.
    }
  }, [collapsed]);

  const wsInitial = ctx.activeWorkspace?.name?.charAt(0)?.toUpperCase() ?? "I";

  return (
    <aside
      className={"app-rail" + (collapsed ? " app-rail--collapsed" : "")}
      aria-label="Primary"
    >
      {/* Toggle is the FIRST element so the expand affordance is
          always at the top of the rail and immediately discoverable
          when collapsed. */}
      <button
        type="button"
        className="app-rail__toggle"
        onClick={() => setCollapsed((v) => !v)}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
      >
        <span className="app-rail__nav-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            {collapsed ? (
              <path d="M9 6l6 6-6 6" />
            ) : (
              <path d="M15 6l-6 6 6 6" />
            )}
          </svg>
        </span>
      </button>

      <div className="app-rail__brand">
        {collapsed ? (
          <div
            className="app-rail__ws-mini"
            title={ctx.activeWorkspace?.name ?? "Workspace"}
            aria-label={ctx.activeWorkspace?.name ?? "Workspace"}
          >
            {wsInitial}
          </div>
        ) : (
          <WorkspaceSwitcher
            variant="brand"
            onCreateWorkspace={() => setCreateOpen(true)}
          />
        )}
      </div>

      {!hideNav ? (
        <nav className="app-rail__nav" aria-label="Workspace">
          {NAV_ITEMS.map((item) => {
            const active =
              location.pathname === item.to ||
              location.pathname.startsWith(`${item.to}/`);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={
                  "app-rail__nav-link" +
                  (active ? " app-rail__nav-link--active" : "")
                }
                title={collapsed ? item.label : undefined}
                aria-current={active ? "page" : undefined}
              >
                <span className="app-rail__nav-icon" aria-hidden="true">
                  {item.icon}
                </span>
                {!collapsed ? (
                  <span className="app-rail__nav-label">{item.label}</span>
                ) : null}
              </Link>
            );
          })}
        </nav>
      ) : null}

      <div className="app-rail__spacer" />

      {rightSlot ? (
        <div className="app-rail__right-slot">{rightSlot}</div>
      ) : null}

      <div className="app-rail__footer">
        <div className="app-rail__user">
          <UserMenu railContext />
        </div>
      </div>

      <CreateWorkspaceDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
    </aside>
  );
}
