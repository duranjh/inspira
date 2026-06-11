// Inspira — Account feature barrel.
//
// Re-exports the page component and its per-section pieces so callers can
// either mount the whole page or compose the sections into another layout.

export { AccountSettingsPage } from "./AccountSettingsPage";
export type { AccountSettingsPageProps } from "./AccountSettingsPage";

export { AccountDeactivatedPage } from "./AccountDeactivatedPage";
export type { AccountDeactivatedPageProps } from "./AccountDeactivatedPage";

export { ProfileSection } from "./sections/ProfileSection";
export type { ProfileSectionProps } from "./sections/ProfileSection";

export { PasswordSection } from "./sections/PasswordSection";
export type { PasswordSectionProps } from "./sections/PasswordSection";

export { ThemeSection } from "./sections/ThemeSection";
export type { ThemeSectionProps } from "./sections/ThemeSection";

export { DangerZoneSection } from "./sections/DangerZoneSection";
export type { DangerZoneSectionProps } from "./sections/DangerZoneSection";

export { SecuritySection } from "./sections/SecuritySection";
export type { SecuritySectionProps } from "./sections/SecuritySection";

export { EmailPreferencesSection } from "./sections/EmailPreferencesSection";
export type { EmailPreferencesSectionProps } from "./sections/EmailPreferencesSection";
