// Barrel exports for the Projects list feature.
//
// Caller wiring (InspiraApp) should import from this module so internal
// file moves inside features/projects don't ripple out.

export { ProjectsListPage } from "./ProjectsListPage";
export type { ProjectsListPageProps } from "./ProjectsListPage";

export { ProjectCard, ProjectCardSkeleton } from "./ProjectCard";
export type { ProjectCardProps } from "./ProjectCard";
