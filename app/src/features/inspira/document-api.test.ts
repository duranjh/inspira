// Tests for the new Document* methods on the api client (#094 part 4a).
//
// Coverage:
//   - generateDocument: 402 → DocumentPlanRequiredError
//   - generateDocument: 422 → DocumentDomainNotMappedError
//   - generateDocument: 429 → DocumentCapReachedError with current_count + cap
//   - generateDocument: 409 → DocumentInFlightError
//   - generateDocument: 202 happy path
//   - getLatestDocument: 404 → returns null (NOT throws)
//   - getLatestDocument: 422 → DocumentDomainNotMappedError
//   - getLatestDocument: 200 happy path
//   - patchDocumentSection: 404 + error=section_not_found → DocumentNotFoundError(sectionId)
//   - patchDocumentSection: 200 happy path
//
// Mocks the global fetch — no network. Each test stubs a single
// fetch response shape; failures throw the typed error which we
// assert via `instanceof`.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  DocumentCapReachedError,
  DocumentDomainNotMappedError,
  DocumentInFlightError,
  DocumentInvalidDocTypeError,
  DocumentNotFoundError,
  DocumentPlanRequiredError,
} from "./api";

type FetchMock = ReturnType<typeof vi.fn>;

// Helper: build a fake Response with a JSON body + status. Mirrors
// the FastAPI {detail: {...}} shape used by the BE.
function fakeResponse(
  status: number,
  body: unknown,
  ok?: boolean,
): Response {
  const text = JSON.stringify(body);
  return {
    ok: ok ?? (status >= 200 && status < 300),
    status,
    statusText: `HTTP ${status}`,
    headers: new Headers(),
    json: () => Promise.resolve(JSON.parse(text)),
    text: () => Promise.resolve(text),
  } as unknown as Response;
}

let fetchSpy: FetchMock;

beforeEach(() => {
  fetchSpy = vi.fn();
  // @ts-expect-error overriding global fetch for the duration of the test
  globalThis.fetch = fetchSpy;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api.generateDocument", () => {
  it("maps 402 → DocumentPlanRequiredError with min_plan", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(402, { detail: { error: "plan_required", min_plan: "pro" } }),
    );
    await expect(api.generateDocument("proj-1")).rejects.toBeInstanceOf(
      DocumentPlanRequiredError,
    );
  });

  it("maps 422 → DocumentDomainNotMappedError with domain", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(422, {
        detail: { error: "domain_not_supported", domain: "career" },
      }),
    );
    try {
      await api.generateDocument("proj-1");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(DocumentDomainNotMappedError);
      expect((err as DocumentDomainNotMappedError).domain).toBe("career");
    }
  });

  it("maps 429 → DocumentCapReachedError surfacing current_count + cap", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(429, {
        detail: {
          error: "document_limit_reached",
          current_count: 1,
          cap: 1,
          plan_slug: "pro",
          doc_type: "business_plan",
        },
      }),
    );
    try {
      await api.generateDocument("proj-1");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(DocumentCapReachedError);
      const e = err as DocumentCapReachedError;
      expect(e.currentCount).toBe(1);
      expect(e.cap).toBe(1);
      expect(e.planSlug).toBe("pro");
      expect(e.docType).toBe("business_plan");
    }
  });

  it("maps 409 → DocumentInFlightError", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(409, {
        detail: { error: "document_already_in_flight", doc_type: "prd" },
      }),
    );
    await expect(api.generateDocument("proj-1")).rejects.toBeInstanceOf(
      DocumentInFlightError,
    );
  });

  it("maps 422 invalid_doc_type → DocumentInvalidDocTypeError (override path)", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(422, {
        detail: {
          error: "invalid_doc_type",
          doc_type: "not_a_real_type",
        },
      }),
    );
    try {
      await api.generateDocument("proj-1", "not_a_real_type" as never);
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(DocumentInvalidDocTypeError);
      expect(
        (err as DocumentInvalidDocTypeError).attemptedDocType,
      ).toBe("not_a_real_type");
    }
  });

  it("forwards docTypeOverride in the request body", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(202, {
        document_id: "doc-1",
        status: "in_progress",
      }),
    );
    await api.generateDocument("proj-1", "course_outline");
    const callArgs = fetchSpy.mock.calls[0];
    const init = callArgs?.[1] as RequestInit | undefined;
    const body = JSON.parse((init?.body as string) ?? "{}");
    expect(body.doc_type).toBe("course_outline");
  });

  it("omits doc_type from body when no override is supplied", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(202, {
        document_id: "doc-1",
        status: "in_progress",
      }),
    );
    await api.generateDocument("proj-1");
    const callArgs = fetchSpy.mock.calls[0];
    const init = callArgs?.[1] as RequestInit | undefined;
    const body = JSON.parse((init?.body as string) ?? "{}");
    expect(body.doc_type).toBeUndefined();
  });

  it("returns DocumentGenerateResponse on 202 happy path", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(202, {
        document_id: "doc-1",
        status: "in_progress",
      }),
    );
    const res = await api.generateDocument("proj-1");
    expect(res.document_id).toBe("doc-1");
    expect(res.status).toBe("in_progress");
  });
});

