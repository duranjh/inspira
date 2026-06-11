import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  type ArtifactComment,
  type ArtifactCommentCategory,
} from "../api";

type CreateInput = {
  filePath: string;
  lineNumber: number;
  lineContent: string;
  category: ArtifactCommentCategory;
  body: string;
  parentCommentId?: string;
};

type UpdateInput = {
  body?: string;
  resolved?: boolean;
};

export type UseArtifactCommentsReturn = {
  comments: ArtifactComment[];
  loading: boolean;
  error: Error | null;
  createComment: (input: CreateInput) => Promise<ArtifactComment>;
  updateComment: (
    commentId: string,
    patch: UpdateInput,
  ) => Promise<ArtifactComment>;
  commentsForFile: (filePath: string) => ArtifactComment[];
  unresolvedCountByFile: Map<string, number>;
  refetch: () => Promise<void>;
};

/**
 * Wave F.4 — fetch + mutate inline artifact comments for one project.
 *
 * Optimistic update on ``createComment``: appends a pending placeholder
 * (``comment_id`` prefixed with ``pending-``) so the chip appears
 * immediately at the click target, then either replaces the placeholder
 * with the server-issued row on 201 or rolls it back on 4xx.
 *
 * Resolved comments are filtered out of the default fetch; toggle
 * ``includeResolved`` at the API layer if you need them (currently
 * the gutter overlay only needs open threads).
 */
export function useArtifactComments(
  projectId: string,
): UseArtifactCommentsReturn {
  const [comments, setComments] = useState<ArtifactComment[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  const pendingIdSeqRef = useRef<number>(0);

  const fetchComments = useCallback(async (): Promise<void> => {
    if (!projectId) return;
    try {
      setLoading(true);
      const res = await api.listArtifactComments(projectId);
      setComments(res.comments);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listArtifactComments(projectId)
      .then((res) => {
        if (cancelled) return;
        setComments(res.comments);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const createComment = useCallback(
    async (input: CreateInput): Promise<ArtifactComment> => {
      pendingIdSeqRef.current += 1;
      const pendingId = `pending-${pendingIdSeqRef.current}`;
      const nowIso = new Date().toISOString();
      const optimistic: ArtifactComment = {
        comment_id: pendingId,
        project_id: projectId,
        file_path: input.filePath,
        line_number: input.lineNumber,
        // Best-effort placeholder; the BE recomputes from line_content
        // and the FE replaces this row when the 201 arrives.
        line_content_hash: "",
        category: input.category,
        body: input.body,
        author_user_id: "",
        parent_comment_id: input.parentCommentId ?? null,
        resolved_at: null,
        created_at: nowIso,
        updated_at: nowIso,
      };
      setComments((prev) => [...prev, optimistic]);
      try {
        const res = await api.createArtifactComment(projectId, {
          file_path: input.filePath,
          line_number: input.lineNumber,
          line_content: input.lineContent,
          category: input.category,
          body: input.body,
          parent_comment_id: input.parentCommentId,
        });
        setComments((prev) =>
          prev.map((c) => (c.comment_id === pendingId ? res.comment : c)),
        );
        return res.comment;
      } catch (err) {
        // Roll back the optimistic insert; surface the error.
        setComments((prev) => prev.filter((c) => c.comment_id !== pendingId));
        throw err instanceof Error ? err : new Error(String(err));
      }
    },
    [projectId],
  );

  const updateComment = useCallback(
    async (commentId: string, patch: UpdateInput): Promise<ArtifactComment> => {
      const res = await api.updateArtifactComment(projectId, commentId, patch);
      setComments((prev) =>
        prev.map((c) => (c.comment_id === commentId ? res.comment : c)),
      );
      return res.comment;
    },
    [projectId],
  );

  const commentsForFile = useCallback(
    (filePath: string): ArtifactComment[] =>
      comments.filter((c) => c.file_path === filePath),
    [comments],
  );

  const unresolvedCountByFile = useMemo<Map<string, number>>(() => {
    const out = new Map<string, number>();
    for (const c of comments) {
      if (c.resolved_at !== null) continue;
      out.set(c.file_path, (out.get(c.file_path) ?? 0) + 1);
    }
    return out;
  }, [comments]);

  return {
    comments,
    loading,
    error,
    createComment,
    updateComment,
    commentsForFile,
    unresolvedCountByFile,
    refetch: fetchComments,
  };
}

/** Helper — SHA-256 over UTF-8 bytes of ``line``, first 16 hex chars.
 *  Must match the BE's ``PlanningStudioStore._hash_artifact_comment_line``
 *  byte-for-byte: same encoding, no trim, no newline-stripping. The
 *  CommentChipGutter calls this when rendering each chip to compare
 *  against the comment's stored ``line_content_hash`` and surface a
 *  "stale" outline on mismatch.
 */
export async function hashArtifactCommentLine(line: string): Promise<string> {
  const buf = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(line),
  );
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 16);
}
