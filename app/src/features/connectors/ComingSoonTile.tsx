// Tier-2 connector tile — mailto-only.
//
// Separate from ConnectorTile so the 4-state discipline on the
// Live tier stays clean. This tile has exactly one action: open
// the partner's mail client with a pre-filled subject. Backend
// supplies the contact_route.

import { ReactElement } from "react";

import type { ComingSoonConnectorPayload } from "./types";

export function ComingSoonTile({
  payload,
}: {
  payload: ComingSoonConnectorPayload;
}): ReactElement {
  const initials = payload.display_name
    .split(/[ /]+/)
    .map((p) => p[0] || "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <div
      className="connector-tile connector-tile--soon"
      data-provider={payload.provider}
    >
      <div className="connector-tile__head">
        <div className="connector-tile__logo connector-tile__logo--gold">
          {initials}
        </div>
        <div className="connector-tile__name">{payload.display_name}</div>
      </div>
      <p className="connector-tile__desc">{payload.summary}</p>
      <a
        className="connector-tile__mailto"
        href={payload.contact_route}
        rel="noopener"
      >
        Talk to us →
      </a>
    </div>
  );
}
