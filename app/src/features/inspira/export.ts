// Client-side export utilities.
//
// Two modes:
//   - topicToMarkdown : a single topic's full content (title, icon, status,
//                       decisions, Q&A thread) rendered as clean Markdown.
//                       The caller feeds this into navigator.clipboard.
//   - projectToHtml   : the entire project (all topics, their decisions,
//                       and a compact Q&A summary per topic) as a self-
//                       contained HTML string ready to hand to html2pdf.js.
//
// Both are pure functions — no DOM reads, no fetches — so they're easy to
// test and don't care whether they run in the main thread or a worker.
// All HTML generation uses an escape helper so user-supplied text can't
// inject markup into the PDF.
//
// Aesthetic: the PDF uses the same cream paper / ink palette as the app,
// a serif body, and restrained editorial rhythm (generous line-height,
// small caps eyebrows). Intent is a document the user might actually want
// to share, not a raw data dump.

import type { Decision, QnaTurn, Relationship, Topic, V2Project } from "./api";

// Matches TopicDetail's iconGlyph — duplicated here so exports work without
// reaching into the component. If the map drifts, both callers just fall
// back to the neutral bullet.
const ICON_GLYPH_MAP: Record<string, string> = {
  lightbulb: "\u25CB",
  feather: "\u270E",
  book: "\u25A1",
  compass: "\u27D0",
  "map-pin": "\u25C9",
  clock: "\u25D0",
  flag: "\u2690",
  heart: "\u2665",
  chart: "\u25A6",
  megaphone: "\u23F5",
  camera: "\u25C7",
  leaf: "\u273F",
};

function iconGlyph(name: string): string {
  return ICON_GLYPH_MAP[name] ?? "\u2022";
}

