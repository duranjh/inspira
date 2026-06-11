import { useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  type ArtifactChatMessage,
  type ArtifactPayload,
} from "../api";

export type ArtifactState =
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "ready"; artifact: ArtifactPayload }
  | { kind: "error"; message: string };

export type ArtifactStatus = "idle" | "thinking";

export type UseArtifactReturn = {
  state: ArtifactState;
  status: ArtifactStatus;
  thinkingLabel: string | null;
  thinkingElapsedS: number;
  /** Selected file path inside the editor pane. Auto-set to the first
   *  file once an artifact lands; updated on file-tree clicks. */
  selectedPath: string | null;
  selectPath: (path: string) => void;
  /** Manually trigger a generate. Called automatically from the hook
   *  when the initial GET returns 404 (empty state). Idempotent — a
   *  second call while still streaming is a no-op.
   *
   *  ``opts.force=true`` bypasses the BE's cached-manifest early-return
   *  (Regenerate kebab path). Default false makes the auto-fire-on-404
   *  call safe — see issues-log #187. */
  generate: (opts?: { force?: boolean }) => void;
  /** Send a chat message + apply the resulting edit. */
  sendMessage: (message: string) => Promise<void>;
  /** Re-fetch the artifact (called after file create/rename/delete
   *  so the file tree + selected-file content reflect the change). */
  refetch: () => Promise<void>;
};

/**
 * Owns the artifact state machine for the viewer page.
 *
 * Lifecycle:
 *   1. mount → GET /artifact
 *      - 200 → state="ready"
 *      - 404 → state="empty" + auto-fire generate()
 *      - else → state="error"
 *   2. generate() → POST /artifact/generate/stream
 *      - heartbeat → status="thinking"
 *      - complete → state="ready", status="idle"
 *      - error → state="error", status="idle"
 *   3. sendMessage(msg) → POST /artifact/edit/stream
 *      - optimistic user message append
 *      - heartbeat → status="thinking"
 *      - complete → swap files + assistant message append, status="idle"
 *      - error → status="idle", chat message marked failed via state
 */
