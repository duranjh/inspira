// Presence avatar stack rendered near the canvas action cluster.
// Each peer shows as a circular chip with their initials + a colored
// ring. Clicking toggles follow mode: the viewing user's canvas
// mirrors the clicked peer's viewport (Figma-style).
//
// Shows the first 6 peers; overflow rolls into a "+N more" pill.

import { useMemo } from "react";

import { useRealtimeContext } from "./RealtimeContext";

function initialsFor(name: string): string {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function PresenceAvatars() {
  const rt = useRealtimeContext();

  const displayedPeers = useMemo(
    () => rt.peers.filter((p) => p.sessionId !== rt.mySessionId).slice(0, 6),
    [rt.peers, rt.mySessionId],
  );
  const overflow = Math.max(
    0,
    rt.peers.filter((p) => p.sessionId !== rt.mySessionId).length - 6,
  );

  // Render NOTHING when the user is alone on the canvas. Showing a
  // single "you" chip adds noise (the user knows they're here) and
  // cost the mobile rail a button slot for no benefit. Only surface
  // the avatar stack once at least one peer has joined.
  if (displayedPeers.length === 0) return null;

  return (
    <div
      className="presence-avatars"
      data-presence-avatars=""
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        marginRight: 8,
      }}
    >
      {/* My own avatar — a subtle "you" chip on the left. Now that we
          only render when peers are present, showing my own color
          alongside theirs is useful context. */}
      {rt.myColor && rt.myDisplayName ? (
        <AvatarChip
          color={rt.myColor}
          displayName={rt.myDisplayName}
          label={`${rt.myDisplayName} (you)`}
          selected={false}
          onClick={undefined}
        />
      ) : null}
      {displayedPeers.map((p) => {
        const isFollowing = rt.followingSessionId === p.sessionId;
        return (
          <AvatarChip
            key={p.sessionId}
            color={p.color}
            displayName={p.displayName}
            label={
              isFollowing
                ? `Following ${p.displayName} — click to stop`
                : `Follow ${p.displayName}`
            }
            selected={isFollowing}
            onClick={() =>
              rt.setFollowing(isFollowing ? null : p.sessionId)
            }
          />
        );
      })}
      {overflow > 0 ? (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 26,
            height: 26,
            borderRadius: "50%",
            background: "var(--paper-2, #efe8d8)",
            color: "var(--ink-2, #4a413a)",
            fontFamily: "var(--ff-mono, monospace)",
            fontSize: 11,
            border: "1px solid var(--paper-edge, #dbcfb6)",
          }}
          title={`${overflow} more`}
        >
          +{overflow}
        </span>
      ) : null}
      {rt.followingSessionId ? (
        <button
          type="button"
          onClick={() => rt.setFollowing(null)}
          style={{
            marginLeft: 4,
            fontFamily: "var(--ff-serif)",
            fontStyle: "italic",
            fontSize: 11,
            padding: "3px 10px",
            borderRadius: 999,
            border: "1px solid var(--paper-edge)",
            background: "transparent",
            color: "var(--ink-3)",
            cursor: "pointer",
          }}
        >
          Exit follow
        </button>
      ) : null}
    </div>
  );
}

function AvatarChip({
  color,
  displayName,
  label,
  selected,
  onClick,
}: {
  color: string;
  displayName: string;
  label: string;
  selected: boolean;
  onClick: (() => void) | undefined;
}) {
  const clickable = !!onClick;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!clickable}
      title={label}
      aria-label={label}
      style={{
        width: 28,
        height: 28,
        borderRadius: "50%",
        padding: 0,
        background: "var(--paper)",
        color: "var(--ink)",
        border: `2px solid ${color}`,
        boxShadow: selected
          ? `0 0 0 2px ${color}, 0 0 8px 2px color-mix(in srgb, ${color} 45%, transparent)`
          : "none",
        fontFamily: "var(--ff-sans)",
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.02em",
        cursor: clickable ? "pointer" : "default",
        transition: "box-shadow 120ms ease, transform 120ms ease",
      }}
    >
      {initialsFor(displayName)}
    </button>
  );
}