describe("api.getLatestDocument", () => {
  it("returns null on 404 (no completed doc yet)", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(404, { detail: { error: "document_not_found" } }),
    );
    const res = await api.getLatestDocument("proj-1");
    expect(res).toBeNull();
  });

  it("maps 422 → DocumentDomainNotMappedError", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(422, {
        detail: { error: "domain_not_supported", domain: "personal" },
      }),
    );
    try {
      await api.getLatestDocument("proj-1");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(DocumentDomainNotMappedError);
      expect((err as DocumentDomainNotMappedError).domain).toBe("personal");
    }
  });

  it("returns DocumentView on 200", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(200, {
        document_id: "doc-1",
        project_id: "proj-1",
        doc_type: "business_plan",
        status: "completed",
        content: { doc_type: "business_plan", sections: [] },
        error_message: null,
        model_id: "gpt-5.5",
        plan_tier: "pro",
        output_tokens_estimate: 0,
        generated_at: "2026-04-29T12:00:00Z",
        completed_at: "2026-04-29T12:01:00Z",
      }),
    );
    const res = await api.getLatestDocument("proj-1");
    expect(res?.document_id).toBe("doc-1");
    expect(res?.doc_type).toBe("business_plan");
  });
});

describe("api.patchDocumentSection", () => {
  it("maps 404 + section_not_found → DocumentNotFoundError(sectionId)", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(404, {
        detail: { error: "section_not_found", section_id: "ghost" },
      }),
    );
    try {
      await api.patchDocumentSection("proj-1", "doc-1", "ghost", {
        title: "x",
      });
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(DocumentNotFoundError);
      // The api.ts handler passes the sectionId from the URL through
      // to the typed error; assert it's preserved.
      expect((err as DocumentNotFoundError).sectionId).toBe("ghost");
    }
  });

  it("returns updated DocumentView on 200", async () => {
    fetchSpy.mockResolvedValueOnce(
      fakeResponse(200, {
        document_id: "doc-1",
        project_id: "proj-1",
        doc_type: "business_plan",
        status: "completed",
        content: {
          doc_type: "business_plan",
          sections: [
            {
              section_id: "exec_summary",
              title: "Executive summary",
              prose_markdown: "Updated.",
              key_points: [],
              cited_topics: [],
            },
          ],
        },
        error_message: null,
        model_id: "gpt-5.5",
        plan_tier: "pro",
        output_tokens_estimate: 0,
        generated_at: "2026-04-29T12:00:00Z",
        completed_at: "2026-04-29T12:01:00Z",
      }),
    );
    const res = await api.patchDocumentSection(
      "proj-1",
      "doc-1",
      "exec_summary",
      { prose_markdown: "Updated." },
    );
    expect(res.content?.sections[0]?.prose_markdown).toBe("Updated.");
  });
});
