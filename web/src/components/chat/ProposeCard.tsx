import type { LucideIcon } from "lucide-react";
import { FilePen, Gauge, Layers, Lightbulb, Sparkles, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { ChatInsightCard } from "@/components/chat/ChatInsightCard";
import { InsightDeletionCard } from "@/components/chat/InsightDeletionCard";
import { OutcomeCard } from "@/components/chat/OutcomeCard";
import { ProposalCard } from "@/components/sets/ProposalCard";
import type { TraceEntry } from "@/hooks/useChat";
import { useSetProposals } from "@/hooks/useSets";

/**
 * What a `propose_` tool created, as a card that links to it.
 *
 * The propose tools each write a row somebody still has to decide about — a Set
 * version, a Set's whole first recipe, a version's grade, a profile draft, an
 * insight waiting to be added, an insight waiting to be deleted — and the
 * difference between "the chat suggested" and "the chat created" is exactly
 * what a reader has to be able to see. So the result is read out of the tool's
 * own output rather than parsed out of the prose, and it is rendered outside
 * the collapsed trace.
 */

export type Proposal = {
  kind: "set_version" | "initial_recipe" | "draft" | "insight" | "insight_deletion" | "outcome";
  label: string;
  detail: string;
  href: string;
  icon: LucideIcon;
  /** A proposed Set change or first recipe: which Set, and which row on it. */
  setId?: number;
  proposalId?: number;
  /** A proposed insight: which, and what it says until the live row is read. */
  insightId?: number;
  insightText?: string;
  /** A proposed deletion: which Set and insight, and its text until the live row is read. */
  deletion?: { insightId: number; text: string };
  /** A proposed grade: what the tool said, shown until the live row is read. */
  grade?: { version: string; outcome: string; countedShots: number };
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
    const proposalId = output.proposal_id;
    const setId = output.set_id;
    if (proposalId === undefined || setId === undefined) return null;
    const changed = Array.isArray(output.changed) ? (output.changed as string[]) : [];
    return {
      kind: "set_version",
      // "Proposed", not "created": the version does not exist until the person
      // presses Accept, and this card is where they press it.
      label: `A change to this Set${changed.length > 0 ? `: ${changed.join(", ")}` : ""}`,
      detail: String(output.change_summary ?? "waiting for you"),
      href: `/sets/${String(setId)}`,
      icon: Layers,
      setId: Number(setId),
      proposalId: Number(proposalId),
    };
  }

  if (entry.name === "propose_initial_recipe") {
    const proposalId = output.proposal_id;
    const setId = output.set_id;
    if (proposalId === undefined || setId === undefined) return null;
    const recipe =
      output.recipe && typeof output.recipe === "object"
        ? (output.recipe as Record<string, unknown>)
        : {};
    const figures = [
      recipe.profile_label ? String(recipe.profile_label) : null,
      recipe.grind_setting ? `grind ${String(recipe.grind_setting)}` : null,
      recipe.dose_g ? `${String(recipe.dose_g)} g in` : null,
      recipe.target_yield_g ? `${String(recipe.target_yield_g)} g out` : null,
    ].filter(Boolean);
    return {
      kind: "initial_recipe",
      // The same live card as a change, read back from the Set's proposals:
      // this is only what shows until that read lands.
      label: "The first recipe for this Set",
      detail: figures.length > 0 ? figures.join(" · ") : "waiting for you",
      href: `/sets/${String(setId)}`,
      icon: Sparkles,
      setId: Number(setId),
      proposalId: Number(proposalId),
    };
  }

  if (entry.name === "propose_outcome") {
    const proposalId = output.proposal_id;
    const setId = output.set_id;
    if (proposalId === undefined || setId === undefined) return null;
    const version = String(output.version ?? "this version");
    const outcome = String(output.outcome ?? "");
    const countedShots = Number(output.counted_shots ?? 0);
    return {
      kind: "outcome",
      // "Proposed", not "recorded": the outcome is the person's until they
      // press Accept, and this card is where they press it.
      label: `A grade for ${version}`,
      detail: `${outcome.replace("_", " ")} — waiting for you`,
      href: `/sets/${String(setId)}`,
      icon: Gauge,
      setId: Number(setId),
      proposalId: Number(proposalId),
      grade: { version, outcome, countedShots },
    };
  }

  if (entry.name === "draft_profile") {
    const draftId = output.draft_id;
    if (draftId === undefined) return null;
    const prediction = String(output.prediction ?? "");
    return {
      kind: "draft",
      label: "Proposed profile change",
      detail: prediction
        ? `${String(output.change_summary ?? "")} — predicts: ${prediction}`
        : String(output.change_summary ?? "waiting for you to make it active"),
      href: "/profiles#staged",
      icon: FilePen,
    };
  }

  if (entry.name === "propose_insight_deletion") {
    const proposalId = output.proposal_id;
    const setId = output.set_id;
    if (proposalId === undefined || setId === undefined) return null;
    const text = String(output.insight_text ?? "");
    return {
      kind: "insight_deletion",
      // "Proposed", not "deleted": the insight stays until the person presses
      // Delete, and this card is where they press it.
      label: "Deleting an insight",
      detail: `${text} — waiting for you to delete or keep it`,
      href: `/sets/${String(setId)}`,
      icon: Trash2,
      setId: Number(setId),
      proposalId: Number(proposalId),
      deletion: { insightId: Number(output.insight_id ?? 0), text },
    };
  }

  if (entry.name === "record_insight") {
    const insightId = output.insight_id;
    if (insightId === undefined) return null;
    return {
      kind: "insight",
      label: "Insight proposed",
      // Waiting is the whole point: it reaches no future prompt until a person
      // adds it, and a card that did not say that would be misleading.
      detail: `${String(output.text ?? "")} — waiting for you to add or dismiss it`,
      // Its own live card is drawn from the insight itself; this is only the
      // link the fallback would carry, and the Knowledge page is no longer
      // where a Set's insight lives.
      href: "/sets",
      icon: Lightbulb,
      insightId: Number(insightId),
      insightText: String(output.text ?? ""),
    };
  }

  return null;
}

