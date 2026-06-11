// Overlays other users' cursors on the canvas. Each peer cursor is a
// tiny SVG arrow in their assigned color, with a rounded name pill
// below. Positioned via ReactFlow's flow→screen transform so we track
// pan + zoom cleanly.
//
// We render one absolutely-positioned <div> per peer at (screenX,
// screenY) with a `transform: translate(...)` for smooth motion. CSS
// transitions interpolate between discrete server updates so motion
// feels continuous at 30fps.
//
// The overlay itself is pointer-events:none so it never interferes
// with canvas interaction.

import { useMemo } from "react";
import { useReactFlow, useStore } from "reactflow";

import { useRealtimeContext } from "./RealtimeContext";

export function RemoteCursors() {
  const { peers, mySessionId } = useRealtimeContext();
  const rf = useReactFlow();

  // Subscribe to viewport changes so this component re-renders when
  // pan/zoom changes. `transform` is ReactFlow's [x, y, zoom] tuple.
  const transform = useStore((s) => s.transform);

  const others = useMemo(
    () => peers.filter((p) => p.sessionId !== mySessionId && p.cursor),
    [peers, mySessionId],
  );

  return (
    <div
      className="rt-cursor-layer"
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        zIndex: 5,
        overflow: "hidden",
      }}
    >
      {others
        .filter((p) => p.cursor)
        .map((p) => {
          // Narrowed by the filter above — safe to assert non-null.
          const cursor = p.cursor!;
          // transform: [tx, ty, zoom]. Flow→screen: screen = flow * zoom + t.
          const [tx, ty, zoom] = transform;
          const screenX = cursor.x * zoom + tx;
          const screenY = cursor.y * zoom + ty;
          return (
            <div
              key={p.sessionId}
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                transform: `translate3d(${screenX}px, ${screenY}px, 0)`,
                transition: "transform 60ms linear",
                willChange: "transform",
              }}
            >
              <RemoteCursorGlyph color={p.color} displayName={p.displayName} />
            </div>
          );
        })}
    </div>
  );
}

function RemoteCursorGlyph({
  color,
  displayName,
}: {
  color: string;
  displayName: string;
}) {
  return (
    <div style={{ position: "relative" }}>
      <svg
        width="18"
        height="22"
        viewBox="0 0 18 22"
        style={{ display: "block", filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.35))" }}
        aria-hidden="true"
      >
        <path
          d="M1.5 1.5 L16 10 L8.5 11 L11 17 L8 18.5 L5.5 13 L1.5 16 Z"
          fill={color}
          stroke="rgba(0,0,0,0.4)"
          strokeWidth="0.6"
          strokeLinejoin="round"
        />
      </svg>
      <div
        style={{
          position: "absolute",
          top: 20,
          left: 10,
          padding: "3px 8px",
          background: color,
          color: "#fff",
          borderRadius: 12,
          fontFamily:
            "var(--ff-sans, 'Source Sans Pro', system-ui, sans-serif)",
          fontSize: 11,
          fontWeight: 500,
          whiteSpace: "nowrap",
          textShadow: "0 1px 1px rgba(0,0,0,0.2)",
          boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
          maxWidth: 160,
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {displayName}
      </div>
    </div>
  );
}