export function useArtifact(projectId: string): UseArtifactReturn {
  const [state, setState] = useState<ArtifactState>({ kind: "loading" });
  const [status, setStatus] = useState<ArtifactStatus>("idle");
  const [thinkingLabel, setThinkingLabel] = useState<string | null>(null);
  const [thinkingElapsedS, setThinkingElapsedS] = useState<number>(0);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const generatingRef = useRef<boolean>(false);

  const setReady = useCallback((artifact: ArtifactPayload) => {
    setState({ kind: "ready", artifact });
    setSelectedPath((prev) => {
      if (prev && artifact.files.some((f) => f.path === prev)) {
        return prev;
      }
      return artifact.files[0]?.path ?? null;
    });
    setStatus("idle");
    setThinkingLabel(null);
    setThinkingElapsedS(0);
  }, []);

  // Client-side watchdog: if neither onComplete nor onError fires
  // within 3 minutes, force-flip to error so the partner isn't
  // stuck on the "Wiring it together…" screen forever. The BE has
  // its own hard timeout that emits a structured error frame, but
  // an SSE socket can silently drop (Cloudflare 100s edge timeout
  // mid-frame, partner's wifi blip, etc.) — this is the failsafe.
  const generateWatchdogRef = useRef<number | null>(null);

  const clearGenerateWatchdog = useCallback(() => {
    if (generateWatchdogRef.current !== null) {
      window.clearTimeout(generateWatchdogRef.current);
      generateWatchdogRef.current = null;
    }
  }, []);

  const generate = useCallback((opts: { force?: boolean } = {}) => {
    if (generatingRef.current) return;
    generatingRef.current = true;
    setStatus("thinking");
    setThinkingElapsedS(0);
    clearGenerateWatchdog();
    generateWatchdogRef.current = window.setTimeout(() => {
      if (!generatingRef.current) return;
      generatingRef.current = false;
      generateWatchdogRef.current = null;
      setState({
        kind: "error",
        message:
          "Code generation went silent for 3 minutes. The stream may have dropped — try again.",
      });
      setStatus("idle");
      setThinkingLabel(null);
    }, 3 * 60_000);
    api
      .generateArtifactStream(
        projectId,
        {
          onHeartbeat: (frame) => {
            setThinkingLabel(frame.message);
            // Backend emits { elapsed_s } too; cast loosely so a future
            // schema bump doesn't break the build.
            const elapsed = (frame as unknown as { elapsed_s?: number })
              .elapsed_s;
            if (typeof elapsed === "number") setThinkingElapsedS(elapsed);
          },
          onComplete: (envelope) => {
            generatingRef.current = false;
            clearGenerateWatchdog();
            setReady(envelope.artifact);
          },
          onError: (err) => {
            generatingRef.current = false;
            clearGenerateWatchdog();
            setState({ kind: "error", message: err.message });
            setStatus("idle");
            setThinkingLabel(null);
          },
        },
        undefined,
        { force: opts.force ?? false },
      )
      .catch((err: unknown) => {
        generatingRef.current = false;
        clearGenerateWatchdog();
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : String(err),
        });
        setStatus("idle");
        setThinkingLabel(null);
      });
  }, [projectId, setReady, clearGenerateWatchdog]);

  // Initial fetch — 404 means "no artifact generated yet, kick off
  // generation now". Any other failure surfaces as state.error.
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    api
      .getArtifact(projectId)
      .then((res) => {
        if (cancelled) return;
        setReady(res.artifact);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 404 path — backend signals "artifact_not_generated".
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("artifact_not_generated") || msg.includes("404")) {
          setState({ kind: "empty" });
          // Product decision: artifact (code) IS the thing
          // that gets approved — not the canvas. Auto-fire generate
          // at any project_state; the ApprovalChip on the viewer
          // handles Draft → In Review → Approved transitions on the
          // generated artifact itself.
          //
          // force=false (explicit) so a partner who clicks Code before
          // the pre-warm BG task finishes replays the cached scaffold
          // instead of triggering a second LLM call (#187 race window).
          generate({ force: false });
        } else {
          setState({ kind: "error", message: msg });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, generate, setReady]);

  const sendMessage = useCallback(
    async (message: string): Promise<void> => {
      const trimmed = message.trim();
      if (!trimmed) return;
      // Optimistically append the user turn so the chat shows the
      // message immediately, before the backend echoes it back.
      const optimisticUserTurn: ArtifactChatMessage = {
        role: "user",
        body: trimmed,
        ts: new Date().toISOString(),
      };
      setState((prev) => {
        if (prev.kind !== "ready") return prev;
        return {
          kind: "ready",
          artifact: {
            ...prev.artifact,
            messages: [...prev.artifact.messages, optimisticUserTurn],
          },
        };
      });
      setStatus("thinking");
      setThinkingElapsedS(0);
      try {
        await api.editArtifactStream(projectId, trimmed, {
          onHeartbeat: (frame) => {
            setThinkingLabel(frame.message);
            const elapsed = (frame as unknown as { elapsed_s?: number })
              .elapsed_s;
            if (typeof elapsed === "number") setThinkingElapsedS(elapsed);
          },
          onComplete: (envelope) => {
            setReady(envelope.artifact);
          },
          onError: (err) => {
            setStatus("idle");
            setThinkingLabel(null);
            setState((prev) => {
              if (prev.kind !== "ready") return prev;
              return {
                kind: "ready",
                artifact: {
                  ...prev.artifact,
                  messages: [
                    ...prev.artifact.messages,
                    {
                      role: "assistant",
                      body: err.message || "Couldn't apply that edit.",
                      ts: new Date().toISOString(),
                    },
                  ],
                },
              };
            });
          },
        });
      } catch (err: unknown) {
        setStatus("idle");
        setThinkingLabel(null);
        const msg = err instanceof Error ? err.message : String(err);
        setState((prev) => {
          if (prev.kind !== "ready") return prev;
          return {
            kind: "ready",
            artifact: {
              ...prev.artifact,
              messages: [
                ...prev.artifact.messages,
                {
                  role: "assistant",
                  body: `Couldn't apply that edit: ${msg}`,
                  ts: new Date().toISOString(),
                },
              ],
            },
          };
        });
      }
    },
    [projectId, setReady],
  );

  const selectPath = useCallback((path: string) => {
    setSelectedPath(path);
  }, []);

  const refetch = useCallback(async (): Promise<void> => {
    try {
      const res = await api.getArtifact(projectId);
      setReady(res.artifact);
    } catch {
      // Refetch failures are non-fatal — the previous state stays
      // visible. Caller (file create/rename/delete) surfaces its
      // own toast on hard error.
    }
  }, [projectId, setReady]);

  return {
    state,
    status,
    thinkingLabel,
    thinkingElapsedS,
    selectedPath,
    selectPath,
    generate,
    sendMessage,
    refetch,
  };
}
