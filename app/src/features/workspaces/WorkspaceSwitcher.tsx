// Top-bar workspace switcher. Pattern mirrors the existing
// ProjectSwitcher in InspiraApp.tsx (pointerdown capture-phase
// click-outside, ref-rooted dropdown).
//
// Visual shape per B4.6 + the v5 design tokens:
//
//  ┌─────────────────────────────┐
//  │ Acme Corp ▾                 │  ← top-bar trigger (chip-like)
//  └─────────────────────────────┘
//       ▽ open ▽
//  ┌─────────────────────────────┐
//  │ Search workspaces…   ⌘K     │  ← search input (placeholder-only
//  │─────────────────────────────│     in C4; full filter ships C5+)
//  │ ● Acme Corp        owner    │  ← active row (sage-filled)
//  │ ● Beta Studio      member   │
//  │ ● Gamma Labs       admin    │
//  │─────────────────────────────│
//  │ + Create new workspace →    │  ← ghost link (opens dialog)
//  └─────────────────────────────┘
//
// 0-workspace state: trigger renders "No workspace" + only the
// "+ Create" footer is visible. Single-workspace: trigger is the
// workspace name + chevron, dropdown lists the one row + footer.

import { ReactElement, useEffect, useMemo, useRef, useState } from "react";

import {
  WorkspaceRole,
  WorkspaceSummary,
  useWorkspaceContext,
} from "./WorkspaceContext";

const ROLE_LABEL: Record<WorkspaceRole, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

export interface WorkspaceSwitcherProps {
  /** Called when the user clicks the "+ Create new workspace"
   * footer link. The CreateWorkspaceDialog is mounted by the
   * shell that hosts this switcher (C5 wires it). */
  onCreateWorkspace: () => void;
  /** "brand"   — large serif label in the top-bar's brand slot.
   *  "compact" — small chip-like button. Default "compact". */
  variant?: "brand" | "compact";
}

export function WorkspaceSwitcher({
  onCreateWorkspace,
  variant = "compact",
}: WorkspaceSwitcherProps): ReactElement {
  const { workspaces, activeWorkspace, setActiveWorkspace, loading } =
    useWorkspaceContext();
  const [open, setOpen] = useState(false);
  // Search filter — substring match on workspace name OR slug.
  // Per the C5 design choice: substring is cheap + predictable;
  // fuzzy match (Fuse.js or similar) is a future polish if
  // partners with 20+ workspaces hit name-recall friction.
  const [filter, setFilter] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

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

  // Reset filter + focus search when the dropdown opens.
  useEffect(() => {
    if (!open) {
      setFilter("");
      return;
    }
    if (workspaces.length > 5 && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [open, workspaces.length]);

  const filteredWorkspaces = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return workspaces;
    return workspaces.filter((w) => {
      const haystack = `${w.name} ${w.slug}`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [workspaces, filter]);

  // Loading state: collapsed trigger with a placeholder. The
  // workspaceReady() promise gates http requests; this just keeps
  // the visual from flashing through "No workspace".
  if (loading) {
    return (
      <div
        className={
          "workspace-switcher" +
          (variant === "brand" ? " workspace-switcher--brand" : "")
        }
      >
        <button
          type="button"
          className="workspace-switcher__trigger"
          disabled
          aria-busy
        >
          <span className="workspace-switcher__title">…</span>
        </button>
      </div>
    );
  }

  const triggerLabel = activeWorkspace?.name ?? "No workspace";

  return (
    <div
      className={
        "workspace-switcher" +
        (variant === "brand" ? " workspace-switcher--brand" : "")
      }
      ref={rootRef}
    >
      <button
        type="button"
        className={
          "workspace-switcher__trigger" +
          (variant === "brand"
            ? " workspace-switcher__trigger--brand"
            : "")
        }
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        title={triggerLabel}
      >
        <span className="workspace-switcher__title">{triggerLabel}</span>
        <span className="workspace-switcher__caret" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open ? (
        <div className="workspace-switcher__menu" role="menu">
          {workspaces.length > 0 ? (
            <>
              {/* Search input only renders when there are enough
                  workspaces to make scanning the list slow.
                  Below the threshold the search would be visual
                  noise. */}
              {workspaces.length > 5 ? (
                <div className="workspace-switcher__search">
                  <input
                    ref={searchInputRef}
                    type="text"
                    className="workspace-switcher__search-input"
                    placeholder="Search workspaces…"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    spellCheck={false}
                  />
                  <span className="workspace-switcher__kbd">⌘K</span>
                </div>
              ) : null}
              <div className="workspace-switcher__list">
                {filteredWorkspaces.map((w) => (
                  <WorkspaceRow
                    key={w.workspace_id}
                    workspace={w}
                    active={
                      w.workspace_id === activeWorkspace?.workspace_id
                    }
                    onClick={() => {
                      setOpen(false);
                      if (
                        w.workspace_id !== activeWorkspace?.workspace_id
                      ) {
                        setActiveWorkspace(w.workspace_id);
                      }
                    }}
                  />
                ))}
                {filter && filteredWorkspaces.length === 0 ? (
                  <div className="workspace-switcher__empty">
                    No workspaces match &ldquo;{filter}&rdquo;.
                  </div>
                ) : null}
              </div>
              <div className="workspace-switcher__divider" />
            </>
          ) : null}
          <button
            type="button"
            className="workspace-switcher__create"
            onClick={() => {
              setOpen(false);
              onCreateWorkspace();
            }}
          >
            + Create new workspace →
          </button>
        </div>
      ) : null}
    </div>
  );
}

function WorkspaceRow({
  workspace,
  active,
  onClick,
}: {
  workspace: WorkspaceSummary;
  active: boolean;
  onClick: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      className={
        "workspace-switcher__item" +
        (active ? " workspace-switcher__item--active" : "")
      }
      onClick={onClick}
      role="menuitem"
    >
      <span className="workspace-switcher__item-name">
        {workspace.name}
      </span>
      <span className="chip chip--ghost workspace-switcher__role-chip">
        {ROLE_LABEL[workspace.role]}
      </span>
    </button>
  );
}
