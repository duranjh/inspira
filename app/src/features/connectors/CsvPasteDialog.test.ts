// CSV / JSON paste-in parser tests (W2 C6).
//
// The dialog itself is a thin wrapper around `parseFeedbackPaste`.
// These tests pin the parser invariants so partner-supplied
// exports of varied shape decode predictably.

import { describe, expect, it } from "vitest";

import { parseCsvRow, parseFeedbackPaste } from "./CsvPasteDialog";

describe("parseCsvRow", () => {
  it("splits a simple comma-separated row", () => {
    expect(parseCsvRow("a,b,c")).toEqual(["a", "b", "c"]);
  });

  it("preserves commas inside quoted fields", () => {
    expect(parseCsvRow('a,"b, with comma",c')).toEqual([
      "a",
      "b, with comma",
      "c",
    ]);
  });

  it("handles escaped quotes inside quoted fields", () => {
    expect(parseCsvRow('a,"she said ""hi""",b')).toEqual([
      "a",
      'she said "hi"',
      "b",
    ]);
  });

  it("trims whitespace around cells", () => {
    expect(parseCsvRow("  a , b ,  c  ")).toEqual(["a", "b", "c"]);
  });
});

describe("parseFeedbackPaste — CSV", () => {
  it("rejects CSV missing a title-like column", () => {
    const result = parseFeedbackPaste("a,b\n1,2\n");
    expect(result.format).toBe("csv");
    expect(result.error).toMatch(/title/i);
    expect(result.rows).toEqual([]);
  });

  it("parses a basic CSV with title + body", () => {
    const csv =
      "title,body\nLogin fails on Safari,Cleared cache no fix\n";
    const result = parseFeedbackPaste(csv);
    expect(result.error).toBeUndefined();
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0]).toMatchObject({
      title: "Login fails on Safari",
      body: "Cleared cache no fix",
      source: "csv-import",
    });
  });

  it("recognizes alternate column names (subject / message)", () => {
    const csv = "subject,message\nApp crashes,Heap overflow\n";
    const result = parseFeedbackPaste(csv);
    expect(result.rows[0].title).toBe("App crashes");
    expect(result.rows[0].body).toBe("Heap overflow");
  });

  it("preserves the source column when present", () => {
    const csv =
      "title,source\nFeedback A,intercom\nFeedback B,linear\n";
    const result = parseFeedbackPaste(csv);
    expect(result.rows[0].source).toBe("intercom");
    expect(result.rows[1].source).toBe("linear");
  });

  it("skips rows with empty title", () => {
    const csv = "title,body\nA,1\n,2\nC,3\n";
    const result = parseFeedbackPaste(csv);
    expect(result.rows.map((r) => r.title)).toEqual(["A", "C"]);
  });
});

describe("parseFeedbackPaste — JSON", () => {
  it("parses a JSON array of objects", () => {
    const json = JSON.stringify([
      { title: "A", body: "x" },
      { title: "B", body: "y" },
    ]);
    const result = parseFeedbackPaste(json);
    expect(result.format).toBe("json");
    expect(result.rows).toHaveLength(2);
    expect(result.rows[0].title).toBe("A");
  });

  it("rejects non-array JSON", () => {
    const result = parseFeedbackPaste('{"title":"A"}');
    expect(result.error).toMatch(/array/i);
    expect(result.rows).toEqual([]);
  });

  it("surfaces JSON parse errors", () => {
    const result = parseFeedbackPaste("[not json");
    expect(result.error).toBeDefined();
  });
});

describe("parseFeedbackPaste — empty", () => {
  it("returns an error for blank input", () => {
    const result = parseFeedbackPaste("   \n  ");
    expect(result.error).toMatch(/paste/i);
  });
});
