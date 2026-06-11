# Frontend Structure

React 19 + Vite 7 app. TypeScript strict. No Redux, no SWR, no Zustand.
State lives in local React hooks; cross-component coordination uses
prop passing and — sparingly — `window.dispatchEvent` for global
actions.

## Component Tree

```
main.tsx
  React.StrictMode
    ErrorBoundary                       app/src/components/ErrorBoundary.tsx
      ToastProvider                     app/src/components/ToastProvider.tsx
        App                             app/src/App.tsx
          InspiraApp                    app/src/features/inspira/InspiraApp.tsx
            (phase: bootstrapping)      loading pulse
            (phase: kickoff)
              KickoffForm               app/src/features/inspira/KickoffForm.tsx
                (idea textarea, chip suggestions, file drop)
            (phase: loading)            loading pulse + idea preview
            (phase: error)              error screen + "start over"
            (phase: canvas)
              top-bar
                ProjectSwitcher         (inline in InspiraApp.tsx:618)
                UserMenu                (inline in InspiraApp.tsx:729)
              ProjectCanvas             app/src/features/inspira/ProjectCanvas.tsx
                ReactFlowProvider
                  ReactFlow
                    TopicNode[]         app/src/features/inspira/TopicNode.tsx
                    Background, Controls
                  canvas-composer       (inline in ProjectCanvas.tsx)
              TopicDetail               app/src/features/inspira/TopicDetail.tsx
                (zoom-morph overlay on open)
              ShortcutHelpOverlay       app/src/components/ShortcutHelpOverlay.tsx
```

Auxiliary features live under `app/src/features/`:

- `projects/` — `ProjectsListPage`, `ProjectCard` (project picker flow
  outside the canvas). Currently not reachable from `InspiraApp.tsx`;
  the top-bar project switcher replaces it for the hosted build.
- `account/` — `AccountSettingsPage`. Backend routes pending
  (`api.ts:202-213` flagged "Coming soon").
- `onboarding/` — `OnboardingWalkthrough` (first-run tour).
- `palette/` — `CommandPalette`, `SearchOverlay`. Command palette is
  stubbed behind a toast ("coming soon"); search overlay falls back to
  local-only filtering when `/api/v2/search` 404s (see `api.ts:325`).
- `errors/` — `NotFoundPage`, `OfflineBanner`, `ServerErrorPage`,
  `SessionExpiredModal`. Global error surfaces.

## State Management Philosophy

- **No Redux, no Zustand, no SWR.** React's built-in state primitives
  are enough at the current surface area.
- **Local state + prop passing.** Each phase of the app lives in a
  discriminated-union `Phase` state in `InspiraApp.tsx:35`. Transitions
  are explicit `setPhase({...})` calls. Children receive only what they
  render, via props.
- **Colocate side effects.** `useEffect` fires where the data is
  consumed. Bootstrap loading, refetch after mutation, and decisions
  fetch all live in `InspiraApp.tsx` because that's where the phase
  lives.
- **Imperative coordination when needed.** The canvas "Tidy" shortcut
  fires a `new CustomEvent("inspira:canvas-tidy")` on `window`
  (`InspiraApp.tsx:426`); `ProjectCanvas` listens for it. This keeps
  `InspiraApp` from needing to know the layout internals.
- **Optimistic updates where cheap.** `ProjectCanvas` mutates local
  `Node` / `Edge` arrays for position drags and edge creates, then
  persists via `api.updateTopic` / `api.createRelationship`
  fire-and-forget. A call to `onRefetch` (prop) reconciles
  authoritative state when the caller wants it.
- **Server state = HTTP.** `app/src/features/inspira/api.ts` wraps
  every call behind a thin fetch helper. No caching layer. The
  TypeScript types in `api.ts` are the contract.

### Discriminated-union phase state

