// Tier-3 connector tile — greyed, no CTA.
//
// Separate from ConnectorTile to keep the Live-tier 4-state
// discipline pure. Renders inline (logo + name + "Future" tag)
// in a horizontal row.

import { ReactElement } from "react";

import type { FutureConnectorPayload } from "./types";

export function FutureTile({
  payload,
}: {
  payload: FutureConnectorPayload;
}): ReactElement {
  const initials = payload.display_name
    .split(/[ /]+/)
    .map((p) => p[0] || "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <div
      className="connector-tile connector-tile--future"
      data-provider={payload.provider}
      title={payload.summary}
    >
      <div className="connector-tile__logo connector-tile__logo--muted">
        {initials}
      </div>
      <span className="connector-tile__name">{payload.display_name}</span>
      <span className="connector-tile__future-tag">Future</span>
    </div>
  );
}
