// Shared helper for turning a picked File into an AttachedSource.
//
// Three composer surfaces (KickoffForm, ProjectCanvas, TopicDetail) all
// need to ingest user-dropped files the same way, so the extraction
// logic lives here instead of being duplicated three times.
//
// Behavior by type:
//   - Text-like MIME (text/*, application/json|xml|yaml|csv|toml) — read
//     the File as text via Blob.text() and truncate to MAX_EXCERPT_CHARS.
//     Falls back to a binary stub if the browser doesn't expose Blob.text.
//   - application/pdf — lazy-import pdfjs-dist, iterate pages, concat
//     textContent.items[*].str with page separators, and prefix with a
//     [PDF: <name>, <N> pages] header. Truncated to MAX_EXCERPT_CHARS.
//     On any failure (corrupt, encrypted, pdfjs not supported), falls
//     back to the binary stub with a short note so the planner still
//     knows a PDF was attached.
//   - image/* (png, jpg, jpeg, webp, gif, bmp, tiff) — lazy-import
//     tesseract.js, run English OCR against the image, prefix with a
//     [Image OCR: <name>, <NxN>px] header. Truncated to the same cap.
//     Images over 10MB, OCR timeouts (60s), OCR errors, and results with
//     less than 20 chars of readable text all fall back to the binary
//     stub with a descriptive note.
//   - Everything else — binary stub: filename + size, no inlined body.
//
// pdfjs-dist and tesseract.js are both imported via dynamic import() so
// Vite code-splits their bundles (pdfjs ~400KB; tesseract.js worker +
// language data ~2MB) out of the initial load. The pdfjs worker source
// is resolved through new URL(...) + import.meta.url so Vite's asset
// pipeline emits and rewrites the URL correctly in both dev and
// production builds.

import type { AttachedSource } from "./api";

// Keep the cap in sync with the URL/paste path in sources.ts so the
// planner payload stays bounded regardless of attachment origin.
const MAX_EXCERPT_CHARS = 8000;

// MIMEs we inline verbatim as text. Anything else falls through to the
// PDF branch or the binary stub. Matches the pattern the three composer
// files used before this helper existed.
const TEXT_LIKE_MIME_PATTERN =
  /^text\/|^application\/(json|xml|x-yaml|yaml|csv|toml)/;

// Image extensions we route to OCR when MIME type is missing or generic
// (some drops on Windows arrive with an empty file.type). The MIME check
// `file.type.startsWith("image/")` is still the primary signal.
const IMAGE_EXT_PATTERN = /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i;

// Skip OCR entirely above this size — a 20MP photo can take minutes to
// OCR and that's not a useful composer experience.
const MAX_OCR_BYTES = 10 * 1024 * 1024;

// Hard cap for a single recognize() call. Past this we assume something
// is wrong (bad image, stuck worker) and fall back to the stub so the
// user isn't staring at a spinner forever.
const OCR_TIMEOUT_MS = 60_000;

// Below this the OCR result is almost certainly noise (blank image,
// whitespace-only, a few stray glyphs) — better to admit we got nothing
// than send the planner garbage.
const OCR_MIN_USEFUL_CHARS = 20;

// Worker URL is resolved once per session. Some older browsers without
// import.meta.url support will throw here; we catch that inside the PDF
// branch and fall back to a binary stub rather than crashing the picker.
let pdfWorkerConfigured = false;

// Cached lazy handle to pdfjs so a user attaching multiple PDFs in a
// single session only pays the import cost once.
type PdfjsModule = typeof import("pdfjs-dist");
let pdfjsModulePromise: Promise<PdfjsModule> | null = null;

/**
 * Entry point for the three composers. Given a File, return a populated
 * AttachedSource — with inlined text when the file is text or PDF, or a
 * binary stub otherwise. Never throws: on any failure we degrade to a
 * stub so the UI stays responsive.
 */
export async function fileToAttachedSource(
  file: File,
): Promise<AttachedSource> {
  const mime = file.type;

  if (TEXT_LIKE_MIME_PATTERN.test(mime)) {
    return await readTextFile(file);
  }

  if (mime === "application/pdf" || /\.pdf$/i.test(file.name)) {
    return await readPdfFile(file);
  }

  if (mime.startsWith("image/") || IMAGE_EXT_PATTERN.test(file.name)) {
    return await readImageFile(file);
  }

  return binaryStub(file);
}

// -- text branch ----------------------------------------------------------

