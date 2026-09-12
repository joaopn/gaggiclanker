import { ChevronDown, ChevronRight, TriangleAlert, Wrench } from "lucide-react";
import { useState } from "react";
import { ProposeCard, proposalFrom } from "@/components/chat/ProposeCard";
import { Badge } from "@/components/ui/badge";
import type { TraceEntry } from "@/hooks/useChat";

/**
 * What the model actually did, between the question and the answer.
 *
 * Collapsed by default and expandable per call, because the trace is the thing
 * that makes an answer checkable and also the thing nobody wants to read every
 * time. What is *not* collapsed is the permission class: a call that created a
 * Set version is labelled "proposal" on the closed row, because a reader
 * skimming past must not miss that something was written.
 */

export type ToolTraceProps = {
  entries: TraceEntry[];
  /** Tool name -> permission class, from `GET /api/chat/tools`. */
  permissions: Record<string, string>;
  /** Shown while the calls are still arriving. */
  live?: boolean;
};

export function ToolTrace({ entries, permissions, live = false }: ToolTraceProps) {
  if (entries.length === 0) return null;
  return (
    <ul className="space-y-1" data-testid="tool-trace">
      {entries.map((entry) => (
        <ToolTraceRow
          key={entry.id}
          entry={entry}
          permission={permissions[entry.name] ?? "read"}
          live={live}
        />
      ))}
    </ul>
  );
}

function ToolTraceRow({
  entry,
  permission,
  live,
}: {
  entry: TraceEntry;
  permission: string;
  live: boolean;
}) {
  const [open, setOpen] = useState(false);
  const pending = entry.ok === undefined;
  const failed = entry.ok === false;
  const proposal = proposalFrom(entry);

  return (
    <li className="rounded-md border border-border/70 bg-muted/30">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="size-3.5 shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0" aria-hidden="true" />
        )}
        {failed ? (
          <TriangleAlert className="size-3.5 shrink-0 text-destructive" aria-hidden="true" />
        ) : (
          <Wrench className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
        <span className="font-mono">{entry.name}</span>
        {permission === "propose" ? (
          <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
            proposal
          </Badge>
        ) : null}
        <span className="ml-auto text-muted-foreground">
          {pending
            ? live
              ? "running…"
              : "no result"
            : failed
              ? "failed"
              : `${entry.durationMs ?? 0} ms`}
        </span>
      </button>

      {open ? (
        <div className="space-y-2 border-border/70 border-t px-2 py-2">
          <Field label="Input" body={JSON.stringify(entry.arguments, null, 2)} />
          {entry.content !== undefined ? <Field label="Output" body={entry.content} /> : null}
        </div>
      ) : null}

      {/* Outside the collapse on purpose: something was created, and the link
          to it is the point of a proposal rather than a detail of it. */}
      {proposal ? <ProposeCard proposal={proposal} /> : null}
    </li>
  );
}

function Field({ label, body }: { label: string; body: string }) {
  return (
    <div>
      <div className="mb-0.5 font-medium text-[11px] text-muted-foreground uppercase">{label}</div>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-background p-1.5 font-mono text-[11px]">
        {body}
      </pre>
    </div>
  );
}
