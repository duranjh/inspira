# Inspira — locale bundles

This folder holds one JSON bundle per language. The app currently ships
with five bundles: English (`en.json`, source of truth), Spanish
(`es.json`), Portuguese (`pt.json`), French (`fr.json`), and German
(`de.json`). Drop a new file here to add another language.

## File format

Each bundle is a flat map of dot-notation keys to display strings:

```json
{
  "kickoff.heading": "Tell me about {your_idea}",
  "auth.login_button": "Sign in"
}
```

No nested objects. The flat shape keeps the key set trivial to diff against
`en.json` — which is exactly what a translator wants to work from.

### Placeholders

Interpolation uses `{name}` syntax. Example:

```json
{ "onboarding.step_indicator": "Go to step {current} of {total}" }
```

Called as:

```ts
t("onboarding.step_indicator", { current: 3, total: 5 });
// → "Go to step 3 of 5"
```

Keep the placeholder tokens exactly as they appear in `en.json`. A translator
may reorder them inside a sentence (e.g. `"Paso {current} de {total}"`) but
must not rename, remove, or spell them differently.

### Unicode + punctuation

- Ellipses use the single `…` character, not three dots.
- Em-dashes use `—`, en-dashes use `–`. Straight hyphens are fine for glue.
- Arrows in button labels (`→`, `←`) stay as-is; they're direction-agnostic.

## Adding a new language

1. Copy `en.json` to `<code>.json` (BCP-47 primary subtag — e.g. `fr.json`,
   `de.json`, `pt.json`). The primary subtag is what `navigator.language`
   auto-detects against, so `fr-CA` → `fr.json`.
2. Translate each value. Leave the keys untouched. Keep `{placeholder}`
   tokens intact.
3. Register the bundle in [`../index.ts`](../index.ts) by importing the
   JSON and adding it to the `BUNDLES` map:

   ```ts
   import frDict from "./locales/fr.json";

   const BUNDLES: Record<string, Dict> = {
     en: enDict as Dict,
     es: esDict as Dict,
     fr: frDict as Dict, // new
   };
   ```

4. Typecheck: `npx tsc --noEmit` from `app/` — should still exit 0.
5. Users whose browsers report the new language will pick it up
   automatically; any user can force it at runtime via
   `setLocale("<code>")` or through the locale picker (once shipped).

## Missing keys are safe

If a bundle is partial (say `fr.json` is only 40% translated), any missing
key falls back to the English value. The UI never breaks — worst case is a
mixed-language screen while translation is in flight. The plumbing lives in
[`../index.ts`](../index.ts)'s `t()` function.

## Keeping bundles in sync

English is the source of truth. When you add or rename a key in `en.json`:

- Add the matching key to every other bundle with the English text as a
  placeholder value so translators can see what's pending.
- Or leave it out: `t()` will fall back to English automatically. The
  trade-off is that a translator working purely from their bundle file
  won't see the new key until someone cross-references.

The first option is friendlier when you have a translator in the loop; the
second is fine for small internal shifts.
