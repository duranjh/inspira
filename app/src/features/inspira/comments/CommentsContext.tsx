// Per-project comment + cascade state container.
//
// Wraps a small reducer so the canvas + topic-detail surfaces share
// one source of truth without prop drilling. State is intentionally
// local-first: comments live in memory until cascade-commit fires,
// then the BE-returned versions become the source of truth.
//
// Mounted in BOTH ProjectCanvas and TopicDetail (separate route
// subtrees in routes.tsx) — both providers key on ``projectId``.

import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";

import { cascadeApi, pollCascadeUntilDone } from "./cascadeApi";
import type {
  AffectedScope,
  CascadePreview,
  CascadeRun,
  CommentTarget,
  CommentStatus,
  Comment,
  ScopeMode,
  VersionAge,
} from "./types";

// -----------------------------------------------------------
// State + actions
// -----------------------------------------------------------

type TargetEntry = {
  comments: Comment[];
  status: CommentStatus;
  pending: boolean;
};

type CascadeCommitState =
  | null
  | { cascade_id: string; status: "running" | "done" | "failed"; error?: string };

type State = {
  byTarget: Record<string, TargetEntry>;
  cascadePreview: CascadePreview | null;
  cascadeCommit: CascadeCommitState;
  // Server-known version ages, keyed on decision_id. Drives DiffBadge.
  versionAgeByDecisionId: Record<string, { age: VersionAge; lastChangedAt: string }>;
};

type Action =
  | { type: "OPTIMISTIC_ADD"; target: CommentTarget; comment: Comment }
  | { type: "ROLLBACK"; target: CommentTarget; commentId: string }
  | { type: "SET_PREVIEW"; preview: CascadePreview | null }
  | { type: "COMMIT_BEGIN"; cascadeId: string }
  | { type: "COMMIT_DONE"; cascadeRun: CascadeRun }
  | { type: "COMMIT_FAILED"; error: string }
  | { type: "MARK_VERSION_AGE"; decisionId: string; age: VersionAge; lastChangedAt: string };

function targetKey(t: CommentTarget): string {
  return `${t.kind}:${t.id}`;
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "OPTIMISTIC_ADD": {
      const key = targetKey(action.target);
      const prior = state.byTarget[key] || {
        comments: [],
        status: "open" as CommentStatus,
        pending: false,
      };
      return {
        ...state,
        byTarget: {
          ...state.byTarget,
          [key]: {
            comments: [...prior.comments, action.comment],
            status: "open",
            pending: true,
          },
        },
      };
    }
    case "ROLLBACK": {
      const key = targetKey(action.target);
      const prior = state.byTarget[key];
      if (!prior) return state;
      return {
        ...state,
        byTarget: {
          ...state.byTarget,
          [key]: {
            ...prior,
            comments: prior.comments.filter(
              (c) => c.comment_id !== action.commentId,
            ),
            pending: false,
          },
        },
      };
    }
    case "SET_PREVIEW":
      return { ...state, cascadePreview: action.preview };
    case "COMMIT_BEGIN":
      return {
        ...state,
        cascadeCommit: { cascade_id: action.cascadeId, status: "running" },
      };
    case "COMMIT_DONE": {
      const versionMap = { ...state.versionAgeByDecisionId };
      const versions =
        action.cascadeRun.affected_scope?.new_decision_versions ?? [];
      const now = new Date().toISOString();
      for (const v of versions) {
        versionMap[v.decision_id] = { age: "fresh", lastChangedAt: now };
      }
      const isComplete = action.cascadeRun.status === "complete";
      // Mark commented decisions addressed ONLY when the cascade actually
      // succeeded — a failed run leaves the comment open so the user can
      // see the regen never landed.
      const byTarget = { ...state.byTarget };
      if (isComplete) {
        for (const c of action.cascadeRun.commented_decisions) {
          const key = `decision:${c.decision_id}`;
          if (byTarget[key]) {
            byTarget[key] = {
              ...byTarget[key],
              status: "addressed",
              pending: false,
            };
          }
        }
      } else {
        // Failed: drop the pending flag so the chip stops spinning, but
        // keep status "open" so the thread reads "Open." not "Addressed".
        for (const c of action.cascadeRun.commented_decisions) {
          const key = `decision:${c.decision_id}`;
          if (byTarget[key]) {
            byTarget[key] = { ...byTarget[key], pending: false };
          }
        }
      }
      return {
        ...state,
        byTarget,
        cascadeCommit: isComplete
          ? { cascade_id: action.cascadeRun.cascade_id, status: "done" }
          : { cascade_id: action.cascadeRun.cascade_id, status: "failed", error: action.cascadeRun.error || undefined },
        cascadePreview: null, // banner clears on commit completion
        versionAgeByDecisionId: versionMap,
      };
    }
    case "COMMIT_FAILED":
      return {
        ...state,
        cascadeCommit: state.cascadeCommit
          ? { ...state.cascadeCommit, status: "failed", error: action.error }
          : { cascade_id: "", status: "failed", error: action.error },
      };
    case "MARK_VERSION_AGE":
      return {
        ...state,
        versionAgeByDecisionId: {
          ...state.versionAgeByDecisionId,
          [action.decisionId]: {
            age: action.age,
            lastChangedAt: action.lastChangedAt,
          },
        },
      };
    default:
      return state;
  }
}

