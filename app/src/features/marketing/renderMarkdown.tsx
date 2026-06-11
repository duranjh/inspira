// Inspira — ultra-thin markdown renderer for the legal + about pages.
//
// Handles the small subset of CommonMark the legal docs actually use:
//   - `#`, `##`, `###` headings
//   - Paragraphs (blank-line separated)
//   - `-` / `*` bullet lists
//   - Inline `**bold**` and `[text](url)` links
//   - Blockquote lines starting with `>` (rendered as <blockquote>)
//   - Horizontal rule (`---` alone on a line)
//   - Fenced ``` code blocks (rendered as <pre><code>)
// Tables, images, nested lists, and inline code are not handled — the
// legal docs don't use them. If that changes, add here, not a new dep.
//
// Everything rendered is escaped; the only HTML that makes it into the
// DOM is produced by this file. Links get `rel="noopener"` unless they
// are same-origin-relative.

import { Fragment, type JSX, type ReactNode } from "react";

type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "blockquote"; text: string }
  | { kind: "code"; text: string }
  | { kind: "hr" };

function tokenize(src: string): Block[] {
  const blocks: Block[] = [];
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") {
      i++;
      continue;
    }

    // Fenced code block
    if (line.startsWith("```")) {
      i++;
      const buf: string[] = [];
      while (i < lines.length && !lines[i].startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // closing fence
      blocks.push({ kind: "code", text: buf.join("\n") });
      continue;
    }

    // Horizontal rule
    if (/^---+\s*$/.test(line)) {
      blocks.push({ kind: "hr" });
      i++;
      continue;
    }

    // Headings
    const hMatch = /^(#{1,3})\s+(.+?)\s*$/.exec(line);
    if (hMatch) {
      const level = hMatch[1].length as 1 | 2 | 3;
      blocks.push({ kind: "heading", level, text: hMatch[2] });
      i++;
      continue;
    }

    // Bullet list (consume contiguous - or * lines)
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      blocks.push({ kind: "list", items });
      continue;
    }

    // Blockquote
    if (/^>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      blocks.push({ kind: "blockquote", text: buf.join(" ") });
      continue;
    }

    // Paragraph: join consecutive non-empty lines that aren't another block
    const buf: string[] = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !lines[i].startsWith("```") &&
      !/^#{1,3}\s+/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^>\s?/.test(lines[i]) &&
      !/^---+\s*$/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i++;
    }
    blocks.push({ kind: "paragraph", text: buf.join(" ") });
  }

  return blocks;
}

// Inline: escape then apply **bold** and [text](url). Because React handles
// text content safely already, the escape is effectively a no-op at the
// DOM layer, but we still strip raw angle brackets defensively so markdown
// with accidental "<script>" doesn't render as structural HTML if someone
// later changes the plumbing to use dangerouslySetInnerHTML.
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const cleaned = text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // We work on a token list, splitting by the two patterns we care about.
  type Token =
    | { kind: "text"; text: string }
    | { kind: "bold"; text: string }
    | { kind: "link"; text: string; href: string };
  const tokens: Token[] = [];

  // Match either [text](url) or **bold**. Global regex, we walk matches.
  const re = /\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(cleaned)) !== null) {
    if (m.index > last) {
      tokens.push({ kind: "text", text: cleaned.slice(last, m.index) });
    }
    if (m[1] !== undefined && m[2] !== undefined) {
      tokens.push({ kind: "link", text: m[1], href: m[2] });
    } else if (m[3] !== undefined) {
      tokens.push({ kind: "bold", text: m[3] });
    }
    last = m.index + m[0].length;
  }
  if (last < cleaned.length) {
    tokens.push({ kind: "text", text: cleaned.slice(last) });
  }

  // Decode the &lt; / &gt; back to chars when rendering as text, so that
  // a document containing, say, "1 < 2" survives round-tripping.
  const decode = (s: string) => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">");

  return tokens.map((tok, idx) => {
    const key = `${keyPrefix}-${idx}`;
    if (tok.kind === "text") return <Fragment key={key}>{decode(tok.text)}</Fragment>;
    if (tok.kind === "bold") return <strong key={key}>{decode(tok.text)}</strong>;
    // Validate the href against an allowlist of URL schemes. The inline
    // regex captures any `[text](href)` pair, so a hostile markdown source
    // could otherwise emit `[click](javascript:alert(1))` and ship
    // executable scheme-URIs into the DOM. Only https?, mailto, and local
    // paths (starting with `/` or `#`) render as a real link; everything
    // else (javascript:, data:, vbscript:, etc.) falls back to a plain
    // text span so the user still sees the label without the exploit.
    const href = tok.href;
    const isSafe =
      /^https?:\/\//i.test(href) ||
      /^mailto:/i.test(href) ||
      href.startsWith("/") ||
      href.startsWith("#");
    if (!isSafe) {
      return <Fragment key={key}>{decode(tok.text)}</Fragment>;
    }
    const isExternal = /^https?:\/\//i.test(href);
    return (
      <a
        key={key}
        href={href}
        {...(isExternal
          ? { target: "_blank", rel: "noopener noreferrer" }
          : {})}
      >
        {decode(tok.text)}
      </a>
    );
  });
}

/**
 * Turn a markdown string into a React tree. Returns a fragment of block
 * elements (headings, paragraphs, lists, …) suitable for dropping inside
 * a prose container.
 */
export function renderMarkdown(src: string): JSX.Element {
  const blocks = tokenize(src);
  return (
    <>
      {blocks.map((block, idx) => {
        const key = `b-${idx}`;
        if (block.kind === "heading") {
          if (block.level === 1) return <h1 key={key}>{renderInline(block.text, key)}</h1>;
          if (block.level === 2) return <h2 key={key}>{renderInline(block.text, key)}</h2>;
          return <h3 key={key}>{renderInline(block.text, key)}</h3>;
        }
        if (block.kind === "paragraph") {
          return <p key={key}>{renderInline(block.text, key)}</p>;
        }
        if (block.kind === "list") {
          return (
            <ul key={key}>
              {block.items.map((item, iidx) => (
                <li key={`${key}-${iidx}`}>{renderInline(item, `${key}-${iidx}`)}</li>
              ))}
            </ul>
          );
        }
        if (block.kind === "blockquote") {
          return (
            <blockquote key={key}>
              {renderInline(block.text, key)}
            </blockquote>
          );
        }
        if (block.kind === "code") {
          return (
            <pre key={key}>
              <code>{block.text}</code>
            </pre>
          );
        }
        return <hr key={key} />;
      })}
    </>
  );
}

export default renderMarkdown;
