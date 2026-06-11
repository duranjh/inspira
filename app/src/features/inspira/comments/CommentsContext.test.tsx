// Reducer-level tests for the CommentsContext store. The reducer is
// exported for direct exercise so we don't need to mount React just
// to assert state transitions.

import { describe, expect, it } from "vitest";

import { __initialState, __reducer } from "./CommentsContext";
import type { CascadeRun, CommentTarget } from "./types";

const target: CommentTarget = { kind: "decision", id: "dec-1" };

function makeComment(text: string, id = "tmp-x") {
  return {
    comment_id: id,
    target,
    text,
    created_at: "2026-05-03T00:00:00Z",
    status: "open" as const,
  };
}

describe("CommentsContext reducer", () => {
  it("OPTIMISTIC_ADD seeds a target entry with pending=true", () => {
    const next = __reducer(__initialState, {
      type: "OPTIMISTIC_ADD",
      target,
      comment: makeComment("hello"),
    });
    expect(next.byTarget["decision:dec-1"].comments).toHaveLength(1);
    expect(next.byTarget["decision:dec-1"].pending).toBe(true);
    expect(next.byTarget["decision:dec-1"].status).toBe("open");
  });

  it("ROLLBACK removes the optimistic comment + clears pending", () => {
    const seeded = __reducer(__initialState, {
      type: "OPTIMISTIC_ADD",
      target,
      comment: makeComment("hello", "tmp-1"),
    });
    const next = __reducer(seeded, {
      type: "ROLLBACK",
      target,
      commentId: "tmp-1",
    });
    expect(next.byTarget["decision:dec-1"].comments).toHaveLength(0);
    expect(next.byTarget["decision:dec-1"].pending).toBe(false);
  });

  it("SET_PREVIEW writes the preview", () => {
    const next = __reducer(__initialState, {
      type: "SET_PREVIEW",
      preview: {
        affected_scope: {
          decision_ids: [],
          topic_ids: [],
          count: 0,
          banner_state: "none",
        },
        estimated_cost_usd: 0.001,
        estimated_seconds: 3,
      },
    });
    expect(next.cascadePreview).not.toBeNull();
    expect(next.cascadePreview?.affected_scope.count).toBe(0);
  });

  it("COMMIT_DONE flips commented-target status to addressed + records fresh version", () => {
    const seeded = __reducer(__initialState, {
      type: "OPTIMISTIC_ADD",
      target,
      comment: makeComment("hello"),
    });
    const cascadeRun: CascadeRun = {
      cascade_id: "csc-1",
      status: "complete",
      scope_mode: "local",
      commented_decisions: [{ decision_id: "dec-1", comment_text: "hello" }],
      affected_scope: {
        decision_ids: [],
        topic_ids: [],
        count: 0,
        banner_state: "none",
        new_decision_versions: [
          {
            decision_id: "dec-1",
            version_int: 2,
            prior_version_int: 1,
            statement: "new",
            rationale: null,
            change_note: "n",
            is_new_decision: false,
          },
        ],
      },
      diff_summary: { updated_count: 1, created_count: 0, failed_count: 0 },
      error: null,
      started_at: "2026-05-03T00:00:00Z",
      completed_at: "2026-05-03T00:00:01Z",
    };
    const next = __reducer(seeded, { type: "COMMIT_DONE", cascadeRun });
    expect(next.byTarget["decision:dec-1"].status).toBe("addressed");
    expect(next.byTarget["decision:dec-1"].pending).toBe(false);
    expect(next.versionAgeByDecisionId["dec-1"].age).toBe("fresh");
    expect(next.cascadeCommit?.status).toBe("done");
    // Preview always clears on commit completion.
    expect(next.cascadePreview).toBeNull();
  });

  it("COMMIT_DONE on failed cascade leaves comment open + clears pending", () => {
    const seeded = __reducer(__initialState, {
      type: "OPTIMISTIC_ADD",
      target,
      comment: makeComment("hello"),
    });
    const cascadeRun: CascadeRun = {
      cascade_id: "csc-2",
      status: "failed",
      scope_mode: "local",
      commented_decisions: [{ decision_id: "dec-1", comment_text: "hello" }],
      affected_scope: null,
      diff_summary: { updated_count: 0, created_count: 0, failed_count: 1 },
      error: "boom",
      started_at: "2026-05-03T00:00:00Z",
      completed_at: "2026-05-03T00:00:01Z",
    };
    const next = __reducer(seeded, { type: "COMMIT_DONE", cascadeRun });
    expect(next.cascadeCommit?.status).toBe("failed");
    expect(next.byTarget["decision:dec-1"].status).toBe("open");
    expect(next.byTarget["decision:dec-1"].pending).toBe(false);
  });

  it("MARK_VERSION_AGE updates the per-decision age map", () => {
    const next = __reducer(__initialState, {
      type: "MARK_VERSION_AGE",
      decisionId: "dec-7",
      age: "stale",
      lastChangedAt: "2026-05-01T00:00:00Z",
    });
    expect(next.versionAgeByDecisionId["dec-7"].age).toBe("stale");
  });
});
