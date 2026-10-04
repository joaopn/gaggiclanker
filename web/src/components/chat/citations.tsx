/**
 * Recognising the citations in an answer, so they can be links the reader can follow.
 *
 * The prompt asks for two forms and this recognises exactly those two: a shot
 * by id (`shot 129`), and a knowledge passage by its `heading_path`
 * (`SLUG#heading/sub`), which is a citation precisely because it survives a
 * re-seed. Nothing else is linkified — guessing at a third form would produce
 * links that go nowhere, and a citation the reader cannot check is worse than
 * plain text.
 *
 * Finding the citations is this module's whole job; turning them into links
 * inside a rendered answer is `markdown.tsx`, which calls `splitCitations`.
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

/** The numeric id in a `shot 129` citation. */
export function shotId(text: string): string {
  return (text.match(/\d+/) ?? ["0"])[0];
}
