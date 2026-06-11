// Inspira — privacy policy wrapper (public page at /legal/privacy).
//
// Thin shim kept for backward compatibility with existing imports. The
// policy body lives in `docs/legal/privacy-policy.md` and is rendered
// through the shared LegalPage component.
//
// Legal-review tracker lives in docs/legal/TODO.md.

import type { JSX } from "react";

import { LegalPage } from "./LegalPage";

export function PrivacyPage(): JSX.Element {
  return <LegalPage doc="privacy" />;
}

export default PrivacyPage;
