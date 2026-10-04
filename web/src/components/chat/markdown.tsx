import { type ComponentProps, type ReactNode, useId } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import { Link } from "react-router-dom";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { knowledgeHref, shotId, splitCitations } from "@/components/chat/citations";
import { cn } from "@/lib/utils";

/**
 * Markdown for what a language model wrote: chat answers (`AnswerText`) and
 * the short prose fields on the cards the agent fills in (`InlineMarkdown`).
 *
 * The prompts tell the model that markdown is fine, so bold, code, headings,
 * lists, quotes, links and GFM tables arrive as markdown and are rendered as
 * such. What makes that safe to do with text nobody reviewed:
 *
 * - No raw HTML. There is no `rehype-raw` and nothing sets inner HTML, so a
 *   `<script>` or `<img onerror>` in an answer is dropped, never an element.
 * - react-markdown's default URL transform stays: `javascript:` and similar
 *   schemes lose their `href`.
 * - Images are never fetched. An image becomes a link to its URL (see
 *   `imagesToLinks`), because model-written text must not make the browser
 *   request arbitrary addresses.
 * - Citations (`shot 129`, `SLUG#heading/path`) are still links, added by a
 *   remark plugin over the parsed tree (`linkCitations`) rather than over the
 *   source text. It skips fenced code (a heading path in a SQL comment is not
 *   a citation), existing links (no nested anchors) and inline code unless the
 *   code is exactly one citation.
 * - A single newline inside a paragraph is a line break (`remark-breaks`),
 *   as the answers were shown before markdown: the model writes short lines
 *   and means them.
 *
 * Partial text is fine: an answer is rendered while it streams, and an
 * unclosed `**` or a half-written table just renders as what has arrived.
 */

type MdNode = {
  type: string;
  value?: string;
  url?: string;
  alt?: string | null;
  children?: MdNode[];
  data?: object;
};

const LINK_CLASS = "underline decoration-dotted underline-offset-2 hover:text-primary";
const CODE_LINK_CLASS = "font-mono text-xs";

function citationLink(kind: "shot" | "path", value: string, extra?: string): MdNode {
  return {
    type: "link",
    url: kind === "shot" ? `/shots/${shotId(value)}` : knowledgeHref(value),
    children: [{ type: "text", value }],
    data: { hProperties: { className: cn(kind === "path" && CODE_LINK_CLASS, extra) } },
  };
}

// Containers whose text must not gain an anchor of its own (no nested links).
// Fenced code and images are leaves, so the walk never reads their text.
const NO_CITATIONS = new Set(["link", "linkReference"]);

function linkChildren(children: MdNode[]): MdNode[] {
  const out: MdNode[] = [];
  for (const child of children) {
    if (child.type === "text" && child.value) {
      for (const piece of splitCitations(child.value)) {
        out.push(
          piece.kind === "text"
            ? { type: "text", value: piece.value }
            : citationLink(piece.kind, piece.value),
        );
      }
    } else if (child.type === "inlineCode" && child.value) {
      const whole = child.value.trim();
      const pieces = splitCitations(whole);
      const only = pieces.length === 1 ? pieces[0] : null;
      out.push(
        only && only.kind !== "text" && only.value === whole
          ? citationLink(only.kind, whole, "font-mono text-xs")
          : child,
      );
    } else {
      if (child.children && !NO_CITATIONS.has(child.type)) {
        child.children = linkChildren(child.children);
      }
      out.push(child);
    }
  }
  return out;
}

/** remark plugin: link the citations in prose, and nowhere else. */
function linkCitations() {
  return (tree: MdNode) => {
    if (tree.children) tree.children = linkChildren(tree.children);
  };
}

function imageLabel(node: MdNode): string {
  return node.alt?.trim() || node.url || "image";
}

function imagesIn(children: MdNode[], inLink: boolean): MdNode[] {
  return children.map((child) => {
    if (child.type === "image") {
      // Inside a link an anchor would nest; the alt text stands in.
      return inLink || !child.url
        ? { type: "text", value: imageLabel(child) }
        : { type: "link", url: child.url, children: [{ type: "text", value: imageLabel(child) }] };
    }
    if (child.type === "imageReference") return { type: "text", value: imageLabel(child) };
    if (child.children) {
      child.children = imagesIn(
        child.children,
        inLink || child.type === "link" || child.type === "linkReference",
      );
    }
    return child;
  });
}

