// B2.3 / W3 δ — collapsible "Decisions preview" section inside the Promote
// dialog. Self-states its open/closed flag so the parent doesn't re-render
// on every toggle. Renders a static example (matching the B2.3 design
// HTML) and a deterministic preview list keyed off the topic seed names.

import { useState } from "react";

export interface DecisionsPreviewExpanderProps {
  /** Active topic-seed names (filtered to exclude removed seeds). */
  topicNames: string[];
}

export function DecisionsPreviewExpander({
  topicNames,
}: DecisionsPreviewExpanderProps) {
  const [open, setOpen] = useState(false);

  return (
    <section className="pm-block">
      <p className="pm-block__intro">
        Inspira will pre-populate decisions on each topic, sourced from the
        cluster&rsquo;s feedback items.
      </p>
      <div className="pm-block__example">
        <em>
          e.g., on &lsquo;Reproduce the bug&rsquo; — &lsquo;Use iOS 17.4 in
          Safari, attempt login from cold cache. Acme ticket #1247 confirms
          the trigger.&rsquo;
        </em>
      </div>
      <button
        type="button"
        className="pm-block__toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? "▾ Hide decision preview" : "▸ Show decision preview"}
      </button>
      {open ? (
        <ul className="pm-decisions__full">
          {topicNames.map((name, i) => (
            <li key={`${name}-${i}`} className="pm-decisions__item">
              <span className="pm-decisions__dot" aria-hidden="true" />
              <span className="pm-decisions__topic">{name}</span>
              <span className="pm-decisions__sep">—</span>
              <span className="pm-decisions__text">
                Inspira will draft an opening decision on this topic, citing
                the matched feedback items as it does.
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