/**
 * A proposed Set change or first recipe, read back live so the buttons tell the truth.
 *
 * The tool's own output is a snapshot of the moment it was called, and this
 * card is read again every time the conversation is scrolled to — days later,
 * after the person has answered it on the Set page. So the row is fetched
 * rather than rendered from the transcript, and a proposal that has since been
 * accepted says so here instead of offering to accept it a second time.
 *
 * Until the fetch lands, the summary from the transcript is what is shown: a
 * card that flashed empty would be worse than one that starts as a line of
 * text.
 */
function ProposedChange({ proposal }: { proposal: Proposal }) {
  const setId = proposal.setId as number;
  const proposals = useSetProposals(setId);
  const row = proposals.data?.items.find((item) => item.id === proposal.proposalId);
  if (!row) {
    return (
      <div className="m-2 mt-0" data-testid={`propose-card-${proposal.kind}`}>
        <p className="rounded-md border border-primary/40 bg-primary/5 p-2 text-sm">
          {proposal.label}
          <span className="block text-muted-foreground text-xs">{proposal.detail}</span>
        </p>
      </div>
    );
  }
  return (
    <div className="m-2 mt-0" data-testid={`propose-card-${proposal.kind}`}>
      <ProposalCard setId={setId} proposal={row} />
    </div>
  );
}

export function ProposeCard({ proposal }: { proposal: Proposal }) {
  const Icon = proposal.icon;
  if (proposal.kind === "insight" && proposal.insightId !== undefined) {
    return <ChatInsightCard insightId={proposal.insightId} text={proposal.insightText ?? ""} />;
  }
  if (
    proposal.kind === "insight_deletion" &&
    proposal.setId &&
    proposal.proposalId &&
    proposal.deletion
  ) {
    return (
      <InsightDeletionCard
        setId={proposal.setId}
        proposalId={proposal.proposalId}
        fallback={proposal.deletion}
      />
    );
  }
  if (proposal.kind === "outcome" && proposal.setId && proposal.proposalId && proposal.grade) {
    return (
      <OutcomeCard
        setId={proposal.setId}
        proposalId={proposal.proposalId}
        fallback={proposal.grade}
      />
    );
  }
  if (
    (proposal.kind === "set_version" || proposal.kind === "initial_recipe") &&
    proposal.setId &&
    proposal.proposalId
  ) {
    return <ProposedChange proposal={proposal} />;
  }
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