/** remark plugin: an image becomes a link to it; nothing is ever fetched. */
function imagesToLinks() {
  return (tree: MdNode) => {
    if (tree.children) tree.children = imagesIn(tree.children, false);
  };
}

const REMARK_PLUGINS: Options["remarkPlugins"] = [
  remarkGfm,
  remarkBreaks,
  imagesToLinks,
  linkCitations,
];

type Passed<T extends keyof React.JSX.IntrinsicElements> = ComponentProps<T> & {
  node?: unknown;
};

function Anchor({ node: _node, href, className, children, ...rest }: Passed<"a">) {
  // A blocked scheme (`javascript:`, `data:`) comes through as an empty href:
  // the words stay, the link does not.
  if (!href) return <>{children}</>;
  const classes = cn(LINK_CLASS, className);
  if (href.startsWith("#")) {
    // A footnote reference or its way back: same page, no new tab, no router.
    // Its `aria-describedby` names the label whose id was dropped above.
    const { "aria-describedby": _label, ...keep } = rest;
    return (
      <a {...keep} href={href} className={classes}>
        {children}
      </a>
    );
  }
  if (href.startsWith("/") && !href.startsWith("//")) {
    return (
      <Link to={href} className={classes}>
        {children}
      </Link>
    );
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={classes}>
      {children}
    </a>
  );
}

function alignOf(props: { align?: unknown; style?: { textAlign?: string } }) {
  const align = props.align ?? props.style?.textAlign;
  return align === "left" || align === "right" || align === "center" ? align : undefined;
}

/** The text of a fenced block, without react-markdown's closing newline. */
function fenceText(node: unknown): string {
  type H = { type?: string; value?: string; children?: H[] };
  const walk = (n: H): string =>
    n.type === "text" ? (n.value ?? "") : (n.children ?? []).map(walk).join("");
  return walk(node as H).replace(/\n$/, "");
}

const BLOCK_COMPONENTS: Components = {
  a: Anchor,
  // A fenced block is plain text in a `pre`: no `code` element, so the inline
  // code styling cannot double up inside it.
  pre: ({ node }) => (
    <pre className="overflow-x-auto rounded-md bg-muted p-2 font-mono text-xs">
      {fenceText(node)}
    </pre>
  ),
  code: ({ node: _node, className, children }) => (
    <code className={cn("rounded bg-muted px-1 py-0.5 font-mono text-xs", className)}>
      {children}
    </code>
  ),
  ul: ({ node: _node, className, children }) => (
    <ul
      className={cn(
        "space-y-0.5 [li_&]:mt-0.5",
        className?.includes("contains-task-list") ? "list-none pl-0" : "list-disc pl-5",
      )}
    >
      {children}
    </ul>
  ),
  // `start` matters: "3. third" must read 3, and a list a fence interrupted
  // continues where it was.
  ol: ({ node: _node, className, children, ...rest }) => (
    <ol {...rest} className={cn("list-decimal space-y-0.5 pl-5 [li_&]:mt-0.5", className)}>
      {children}
    </ol>
  ),
  h1: ({ node: _node, className, children }) => (
    <h1 className={cn("font-semibold text-base", className)}>{children}</h1>
  ),
  // react-markdown's footnote label is an `sr-only` h2; its fixed id would
  // repeat for every answer on the page, so it is dropped.
  h2: ({ node: _node, className, children }) => (
    <h2 className={cn("font-semibold text-base", className)}>{children}</h2>
  ),
  h3: ({ node: _node, className, children }) => (
    <h3 className={cn("font-semibold text-sm", className)}>{children}</h3>
  ),
  h4: ({ node: _node, className, children }) => (
    <h4 className={cn("font-semibold text-sm", className)}>{children}</h4>
  ),
  h5: ({ node: _node, className, children }) => (
    <h5 className={cn("font-semibold text-sm", className)}>{children}</h5>
  ),
  h6: ({ node: _node, className, children }) => (
    <h6 className={cn("font-semibold text-sm", className)}>{children}</h6>
  ),
  blockquote: ({ node: _node, children }) => (
    <blockquote className="space-y-2 border-l-2 pl-3 text-muted-foreground">{children}</blockquote>
  ),
  hr: () => <hr className="border-border" />,
  // A wide table scrolls inside the bubble instead of widening the page.
  table: ({ node: _node, children }) => (
    <div className="overflow-x-auto">
      <table className="border-collapse text-xs [overflow-wrap:normal]">{children}</table>
    </div>
  ),
  th: ({ node: _node, children, ...rest }: Passed<"th">) => (
    <th
      className="border bg-muted px-2 py-1 text-left font-medium"
      style={{ textAlign: alignOf(rest) }}
    >
      {children}
    </th>
  ),
  td: ({ node: _node, children, ...rest }: Passed<"td">) => (
    <td className="border px-2 py-1" style={{ textAlign: alignOf(rest) }}>
      {children}
    </td>
  ),
  // Never reached for a parsed image (they become links first); a safety net
  // so no path can emit an `img`.
  img: ({ node: _node, alt }) => <span>{alt}</span>,
};