// -----------------------------------------------------------------------
// Markdown escape helper
// -----------------------------------------------------------------------
//
// Escapes the characters that Markdown treats as formatting so user-supplied
// strings can't silently reformat the output. We escape backticks, asterisks,
// underscores, curly braces, brackets, parens, pound signs, plus, minus,
// exclamation, pipe, greater-than, and the escape character itself.
//
// Important: we do NOT escape the markers that OUR templates add (the leading
// `#` in a heading, the `- ` in a bullet, the `_..._` italic wrappers). Those
// live in the template string unescaped; only the interpolated user content
// passes through this helper.
function escapeMarkdown(s: string): string {
  if (!s) return s;
  return s.replace(/([\\`*_{}\[\]()#+\-!|>])/g, "\\$1");
}

function statusLabel(status: Topic["status"]): string {
  switch (status) {
    case "empty":
      return "Empty";
    case "in_progress":
      return "In progress";
    case "fleshed_out":
      return "Fleshed out";
    default:
      return status;
  }
}

// -----------------------------------------------------------------------
// Topic-as-Markdown
// -----------------------------------------------------------------------

/**
 * Render one topic as a self-contained Markdown document. Suitable for
 * pasting into Notion, a doc, or plain-text email.
 *
 * Format (see the feature request for the canonical spec):
 *   # <title> <icon>
 *
 *   Status: <status>
 *
 *   ## Decisions
 *   - <statement>
 *     _<rationale>_
 *
 *   ## Q&A Thread
 *   **Planner:** <question>
 *   > _Why this matters: <why>_
 *
 *   **You:** <answer>
 */
export function topicToMarkdown(
  topic: Topic,
  turns: QnaTurn[],
  decisions: Decision[],
): string {
  const parts: string[] = [];

  const glyph = iconGlyph(topic.icon);
  // H1 header: topic title is H1 here. Per spec we leave the H1 line
  // structurally unescaped — but the title text itself still gets its
  // formatting characters escaped so a rogue backtick in a user title
  // can't accidentally open a code block.
  parts.push(`# ${escapeMarkdown(topic.title)} ${glyph}`.trimEnd());
  parts.push("");
  parts.push(`Status: ${statusLabel(topic.status)}`);
  parts.push("");

  parts.push("## Decisions");
  if (decisions.length === 0) {
    parts.push("");
    parts.push("_No decisions captured yet._");
  } else {
    for (const d of decisions) {
      parts.push(`- ${escapeMarkdown(d.statement)}`);
      if (d.rationale && d.rationale.trim()) {
        parts.push(`  _${escapeMarkdown(d.rationale.trim())}_`);
      }
    }
  }
  parts.push("");

  parts.push("## Q&A Thread");
  if (turns.length === 0) {
    parts.push("");
    parts.push("_No conversation yet._");
  } else {
    // Turns arrive ordered by order_index (server-side). We preserve that
    // order; alternating planner/user is the common pattern but we don't
    // assume strict alternation — just render each in sequence.
    const ordered = [...turns].sort((a, b) => a.order_index - b.order_index);
    for (let i = 0; i < ordered.length; i++) {
      const t = ordered[i];
      parts.push("");
      if (t.role === "planner") {
        parts.push(`**Planner:** ${escapeMarkdown(t.body)}`);
        if (t.why_this_matters && t.why_this_matters.trim()) {
          parts.push(
            `> _Why this matters: ${escapeMarkdown(t.why_this_matters.trim())}_`,
          );
        }
      } else {
        parts.push(`**You:** ${escapeMarkdown(t.body)}`);
      }
    }
  }

  // Trailing newline so downstream paste lands cleanly.
  return parts.join("\n") + "\n";
}

// -----------------------------------------------------------------------
// Project-as-HTML (for PDF via html2pdf.js)
// -----------------------------------------------------------------------

export type ProjectExportInput = {
  projectTitle?: string | null;
  topics: Topic[];
  // Per-topic Q&A threads. Keyed by topic_id. Missing entries are rendered
  // as "no thread yet" — we don't fetch on behalf of the caller.
  turnsByTopicId?: Map<string, QnaTurn[]>;
  // Per-topic decisions. Keyed by topic_id. Missing entries are rendered
  // as "no decisions yet".
  decisionsByTopicId?: Map<string, Decision[]>;
  // When falsy, the cover page shows a warning note that decisions /
  // Q&A content is missing. Used when the caller only has topic-level
  // metadata at export time.
  hasFullContent: boolean;
};

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatDate(d: Date): string {
  // Keep format locale-agnostic and deterministic: "April 20, 2026".
  const months = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ];
  return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

/**
 * Render the entire project as an HTML document ready for html2pdf.js.
 * Returns a FULL HTML string — the caller wraps it in a detached DOM
 * element and hands it to html2pdf() for rendering.
 *
 * The layout follows the app's warm editorial aesthetic (cream paper,
 * ink palette, serif body) but inlines all styles so html2canvas sees
 * a complete render without the page's own stylesheet.
 */
export function projectToHtml(input: ProjectExportInput): string {
  const title =
    input.projectTitle && input.projectTitle.trim()
      ? input.projectTitle.trim()
      : "Inspira Project";
  const dateLine = formatDate(new Date());

  const styles = `
    * { box-sizing: border-box; }
    body {
      font-family: Georgia, "Times New Roman", serif;
      color: #2B2520;
      background: #F5F0E6;
      margin: 0;
      padding: 0;
      line-height: 1.55;
      font-size: 12pt;
    }
    .doc { padding: 48px 56px; }
    .cover {
      min-height: 760px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: flex-start;
      padding: 56px;
      border-bottom: 1px solid #E3D9C6;
      page-break-after: always;
    }
    .cover__eyebrow {
      font-family: "Helvetica Neue", Arial, sans-serif;
      font-size: 10pt;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      color: #7A6F64;
      margin: 0 0 18px 0;
    }
    .cover__title {
      /* Long titles used to overflow the 620px box because the 40pt
         serif rendered past the right edge with no word-break. Shrink
         the default type a notch + let words break mid-token if
         required. min() caps width relative to the parent so narrower
         renderers don't pin to 620px and still clip. */
      font-size: 32pt;
      font-weight: 400;
      line-height: 1.15;
      margin: 0 0 24px 0;
      color: #2B2520;
      max-width: min(620px, 100%);
      overflow-wrap: anywhere;
      word-break: break-word;
      hyphens: auto;
    }
    .cover__date {
      font-size: 12pt;
      color: #4A413A;
      font-style: italic;
      margin: 0 0 8px 0;
    }
    .cover__summary {
      font-size: 11pt;
      color: #4A413A;
      margin: 32px 0 0 0;
      max-width: 520px;
    }
    .cover__warning {
      margin-top: 28px;
      padding: 14px 18px;
      background: #FBF2DC;
      border-left: 3px solid #C89A3F;
      color: #4A413A;
      font-size: 10.5pt;
      max-width: 560px;
      font-family: "Helvetica Neue", Arial, sans-serif;
      font-style: normal;
    }
    .topic {
      page-break-inside: avoid;
      margin: 0 0 48px 0;
    }
    .topic__eyebrow {
      font-family: "Helvetica Neue", Arial, sans-serif;
      font-size: 9.5pt;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      color: #7A6F64;
      margin: 0 0 6px 0;
    }
    .topic__title {
      font-size: 22pt;
      font-weight: 400;
      margin: 0 0 18px 0;
      color: #2B2520;
      border-bottom: 1px solid #E3D9C6;
      padding-bottom: 10px;
    }
    .topic__icon {
      display: inline-block;
      font-size: 18pt;
      margin-right: 10px;
      color: #7A6F64;
    }
    .topic__status {
      display: inline-block;
      font-family: "Helvetica Neue", Arial, sans-serif;
      font-size: 9pt;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: #7A6F64;
      padding: 3px 10px;
      border: 1px solid #E3D9C6;
      border-radius: 999px;
      margin-left: 10px;
      vertical-align: middle;
    }
    .section__heading {
      font-family: "Helvetica Neue", Arial, sans-serif;
      font-size: 10pt;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      color: #7A6F64;
      margin: 22px 0 10px 0;
    }
    .decisions {
      margin: 0 0 22px 0;
      padding: 0;
      list-style: none;
    }
    .decisions__item {
      padding: 10px 0;
      border-bottom: 1px dotted #E3D9C6;
    }
    .decisions__item:last-child { border-bottom: none; }
    .decisions__statement {
      font-size: 12pt;
      color: #2B2520;
      margin: 0;
    }
    .decisions__rationale {
      font-size: 10.5pt;
      color: #4A413A;
      font-style: italic;
      margin: 4px 0 0 0;
    }
    .decisions__empty, .qna__empty {
      color: #7A6F64;
      font-style: italic;
      font-size: 11pt;
      margin: 0 0 22px 0;
    }
    .qna__turn {
      margin: 0 0 14px 0;
      padding-left: 14px;
      border-left: 2px solid #E3D9C6;
    }
    .qna__turn--planner {
      border-left-color: #7A6F64;
    }
    .qna__turn--user {
      border-left-color: #6A9A7A;
    }
    .qna__author {
      font-family: "Helvetica Neue", Arial, sans-serif;
      font-size: 9pt;
      letter-spacing: 0.15em;
      text-transform: uppercase;
      color: #7A6F64;
      margin: 0 0 4px 0;
    }
    .qna__body {
      font-size: 11.5pt;
      color: #2B2520;
      margin: 0;
    }
  `;

  // --- cover page -------------------------------------------------------
  const topicCount = input.topics.length;
  const coverSummary = topicCount === 0
    ? "This project has no topics yet."
    : `${topicCount} topic${topicCount === 1 ? "" : "s"} — captured below.`;

  const warning = input.hasFullContent
    ? ""
    : `<div class="cover__warning">Decisions and Q&amp;A thread content were not available at export time. Only topic metadata is included below.</div>`;

  const cover = `
    <section class="cover">
      <p class="cover__eyebrow">Inspira</p>
      <h1 class="cover__title">${escapeHtml(title)}</h1>
      <p class="cover__date">${escapeHtml(dateLine)}</p>
      <p class="cover__summary">${escapeHtml(coverSummary)}</p>
      ${warning}
    </section>
  `;

  // --- per-topic sections ----------------------------------------------
  const orderedTopics = [...input.topics].sort(
    (a, b) => a.order_index - b.order_index,
  );

  const topicSections = orderedTopics
    .map((topic) => renderTopicSection(topic, input))
    .join("\n");

  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(title)}</title>
  <style>${styles}</style>
</head>
<body>
  ${cover}
  <div class="doc">
    ${topicSections}
  </div>
</body>
</html>`;
}

function renderTopicSection(topic: Topic, input: ProjectExportInput): string {
  const decisions = input.decisionsByTopicId?.get(topic.topic_id) ?? [];
  const turns = input.turnsByTopicId?.get(topic.topic_id) ?? [];

  const decisionsHtml =
    decisions.length === 0
      ? `<p class="decisions__empty">No decisions captured for this topic.</p>`
      : `<ul class="decisions">${decisions
          .map(
            (d) => `
            <li class="decisions__item">
              <p class="decisions__statement">${escapeHtml(d.statement)}</p>
              ${
                d.rationale && d.rationale.trim()
                  ? `<p class="decisions__rationale">${escapeHtml(d.rationale.trim())}</p>`
                  : ""
              }
            </li>`,
          )
          .join("")}</ul>`;

  // Compact Q&A summary: we include planner questions and user answers,
  // but drop suggested-response chips and other scaffolding. That keeps
  // the PDF readable without flooding it with UI ephemera.
  const orderedTurns = [...turns].sort(
    (a, b) => a.order_index - b.order_index,
  );

  const qnaHtml =
    orderedTurns.length === 0
      ? `<p class="qna__empty">No conversation on this topic yet.</p>`
      : orderedTurns
          .map(
            (t) => `
            <div class="qna__turn qna__turn--${t.role}">
              <p class="qna__author">${t.role === "planner" ? "Planner" : "You"}</p>
              <p class="qna__body">${escapeHtml(t.body)}</p>
            </div>`,
          )
          .join("");

  return `
    <section class="topic">
      <p class="topic__eyebrow">Topic</p>
      <h2 class="topic__title">
        <span class="topic__icon">${escapeHtml(iconGlyph(topic.icon))}</span>
        ${escapeHtml(topic.title)}
        <span class="topic__status">${escapeHtml(statusLabel(topic.status))}</span>
      </h2>
      <h3 class="section__heading">Decisions</h3>
      ${decisionsHtml}
      <h3 class="section__heading">Conversation</h3>
      ${qnaHtml}
    </section>
  `;
}

// -----------------------------------------------------------------------
// Project-as-Markdown
// -----------------------------------------------------------------------

/**
 * Render the entire project as a single Markdown document. One "## Topic"
 * section per topic, each with decisions, the Q&A thread, and the topic's
 * "why this topic" pulled from `topic.metadata.why_this_topic` if present.
 *
 * A trailing "## Relationships" section lists every dotted connection as
 * `**A** -> **B**: label` (or "-" when the relationship has no label).
 *
 * The function is pure and deterministic: no `Date.now()`, no DOM. The
 * caller provides decisions-by-topic and (optionally) turns-by-topic maps
 * so this file doesn't fan out to the network.
 */
export function projectToMarkdown(
  projectTitle: string,
  topics: Topic[],
  relationships: Relationship[],
  decisionsByTopicId: Map<string, Decision[]>,
  turnsByTopicId?: Map<string, QnaTurn[]>,
): string {
  const parts: string[] = [];

  const cleanTitle = projectTitle.trim() || "Untitled project";
  // Per spec: keep the H1 project title unescaped — it's the document's
  // primary heading and we trust the in-prose H1 case. User-supplied prose
  // elsewhere (topic titles, decisions, rationales, turn bodies, labels)
  // is escaped below.
  parts.push(`# ${cleanTitle}`);
  parts.push("");

  // Cover line: simple human summary of what's inside. We compute the
  // decision count up front (one linear pass, not quadratic).
  const orderedTopics = [...topics].sort(
    (a, b) => a.order_index - b.order_index,
  );
  let decisionCount = 0;
  for (const t of orderedTopics) {
    decisionCount += decisionsByTopicId.get(t.topic_id)?.length ?? 0;
  }
  const topicLabel = orderedTopics.length === 1 ? "topic" : "topics";
  const decisionLabel = decisionCount === 1 ? "decision" : "decisions";
  // We intentionally omit the export date here — the caller can prepend
  // it if desired; projectToMarkdown is date-agnostic for determinism.
  parts.push(
    `_${orderedTopics.length} ${topicLabel} \u00B7 ${decisionCount} ${decisionLabel}_`,
  );
  parts.push("");

  for (const topic of orderedTopics) {
    const glyph = iconGlyph(topic.icon);
    // H2 header line stays a valid H2 — we escape the title TEXT but not
    // the leading `## `. Escaping the whole line (`\#\# ...`) would
    // destroy the heading.
    parts.push(`## ${escapeMarkdown(topic.title)} ${glyph}`.trimEnd());
    parts.push("");

    const why =
      typeof topic.metadata?.why_this_topic === "string"
        ? (topic.metadata.why_this_topic as string).trim()
        : "";
    if (why) {
      parts.push(`_${escapeMarkdown(why)}_`);
      parts.push("");
    }

    const decisions = decisionsByTopicId.get(topic.topic_id) ?? [];
    parts.push("### Decisions");
    if (decisions.length === 0) {
      parts.push("");
      parts.push("_No decisions captured yet._");
    } else {
      for (const d of decisions) {
        parts.push(`- ${escapeMarkdown(d.statement)}`);
        if (d.rationale && d.rationale.trim()) {
          parts.push(`  _${escapeMarkdown(d.rationale.trim())}_`);
        }
      }
    }
    parts.push("");

    const turns = turnsByTopicId?.get(topic.topic_id);
    if (turns && turns.length > 0) {
      parts.push("### Q&A");
      const ordered = [...turns].sort((a, b) => a.order_index - b.order_index);
      for (const t of ordered) {
        parts.push("");
        if (t.role === "planner") {
          parts.push(`**Planner:** ${escapeMarkdown(t.body)}`);
          if (t.why_this_matters && t.why_this_matters.trim()) {
            parts.push(`> _${escapeMarkdown(t.why_this_matters.trim())}_`);
          }
        } else {
          parts.push(`**You:** ${escapeMarkdown(t.body)}`);
        }
      }
      parts.push("");
    }

    parts.push("---");
    parts.push("");
  }

  // Relationship lookup by topic id -> title, built once up front.
  if (relationships.length > 0) {
    const titleById = new Map<string, string>();
    for (const t of topics) {
      titleById.set(t.topic_id, t.title);
    }
    parts.push("## Relationships");
    parts.push("");
    for (const r of relationships) {
      const from = titleById.get(r.source_topic_id) ?? "?";
      const to = titleById.get(r.target_topic_id) ?? "?";
      const label = r.label && r.label.trim() ? r.label.trim() : "-";
      parts.push(
        `- **${escapeMarkdown(from)}** \u2192 **${escapeMarkdown(to)}**: ${escapeMarkdown(label)}`,
      );
    }
    parts.push("");
  }

  return parts.join("\n");
}

// -----------------------------------------------------------------------
// Project-as-JSON
// -----------------------------------------------------------------------

/**
 * Structured, version-stamped snapshot of the whole project. Intended as
 * the canonical "takeout" format — round-trip friendly for future import
 * and stable enough for users to diff.
 *
 * Format version bumps whenever the shape changes in a breaking way.
 */
const PROJECT_JSON_FORMAT_VERSION = 1;

type JsonProjectMeta = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

// Fields on Topic.metadata that we consider user-value and safe to export.
// Anything else is treated as internal state (e.g. transient UI flags,
// tracer IDs, planner scratch) and dropped from the JSON.
const TOPIC_METADATA_ALLOWLIST = new Set<string>(["why_this_topic"]);

function filterTopicMetadata(
  metadata: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!metadata) return undefined;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(metadata)) {
    if (TOPIC_METADATA_ALLOWLIST.has(key) && value !== undefined) {
      out[key] = value;
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

export function projectToJSON(
  project: { id: string; title: string; created_at: string; updated_at: string },
  topics: Topic[],
  relationships: Relationship[],
  decisions: Decision[],
  turnsByTopicId?: Map<string, QnaTurn[]>,
): string {
  const meta: JsonProjectMeta = {
    id: project.id,
    title: project.title,
    created_at: project.created_at,
    updated_at: project.updated_at,
  };

  // Produce a deterministic clone of each topic with the metadata scrubbed
  // to only user-facing fields.
  const jsonTopics = topics.map((t) => {
    const scrubbed = filterTopicMetadata(t.metadata);
    // We spread last so our `metadata` override replaces the source field.
    const { metadata: _drop, ...rest } = t;
    void _drop;
    return scrubbed ? { ...rest, metadata: scrubbed } : rest;
  });

  // turnsByTopicId -> plain record so JSON.stringify handles it natively.
  const turnsByTopic: Record<string, QnaTurn[]> = {};
  if (turnsByTopicId) {
    for (const [topicId, turns] of turnsByTopicId.entries()) {
      const ordered = [...turns].sort(
        (a, b) => a.order_index - b.order_index,
      );
      turnsByTopic[topicId] = ordered;
    }
  }

  const payload = {
    format_version: PROJECT_JSON_FORMAT_VERSION,
    generated_at: new Date(project.updated_at).toISOString(),
    project: meta,
    topics: jsonTopics,
    relationships,
    decisions,
    turns_by_topic: turnsByTopic,
  };

  return JSON.stringify(payload, null, 2);
}

// -----------------------------------------------------------------------
// Project-as-Shareable-HTML
// -----------------------------------------------------------------------

export type ShareableHtmlParams = {
  projectTitle: string;
  projectSubtitle?: string;
  topics: Topic[];
  relationships: Relationship[];
  decisionsByTopicId: Map<string, Decision[]>;
  turnsByTopicId?: Map<string, QnaTurn[]>;
  /** ISO date. Used verbatim inside the document — no Date.now() inlining. */
  generatedAt: string;
  /** Optional extra line in the footer, below the "Exported from Inspira" tag. */
  footerNote?: string;
};

/**
 * Render the whole project as ONE self-contained HTML document. No `<link>`
 * tags, no `<script>` tags, no external fonts or images — the file opens
 * anywhere, even offline. This is the "printable snapshot" shared with
 * someone who doesn't have an Inspira account.
 *
 * Aesthetic: warm editorial palette matching the in-app canvas, system
 * serif fallback (Georgia), centered 760px column. Print and small-viewport
 * stylesheets are inlined.
 */
export function projectToShareableHTML(params: ShareableHtmlParams): string {
  // Private escape helper — tucked into the function as required by the
  // spec so the shareable HTML pass is self-contained and can't be fed a
  // drifting module-level definition. All user content passes through it.
  const escapeHTML = (raw: string): string =>
    raw
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const {
    projectTitle,
    projectSubtitle,
    topics: rawTopics,
    relationships,
    decisionsByTopicId,
    turnsByTopicId,
    generatedAt,
    footerNote,
  } = params;

  // Belt-and-suspenders: strip ``private_notes`` from every topic before
  // any rendering happens. The shareable HTML body never references the
  // field today, but the Topic type carries it (see api.ts) and a future
  // refactor that serializes a topic wholesale — or a template tweak that
  // dumps metadata alongside the title — would leak the owner's private
  // note into a document they're handing out publicly. Stripping here
  // means a single typo can't undo the guarantee that private notes stay
  // private. The server-side ``/api/v2/shared/{token}`` route also drops
  // the field; this is the matching client-side pass for the export path.
  const topics: Topic[] = rawTopics.map((topic) => {
    const { private_notes: _privateNotes, ...rest } = topic;
    void _privateNotes;
    return rest as Topic;
  });

  // Topic and relationship rendering are O(N+M), not quadratic. We prebuild
  // an id -> title map once so relationship rendering is a single pass.
  const titleById = new Map<string, string>();
  for (const t of topics) {
    titleById.set(t.topic_id, t.title);
  }
  const orderedTopics = [...topics].sort(
    (a, b) => a.order_index - b.order_index,
  );

  const cleanTitle =
    projectTitle && projectTitle.trim()
      ? projectTitle.trim()
      : "Untitled project";

  const styles = `
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      font-family: Georgia, "Times New Roman", serif;
      background: #F5F0E6;
      color: #2B2520;
      line-height: 1.6;
      font-size: 16px;
      -webkit-font-smoothing: antialiased;
    }
    .page {
      max-width: 760px;
      margin: 0 auto;
      padding: 64px 40px 96px;
    }
    .hero { margin-bottom: 48px; }
    .hero__eyebrow {
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      font-size: 11px;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      color: #7A6F64;
      margin: 0 0 18px 0;
    }
    .hero__title {
      font-size: 48px;
      font-weight: 400;
      line-height: 1.1;
      margin: 0 0 16px 0;
      color: #2B2520;
      letter-spacing: -0.01em;
    }
    .hero__subtitle {
      font-size: 16px;
      font-style: italic;
      color: #7A6F64;
      margin: 0 0 16px 0;
      line-height: 1.5;
    }
    .hero__date {
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      font-size: 11px;
      color: #7A6F64;
      margin: 0;
      letter-spacing: 0.05em;
    }
    .card {
      background: #FBF7EE;
      border: 1px solid #E3D9C6;
      border-radius: 4px;
      box-shadow: 0 1px 2px rgba(43, 37, 32, 0.04),
                  0 2px 8px rgba(43, 37, 32, 0.04);
      padding: 32px;
      margin: 0 0 28px 0;
    }
    .card__title {
      font-size: 26px;
      font-weight: 400;
      margin: 0 0 14px 0;
      color: #2B2520;
      line-height: 1.25;
    }
    .card__icon {
      display: inline-block;
      margin-right: 10px;
      color: #7A6F64;
      font-size: 22px;
    }
    .card__why {
      font-size: 15px;
      font-style: italic;
      color: #4A413A;
      margin: 0 0 22px 0;
      line-height: 1.55;
    }
    .section-heading {
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      font-size: 10px;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      color: #7A6F64;
      margin: 22px 0 12px 0;
      font-weight: 400;
    }
    .decisions {
      list-style: none;
      padding: 0;
      margin: 0 0 6px 0;
    }
    .decisions__item {
      padding: 10px 0;
      border-bottom: 1px dotted #E3D9C6;
    }
    .decisions__item:last-child { border-bottom: none; }
    .decisions__statement {
      font-size: 15px;
      color: #2B2520;
      margin: 0;
    }
    .decisions__rationale {
      font-size: 14px;
      color: #4A413A;
      font-style: italic;
      margin: 4px 0 0 0;
    }
    .empty {
      color: #7A6F64;
      font-style: italic;
      font-size: 14px;
      margin: 0 0 12px 0;
    }
    .qna { margin: 4px 0 0 0; }
    .qna__turn {
      padding: 10px 14px;
      margin: 0 0 10px 0;
      border-left: 3px solid #E3D9C6;
      background: rgba(255, 255, 255, 0.35);
    }
    .qna__turn--planner {
      border-left-color: #6A8A6E;
    }
    .qna__turn--user {
      border-left-color: #B06A3B;
    }
    .qna__author {
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      font-size: 10px;
      letter-spacing: 0.15em;
      text-transform: uppercase;
      color: #7A6F64;
      margin: 0 0 4px 0;
    }
    .qna__body {
      font-size: 15px;
      color: #2B2520;
      margin: 0;
      line-height: 1.55;
    }
    .qna__why {
      font-size: 13px;
      color: #7A6F64;
      font-style: italic;
      margin: 6px 0 0 0;
      line-height: 1.5;
    }
    .connections {
      margin-top: 48px;
      padding-top: 24px;
      border-top: 1px solid #E3D9C6;
    }
    .connections__title {
      font-size: 18px;
      font-weight: 400;
      margin: 0 0 16px 0;
      color: #2B2520;
    }
    .connections__list {
      list-style: none;
      padding: 0;
      margin: 0;
    }
    .connections__item {
      padding: 8px 0;
      font-size: 14px;
      color: #4A413A;
      border-bottom: 1px dotted #E3D9C6;
    }
    .connections__item:last-child { border-bottom: none; }
    .connections__item strong { color: #2B2520; font-weight: 600; }
    .connections__label {
      color: #7A6F64;
      font-style: italic;
      margin-left: 4px;
    }
    .footer {
      margin-top: 64px;
      padding-top: 20px;
      border-top: 1px solid #E3D9C6;
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      font-size: 10px;
      color: #7A6F64;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      text-align: center;
    }
    .footer__note {
      display: block;
      font-family: Georgia, "Times New Roman", serif;
      font-style: italic;
      font-size: 12px;
      letter-spacing: 0;
      text-transform: none;
      margin-top: 8px;
      color: #7A6F64;
    }
    @media (max-width: 640px) {
      .page { padding: 32px 20px 64px; }
      .hero__title { font-size: 34px; }
      .card { padding: 20px; }
      .card__title { font-size: 22px; }
    }
    @media print {
      body { background: #FFFFFF; }
      .page { padding: 0; max-width: 100%; }
      .card {
        box-shadow: none;
        page-break-inside: avoid;
        background: #FBF7EE;
      }
      .footer { page-break-inside: avoid; }
      a[href]::after { content: ""; }
    }
  `;

  // --- hero -------------------------------------------------------------
  const subtitleHtml = projectSubtitle && projectSubtitle.trim()
    ? `<p class="hero__subtitle">${escapeHTML(projectSubtitle.trim())}</p>`
    : "";

  const heroHtml = `
    <header class="hero">
      <p class="hero__eyebrow">Inspira</p>
      <h1 class="hero__title">${escapeHTML(cleanTitle)}</h1>
      ${subtitleHtml}
      <p class="hero__date">${escapeHTML(generatedAt)}</p>
    </header>
  `;

  // --- topic cards ------------------------------------------------------
  const cardBlocks: string[] = [];
  for (const topic of orderedTopics) {
    const glyph = iconGlyph(topic.icon);
    const decisions = decisionsByTopicId.get(topic.topic_id) ?? [];
    const turns = turnsByTopicId?.get(topic.topic_id);

    const why =
      typeof topic.metadata?.why_this_topic === "string"
        ? (topic.metadata.why_this_topic as string).trim()
        : "";
    const whyHtml = why
      ? `<p class="card__why">${escapeHTML(why)}</p>`
      : "";

    const decisionsHtml =
      decisions.length === 0
        ? `<p class="empty">No decisions captured yet.</p>`
        : `<ul class="decisions">${decisions
            .map((d) => {
              const rationale =
                d.rationale && d.rationale.trim()
                  ? `<p class="decisions__rationale">${escapeHTML(d.rationale.trim())}</p>`
                  : "";
              return `<li class="decisions__item"><p class="decisions__statement">${escapeHTML(d.statement)}</p>${rationale}</li>`;
            })
            .join("")}</ul>`;

    let qnaHtml = "";
    if (turns && turns.length > 0) {
      const ordered = [...turns].sort(
        (a, b) => a.order_index - b.order_index,
      );
      const turnHtml = ordered
        .map((t) => {
          const whyMattersHtml =
            t.role === "planner" &&
            t.why_this_matters &&
            t.why_this_matters.trim()
              ? `<p class="qna__why">${escapeHTML(t.why_this_matters.trim())}</p>`
              : "";
          const author = t.role === "planner" ? "Planner" : "You";
          return `<div class="qna__turn qna__turn--${t.role}"><p class="qna__author">${escapeHTML(author)}</p><p class="qna__body">${escapeHTML(t.body)}</p>${whyMattersHtml}</div>`;
        })
        .join("");
      qnaHtml = `<h3 class="section-heading">Conversation</h3><div class="qna">${turnHtml}</div>`;
    }

    cardBlocks.push(`
      <section class="card">
        <h2 class="card__title"><span class="card__icon">${escapeHTML(glyph)}</span>${escapeHTML(topic.title)}</h2>
        ${whyHtml}
        <h3 class="section-heading">Decisions</h3>
        ${decisionsHtml}
        ${qnaHtml}
      </section>
    `);
  }
  const cardsHtml = cardBlocks.join("\n");

  // --- connections -------------------------------------------------------
  let connectionsHtml = "";
  if (relationships.length > 0) {
    const items = relationships
      .map((r) => {
        const from = titleById.get(r.source_topic_id) ?? "?";
        const to = titleById.get(r.target_topic_id) ?? "?";
        const label =
          r.label && r.label.trim()
            ? `<span class="connections__label">${escapeHTML(r.label.trim())}</span>`
            : "";
        return `<li class="connections__item"><strong>${escapeHTML(from)}</strong> \u2192 <strong>${escapeHTML(to)}</strong>${label ? `: ${label}` : ""}</li>`;
      })
      .join("");
    connectionsHtml = `
      <section class="connections">
        <h2 class="connections__title">Connections between topics</h2>
        <ul class="connections__list">${items}</ul>
      </section>
    `;
  }

  // --- footer -----------------------------------------------------------
  const footerNoteHtml = footerNote && footerNote.trim()
    ? `<span class="footer__note">${escapeHTML(footerNote.trim())}</span>`
    : "";
  const footerHtml = `
    <footer class="footer">
      Exported from Inspira \u00B7 ${escapeHTML(generatedAt)}
      ${footerNoteHtml}
    </footer>
  `;

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${escapeHTML(cleanTitle)}</title>
<style>${styles}</style>
</head>
<body>
<div class="page">
${heroHtml}
${cardsHtml}
${connectionsHtml}
${footerHtml}
</div>
</body>
</html>`;
}

// -----------------------------------------------------------------------
// Project-as-plain-text (.txt)
// -----------------------------------------------------------------------

/**
 * Render the entire project as clean, readable plain text — no Markdown
 * symbols. Suitable for pasting anywhere or opening in the most basic
 * text editor.
 *
 * Strips: # headings, *, _, `, and leading list markers (- / * / 1.).
 */
export function projectToPlainText(
  projectTitle: string,
  topics: Topic[],
  decisionsByTopicId: Map<string, Decision[]>,
  turnsByTopicId?: Map<string, QnaTurn[]>,
): string {
  function stripMarkdown(s: string): string {
    return s
      .replace(/^#{1,6}\s+/gm, "")   // headings
      .replace(/\*\*(.+?)\*\*/g, "$1") // bold
      .replace(/\*(.+?)\*/g, "$1")     // italic *
      .replace(/_(.+?)_/g, "$1")       // italic _
      .replace(/`{1,3}[^`]*`{1,3}/g, (m) => m.replace(/`/g, "")) // code
      .replace(/^[-*]\s+/gm, "")       // list markers
      .replace(/^\d+\.\s+/gm, "")      // ordered list markers
      .replace(/^>\s*/gm, "")          // blockquotes
      .trim();
  }

  const lines: string[] = [];
  const cleanTitle = projectTitle.trim() || "Untitled project";

  lines.push(cleanTitle.toUpperCase());
  lines.push("=".repeat(Math.min(cleanTitle.length, 60)));
  lines.push("");

  const orderedTopics = [...topics].sort((a, b) => a.order_index - b.order_index);

  for (const topic of orderedTopics) {
    lines.push(topic.title);
    lines.push("-".repeat(Math.min(topic.title.length, 40)));
    lines.push("");

    const decisions = decisionsByTopicId.get(topic.topic_id) ?? [];
    if (decisions.length > 0) {
      lines.push("Decisions");
      for (const d of decisions) {
        lines.push(`  * ${stripMarkdown(d.statement)}`);
        if (d.rationale && d.rationale.trim()) {
          lines.push(`    ${stripMarkdown(d.rationale.trim())}`);
        }
      }
      lines.push("");
    }

    const turns = turnsByTopicId?.get(topic.topic_id);
    if (turns && turns.length > 0) {
      const ordered = [...turns].sort((a, b) => a.order_index - b.order_index);
      lines.push("Conversation");
      for (const turn of ordered) {
        const author = turn.role === "planner" ? "Planner" : "You";
        lines.push(`  ${author}: ${stripMarkdown(turn.body)}`);
      }
      lines.push("");
    }

    lines.push("");
  }

  return lines.join("\n");
}

/**
 * Small non-cryptographic string hash. Used to give the "empty after slug"
 * fallback a stable, deterministic suffix so that two projects with unrelated
 * titles don't both collide on the literal string "inspira-project".
 * (cyrb53 variant, unsigned 32-bit output.)
 */
function simpleStringHash(input: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < input.length; i++) {
    const ch = input.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507);
  h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507);
  h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  const hashed = 4294967296 * (2097151 & h2) + (h1 >>> 0);
  return hashed.toString(16);
}

/**
 * Derive a filesystem-friendly filename base from a project title.
 *
 * Behavior:
 *   - lowercase
 *   - whitespace -> "-"
 *   - keep letters + digits from ANY script (Unicode \p{L}\p{N})
 *   - drop everything else
 *   - collapse consecutive "-" runs
 *   - if nothing survives, fall back to `inspira-project-{6-char-hash}` so
 *     two CJK/emoji-only titles don't both collide on "inspira-project".
 */
export function slugifyForFilename(raw: string | null | undefined): string {
  if (!raw) return "inspira-project";
  const trimmed = raw.trim();
  if (!trimmed) return "inspira-project";
  const slug = trimmed
    .toLowerCase()
    .replace(/\s+/g, "-")
    // Keep letters + digits from any script, plus the "-" we just put in for
    // whitespace. `u` flag enables the Unicode property escapes.
    .replace(/[^\p{L}\p{N}\-]/gu, "")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");
  if (slug) return slug;
  // Nothing survived — e.g. an emoji-only title, or a string made entirely
  // of punctuation. Disambiguate the filename with a short hash of the
  // original input so two unrelated empties don't silently overwrite each
  // other in the user's Downloads folder.
  const suffix = simpleStringHash(trimmed).slice(0, 6);
  return `inspira-project-${suffix}`;
}

// =======================================================================
// Canvas export: four formats (PDF handled separately via html2pdf.js)
// =======================================================================
//
// The three functions below — exportToMarkdown / exportToJson / exportToCsv —
// each build a full-canvas artefact (project + topics + relationships +
// decisions + Q&A turns) and, as a side effect, trigger a browser download.
// They also return the serialized content so callers and tests can inspect
// the output without intercepting the download.
//
// Contract
// --------
//   * Downloads go through `triggerDownload(blob, filename)` which creates a
//     transient `<a>` tag, clicks it, then revokes the object URL. Tests
//     mock `HTMLAnchorElement.prototype.click` (or stub
//     `document.createElement` outright) so nothing actually hits disk.
//   * Filenames follow `{slug}-{yyyy-mm-dd}.{ext}` using `slugifyForFilename`.
//   * The CSV export skips decisions and Q&A turns — those have rich text
//     that doesn't flatten cleanly into columns. If you need them, grab the
//     JSON export. See the per-CSV comments for the column schemas.
//
// CSV caveat (read before extending the shape):
//   Decisions and turns are intentionally NOT included in the CSV zip. Both
//   contain free-form prose (rationale, body, why_this_matters) that would
//   balloon cell widths, and decisions in particular depend on topic context
//   that's hard to express in a flat table. Users who need those should use
//   JSON or Markdown.

// -----------------------------------------------------------------------
// Shared helpers
// -----------------------------------------------------------------------

/**
 * Format a date as ISO yyyy-mm-dd (UTC). Used in filenames so they sort
 * lexicographically and don't drift across locales.
 */
function isoDateStamp(d: Date): string {
  const y = d.getUTCFullYear().toString().padStart(4, "0");
  const m = (d.getUTCMonth() + 1).toString().padStart(2, "0");
  const day = d.getUTCDate().toString().padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * Create a transient anchor, point it at a blob URL, click it, and clean up.
 * Throws only if the document isn't available (e.g. SSR) — the caller gets
 * to decide whether to surface the error.
 *
 * Tests mock `HTMLAnchorElement.prototype.click` so the function runs end
 * to end without hitting the browser's download machinery.
 */
function triggerDownload(blob: Blob, filename: string): void {
  if (typeof document === "undefined") {
    throw new Error("triggerDownload requires a DOM");
  }
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    // Some browsers require the anchor to be in the document for programmatic
    // click to actually trigger a download; we append + remove synchronously.
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } finally {
    // Revoke after a microtask so the browser has a chance to start the
    // download before the URL dies. `setTimeout(fn, 0)` is the conservative
    // cross-browser choice over `queueMicrotask` here.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

// -----------------------------------------------------------------------
// 1. Markdown export
// -----------------------------------------------------------------------

/**
 * Render the whole canvas as a shareable Markdown document and trigger a
 * `.md` download. Returns the markdown string so tests / callers can
 * inspect it without touching the DOM.
 *
 * Layout follows the feature spec:
 *
 *   # <title>
 *   *Last updated: <iso-date>*
 *
 *   ## Topics
 *   ### <topic.title> <icon>
 *   <why_this_topic>
 *
 *   **Decisions:**
 *   - <decision.text>
 *
 *   **Discussion:**
 *   > <question>
 *   >
 *   > <answer>
 *
 *   ## Relationships
 *   - **<source.title>** -> **<target.title>** -- <relationship.label>
 *
 *   ---
 *   *Exported from Inspira - tryinspira.com*
 */
export function exportToMarkdown(
  project: V2Project,
  topics: Topic[],
  relationships: Relationship[],
  decisions: Decision[] | undefined,
  turns: QnaTurn[] | undefined,
): string {
  const parts: string[] = [];
  const title = project.title?.trim() || "Untitled project";

  // Per spec: H1 project title stays unescaped as the document's primary
  // heading. All other interpolated user-supplied content below passes
  // through escapeMarkdown.
  parts.push(`# ${title}`);
  parts.push("");

  // Spec asks for ISO date of `updated_at`. We parse and re-serialize to
  // yyyy-mm-dd rather than dumping the raw timestamp so the line reads
  // cleanly as a date.
  const updatedStamp = (() => {
    const raw = project.updated_at;
    if (!raw) return isoDateStamp(new Date());
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return raw;
    return isoDateStamp(d);
  })();
  parts.push(`*Last updated: ${updatedStamp}*`);
  parts.push("");

  parts.push("## Topics");
  parts.push("");

  // Bucket decisions and turns by topic_id once up front (O(N+M)).
  const decisionsByTopic = new Map<string, Decision[]>();
  for (const d of decisions ?? []) {
    const arr = decisionsByTopic.get(d.topic_id) ?? [];
    arr.push(d);
    decisionsByTopic.set(d.topic_id, arr);
  }
  const turnsByTopic = new Map<string, QnaTurn[]>();
  for (const turn of turns ?? []) {
    const arr = turnsByTopic.get(turn.topic_id) ?? [];
    arr.push(turn);
    turnsByTopic.set(turn.topic_id, arr);
  }

  const orderedTopics = [...topics].sort(
    (a, b) => a.order_index - b.order_index,
  );

  for (const topic of orderedTopics) {
    const glyph = iconGlyph(topic.icon);
    // H3 header line: escape the title TEXT but preserve the `### `
    // prefix so the heading renders correctly.
    parts.push(`### ${escapeMarkdown(topic.title)} ${glyph}`.trimEnd());

    const why =
      typeof topic.metadata?.why_this_topic === "string"
        ? (topic.metadata.why_this_topic as string).trim()
        : "";
    if (why) {
      parts.push(escapeMarkdown(why));
    }
    parts.push("");

    const topicDecisions = decisionsByTopic.get(topic.topic_id) ?? [];
    if (topicDecisions.length > 0) {
      parts.push("**Decisions:**");
      for (const d of topicDecisions) {
        parts.push(`- ${escapeMarkdown(d.statement)}`);
      }
      parts.push("");
    }

    const topicTurns = turnsByTopic.get(topic.topic_id) ?? [];
    if (topicTurns.length > 0) {
      parts.push("**Discussion:**");
      const ordered = [...topicTurns].sort(
        (a, b) => a.order_index - b.order_index,
      );
      // We pair adjacent planner -> user turns into question/answer blocks.
      // Unpaired planner questions still render (the answer line becomes a
      // placeholder), and unpaired user turns render solo.
      for (let i = 0; i < ordered.length; i++) {
        const turn = ordered[i];
        if (turn.role === "planner") {
          const next = ordered[i + 1];
          parts.push(`> ${escapeMarkdown(turn.body)}`);
          parts.push(">");
          if (next && next.role === "user") {
            parts.push(`> ${escapeMarkdown(next.body)}`);
            i++;
          } else {
            parts.push("> _(no answer yet)_");
          }
        } else {
          parts.push(`> ${escapeMarkdown(turn.body)}`);
        }
        parts.push("");
      }
    }
  }

  // Relationships section. Build topic_id -> title once so the loop stays
  // O(M) rather than O(M*N).
  if (relationships.length > 0) {
    const titleById = new Map<string, string>();
    for (const t of topics) {
      titleById.set(t.topic_id, t.title);
    }
    parts.push("## Relationships");
    parts.push("");
    for (const r of relationships) {
      const from = titleById.get(r.source_topic_id) ?? "?";
      const to = titleById.get(r.target_topic_id) ?? "?";
      const label = r.label && r.label.trim() ? r.label.trim() : "related";
      parts.push(
        `- **${escapeMarkdown(from)}** \u2192 **${escapeMarkdown(to)}** \u2014 ${escapeMarkdown(label)}`,
      );
    }
    parts.push("");
  }

  parts.push("---");
  parts.push("");
  parts.push("*Exported from Inspira \u00B7 tryinspira.com*");
  parts.push("");

  const markdown = parts.join("\n");

  const slug = slugifyForFilename(project.title);
  const filename = `${slug}-${isoDateStamp(new Date())}.md`;
  triggerDownload(new Blob([markdown], { type: "text/markdown" }), filename);

  return markdown;
}

// -----------------------------------------------------------------------
// 2. JSON export
// -----------------------------------------------------------------------

/**
 * Render the whole canvas as a structured JSON document and trigger a
 * `.json` download. Pretty-printed with 2-space indent. Returns the JSON
 * string (not the parsed object) for easy inspection in tests.
 *
 * The `schema` field is a version tag. Future importers can branch on it.
 *
 * TODO: write a matching importer once we have user demand. The idea is
 * that an Inspira user drops the .json back into a new canvas and we
 * recreate the full graph — same project id is not reused, but titles,
 * positions, relationships, decisions, and Q&A turns all round-trip.
 */
export function exportToJson(
  project: V2Project,
  topics: Topic[],
  relationships: Relationship[],
  decisions: Decision[] | undefined,
  turns: QnaTurn[] | undefined,
): string {
  const payload = {
    schema: "inspira.canvas.v1",
    exported_at: new Date().toISOString(),
    project,
    topics,
    relationships,
    decisions: decisions ?? [],
    turns: turns ?? [],
  };

  const json = JSON.stringify(payload, null, 2);

  const slug = slugifyForFilename(project.title);
  const filename = `${slug}-${isoDateStamp(new Date())}.json`;
  triggerDownload(new Blob([json], { type: "application/json" }), filename);

  return json;
}

// -----------------------------------------------------------------------
// 3. CSV export (zip of two CSVs via jszip)
// -----------------------------------------------------------------------

/**
 * Quote a cell per RFC 4180: wrap in double quotes if it contains a comma,
 * double quote, CR, or LF; double up any embedded quotes. null/undefined
 * become the empty string so downstream spreadsheets get a blank cell
 * rather than the literal word "null".
 */
function csvCell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const s = typeof value === "string" ? value : String(value);
  if (/[",\r\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function csvRow(cells: Array<string | number | null | undefined>): string {
  return cells.map(csvCell).join(",");
}

/**
 * Build a zip containing two CSVs — `topics.csv` and `relationships.csv` —
 * and trigger a `.zip` download. Returns the Blob so tests can inspect
 * the zip contents without hitting the download path.
 *
 * We resolve jszip via dynamic import so it stays out of the main bundle
 * until the user actually picks CSV.
 */
export async function exportToCsv(
  project: V2Project,
  topics: Topic[],
  relationships: Relationship[],
  // Included in the signature for parity with the other two exports and
  // future-proofing. Current implementation intentionally skips them; the
  // file header comment explains why.
  _decisions?: Decision[] | undefined,
  _turns?: QnaTurn[] | undefined,
): Promise<Blob> {
  // Dynamic import so jszip is code-split and not pulled into the main
  // bundle for users who never export CSV.
  const { default: JSZip } = await import("jszip");
  const zip = new JSZip();

  // --- topics.csv ---
  //
  // Columns: topic_id, title, icon, status, origin, why_this_topic,
  // position_x, position_y, created_at
  //
  // why_this_topic is pulled from topic.metadata; positions are integers
  // from the canvas layout.
  const topicLines: string[] = [];
  topicLines.push(
    csvRow([
      "topic_id",
      "title",
      "icon",
      "status",
      "origin",
      "why_this_topic",
      "position_x",
      "position_y",
      "created_at",
    ]),
  );
  for (const t of topics) {
    const why =
      typeof t.metadata?.why_this_topic === "string"
        ? (t.metadata.why_this_topic as string)
        : "";
    topicLines.push(
      csvRow([
        t.topic_id,
        t.title,
        t.icon,
        t.status,
        t.origin,
        why,
        t.position_x,
        t.position_y,
        t.created_at,
      ]),
    );
  }
  zip.file("topics.csv", topicLines.join("\r\n") + "\r\n");

  // --- relationships.csv ---
  //
  // Columns: relationship_id, source_title, target_title, label, origin,
  // created_at
  //
  // We resolve source/target titles from the topics map. If a relationship
  // references a topic that isn't in the export (shouldn't happen, but we
  // don't want the CSV to crash), we write the id as a fallback so the
  // row is still useful.
  const titleById = new Map<string, string>();
  for (const t of topics) titleById.set(t.topic_id, t.title);

  const relLines: string[] = [];
  relLines.push(
    csvRow([
      "relationship_id",
      "source_title",
      "target_title",
      "label",
      "origin",
      "created_at",
    ]),
  );
  for (const r of relationships) {
    relLines.push(
      csvRow([
        r.relationship_id,
        titleById.get(r.source_topic_id) ?? r.source_topic_id,
        titleById.get(r.target_topic_id) ?? r.target_topic_id,
        r.label ?? "",
        r.origin,
        r.created_at,
      ]),
    );
  }
  zip.file("relationships.csv", relLines.join("\r\n") + "\r\n");

  const blob = await zip.generateAsync({ type: "blob" });

  const slug = slugifyForFilename(project.title);
  const filename = `${slug}-${isoDateStamp(new Date())}.zip`;
  triggerDownload(blob, filename);

  return blob;
}
