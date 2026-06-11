// @vitest-environment happy-dom
//
// Tests for the four canvas export helpers in export.ts.
//
// Each test shims out the download side effect by stubbing the anchor
// element's `click` so nothing hits the user's disk. We inspect the
// function's return value (string / Blob) for content correctness.
//
// Environment: happy-dom gives us a lightweight DOM (HTMLAnchorElement,
// document, URL.createObjectURL/revokeObjectURL) so triggerDownload's
// anchor-click trick runs end to end without the real browser.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  exportToCsv,
  exportToJson,
  exportToMarkdown,
  projectToHtml,
  projectToMarkdown,
  projectToPlainText,
  projectToShareableHTML,
  slugifyForFilename,
  topicToMarkdown,
} from "./export";
import type {
  Decision,
  QnaTurn,
  Relationship,
  Topic,
  V2Project,
} from "./api";

// ---------------------------------------------------------------------
// Fixtures — one project with two topics, one relationship, two decisions,
// a short Q&A thread. Enough to exercise every code path without being
// noisy.
// ---------------------------------------------------------------------

function makeProject(): V2Project {
  return {
    project_id: "proj-1",
    user_id: "user-1",
    title: "Kitchen Renovation",
    created_at: "2026-04-01T12:00:00Z",
    updated_at: "2026-04-20T09:00:00Z",
    metadata: {},
  };
}

function makeTopics(): Topic[] {
  return [
    {
      topic_id: "topic-a",
      project_id: "proj-1",
      title: "Budget",
      icon: "lightbulb",
      position_x: 120,
      position_y: 240,
      status: "in_progress",
      order_index: 0,
      origin: "planner_initial",
      metadata: { why_this_topic: "We need to know what we can afford." },
      created_at: "2026-04-01T12:05:00Z",
      updated_at: "2026-04-02T10:00:00Z",
    },
    {
      topic_id: "topic-b",
      project_id: "proj-1",
      title: "Cabinetry",
      icon: "book",
      position_x: 420,
      position_y: 240,
      status: "empty",
      order_index: 1,
      origin: "planner_proposed",
      metadata: {},
      created_at: "2026-04-01T12:06:00Z",
      updated_at: "2026-04-01T12:06:00Z",
    },
  ];
}

function makeRelationships(): Relationship[] {
  return [
    {
      relationship_id: "rel-1",
      project_id: "proj-1",
      source_topic_id: "topic-a",
      target_topic_id: "topic-b",
      label: "constrains",
      origin: "planner_inferred",
      strength: null,
      created_at: "2026-04-01T12:07:00Z",
    },
  ];
}

function makeDecisions(): Decision[] {
  return [
    {
      decision_id: "dec-1",
      topic_id: "topic-a",
      project_id: "proj-1",
      statement: "Cap the renovation budget at $45,000.",
      rationale: "Above that we'd need to refinance.",
      status: "confirmed",
      source_turn_id: null,
      proposed_by: "user",
      confirmed_by_user_id: "user-1",
      created_at: "2026-04-02T09:00:00Z",
      updated_at: "2026-04-02T09:00:00Z",
      retracted_at: null,
    },
    {
      decision_id: "dec-2",
      topic_id: "topic-b",
      project_id: "proj-1",
      statement: "Paint existing cabinets rather than replace.",
      rationale: null,
      status: "proposed",
      source_turn_id: null,
      proposed_by: "user",
      confirmed_by_user_id: null,
      created_at: "2026-04-05T14:00:00Z",
      updated_at: "2026-04-05T14:00:00Z",
      retracted_at: null,
    },
  ];
}

function makeTurns(): QnaTurn[] {
  return [
    {
      turn_id: "turn-1",
      topic_id: "topic-a",
      project_id: "proj-1",
      role: "planner",
      order_index: 0,
      body: "What is the absolute ceiling you can spend?",
      why_this_matters: "Anchors every downstream trade-off.",
      action: "ask",
      suggested_responses: [],
      status: "answered",
      created_at: "2026-04-01T13:00:00Z",
    },
    {
      turn_id: "turn-2",
      topic_id: "topic-a",
      project_id: "proj-1",
      role: "user",
      order_index: 1,
      body: "Forty-five thousand, no more.",
      why_this_matters: null,
      action: null,
      suggested_responses: [],
      status: "answered",
      created_at: "2026-04-01T13:01:00Z",
    },
  ];
}