/** An answer as markdown blocks, with citations linked. */
export function AnswerText({ text }: { text: string }): ReactNode {
  // Footnote ids are page-wide; one prefix per answer keeps two answers (or
  // a stored one and the live one) from sharing them. `:` is dropped so the
  // id is also a valid selector.
  const prefix = `${useId().replace(/:/g, "")}-`;
  return (
    // A long URL or code span wraps instead of scrolling the transcript.
    <div className="space-y-2 text-sm leading-relaxed [overflow-wrap:anywhere]">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        remarkRehypeOptions={{ clobberPrefix: prefix }}
        components={BLOCK_COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

// Only these survive in `InlineMarkdown`; the rest are unwrapped to their text.
const INLINE_ELEMENTS = [
  "p",
  "li",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "strong",
  "em",
  "del",
  "code",
  "a",
  "br",
];

// A block's text is kept and set off with a space, so "- a\n- b" reads "a b".
const spaced = ({ children }: { children?: ReactNode }) => <>{children} </>;

const INLINE_COMPONENTS: Components = {
  a: Anchor,
  code: BLOCK_COMPONENTS.code,
  p: spaced,
  li: spaced,
  h1: spaced,
  h2: spaced,
  h3: spaced,
  h4: spaced,
  h5: spaced,
  h6: spaced,
};

/**
 * remark plugin for the inline variant: a card's line has no room for a
 * footnotes section, so definitions are dropped and a reference reads as its
 * label.
 */
function dropFootnotes() {
  const walk = (children: MdNode[]): MdNode[] =>
    children
      .filter((child) => child.type !== "footnoteDefinition")
      .map((child) => {
        if (child.type === "footnoteReference") {
          const label = (child as MdNode & { label?: string; identifier?: string }).label;
          return { type: "text", value: `[${label ?? ""}]` };
        }
        if (child.children) child.children = walk(child.children);
        return child;
      });
  return (tree: MdNode) => {
    if (tree.children) tree.children = walk(tree.children);
  };
}

const INLINE_PLUGINS: Options["remarkPlugins"] = [
  remarkGfm,
  dropFootnotes,
  remarkBreaks,
  imagesToLinks,
  linkCitations,
];

/**
 * Inline markdown for a field the agent wrote into a card (a proposal's
 * reason, an insight's text): bold, italics, code, links and citations, with
 * no block wrappers, so it sits in the card's own paragraph. A heading or a
 * list in the text keeps its words and loses its block.
 */
export function InlineMarkdown({ text }: { text: string }): ReactNode {
  return (
    <span className="[overflow-wrap:anywhere]">
      <ReactMarkdown
        remarkPlugins={INLINE_PLUGINS}
        components={INLINE_COMPONENTS}
        allowedElements={INLINE_ELEMENTS}
        unwrapDisallowed
      >
        {text}
      </ReactMarkdown>
    </span>
  );
}