// -----------------------------------------------------------
// Context shape
// -----------------------------------------------------------

type Ctx = {
  state: State;
  // Local-only "draft" comment add (no server round-trip yet).
  optimisticAddComment: (target: CommentTarget, text: string) => Comment;
  // Preview + commit (Apply / Apply-all).
  previewCascade: (
    target: CommentTarget,
    text: string,
    scope_mode: ScopeMode,
  ) => Promise<CascadePreview>;
  commitCascade: (
    target: CommentTarget,
    text: string,
    scope_mode: ScopeMode,
  ) => Promise<CascadeRun>;
  clearPreview: () => void;
  // For tests + page-level reset.
  reset: () => void;
};

const CommentsContext = createContext<Ctx | null>(null);

export function useComments(): Ctx {
  const ctx = useContext(CommentsContext);
  if (!ctx) {
    throw new Error("useComments must be used inside <CommentsProvider>");
  }
  return ctx;
}

// Utility hooks ---------------------------------------------

export function useCommentsForTarget(target: CommentTarget): TargetEntry {
  const { state } = useComments();
  return (
    state.byTarget[targetKey(target)] || {
      comments: [],
      status: "open",
      pending: false,
    }
  );
}

export function useCascadePreview(): CascadePreview | null {
  return useComments().state.cascadePreview;
}

export function useVersionAge(decisionId: string): { age: VersionAge; lastChangedAt: string } | null {
  const { state } = useComments();
  return state.versionAgeByDecisionId[decisionId] ?? null;
}

// -----------------------------------------------------------
// Provider
// -----------------------------------------------------------

const initialState: State = {
  byTarget: {},
  cascadePreview: null,
  cascadeCommit: null,
  versionAgeByDecisionId: {},
};

export type CommentsProviderProps = {
  projectId: string;
  children: ReactNode;
  // Test seam: inject a custom api implementation.
  api?: typeof cascadeApi;
};

export function CommentsProvider({
  projectId,
  children,
  api = cascadeApi,
}: CommentsProviderProps): React.JSX.Element {
  const [state, dispatch] = useReducer(reducer, initialState);
  const pollAbortRef = useRef<AbortController | null>(null);

  const optimisticAddComment = useCallback(
    (target: CommentTarget, text: string): Comment => {
      const comment: Comment = {
        comment_id: `tmp-${Math.random().toString(36).slice(2, 10)}`,
        target,
        text,
        created_at: new Date().toISOString(),
        status: "open",
      };
      dispatch({ type: "OPTIMISTIC_ADD", target, comment });
      return comment;
    },
    [],
  );

  const previewCascade = useCallback(
    async (
      target: CommentTarget,
      text: string,
      scope_mode: ScopeMode,
    ): Promise<CascadePreview> => {
      const preview = await api.preview(projectId, {
        commented_decisions: [{ decision_id: target.id, comment_text: text }],
        scope_mode,
      });
      dispatch({ type: "SET_PREVIEW", preview });
      return preview;
    },
    [projectId, api],
  );

  const commitCascade = useCallback(
    async (
      target: CommentTarget,
      text: string,
      scope_mode: ScopeMode,
    ): Promise<CascadeRun> => {
      const { cascade_id } = await api.commit(projectId, {
        commented_decisions: [{ decision_id: target.id, comment_text: text }],
        scope_mode,
      });
      dispatch({ type: "COMMIT_BEGIN", cascadeId: cascade_id });
      pollAbortRef.current?.abort();
      const ctrl = new AbortController();
      pollAbortRef.current = ctrl;
      try {
        const run = await pollCascadeUntilDone(projectId, cascade_id, {
          signal: ctrl.signal,
        });
        dispatch({ type: "COMMIT_DONE", cascadeRun: run });
        return run;
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        dispatch({ type: "COMMIT_FAILED", error: message });
        throw e;
      }
    },
    [projectId, api],
  );

  const clearPreview = useCallback(() => {
    dispatch({ type: "SET_PREVIEW", preview: null });
  }, []);

  const reset = useCallback(() => {
    pollAbortRef.current?.abort();
    pollAbortRef.current = null;
  }, []);

  const value: Ctx = useMemo(
    () => ({
      state,
      optimisticAddComment,
      previewCascade,
      commitCascade,
      clearPreview,
      reset,
    }),
    [state, optimisticAddComment, previewCascade, commitCascade, clearPreview, reset],
  );

  return (
    <CommentsContext.Provider value={value}>
      {children}
    </CommentsContext.Provider>
  );
}

// Internal export for tests only — lets a unit test exercise the
// reducer in isolation without spinning up React.
export const __reducer = reducer;
export const __initialState = initialState;