// ---------------------------------------------------------------------
// Download-mocking. Before each test we stub the anchor element's click
// (and URL.createObjectURL / revokeObjectURL) so triggerDownload runs
// cleanly without jsdom spinning up a real download.
// ---------------------------------------------------------------------

let clickSpy: ReturnType<typeof vi.fn>;
let createObjectURLSpy: ReturnType<typeof vi.spyOn>;
let revokeObjectURLSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  clickSpy = vi.fn();
  // Patch the prototype so every anchor created during the test inherits
  // our no-op click.
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clickSpy(this.download, this.href);
  });

  // Stub the URL helpers; jsdom has them but they return blob: URLs we
  // never want to actually hit.
  createObjectURLSpy = vi
    .spyOn(URL, "createObjectURL")
    .mockImplementation(() => "blob:mock-url");
  revokeObjectURLSpy = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------
// exportToMarkdown
// ---------------------------------------------------------------------

describe("exportToMarkdown", () => {
  it("includes project title, topic titles, and relationship edges", async () => {
    const project = makeProject();
    const topics = makeTopics();
    const relationships = makeRelationships();
    const decisions = makeDecisions();
    const turns = makeTurns();

    const md = exportToMarkdown(project, topics, relationships, decisions, turns);

    // Project title in the H1 line.
    expect(md).toContain("# Kitchen Renovation");

    // Both topic titles appear as H3s.
    expect(md).toContain("Budget");
    expect(md).toContain("Cabinetry");

    // Relationship edge renders with both endpoint titles and the label.
    // The exact glyphs are arrow + em-dash but we assert on the labels
    // so the test isn't brittle to glyph changes.
    expect(md).toMatch(/Budget.*Cabinetry/);
    expect(md).toContain("constrains");

    // Footer tag is present.
    expect(md).toContain("Exported from Inspira");

    // Download was triggered with a .md filename.
    expect(clickSpy).toHaveBeenCalledTimes(1);
    const [filename] = clickSpy.mock.calls[0];
    expect(filename).toMatch(/^kitchen-renovation-\d{4}-\d{2}-\d{2}\.md$/);

    expect(createObjectURLSpy).toHaveBeenCalledTimes(1);
    // revokeObjectURL fires on the next macrotask so we don't race the
    // browser's download initiation; wait a tick before checking.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(revokeObjectURLSpy).toHaveBeenCalledTimes(1);
  });

  it("handles missing decisions / turns without crashing", () => {
    const project = makeProject();
    const md = exportToMarkdown(
      project,
      makeTopics(),
      makeRelationships(),
      undefined,
      undefined,
    );
    expect(md).toContain("# Kitchen Renovation");
    // No decisions section when there are no decisions.
    expect(md).not.toContain("**Decisions:**");
  });
});

// ---------------------------------------------------------------------
// exportToJson
// ---------------------------------------------------------------------

describe("exportToJson", () => {
  it("produces valid JSON with the schema field", () => {
    const project = makeProject();
    const topics = makeTopics();
    const relationships = makeRelationships();
    const decisions = makeDecisions();
    const turns = makeTurns();

    const json = exportToJson(project, topics, relationships, decisions, turns);

    // Parses as JSON.
    const parsed = JSON.parse(json);
    expect(parsed).toBeTypeOf("object");

    // Has the version tag.
    expect(parsed.schema).toBe("inspira.canvas.v1");

    // Project, topics, relationships all round-trip.
    expect(parsed.project.title).toBe("Kitchen Renovation");
    expect(parsed.topics).toHaveLength(2);
    expect(parsed.relationships).toHaveLength(1);
    expect(parsed.decisions).toHaveLength(2);
    expect(parsed.turns).toHaveLength(2);

    // exported_at is an ISO timestamp.
    expect(parsed.exported_at).toMatch(/\d{4}-\d{2}-\d{2}T/);

    // Download was triggered with a .json filename.
    expect(clickSpy).toHaveBeenCalledTimes(1);
    const [filename] = clickSpy.mock.calls[0];
    expect(filename).toMatch(/^kitchen-renovation-\d{4}-\d{2}-\d{2}\.json$/);
  });

  it("falls back to empty arrays when decisions / turns are absent", () => {
    const project = makeProject();
    const json = exportToJson(
      project,
      makeTopics(),
      makeRelationships(),
      undefined,
      undefined,
    );
    const parsed = JSON.parse(json);
    expect(parsed.decisions).toEqual([]);
    expect(parsed.turns).toEqual([]);
  });
});

