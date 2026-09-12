import { AlertTriangle, ArrowLeft, BookOpen, RotateCcw, Search } from "lucide-react";
import { useEffect, useState } from "react";
import type { KnowledgeChunk, KnowledgeChunkHit } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useKnowledgeDoc,
  useKnowledgeDocs,
  useKnowledgeSearch,
  useResetKnowledgeDoc,
  useSaveKnowledgeDoc,
} from "@/hooks/useKnowledge";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * Tier 2: the prose the analyzer quotes from, and the search over it.
 *
 * Two things are on screen for a reason. The **chunks**, not just the markdown:
 * retrieval sees chunks, an analysis cites one by `heading_path`, and "why did
 * my edit split that section in two" is otherwise unanswerable. And the
 * **search**, because it is the same BM25 call the analyzer makes — typing the
 * words a shot would produce is how you find out what the model will be shown.
 */
export function DocsTab({
  slug,
  onOpen,
  highlightedChunk,
}: {
  /** The open document, or `null` for the directory. Comes from `?doc=` so a
   *  citation in an analysis deep-links straight to the passage. */
  slug: string | null;
  /** `chunk` is the heading path to open at, so a search hit lands on the
   *  passage rather than on the top of the document. */
  onOpen: (slug: string | null, chunk?: string) => void;
  /** A `heading_path` to scroll to and mark. From `?chunk=`. */
  highlightedChunk?: string | null;
}) {
  const [query, setQuery] = useState("");
  const docs = useKnowledgeDocs();
  const search = useKnowledgeSearch(query);
  useQueryErrorToast(docs.error, "Could not load the knowledge documents");

  if (slug) {
    return <DocView slug={slug} onBack={() => onOpen(null)} highlighted={highlightedChunk} />;
  }

  const items = docs.data?.items ?? [];
  const chunks = items.reduce((total, doc) => total + doc.chunk_count, 0);
  const tokens = items.reduce((total, doc) => total + doc.tokens_estimate, 0);

  return (
    <div className="space-y-4">
      <SectionCard
        title="Search the knowledge base"
        description="The same BM25 search the analyser runs when it picks excerpts for a shot. Headings are weighted above bodies, and the words are stemmed — 'channeling' finds 'channel'."
      >
        <div className="relative">
          <Search
            className="-translate-y-1/2 absolute top-1/2 left-2 size-3.5 text-muted-foreground"
            aria-hidden="true"
          />
          <input
            className="h-9 w-full rounded-md border border-input bg-background pl-7 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="sour and bitter, pressure decline, bloom…"
            aria-label="Search the knowledge base"
            data-testid="doc-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        {query.trim().length > 1 ? (
          search.isPending ? (
            <Skeleton className="mt-3 h-24 w-full" />
          ) : (
            <ul className="mt-3 space-y-2" data-testid="search-results">
              {(search.data?.items ?? []).map((hit) => (
                <SearchHit key={hit.chunk.heading_path} hit={hit} query={query} onOpen={onOpen} />
              ))}
              {/* A failed search and an empty one are different answers, and
                  reporting the first as the second is how somebody concludes
                  the knowledge base does not mention channeling. */}
              {search.isError ? (
                <li
                  className="flex items-center gap-1.5 text-sm text-status-bad-text"
                  data-testid="search-error"
                >
                  <AlertTriangle className="size-3.5" aria-hidden="true" />
                  The search failed: {search.error.message}
                </li>
              ) : (search.data?.items ?? []).length === 0 ? (
                <li className="text-muted-foreground text-sm">Nothing matched those words.</li>
              ) : null}
            </ul>
          )
        ) : null}
      </SectionCard>

      <SectionCard
        title="Documents"
        description={
          docs.isPending
            ? "Loading…"
            : `${items.length} documents, ${chunks} chunks, about ${tokens.toLocaleString()} tokens in total — of which one analysis is given a couple of thousand.`
        }
      >
        {docs.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <ul className="divide-y divide-border" data-testid="doc-list">
            {items.map((doc) => (
              <li key={doc.slug}>
                <button
                  type="button"
                  data-testid="doc-row"
                  data-doc={doc.slug}
                  onClick={() => onOpen(doc.slug)}
                  className="flex w-full flex-wrap items-baseline justify-between gap-2 px-1 py-2 text-left hover:bg-accent/50"
                >
                  <span className="flex flex-wrap items-baseline gap-2">
                    <span className="font-medium text-sm">{doc.title || doc.slug}</span>
                    <span className="font-mono text-muted-foreground text-xs">{doc.slug}</span>
                    {doc.edited ? <Badge variant="secondary">edited</Badge> : null}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {doc.chunk_count} chunk{doc.chunk_count === 1 ? "" : "s"} ·{" "}
                    {doc.tokens_estimate.toLocaleString()} tokens
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}

function SearchHit({
  hit,
  query,
  onOpen,
}: {
  hit: KnowledgeChunkHit;
  query: string;
  onOpen: (slug: string, chunk?: string) => void;
}) {
  return (
    <li className="rounded-lg border border-border p-2" data-testid="search-hit">
      <button
        type="button"
        className="text-left font-mono text-xs hover:underline"
        // The chunk, not just the document: a hit found one passage in a
        // twenty-chunk file, and opening at the top makes the reader find it
        // again by eye.
        onClick={() => onOpen(hit.chunk.doc_slug, hit.chunk.heading_path)}
      >
        {hit.chunk.heading_path}
      </button>
      {hit.chunk.heading ? (
        <p className="mt-0.5 text-muted-foreground text-xs">{hit.chunk.heading}</p>
      ) : null}
      <p className="mt-1 text-sm">{highlight(hit.snippet, query)}</p>
    </li>
  );
}

/**
 * A heading path as a DOM id.
 *
 * `#` and `/` are legal in an id attribute but not in the CSS selector a
 * `querySelector` would build from one, and `#` in particular reads as a
 * fragment everywhere else. Encoded rather than hashed so the id is still
 * greppable against the citation it came from.
 */
function anchorId(headingPath: string): string {
  return `chunk-${headingPath.replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
}

/**
 * The snippet with the searched words marked.
 *
 * Done here rather than server-side because the server's snippet is the body as
 * written — FTS5's own `snippet()` marks up the *stemmed* text, which is not
 * what the reader is looking at. Splitting on the terms the user typed keeps
 * the marking honest about what they asked for.
 */
function highlight(text: string, query: string) {
  const terms = query.match(/[0-9a-zA-Z]+/g) ?? [];
  if (terms.length === 0) return text;
  // A capturing group, so `split` interleaves the matches into the result and
  // the odd indices are exactly the parts that matched. Testing each part
  // against the pattern instead would be wrong as well as redundant: a `/g`
  // regex carries `lastIndex` between calls to `test`, so it would mark every
  // other hit.
  const pattern = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi");
  return text.split(pattern).map((part, index) =>
    index % 2 === 1 ? (
      // biome-ignore lint/suspicious/noArrayIndexKey: split() output has no identity of its own
      <mark key={index} className="rounded bg-accent px-0.5 text-accent-foreground">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * One document: what it says, what retrieval sees, and an editor.
 *
 * An edited document keeps the user's text through every later re-seed; only
 * its shipped default moves. That is what makes editing safe to do, and it is
 * why "reset" restores the *current* shipped wording rather than the version
 * they forked from.
 */
function DocView({
  slug,
  onBack,
  highlighted,
}: {
  slug: string;
  onBack: () => void;
  highlighted?: string | null;
}) {
  const detail = useKnowledgeDoc(slug);
  const save = useSaveKnowledgeDoc();
  const reset = useResetKnowledgeDoc();
  const [draft, setDraft] = useState<string | null>(null);
  useQueryErrorToast(detail.error, `Could not load ${slug}`);

  // Scroll the cited passage into view once the chunks are on screen. A
  // citation lands on a document of twenty chunks; without this the link is
  // only half of what it promised, and the reader hunts for the heading path
  // they just clicked.
  const arrived = detail.data !== undefined;
  useEffect(() => {
    if (!arrived || !highlighted) return;
    document
      .getElementById(anchorId(highlighted))
      ?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [arrived, highlighted]);

  if (detail.isPending) return <Skeleton className="h-96 w-full" />;
  if (!detail.data) {
    return (
      <EmptyState
        icon={BookOpen}
        title="No such document"
        description={`Nothing here is called ${slug}.`}
        action={<Button onClick={onBack}>Back to the list</Button>}
      />
    );
  }

  const { doc, chunks } = detail.data;
  const editing = draft !== null;

  return (
    <div className="space-y-4">
      <SectionCard
        title={doc.title || doc.slug}
        description={`${doc.source}${doc.licence ? ` · ${doc.licence}` : ""} — ${doc.attribution}`}
        actions={
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={onBack} data-testid="back-to-docs">
              <ArrowLeft className="size-3.5" aria-hidden="true" />
              All documents
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setDraft(editing ? null : doc.body)}
              data-testid="edit-doc"
            >
              {editing ? "Cancel" : "Edit"}
            </Button>
            {doc.edited ? (
              <Button
                size="sm"
                variant="outline"
                disabled={reset.isPending}
                data-testid="reset-doc"
                onClick={async () => {
                  const restored = await attempt(() => reset.mutateAsync(doc.slug));
                  if (restored) setDraft(null);
                }}
              >
                <RotateCcw className="size-3.5" aria-hidden="true" />
                Reset
              </Button>
            ) : null}
          </div>
        }
      >
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="font-mono text-muted-foreground text-xs">{doc.slug}</span>
          {doc.edited ? <Badge variant="secondary">edited</Badge> : null}
          <span className="text-muted-foreground text-xs">
            {chunks.length} chunk{chunks.length === 1 ? "" : "s"}
          </span>
        </div>
      </SectionCard>

      {editing ? (
        <SectionCard
          title="Markdown"
          description="Saving re-splits the document at its H2 and H3 headings, so moving a heading moves every citation below it."
        >
          <form
            className="space-y-2"
            onSubmit={async (event) => {
              event.preventDefault();
              const saved = await attempt(() =>
                save.mutateAsync({ slug: doc.slug, markdown: draft }),
              );
              if (saved) setDraft(null);
            }}
          >
            <textarea
              className="h-[28rem] w-full rounded-md border border-input bg-background p-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={`Markdown for ${doc.slug}`}
              data-testid="doc-editor"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <Button type="submit" size="sm" disabled={save.isPending}>
              Save and re-chunk
            </Button>
          </form>
        </SectionCard>
      ) : (
        <SectionCard
          title="Chunks"
          description="What retrieval actually sees. The heading path is the citation an analysis prints."
        >
          <ul className="space-y-3" data-testid="chunk-list">
            {chunks.map((chunk) => (
              <ChunkBlock
                key={chunk.heading_path}
                chunk={chunk}
                highlighted={chunk.heading_path === highlighted}
              />
            ))}
          </ul>
        </SectionCard>
      )}
    </div>
  );
}

function ChunkBlock({ chunk, highlighted }: { chunk: KnowledgeChunk; highlighted: boolean }) {
  return (
    <li
      id={anchorId(chunk.heading_path)}
      data-testid="chunk"
      data-chunk={chunk.heading_path}
      className={cn("rounded-lg border p-3", highlighted ? "border-primary" : "border-border")}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-xs">{chunk.heading_path}</span>
        <span className="text-muted-foreground text-xs">~{chunk.tokens_estimate} tokens</span>
      </div>
      {chunk.heading ? (
        <p className="mt-0.5 text-muted-foreground text-xs">{chunk.heading}</p>
      ) : null}
      <pre className="mt-2 whitespace-pre-wrap font-sans text-sm">{chunk.body}</pre>
    </li>
  );
}
