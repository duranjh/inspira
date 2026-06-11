// B2.3 — invisible controller that owns the global Promote-to-Project
// listener + dialog state.
//
// EAGER MOUNT REQUIRED — must be mounted as a direct sibling of
// <Routes /> in `routes.tsx`, not inside <App /> / <InspiraApp />.
// The /inbox route bypasses <App /> entirely (see routes.tsx:155),
// so a controller mounted lower in the tree would miss the dispatch
// when the user clicks Promote from the inbox drawer.
//
// On Promote success, navigates to /app with router state pointing at
// the new project + pendingReview marker. InspiraApp's bootstrap reads
// `useLocation().state` to open it.

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { FeedbackItem } from "../../inbox/types";
import {
  PromoteToProjectDialog,
  type ClusterSummary,
} from "./PromoteToProjectDialog";

interface PromoteEventDetail {
  feedbackItem: FeedbackItem;
  cluster?: ClusterSummary | null;
}

export function PromoteToProjectController() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [item, setItem] = useState<FeedbackItem | null>(null);
  const [cluster, setCluster] = useState<ClusterSummary | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onPromote = (e: Event) => {
      const detail = (e as CustomEvent<PromoteEventDetail>).detail;
      if (!detail?.feedbackItem) return;
      // Idempotent: ignore additional dispatches while the dialog is
      // already open. Spam-clicking the inbox drawer's button can fire
      // the event multiple times in quick succession.
      setOpen((prev) => {
        if (prev) return prev;
        setItem(detail.feedbackItem);
        setCluster(detail.cluster ?? null);
        return true;
      });
    };
    window.addEventListener("inspira:promote-to-project", onPromote);
    return () => {
      window.removeEventListener("inspira:promote-to-project", onPromote);
    };
  }, []);

  const handleClose = useCallback(() => {
    setOpen(false);
  }, []);

  const handlePromoted = useCallback(
    (projectId: string) => {
      setOpen(false);
      // react-router state — InspiraApp's bootstrap effect reads
      // useLocation().state and opens the new project automatically.
      navigate("/app", {
        state: { openProject: projectId, pendingReview: true },
      });
    },
    [navigate],
  );

  return (
    <PromoteToProjectDialog
      open={open}
      feedbackItem={item}
      cluster={cluster}
      onClose={handleClose}
      onPromoted={handlePromoted}
    />
  );
}