// ---------------------------------------------------------------------
// exportToCsv
// ---------------------------------------------------------------------

describe("exportToCsv", () => {
  it("produces a zip with topics.csv and relationships.csv, each with header + data rows", async () => {
    const project = makeProject();
    const topics = makeTopics();
    const relationships = makeRelationships();

    const blob = await exportToCsv(project, topics, relationships);
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.size).toBeGreaterThan(0);

    // Unpack with jszip to verify the two CSVs are present with the
    // expected shape.
    const JSZip = (await import("jszip")).default;
    const zip = await JSZip.loadAsync(blob);

    const topicsFile = zip.file("topics.csv");
    const relsFile = zip.file("relationships.csv");
    expect(topicsFile).not.toBeNull();
    expect(relsFile).not.toBeNull();

    const topicsText = await topicsFile!.async("string");
    const topicLines = topicsText.trim().split(/\r?\n/);
    // Header + one row per topic.
    expect(topicLines.length).toBe(topics.length + 1);
    // Header contains the expected columns.
    expect(topicLines[0]).toContain("topic_id");
    expect(topicLines[0]).toContain("title");
    expect(topicLines[0]).toContain("position_x");
    // A data row contains one of the topic titles.
    expect(topicsText).toContain("Budget");
    expect(topicsText).toContain("Cabinetry");

    const relsText = await relsFile!.async("string");
    const relLines = relsText.trim().split(/\r?\n/);
    expect(relLines.length).toBe(relationships.length + 1);
    expect(relLines[0]).toContain("relationship_id");
    expect(relLines[0]).toContain("source_title");
    expect(relsText).toContain("constrains");

    // Download was triggered with a .zip filename.
    expect(clickSpy).toHaveBeenCalledTimes(1);
    const [filename] = clickSpy.mock.calls[0];
    expect(filename).toMatch(/^kitchen-renovation-\d{4}-\d{2}-\d{2}\.zip$/);
  });

  it("escapes cells that contain commas and quotes", async () => {
    const project = makeProject();
    const topics: Topic[] = [
      {
        ...makeTopics()[0],
        title: 'Budget, phase "one"',
      },
    ];
    const blob = await exportToCsv(project, topics, []);
    const JSZip = (await import("jszip")).default;
    const zip = await JSZip.loadAsync(blob);
    const csv = await zip.file("topics.csv")!.async("string");

    // RFC 4180: cells with commas or quotes get wrapped; internal quotes
    // are doubled.
    expect(csv).toContain('"Budget, phase ""one"""');
  });
});

// ---------------------------------------------------------------------
// slugifyForFilename — tiny smoke test since the export helpers all
// funnel through it for the filename base.
// ---------------------------------------------------------------------

describe("slugifyForFilename", () => {
  it("lowercases, hyphenates, and strips non-alnum", () => {
    expect(slugifyForFilename("Kitchen Renovation")).toBe("kitchen-renovation");
    expect(slugifyForFilename("  My Project!!! ")).toBe("my-project");
    expect(slugifyForFilename("")).toBe("inspira-project");
    expect(slugifyForFilename(null)).toBe("inspira-project");
    expect(slugifyForFilename(undefined)).toBe("inspira-project");
  });

  it("preserves pure-CJK titles via Unicode letter class", () => {
    // Japanese: Kanji + Hiragana for "My Project". Before the Unicode fix the
    // regex stripped every one of these and fell through to "inspira-project".
    const slug = slugifyForFilename("私のプロジェクト");
    expect(slug).toBe("私のプロジェクト");
  });

  it("keeps mixed ASCII + CJK titles", () => {
    const slug = slugifyForFilename("Kitchen キッチン Renovation");
    expect(slug).toBe("kitchen-キッチン-renovation");
  });

  it("falls back to inspira-project-{hash} for emoji-only titles", () => {
    // A pure-emoji title strips to nothing under \p{L}\p{N}. We disambiguate
    // via a short stable hash so two different emoji titles don't collide on
    // the literal "inspira-project" filename.
    const slugA = slugifyForFilename("🎉🎊🥳");
    const slugB = slugifyForFilename("😀😃😄");
    expect(slugA).toMatch(/^inspira-project-[a-f0-9]{1,6}$/);
    expect(slugB).toMatch(/^inspira-project-[a-f0-9]{1,6}$/);
    expect(slugA).not.toBe(slugB);
    // And the hash is deterministic — same input, same suffix.
    expect(slugifyForFilename("🎉🎊🥳")).toBe(slugA);
  });

  it("leaves pure-ASCII unchanged from the legacy behavior", () => {
    expect(slugifyForFilename("Hello World")).toBe("hello-world");
    expect(slugifyForFilename("foo-bar")).toBe("foo-bar");
    expect(slugifyForFilename("123 abc")).toBe("123-abc");
  });
});

