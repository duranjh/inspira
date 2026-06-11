// Barrel — scaffold feature surface.
//
// PR 2 deleted the credit-pack purchase flow (BuyCreditsDialog) and the
// credit balance display (CreditMeter). Plan-tier gating now lives at
// the route layer (a 402 with `error: "upgrade_required"` is what the
// frontend listens for). The scaffold UI primitives below are still
// the right shape for the success path.

import "./scaffold.css";

export { ScaffoldButton } from "./ScaffoldButton";
export type { ScaffoldButtonProps } from "./ScaffoldButton";
export { ScaffoldProgress } from "./ScaffoldProgress";
export type { ScaffoldProgressProps } from "./ScaffoldProgress";
export { ScaffoldResult } from "./ScaffoldResult";
export type { ScaffoldResultProps } from "./ScaffoldResult";
