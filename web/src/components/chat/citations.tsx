import type { ReactNode } from "react";
import { Link } from "react-router-dom";

/**
 * Turning an answer's citations into links the reader can follow.
 *
 * The prompt asks for two forms and this recognises exactly those two: a shot
 * by id (`shot 129`), and a knowledge passage by its `heading_path`
 * (`SLUG#heading/sub`), which is a citation precisely because it survives a
 * re-seed. Nothing else is linkified — guessing at a third form would produce
 * links that go nowhere, and a citation the reader cannot check is worse than
 * plain text.
 *
 * Deliberately not a markdown renderer. The answers are short prose with
 * numbers in them; pulling in a markdown pipeline plus a sanitiser to render
 * text a language model wrote is a large attack surface for a small gain, and
 * the one thing markdown would buy — tables — is something the prompt asks for
 * rarely. Paragraphs, list items and inline code are handled here; everything
 * else renders as what it is.
 */

//: `shot 129`, `shots 129 and 130`. The word is required: a bare number in a
//: sentence about grind settings is not a shot id.
const SHOT = /\bshots?\s+#?(\d+)\b/gi;

//: A heading path: a document slug, a `#`, and a slash-separated heading trail.
//: Anchored on the `#` so an ordinary sentence cannot match.
const HEADING_PATH = /\b([A-Za-z0-9_-]+#[A-Za-z0-9_\-/.]+)/g;

export function knowledgeHref(headingPath: string): string {
  const slug = headingPath.split("#")[0];
  return `/knowledge?tab=docs&doc=${encodeURIComponent(slug)}&chunk=${encodeURIComponent(
    headingPath,
  )}`;
}

type Piece = { kind: "text" | "shot" | "path"; value: string };

/** Split one line into plain text, shot ids and heading paths, in order. */
export function splitCitations(line: string): Piece[] {
  const marks: { start: number; end: number; kind: "shot" | "path"; value: string }[] = [];
  for (const match of line.matchAll(SHOT)) {
    marks.push({
      start: match.index ?? 0,
      end: (match.index ?? 0) + match[0].length,
      kind: "shot",
      value: match[1],
    });
  }
  for (const match of line.matchAll(HEADING_PATH)) {
    marks.push({
      start: match.index ?? 0,
      end: (match.index ?? 0) + match[0].length,
      kind: "path",
      value: match[1],
    });
  }
  marks.sort((left, right) => left.start - right.start);

  const pieces: Piece[] = [];
  let cursor = 0;
  for (const mark of marks) {
    // Overlaps are possible in principle and a second link inside the first
    // would render as nested anchors; the earlier match wins.
    if (mark.start < cursor) continue;
    if (mark.start > cursor) pieces.push({ kind: "text", value: line.slice(cursor, mark.start) });
    pieces.push({ kind: mark.kind, value: line.slice(mark.start, mark.end) });
    cursor = mark.end;
  }
  if (cursor < line.length) pieces.push({ kind: "text", value: line.slice(cursor) });
  return pieces;
}

function shotId(text: string): string {
  return (text.match(/\d+/) ?? ["0"])[0];
}

/** One line of an answer, with its citations linked. */
export function CitedLine({ line }: { line: string }): ReactNode {
  return (
    <>
      {splitCitations(line).map((piece, index) => {
        const key = `${index}-${piece.value}`;
        if (piece.kind === "shot") {
          return (
            <Link
              key={key}
              to={`/shots/${shotId(piece.value)}`}
              className="underline decoration-dotted underline-offset-2 hover:text-primary"
            >
              {piece.value}
            </Link>
          );
        }
        if (piece.kind === "path") {
          return (
            <Link
              key={key}
              to={knowledgeHref(piece.value)}
              className="font-mono text-xs underline decoration-dotted underline-offset-2 hover:text-primary"
            >
              {piece.value}
            </Link>
          );
        }
        return <span key={key}>{piece.value}</span>;
      })}
    </>
  );
}

/**
 * An answer as blocks: paragraphs, bullets, and fenced code left alone.
 *
 * A blank line separates paragraphs, a leading `-` or `*` or `1.` is a bullet.
 * Inside a fence nothing is linkified — a heading path in a SQL comment is not
 * a citation the reader should be sent to.
 */
export function AnswerText({ text }: { text: string }): ReactNode {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let paragraph: string[] = [];
  let fence: string[] | null = null;

  const flush = () => {
    if (paragraph.length === 0) return;
    const body = paragraph;
    paragraph = [];
    const bullets = body.every((line) => /^\s*([-*]|\d+\.)\s+/.test(line));
    blocks.push(
      bullets ? (
        <ul key={`b${blocks.length}`} className="list-disc space-y-0.5 pl-5">
          {body.map((line) => (
            <li key={line}>
              <CitedLine line={line.replace(/^\s*([-*]|\d+\.)\s+/, "")} />
            </li>
          ))}
        </ul>
      ) : (
        <p key={`p${blocks.length}`} className="whitespace-pre-wrap">
          {/* One span per source line so a soft-wrapped paragraph keeps the
              newlines the model wrote. Keyed on the line itself: a repeated
              line inside one paragraph is vanishingly rare and a re-keyed
              re-render of a static block costs nothing. */}
          {body.map((line, index) => (
            <span key={line}>
              {index > 0 ? "\n" : null}
              <CitedLine line={line} />
            </span>
          ))}
        </p>
      ),
    );
  };

  for (const line of lines) {
    if (line.trimStart().startsWith("```")) {
      if (fence === null) {
        flush();
        fence = [];
      } else {
        blocks.push(
          <pre
            key={`f${blocks.length}`}
            className="overflow-x-auto rounded-md bg-muted p-2 font-mono text-xs"
          >
            {fence.join("\n")}
          </pre>,
        );
        fence = null;
      }
      continue;
    }
    if (fence !== null) {
      fence.push(line);
      continue;
    }
    if (line.trim() === "") flush();
    else paragraph.push(line);
  }
  if (fence !== null) {
    blocks.push(
      <pre
        key={`f${blocks.length}`}
        className="overflow-x-auto rounded-md bg-muted p-2 font-mono text-xs"
      >
        {fence.join("\n")}
      </pre>,
    );
  }
  flush();

  return <div className="space-y-2 text-sm leading-relaxed">{blocks}</div>;
}
