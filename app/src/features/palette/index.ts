// Barrel exports for the command palette + search overlay feature.
//
// Callers (e.g. InspiraApp) should import from this module rather than
// reaching into the individual files, so the internal layout of the
// feature stays encapsulated.

export { CommandPalette } from "./CommandPalette";
export type { Command, CommandPaletteProps } from "./CommandPalette";

export { SearchOverlay } from "./SearchOverlay";
export type { SearchOverlayProps } from "./SearchOverlay";

export { useFuzzyMatch } from "./useFuzzyMatch";
export type { FuzzyResult } from "./useFuzzyMatch";
