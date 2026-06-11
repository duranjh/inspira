// B2.3 / W3 δ — spawning overlay rendered inside the Promote dialog card
// after the user clicks "Promote — Inspira drafts the canvas".
// Position absolute / inset 0 inside the dialog card (which is
// position: relative via the base dialog styles); a sage spinner +
// "Spawning sub-agents…" italic line. Pure presentational.

export function SpawningOverlay() {
  return (
    <div
      className="pm-spawning"
      role="status"
      aria-live="polite"
      aria-label="Spawning sub-agents"
    >
      <span className="pm-spawning__spinner" aria-hidden="true" />
      <span className="pm-spawning__text">Spawning sub-agents…</span>
    </div>
  );
}
