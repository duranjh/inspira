// Read-only shared canvas page — rendered when someone visits
// /shared/<token>  from a share link.
//
// What it shows:
//   - The project canvas: topic cards + relationship lines (read-only).
//   - Decisions summarised as bullets on each topic card.
//   - A slim footer attribution: "Shared by <display_name> · tryinspira.com"
//     plus a "Start your own Inspira" CTA.
//
// What it deliberately omits:
//   - Composer / Q&A thread.
//   - Topic detail drawer (click on a topic is a no-op).
//   - User menu / nav chrome.
//   - Any write actions whatsoever.
//
// If the token resolves to a 404 (revoked / unknown) we show a simple
// "This link is no longer active" message rather than the app's full
// NotFoundPage, to keep the page self-contained.

import { useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  MarkerType,
  type Node,
  ReactFlowProvider,
} from "reactflow";
import "reactflow/dist/style.css";

import { t } from "../../i18n";
import { TopicNodeSkeleton } from "../../components/Skeleton";
import { type Decision, type Relationship, type Topic, api } from "../inspira/api";
import { TopicNode, type TopicNodeData } from "../inspira/TopicNode";

const nodeTypes = { topic: TopicNode };

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SharedProject = {
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  owner_display_name: string;
};

type SharedEnvelope = {
  project: SharedProject;
  topics: Topic[];
  relationships: Relationship[];
  decisions: Decision[];
  turns_by_topic: Record<string, unknown[]>;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "not_found" }
  | { kind: "ready"; data: SharedEnvelope };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildNodes(
  topics: Topic[],
  decisions: Decision[],
): Node<TopicNodeData>[] {
  const decsByTopic = new Map<string, Decision[]>();
  for (const d of decisions) {
    const list = decsByTopic.get(d.topic_id) ?? [];
    list.push(d);
    decsByTopic.set(d.topic_id, list);
  }

  return topics.map((topic) => {
    const topicDecs = decsByTopic.get(topic.topic_id) ?? [];
    return {
      id: topic.topic_id,
      type: "topic",
      position: { x: topic.position_x, y: topic.position_y },
      draggable: false,
      selectable: false,
      data: {
        title: topic.title,
        icon: topic.icon,
        decisions: topicDecs.filter((d) => d.status === "confirmed"),
        status: topic.status,
        // onOpen intentionally omitted — topic clicks are no-ops.
      } satisfies TopicNodeData,
    };
  });
}

function buildEdges(relationships: Relationship[]): Edge[] {
  return relationships.map((rel) => ({
    id: rel.relationship_id,
    source: rel.source_topic_id,
    target: rel.target_topic_id,
    label: rel.label ?? undefined,
    type: "straight",
    animated: false,
    style: { stroke: "#a0856c", strokeDasharray: "5 4", strokeWidth: 1.5 },
    markerEnd: {
      type: MarkerType.Arrow,
      color: "#a0856c",
      width: 14,
      height: 14,
    },
  }));
}

// ---------------------------------------------------------------------------
// Inner canvas — needs ReactFlowProvider wrapper
// ---------------------------------------------------------------------------

function SharedCanvas({ data }: { data: SharedEnvelope }) {
  const nodes = useMemo(
    () => buildNodes(data.topics, data.decisions),
    [data.topics, data.decisions],
  );
  const edges = useMemo(
    () => buildEdges(data.relationships),
    [data.relationships],
  );

  return (
    <div style={{ width: "100%", height: "100%", background: "var(--paper-lifted, #fbf7ee)" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll
        minZoom={0.25}
        maxZoom={2}
      >
        <Controls showInteractive={false} />
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="#d9cdb8"
        />
      </ReactFlow>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function SharedCanvasPage({ token }: { token: string }) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    api
      .fetchSharedProject(token)
      .then((data) => {
        if (cancelled) return;
        setState({ kind: "ready", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("404")) {
          setState({ kind: "not_found" });
        } else {
          console.error("[Inspira] shared canvas load failed", err);
          setState({ kind: "error", message: msg });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // ---------- loading ----------
  // Render 4 TopicNodeSkeletons in roughly the spots populated canvases
  // tend to land — a soft "your canvas is opening" hint that survives
  // the network round-trip without flashing a single spinner. The
  // status text is kept (visually-hidden) so screen readers still hear
  // the loading announcement.
  if (state.kind === "loading") {
    return (
      <div
        className="shared-canvas-page__loading"
        aria-busy="true"
        aria-label={t("shared_view.loading")}
      >
        <div className="shared-canvas-page__skeleton-stage" aria-hidden="true">
          <TopicNodeSkeleton
            style={{ position: "absolute", top: "18%", left: "12%" }}
          />
          <TopicNodeSkeleton
            style={{ position: "absolute", top: "22%", right: "14%" }}
          />
          <TopicNodeSkeleton
            style={{ position: "absolute", bottom: "20%", left: "20%" }}
          />
          <TopicNodeSkeleton
            style={{ position: "absolute", bottom: "16%", right: "18%" }}
          />
        </div>
        <p className="visually-hidden">{t("shared_view.loading")}</p>
      </div>
    );
  }

  // ---------- not found / revoked ----------
  if (state.kind === "not_found") {
    return (
      <div className="shared-canvas-page__not-found">
        <h1>{t("shared_view.not_found_title")}</h1>
        <p>{t("shared_view.not_found_body")}</p>
        <a href="/" className="shared-canvas-page__cta">
          {t("shared_view.cta")}
        </a>
      </div>
    );
  }

  // ---------- unexpected error ----------
  if (state.kind === "error") {
    return (
      <div className="shared-canvas-page__error">
        <h1>{t("shared_view.error_title")}</h1>
        <p>{t("shared_view.error_body")}</p>
      </div>
    );
  }

  // ---------- ready ----------
  const { data } = state;

  return (
    <div className="shared-canvas-page">
      <header className="shared-canvas-page__header">
        <div className="shared-canvas-page__title-row">
          <span className="shared-canvas-page__brand">
            {t("app.brand")}
          </span>
          <span className="shared-canvas-page__project-title">
            {data.project.title}
          </span>
        </div>
        <div className="shared-canvas-page__attribution">
          {data.project.owner_display_name
            ? t("shared_view.shared_by", {
                name: data.project.owner_display_name,
              })
            : null}
        </div>
      </header>

      <main className="shared-canvas-page__canvas-area">
        <ReactFlowProvider>
          <SharedCanvas data={data} />
        </ReactFlowProvider>
      </main>

      <footer className="shared-canvas-page__footer">
        <span className="shared-canvas-page__read-only-badge">
          {t("shared_view.read_only_badge")}
        </span>
        <a href="/" className="shared-canvas-page__cta">
          {t("shared_view.cta")}
        </a>
      </footer>
    </div>
  );
}
