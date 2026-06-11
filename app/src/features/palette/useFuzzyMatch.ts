// A tiny in-order fuzzy matcher. Walks the candidate string and the query
// string together, scoring:
//   +10 per matched character (case-insensitive, in order)
//    +5 consecutive-match bonus (runs read as "word match" to a human)
//    -1 per skipped candidate character between matches
// Items whose query characters cannot all be matched in order are
// filtered out. Returns a sorted list (best first).
//
// `highlightedLabel` is the candidate string with matched chars wrapped
// in <mark> tags; the caller renders it via dangerouslySetInnerHTML.
// We escape the candidate's own HTML-significant characters first so a
// project titled "Q&A" or "<alpha>" does not open a hole in the DOM.

import { useMemo } from "react";

export type FuzzyResult<T> = {
  item: T;
  score: number;
  highlightedLabel: string;
};

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Lowercased single-pass match. Returns null when the query is not a
// subsequence of the candidate; otherwise returns the score plus the
// ordered indices of matched candidate characters (for highlighting).
function scoreOne(
  candidate: string,
  query: string,
): { score: number; matched: number[] } | null {
  if (query.length === 0) {
    return { score: 0, matched: [] };
  }
  const cand = candidate.toLowerCase();
  const q = query.toLowerCase();
  const matched: number[] = [];
  let ci = 0;
  let qi = 0;
  let score = 0;
  let skipped = 0;
  let lastMatchIdx = -2;
  while (ci < cand.length && qi < q.length) {
    if (cand[ci] === q[qi]) {
      score += 10;
      if (ci === lastMatchIdx + 1) {
        score += 5;
      }
      matched.push(ci);
      lastMatchIdx = ci;
      qi++;
    } else {
      score -= 1;
      skipped++;
    }
    ci++;
  }
  if (qi < q.length) {
    return null;
  }
  // Penalise remaining skipped tail only lightly; mostly we care about
  // density up to the last match, not the full trailing string.
  void skipped;
  return { score, matched };
}

function highlight(candidate: string, matched: number[]): string {
  if (matched.length === 0) {
    return escapeHtml(candidate);
  }
  const set = new Set(matched);
  let out = "";
  let run = false;
  for (let i = 0; i < candidate.length; i++) {
    const isMatch = set.has(i);
    if (isMatch && !run) {
      out += "<mark>";
      run = true;
    } else if (!isMatch && run) {
      out += "</mark>";
      run = false;
    }
    out += escapeHtml(candidate[i] ?? "");
  }
  if (run) {
    out += "</mark>";
  }
  return out;
}

export function useFuzzyMatch<T>(
  items: T[],
  query: string,
  toString: (item: T) => string,
): FuzzyResult<T>[] {
  return useMemo(() => {
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      // No query: return everything in its original order, no highlights.
      return items.map((item) => ({
        item,
        score: 0,
        highlightedLabel: escapeHtml(toString(item)),
      }));
    }
    const out: FuzzyResult<T>[] = [];
    for (const item of items) {
      const label = toString(item);
      const scored = scoreOne(label, trimmed);
      if (scored === null) continue;
      out.push({
        item,
        score: scored.score,
        highlightedLabel: highlight(label, scored.matched),
      });
    }
    out.sort((a, b) => b.score - a.score);
    return out;
  }, [items, query, toString]);
}