```typescript
type Phase =
  | { kind: "bootstrapping" }
  | { kind: "kickoff"; error: string | null }
  | { kind: "loading"; idea: string }
  | { kind: "canvas"; projectId: string; envelope: KickoffEnvelope;
      openTopicId: string | null; openOriginRect: DOMRect | null }
  | { kind: "error"; message: string };
```

`InspiraApp.tsx:35`. Every render branch narrows via
`phase.kind === "..."` — TypeScript enforces exhaustiveness.

## React Flow Integration

`ProjectCanvas` is the only React Flow consumer.

Key touchpoints:

- `useNodesState` / `useEdgesState` hooks for local node/edge arrays.
- `nodeTypes = { topic: TopicNode }` registered once at module scope
  (`ProjectCanvas.tsx:59`) so the registry is stable between renders.
- Custom `TopicNode` at `app/src/features/inspira/TopicNode.tsx` —
  cream paper card, serif title, curated icon, status dot, decision
  bullets. Handles on left/right edges only (per design rules).
- Edge creation via `onConnect` callback; persists to backend with
  `api.createRelationship`.
- Edge deletion via React Flow's Delete/Backspace keybinding.
- Position drag persists via `api.updateTopic` in the `onNodesChange`
  handler when a `NodePositionChange.dragging === false` transition
  fires.
- Auto-layout uses dagre (`app/src/features/inspira/layout.ts`). Called
  on initial kickoff, on "Tidy" shortcut, and on overlap detection.

### Layout pipeline

`app/src/features/inspira/layout.ts`:

- `computeTopicLayout(topics, relationships)` — dagre LR layout with
  `rankSep=140`, `nodeSep=80`. Isolated topics (no edges) drop to a
  row below the main graph.
- `applyLayout(topics, layout)` — merges dagre positions into Topic
  objects.
- `ensureNoOverlaps(topics)` — post-pass nudges overlapping cards
  apart. Called after every topic-set mutation.
- `resolveOverlap(topics, newlyPlaced)` — single-card variant for
  drag-drop land points.

## Theme System

Styling is plain CSS with CSS custom properties for color tokens,
concentrated in `app/src/App.css` (47 KB). Component-specific CSS
lives alongside the component (e.g. `Toast.css`, `onboarding.css`,
`projects.css`, `dialogs.css`, `account.css`, `palette.css`,
`errors.css`).

No CSS-in-JS library, no Tailwind. The editorial aesthetic (cream paper,
serif display, sage/gold/ink palette) is locked in via variable tokens
at the root:

- `--cream`, `--ink`, `--ink-2`, `--ink-5` — paper + text.
- `--sage`, `--gold` — accent colors.
- Serif stack for display; sans fallback for body.

Dark mode is implemented (see `App.css` sections keyed on
`prefers-color-scheme: dark` or a theme class). No in-app toggle today.

## File Extraction Pipeline

When users drop files into a composer, they get inlined as text
excerpts for the planner:

`app/src/features/inspira/file_extract.ts` — central `fileToAttachedSource(file)`
helper:

- **text-like MIMEs** (`text/*`, `application/json|xml|yaml|csv|toml`):
  `Blob.text()` + truncate to 8,000 chars.
- **`application/pdf`** or `.pdf` extension: lazy-import `pdfjs-dist`
  (~400 KB chunk), walk pages, concat `textContent.items[*].str` with
  page separators, prefix `[PDF: <name>, <N> pages]`, truncate to
  8,000 chars. The pdfjs worker URL is resolved via
  `new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url)`
  so Vite's asset pipeline handles it in both dev and prod builds.
- **everything else:** binary stub with file name + size; excerpt reads
  `(binary file, X KB — content not inlined)`.

The function never throws — on any extraction failure it falls back to
the binary stub with a short note so the UI stays responsive.

Three composer surfaces use this: `KickoffForm`, `ProjectCanvas`
composer, `TopicDetail` composer.