async function readTextFile(file: File): Promise<AttachedSource> {
  // Blob.text() is widely supported but not universal. If it's missing
  // or throws, fall back to FileReader; if that's also unavailable, give
  // up and return a binary stub.
  if (typeof file.text === "function") {
    try {
      const text = await file.text();
      return {
        display_name: file.name,
        kind: file.type || "file:text",
        excerpt: text.slice(0, MAX_EXCERPT_CHARS),
      };
    } catch (err) {
      console.warn("[Inspira] failed to read file as text", file.name, err);
    }
  }

  const fallback = await readWithFileReader(file);
  if (fallback !== null) {
    return {
      display_name: file.name,
      kind: file.type || "file:text",
      excerpt: fallback.slice(0, MAX_EXCERPT_CHARS),
    };
  }

  return binaryStub(file, "could not read as text");
}

function readWithFileReader(file: File): Promise<string | null> {
  return new Promise((resolve) => {
    if (typeof FileReader === "undefined") {
      resolve(null);
      return;
    }
    try {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result;
        resolve(typeof result === "string" ? result : null);
      };
      reader.onerror = () => resolve(null);
      reader.readAsText(file);
    } catch {
      resolve(null);
    }
  });
}

// -- pdf branch -----------------------------------------------------------

async function readPdfFile(file: File): Promise<AttachedSource> {
  let pdfjs: PdfjsModule;
  try {
    pdfjs = await loadPdfjs();
  } catch (err) {
    console.warn("[Inspira] pdfjs-dist unavailable", file.name, err);
    return binaryStub(file, "PDF extraction unavailable in this browser");
  }

  try {
    const buffer = await file.arrayBuffer();
    // pdfjs mutates the buffer during parsing, so hand it its own copy.
    const data = new Uint8Array(buffer);
    const loadingTask = pdfjs.getDocument({ data });
    const pdf = await loadingTask.promise;

    const pageCount = pdf.numPages;
    const pageTexts: string[] = [];
    let totalChars = 0;

    // Cap budget: `[PDF: ..., N pages]\n\n` header + MAX_EXCERPT_CHARS of
    // body. We stop pulling pages once the body budget is exhausted so we
    // don't parse a 500-page PDF whose text we'll truncate anyway.
    for (let pageNum = 1; pageNum <= pageCount; pageNum++) {
      if (totalChars >= MAX_EXCERPT_CHARS) break;
      const page = await pdf.getPage(pageNum);
      const content = await page.getTextContent();
      const parts: string[] = [];
      for (const item of content.items) {
        // TextMarkedContent has no `str`; filter it out via the guard.
        if (typeof (item as { str?: unknown }).str === "string") {
          parts.push((item as { str: string }).str);
        }
      }
      const pageText = parts.join(" ").replace(/\s+/g, " ").trim();
      pageTexts.push(pageText);
      totalChars += pageText.length + 2; // +2 for the page separator
    }

    const header = `[PDF: ${file.name}, ${pageCount} page${
      pageCount === 1 ? "" : "s"
    }]\n\n`;
    const body = pageTexts.join("\n\n");
    const truncatedBody =
      body.length > MAX_EXCERPT_CHARS
        ? body.slice(0, MAX_EXCERPT_CHARS) + "\n\n[...truncated]"
        : body;

    return {
      display_name: file.name,
      kind: "application/pdf",
      excerpt: header + truncatedBody,
    };
  } catch (err) {
    console.warn("[Inspira] PDF text extraction failed", file.name, err);
    return binaryStub(
      file,
      "PDF could not be read (corrupt or encrypted); filename attached for reference",
    );
  }
}

async function loadPdfjs(): Promise<PdfjsModule> {
  if (pdfjsModulePromise) return pdfjsModulePromise;
  pdfjsModulePromise = (async () => {
    const mod = await import("pdfjs-dist");
    if (!pdfWorkerConfigured) {
      // Vite asset-URL pattern: this resolves to a hashed URL at build
      // time and a dev-server URL in `npm run dev`. The worker file ships
      // inside pdfjs-dist at build/pdf.worker.min.mjs (verified against
      // pdfjs-dist 5.6.x on disk).
      try {
        mod.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();
        pdfWorkerConfigured = true;
      } catch (err) {
        // Older environments without import.meta.url support. pdfjs will
        // fall back to a synchronous in-thread worker, which is slower
        // but still functional.
        console.warn("[Inspira] could not configure pdfjs worker URL", err);
        pdfWorkerConfigured = true;
      }
    }
    return mod;
  })();
  return pdfjsModulePromise;
}

