/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { execSync } from "node:child_process";

// ---------------------------------------------------------------------------
// Release tag baked into the bundle at build time.
// Prefer $GITHUB_SHA (set by GitHub Actions) and fall back to `git rev-parse
// --short HEAD` during local builds. Both paths guard against a missing git
// binary / non-git checkout by catching the error and surfacing "dev".
// The result is exposed to the runtime as `import.meta.env.VITE_RELEASE`,
// read by src/observability/sentry.ts.
// ---------------------------------------------------------------------------
function resolveReleaseTag(): string {
  const envSha = process.env.GITHUB_SHA;
  if (envSha && envSha.length >= 7) return envSha.slice(0, 7);
  try {
    return execSync("git rev-parse --short HEAD").toString().trim();
  } catch {
    return "dev";
  }
}

export default defineConfig({
  define: {
    // JSON.stringify so Vite substitutes a string literal in the bundle.
    "import.meta.env.VITE_RELEASE": JSON.stringify(resolveReleaseTag()),
  },
  plugins: [
    react(),
    VitePWA({
      // Hand-rolled service worker at src/pwa/sw.ts. The plugin injects the
      // precache manifest (self.__WB_MANIFEST) and compiles the TS SW to
      // dist/sw.js; caching strategies live entirely in our source.
      strategies: "injectManifest",
      srcDir: "src/pwa",
      filename: "sw.ts",
      // We hand-author the manifest file in public/ so we keep a single
      // source of truth. Disable the plugin's generated manifest.
      manifest: false,
      injectRegister: null, // we register manually in registerSW.ts
      injectManifest: {
        // Precache the hashed build output; everything else is served by
        // our runtime strategies (SWR / CacheFirst / NetworkFirst).
        globPatterns: ["**/*.{js,css,html,svg,woff,woff2}"],
      },
      devOptions: {
        // Keep the service worker off in `vite dev`; registerSW is also
        // gated on import.meta.env.PROD.
        enabled: false,
      },
    }),
  ],
  // Force react + react-dom to resolve from a single copy. Without this,
  // Vite's pre-bundle can serve react-router-dom a React whose hook
  // exports read as null — BrowserRouter crashes with
  // "Cannot read properties of null (reading 'useRef')". Repro path:
  // pnpm→npm migration left the lockfile resolving fine but the
  // dev-server pre-bundle still splits react across deps.
  resolve: {
    dedupe: ["react", "react-dom"],
  },
  optimizeDeps: {
    // Ensure react-router-dom is pre-bundled against the deduped React.
    include: ["react", "react-dom", "react-dom/client", "react-router-dom"],
  },
  server: {
    port: 4175,
    host: "0.0.0.0"
  },
  test: {
    // jsdom lets us exercise DOM APIs (localStorage, document.head) in
    // the observability tests. Keep the default `node` environment per
    // file via `/** @vitest-environment node */` if a test is cheaper
    // that way.
    environment: "jsdom",
    // Only run our .test.* files — don't scan node_modules or e2e/.
    include: ["src/**/*.test.{ts,tsx}"],
    // useKeyboardShortcuts.test.ts uses `node:test` directly (not
    // vitest), so vitest picks it up via the glob but finds no
    // `describe`/`test` registrations, which vitest 4 surfaces as a
    // failed suite even though the underlying tests all pass when run
    // via `node --test`. Exclude until the file is migrated to vitest
    // OR moved under a non-matching name.
    exclude: [
      "node_modules",
      "e2e",
      "dist",
      "src/hooks/useKeyboardShortcuts.test.ts",
    ],
    // Keep the output compact in CI.
    reporters: ["default"],
  },
});
