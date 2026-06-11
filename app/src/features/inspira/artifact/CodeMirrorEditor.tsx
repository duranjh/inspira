import { ReactElement, useEffect, useRef } from "react";
import { EditorState, Compartment } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import {
  syntaxHighlighting,
  defaultHighlightStyle,
  bracketMatching,
  indentOnInput,
} from "@codemirror/language";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";

/**
 * Lightweight CodeMirror 6 wrapper for the artifact viewer.
 *
 * ~200KB total bundle for syntax highlighting + multi-cursor +
 * find/replace + line numbers — vs Monaco's 5MB+. Self-hosted by
 * Vite (no CDN round-trip).
 *
 * Replaces an earlier Monaco prototype that was too heavy for the
 * artifact viewer's first-paint budget.
 *
 * Lifecycle:
 *  - Mounts a single EditorView in a div ref on first render.
 *  - On `value` prop change, dispatch a transaction to replace the
 *    document iff the prop differs from the editor's current text
 *    (prevents the cursor from jumping when the parent re-renders
 *    after the user's own keystrokes).
 *  - On `language` change, reconfigure the language compartment
 *    (no full re-init).
 *  - On unmount, destroy the view.
 */
export interface CodeMirrorEditorProps {
  value: string;
  language: string;
  readOnly?: boolean;
  onChange?: (value: string) => void;
  /** Wave F.4 — receive the live EditorView for overlay components
   *  (CommentChipGutter) that need ``coordsAtPos``/``posAtCoords`` to
   *  align with line geometry. Fires once on mount and on unmount
   *  with ``null``. */
  onViewReady?: (view: EditorView | null) => void;
  /** Wave F.4 — invoked whenever the editor view updates (doc edits,
   *  scrolls, geometry changes). Lets the overlay re-anchor chips
   *  without doing its own polling. */
  onViewUpdate?: () => void;
}

function languageExtension(lang: string) {
  switch (lang) {
    case "typescript":
      return javascript({ typescript: true, jsx: true });
    case "javascript":
      return javascript({ typescript: false, jsx: true });
    case "json":
      return json();
    case "css":
    case "scss":
      return css();
    case "html":
      return html();
    case "markdown":
      return markdown();
    case "python":
      return python();
    default:
      return [];
  }
}

export function CodeMirrorEditor(props: CodeMirrorEditorProps): ReactElement {
  const { value, language, readOnly, onChange, onViewReady, onViewUpdate } =
    props;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const langCompartment = useRef(new Compartment());
  const readOnlyCompartment = useRef(new Compartment());
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const onViewUpdateRef = useRef(onViewUpdate);
  onViewUpdateRef.current = onViewUpdate;
  const onViewReadyRef = useRef(onViewReady);
  onViewReadyRef.current = onViewReady;

  useEffect(() => {
    if (!hostRef.current) return;
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        highlightActiveLine(),
        history(),
        bracketMatching(),
        indentOnInput(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        EditorView.lineWrapping,
        langCompartment.current.of(languageExtension(language)),
        readOnlyCompartment.current.of(EditorState.readOnly.of(!!readOnly)),
        EditorView.theme(
          {
            "&": {
              fontFamily: "var(--ff-mono), Menlo, monospace",
              fontSize: "12px",
              height: "100%",
            },
            ".cm-scroller": {
              fontFamily: "var(--ff-mono), Menlo, monospace",
              lineHeight: "1.65",
              overflow: "auto",
            },
            ".cm-content": {
              padding: "12px 0",
            },
            ".cm-gutters": {
              backgroundColor: "transparent",
              borderRight: "1px solid var(--paper-edge, #e5e0d4)",
              fontFamily: "var(--ff-serif), Georgia, serif",
              fontStyle: "italic",
              color: "var(--ink-5, #b8aa92)",
              minWidth: "44px",
            },
            ".cm-activeLine": {
              backgroundColor: "rgba(120, 130, 140, 0.06)",
            },
            ".cm-activeLineGutter": {
              backgroundColor: "transparent",
              color: "var(--ink-2, #4a5562)",
            },
          },
          { dark: false },
        ),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            const next = update.state.doc.toString();
            onChangeRef.current?.(next);
          }
          if (
            update.docChanged ||
            update.geometryChanged ||
            update.viewportChanged
          ) {
            onViewUpdateRef.current?.();
          }
        }),
      ],
    });
    const view = new EditorView({ state, parent: hostRef.current });
    viewRef.current = view;
    onViewReadyRef.current?.(view);
    const scrollEl = view.scrollDOM;
    const handleScroll = (): void => {
      onViewUpdateRef.current?.();
    };
    scrollEl.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      scrollEl.removeEventListener("scroll", handleScroll);
      onViewReadyRef.current?.(null);
      view.destroy();
      viewRef.current = null;
    };
    // We intentionally only init once; subsequent prop changes flow
    // through reconfiguration effects below to avoid resetting the
    // user's cursor on every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync `value` prop → editor doc when it diverges (e.g. file
  // switch in the parent file tree).
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
  }, [value]);

  // Swap language extension when the file's language changes.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: langCompartment.current.reconfigure(languageExtension(language)),
    });
  }, [language]);

  // Toggle read-only when project state changes (Draft → In Review).
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: readOnlyCompartment.current.reconfigure(
        EditorState.readOnly.of(!!readOnly),
      ),
    });
  }, [readOnly]);

  return (
    <div
      ref={hostRef}
      className="av-codemirror"
      style={{ width: "100%", height: "100%", overflow: "hidden" }}
    />
  );
}
