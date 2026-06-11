// Thin wrapper over POST /api/feedback.
//
// TODO(backend): wire the real route on planning_studio_service.
// Until then the fetch 404s and the widget surfaces a "coming soon"
// style inline toast. The wrapper keeps the widget itself network-layer
// agnostic so the real route drops in with no call-site changes.

import type { FeedbackSubmission } from "../inspira/api";

const DEFAULT_BASE_URL =
  (import.meta.env.VITE_INSPIRA_API_URL as string | undefined) ??
  "http://127.0.0.1:4174";

export type FeedbackResponse = {
  submitted: boolean;
};

export async function submitFeedback(
  payload: FeedbackSubmission,
): Promise<FeedbackResponse> {
  const path = "/api/feedback";
  const res = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    credentials: "include",
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `POST ${path} failed: ${res.status} ${res.statusText} — ${detail}`,
    );
  }
  return res.json() as Promise<FeedbackResponse>;
}