For URLs and pasted text, `app/src/features/inspira/sources.ts` exposes
`fetchUrlAsSource` and `textAsSource` — same `AttachedSource` shape,
different origin. The 8,000-char cap is consistent across all paths.

## Authenticated Fetch

`app/src/features/inspira/api.ts`:

- `DEFAULT_BASE_URL` reads `import.meta.env.VITE_INSPIRA_API_URL`,
  defaulting to `http://127.0.0.1:4174`.
- `postJson` / `getJson` set `credentials: "include"` so the
  `inspira_session` cookie rides along on cross-origin API calls from
  the Vite dev server (different origin from the backend).
- Errors throw `Error` with the raw status + body text. Upstream
  handlers (`InspiraApp.tsx:219`, `ErrorBoundary`) translate to UI
  state.

The `api` module is an object literal — one method per endpoint.
Consumers import named fn references, never the whole thing wrapped in
a hook. Keeps types simple and tree-shakes cleanly.

## Global Infrastructure

### ErrorBoundary (`app/src/components/ErrorBoundary.tsx`)

- Wraps the entire app in `main.tsx`.
- Catches render errors, shows a warm editorial fallback with "Try
  again" (reset boundary in place) and "Start over" (reload).
- Resets on `resetKey` prop change — useful for project switches.
- Calls `window.Sentry?.captureException` when present (loose ambient
  declaration; the SDK is never imported here).

### ToastProvider (`app/src/components/ToastProvider.tsx`)

- Stack at bottom-right, auto-dismiss, click to dismiss.
- Exports a hook `useToast()` and a module-level `toast` singleton
  backed by a pub-sub so non-hook code (api.ts top-level callbacks) can
  push toasts.

### ShortcutHelpOverlay (`app/src/components/ShortcutHelpOverlay.tsx`)

- Triggered by `?`, grouped by registered `group` label ("Global",
  "Canvas", "Topic detail").
- Reads from the module-level registry in `useKeyboardShortcuts`
  (`app/src/hooks/useKeyboardShortcuts.ts`). Any component calling the
  hook with a binding list registers into that registry for the overlay
  to consume.

### Online-status hook (`app/src/hooks/useOnlineStatus.ts`)

Used by `OfflineBanner` (`app/src/features/errors/OfflineBanner.tsx`)
to display a yellow bar when `navigator.onLine === false`.

## Desktop packaging

Tauri wrapper at `app/src-tauri/`. The same Vite build produces the
desktop asset payload. No desktop-only code paths today; the user
experience is identical to the web build. See the repo `README.md` for
`npm run tauri:dev` and `npm run desktop:build:windows` helpers.

## What is **not** implemented

Status flagged explicitly so contributors don't mistake them for live
features:

- **Command palette (`Mod+K`)** — UI scaffold exists in
  `app/src/features/palette/CommandPalette.tsx`, but the `Mod+K` handler
  in `InspiraApp.tsx:392` shows a "coming soon" toast instead of opening
  it.
- **Cross-project search (`/api/v2/search`)** — frontend
  `api.searchAll` is wired; backend route does not exist. `SearchOverlay`
  falls back to local filter over currently-loaded topics and the known
  project list when the fetch 404s (`api.ts:325`).
- **Account settings page** — form + routes (`api.ts:202-213`) are in
  place; backend (`/api/auth/profile`, `/api/auth/change-password`,
  `/api/auth/delete-account`) returns 404 today. Frontend detects and
  toasts "Coming soon".
- **PWA service worker** — Vite plugin wired, SW source at
  `app/src/pwa/sw.ts`, registration gated on `import.meta.env.PROD`.
  Effectively inactive until the first production build goes live.
- **Google OAuth** — backend scaffolds the routes and returns 501; the
  frontend does not yet render the "Continue with Google" button.
- **Suggestions UI** — backend `/api/v2/projects/suggest` ships; the
  frontend new-project screen does not yet render suggestion chips
  (feature-flagged to the kickoff rotation currently).
