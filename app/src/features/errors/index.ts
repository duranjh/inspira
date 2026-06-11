// Inspira — error-state screens barrel.
//
// Single import site for the four error surfaces so InspiraApp (and
// any future router) can pull them all from one place:
//
//   import {
//     NotFoundPage,
//     ServerErrorPage,
//     OfflineBanner,
//     SessionExpiredModal,
//   } from "./features/errors";

export { NotFoundPage } from "./NotFoundPage";
export type { NotFoundPageProps } from "./NotFoundPage";

export { ServerErrorPage } from "./ServerErrorPage";
export type { ServerErrorPageProps } from "./ServerErrorPage";

export { OfflineBanner } from "./OfflineBanner";

export { SessionExpiredModal } from "./SessionExpiredModal";
export type { SessionExpiredModalProps } from "./SessionExpiredModal";
