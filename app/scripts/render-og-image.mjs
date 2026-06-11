// Inspira — render og-image.svg to og-image.png at 1200x630.
//
// Some older/niche platforms (LinkedIn legacy crawlers, a handful of
// enterprise Slack installs) still refuse SVG og-images. To cover them
// we keep a PNG alongside the SVG and reference both in index.html —
// modern crawlers take the first <meta property="og:image">, older
// ones fall back to the next one they understand.
//
// Run:  node scripts/render-og-image.mjs
//
// Reads public/og-image.svg, writes public/og-image.png. Idempotent;
// rerun whenever the SVG source changes.

import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PUBLIC_DIR = resolve(__dirname, "..", "public");

const SRC = resolve(PUBLIC_DIR, "og-image.svg");
const OUT = resolve(PUBLIC_DIR, "og-image.png");

async function main() {
  const svg = await readFile(SRC);
  // Force 1200x630 regardless of the SVG's declared viewBox/width so we
  // always hit the canonical og-image aspect ratio.
  const png = await sharp(svg, { density: 192 })
    .resize({ width: 1200, height: 630, fit: "contain", background: { r: 245, g: 240, b: 230 } })
    .png({ compressionLevel: 9, adaptiveFiltering: true })
    .toBuffer();
  await writeFile(OUT, png);
  console.log(`wrote ${OUT} (${png.length} bytes)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
