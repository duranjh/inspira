// Barrel exports for the Shelves feature.
//
// Caller wiring (ProjectsListPage, InspiraApp) should import from this
// module so internal file moves inside features/shelves don't ripple out.

export { ShelvesView } from "./ShelvesView";
export type { ShelvesViewProps } from "./ShelvesView";

export { ShelfRow } from "./ShelfRow";
export type { ShelfRowProps } from "./ShelfRow";

export { ShelfHeader } from "./ShelfHeader";
export type { ShelfHeaderProps } from "./ShelfHeader";

export { NewShelfDialog } from "./NewShelfDialog";
export type { NewShelfDialogProps } from "./NewShelfDialog";