// -- image branch ---------------------------------------------------------

async function readImageFile(file: File): Promise<AttachedSource> {
  // Very large images aren't worth OCRing in the browser — the user
  // gets a better experience attaching the filename stub than waiting
  // minutes for a worker to grind through a 20MP photo.
  if (file.size > MAX_OCR_BYTES) {
    const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
    return binaryStub(
      file,
      `image too large for OCR (${sizeMb} MB > 10 MB); filename attached for reference`,
    );
  }

  // Dimensions go into the header so the planner knows roughly what
  // kind of image it got. createImageBitmap is broadly supported and
  // cheaper than decoding into a canvas. If it fails we still try OCR
  // and fall back to "unknown" dimensions in the header.
  let dimensions = "unknown";
  try {
    const bitmap = await createImageBitmap(file);
    dimensions = `${bitmap.width}x${bitmap.height}`;
    // Free the decoded bitmap once we have its size.
    bitmap.close?.();
  } catch (err) {
    console.warn("[Inspira] could not read image dimensions", file.name, err);
  }

  let tesseract: typeof import("tesseract.js");
  try {
    tesseract = await import("tesseract.js");
  } catch (err) {
    console.warn("[Inspira] tesseract.js unavailable", file.name, err);
    return binaryStub(file, "image OCR unavailable in this browser");
  }

  const isDev =
    typeof import.meta !== "undefined" &&
    (import.meta as { env?: { DEV?: boolean } }).env?.DEV === true;

  try {
    const worker = await tesseract.createWorker("eng", undefined, {
      logger: isDev
        ? (m: { status: string; progress: number }) => {
            // Dev-only breadcrumb. Production builds stay silent so the
            // console doesn't get spammed with per-image progress noise.
            console.debug(
              "[Inspira] OCR",
              file.name,
              m.status,
              Math.round(m.progress * 100) + "%",
            );
          }
        : undefined,
    });

    let text: string;
    try {
      // 60s safety net: if recognize() hangs we reject, terminate the
      // worker in finally, and degrade to the binary stub.
      const recognizePromise = worker.recognize(file).then((r) => r.data.text);
      let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
      const timeoutPromise = new Promise<never>((_, reject) => {
        timeoutHandle = setTimeout(
          () => reject(new Error("ocr-timeout")),
          OCR_TIMEOUT_MS,
        );
      });
      try {
        text = await Promise.race([recognizePromise, timeoutPromise]);
      } finally {
        if (timeoutHandle !== undefined) clearTimeout(timeoutHandle);
      }
    } finally {
      // terminate() returns a promise; we don't need to await it for
      // correctness, but awaiting makes the worker cleanup deterministic
      // if tests or repeated uploads follow. Swallow any terminate error
      // so it never masks a successful OCR.
      try {
        await worker.terminate();
      } catch (termErr) {
        console.warn("[Inspira] OCR worker terminate failed", termErr);
      }
    }

    const trimmed = text.trim();
    if (trimmed.length < OCR_MIN_USEFUL_CHARS) {
      return binaryStub(
        file,
        "image attached; OCR extracted no readable text",
      );
    }

    const header = `[Image OCR: ${file.name}, ${dimensions}px]\n\n`;
    const body =
      trimmed.length > MAX_EXCERPT_CHARS
        ? trimmed.slice(0, MAX_EXCERPT_CHARS) + "\n\n[...truncated]"
        : trimmed;

    return {
      display_name: file.name,
      kind: file.type || "image",
      excerpt: header + body,
    };
  } catch (err) {
    const isTimeout =
      err instanceof Error && err.message === "ocr-timeout";
    console.warn(
      "[Inspira] image OCR failed",
      file.name,
      isTimeout ? "(timeout)" : err,
    );
    return binaryStub(
      file,
      isTimeout ? "image OCR timed out" : "image OCR failed",
    );
  }
}

// -- fallback stub --------------------------------------------------------

function binaryStub(file: File, note?: string): AttachedSource {
  const sizeKb = Math.round(file.size / 1024);
  const base = `(binary file, ${sizeKb} KB — content not inlined)`;
  return {
    display_name: file.name,
    kind: file.type || "file:binary",
    excerpt: note ? `${base} [${note}]` : base,
  };
}