// ---------------------------------------------------------------------
// Markdown escape coverage — topicToMarkdown / projectToMarkdown /
// exportToMarkdown now escape every interpolated user-supplied string so
// a rogue `*`, `_`, backtick, etc. can't silently reformat the output.
// ---------------------------------------------------------------------

describe("markdown escape", () => {
  it("escapes significant characters inside decision statements", () => {
    const topic = makeTopics()[0];
    const decisions: Decision[] = [
      {
        ...makeDecisions()[0],
        statement: "Cap *bold* and `code` and [links](x)",
        rationale: "Because _italics_ and **strong** matter.",
      },
    ];
    const md = topicToMarkdown(topic, [], decisions);

    // Backslash-escaped versions appear.
    expect(md).toContain("\\*bold\\*");
    expect(md).toContain("\\`code\\`");
    expect(md).toContain("\\[links\\]\\(x\\)");
    expect(md).toContain("\\_italics\\_");
    expect(md).toContain("\\*\\*strong\\*\\*");

    // The H2 header structure is preserved — we did NOT escape the "## "
    // prefix of the "Decisions" heading.
    expect(md).toMatch(/^## Decisions/m);
  });

  it("escapes turn bodies, planner why-this-matters, and topic titles", () => {
    // Give the topic a title with a leading hash + asterisks + backticks.
    const topic: Topic = {
      ...makeTopics()[0],
      title: "# Budget *with* `code`",
    };
    const turns: QnaTurn[] = [
      {
        ...makeTurns()[0],
        body: "What about _sneaky_ `formatting`?",
        why_this_matters: "To avoid **accidental** headings.",
      },
      {
        ...makeTurns()[1],
        body: "A user reply with [brackets] and #hashes.",
      },
    ];
    const md = topicToMarkdown(topic, turns, []);

    // Topic title text is escaped inside the H1.
    expect(md).toContain("\\# Budget \\*with\\* \\`code\\`");
    // And the H1 prefix is still intact.
    expect(md).toMatch(/^# /m);

    // Turn bodies are escaped.
    expect(md).toContain("\\_sneaky\\_ \\`formatting\\`");
    expect(md).toContain("\\[brackets\\] and \\#hashes");

    // Why-this-matters is escaped too.
    expect(md).toContain("\\*\\*accidental\\*\\* headings");
  });

  it("preserves section headings in projectToMarkdown while escaping relationship labels", () => {
    const topics = makeTopics();
    // Label with angle bracket + asterisk + pipe to prove they get escaped.
    const relationships: Relationship[] = [
      {
        ...makeRelationships()[0],
        label: "blocks > *always* | sometimes",
      },
    ];
    const decisionsByTopic = new Map<string, Decision[]>();
    decisionsByTopic.set(topics[0].topic_id, makeDecisions().slice(0, 1));

    const md = projectToMarkdown(
      "My Project",
      topics,
      relationships,
      decisionsByTopic,
    );

    // H1 for the project + H2 for each topic + H3 for Decisions + H2 for
    // Relationships all still render as headings (the leading marks are NOT
    // escaped — only the interpolated text is).
    expect(md).toMatch(/^# My Project/m);
    expect(md).toMatch(/^## Budget/m);
    expect(md).toMatch(/^### Decisions/m);
    expect(md).toMatch(/^## Relationships/m);

    // Relationship label has every significant char escaped.
    expect(md).toContain("blocks \\> \\*always\\* \\| sometimes");
  });
});

// ---------------------------------------------------------------------
// Smoke tests for the HTML + plain-text renderers. These paths were not
// previously covered — we just need a minimal project through each and
// assertions on key output shape.
// ---------------------------------------------------------------------

describe("projectToHtml", () => {
  it("renders an HTML document with title, topics, and HTML-escaped user input", () => {
    const topics: Topic[] = [
      {
        ...makeTopics()[0],
        title: "<script>alert(1)</script>",
      },
      makeTopics()[1],
    ];
    const decisionsByTopicId = new Map<string, Decision[]>();
    decisionsByTopicId.set(topics[0].topic_id, [
      {
        ...makeDecisions()[0],
        statement: "Budget & scope must align",
      },
    ]);
    const turnsByTopicId = new Map<string, QnaTurn[]>();
    turnsByTopicId.set(topics[0].topic_id, makeTurns());

    const html = projectToHtml({
      projectTitle: "My <Project>",
      topics,
      turnsByTopicId,
      decisionsByTopicId,
      hasFullContent: true,
    });

    // Whole-document sanity.
    expect(html).toContain("<!doctype html>");
    expect(html).toContain("<html>");

    // Project title is HTML-escaped in the cover.
    expect(html).toContain("My &lt;Project&gt;");
    // The raw "<script>" from the topic title never escapes unescaped.
    expect(html).not.toContain("<script>alert(1)</script>");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");

    // The other topic's title is visible.
    expect(html).toContain("Cabinetry");

    // Decision statement is HTML-escaped (ampersand becomes &amp;).
    expect(html).toContain("Budget &amp; scope must align");

    // Each topic has its Conversation section heading.
    expect(html).toContain("Conversation");
  });
});

describe("projectToShareableHTML", () => {
  it("produces a self-contained HTML document with hero, cards, and HTML escaping", () => {
    const topics = makeTopics();
    const decisionsByTopicId = new Map<string, Decision[]>();
    decisionsByTopicId.set(topics[0].topic_id, [
      {
        ...makeDecisions()[0],
        statement: "Cap <the> budget",
      },
    ]);
    const turnsByTopicId = new Map<string, QnaTurn[]>();
    turnsByTopicId.set(topics[0].topic_id, makeTurns());

    const html = projectToShareableHTML({
      projectTitle: "Renovation & Co",
      projectSubtitle: "A short note",
      topics,
      relationships: makeRelationships(),
      decisionsByTopicId,
      turnsByTopicId,
      generatedAt: "2026-04-21",
    });

    // Document shell.
    expect(html).toContain("<!DOCTYPE html>");
    expect(html).toContain('<html lang="en">');

    // Title is HTML-escaped in both the <title> tag and the hero title.
    expect(html).toContain("Renovation &amp; Co");
    // Subtitle made it through.
    expect(html).toContain("A short note");

    // Topic titles appear.
    expect(html).toContain("Budget");
    expect(html).toContain("Cabinetry");

    // Decision statement is HTML-escaped.
    expect(html).toContain("Cap &lt;the&gt; budget");

    // Connections block rendered with both endpoint labels.
    expect(html).toContain("Connections between topics");
    expect(html).toContain("constrains");

    // Date + footer tag.
    expect(html).toContain("2026-04-21");
    expect(html).toContain("Exported from Inspira");
  });
});

describe("projectToPlainText", () => {
  it("renders plain text with no markdown syntax and preserves content", () => {
    const topics = makeTopics();
    const decisionsByTopicId = new Map<string, Decision[]>();
    decisionsByTopicId.set(topics[0].topic_id, [
      {
        ...makeDecisions()[0],
        statement: "**Cap** the budget",
        rationale: "_important_ reason",
      },
    ]);
    const turnsByTopicId = new Map<string, QnaTurn[]>();
    turnsByTopicId.set(topics[0].topic_id, [
      {
        ...makeTurns()[0],
        body: "## A heading body with *stars*",
      },
    ]);

    const txt = projectToPlainText(
      "My Project",
      topics,
      decisionsByTopicId,
      turnsByTopicId,
    );

    // Uppercase title + rule underline.
    expect(txt).toContain("MY PROJECT");

    // Topic titles appear verbatim.
    expect(txt).toContain("Budget");
    expect(txt).toContain("Cabinetry");

    // Decision and rationale stripped of markdown marks.
    expect(txt).toContain("Cap the budget");
    expect(txt).toContain("important reason");
    // No leftover `**` or `_` wrapping.
    expect(txt).not.toContain("**Cap**");
    expect(txt).not.toContain("_important_");

    // The turn body's "## " heading prefix is stripped.
    expect(txt).toContain("A heading body with stars");
    expect(txt).not.toContain("## A heading");

    // Conversation author label present.
    expect(txt).toContain("Planner:");
  });
});
