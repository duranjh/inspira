// Inspira — terms of service wrapper (public page at /legal/terms).
//
// Thin shim kept for backward compatibility with existing imports. The
// terms live in `docs/legal/terms-of-service.md` and are rendered
// through the shared LegalPage component.
//
// Legal review status is tracked outside this repo.

import type { JSX } from "react";

import { LegalPage } from "./LegalPage";

export function TermsPage(): JSX.Element {
  return <LegalPage doc="terms" />;
}

export default TermsPage;
