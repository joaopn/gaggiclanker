import type { LucideIcon } from "lucide-react";
import { FilePen, Layers, Lightbulb } from "lucide-react";
import { Link } from "react-router-dom";
import type { TraceEntry } from "@/hooks/useChat";

/**
 * What a `propose_` tool created, as a card that links to it.
 *
 * The three propose tools each write a row somebody still has to decide about —
 * a Set version, a profile draft, an insight waiting to be confirmed — and the
 * difference between "the chat suggested" and "the chat created" is exactly
 * what a reader has to be able to see. So the result is read out of the tool's
 * own output rather than parsed out of the prose, and it is rendered outside
 * the collapsed trace.
 */

export type Proposal = {
  kind: "set_version" | "draft" | "insight";
  label: string;
  detail: string;
  href: string;
  icon: LucideIcon;
};

function parse(content: string | undefined): Record<string, unknown> | null {
  if (!content) return null;
  try {
    const parsed: unknown = JSON.parse(content);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** The card for one trace entry, or `null` when it did not create anything. */
export function proposalFrom(entry: TraceEntry): Proposal | null {
  if (entry.ok !== true) return null;
  const output = parse(entry.content);
  if (!output) return null;

  if (entry.name === "propose_set_version") {
    const version = output.version as Record<string, unknown> | undefined;
    if (!version) return null;
    const changed = Array.isArray(output.changed) ? (output.changed as string[]) : [];
    return {
      kind: "set_version",
      label: `Set version v${String(version.version_no ?? "?")}`,
      detail: changed.length > 0 ? `changed ${changed.join(", ")}` : "created",
      href: `/sets/${String(version.set_id ?? "")}`,
      icon: Layers,
    };
  }

  if (entry.name === "draft_profile") {
    const draftId = output.draft_id;
    if (draftId === undefined) return null;
    return {
      kind: "draft",
      label: `Profile draft #${String(draftId)}`,
      detail: String(output.change_summary ?? "waiting for approval"),
      href: "/profiles#staged",
      icon: FilePen,
    };
  }

  if (entry.name === "record_insight") {
    const insightId = output.insight_id;
    if (insightId === undefined) return null;
    return {
      kind: "insight",
      label: "Insight proposed",
      // Unconfirmed is the whole point: it reaches no future prompt until a
      // person says so, and a card that did not say that would be misleading.
      detail: `${String(output.text ?? "")} — unconfirmed`,
      href: "/knowledge?tab=insights",
      icon: Lightbulb,
    };
  }

  return null;
}

export function ProposeCard({ proposal }: { proposal: Proposal }) {
  const Icon = proposal.icon;
  return (
    <div
      className="m-2 mt-0 rounded-md border border-primary/40 bg-primary/5 p-2"
      data-testid={`propose-card-${proposal.kind}`}
    >
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
        <div className="min-w-0">
          <Link
            to={proposal.href}
            className="font-medium text-sm underline-offset-2 hover:underline"
          >
            {proposal.label}
          </Link>
          <p className="text-muted-foreground text-xs">{proposal.detail}</p>
        </div>
      </div>
    </div>
  );
}
