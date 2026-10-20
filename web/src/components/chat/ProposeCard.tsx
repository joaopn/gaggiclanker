import type { LucideIcon } from "lucide-react";
import { FilePen, Gauge, Layers, Lightbulb, Sparkles, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { ChatInsightCard } from "@/components/chat/ChatInsightCard";
import { InsightDeletionCard } from "@/components/chat/InsightDeletionCard";
import { OutcomeCard } from "@/components/chat/OutcomeCard";
import { ProfileProposalCard } from "@/components/profiles/ProfileProposalCard";
import { ProposalCard } from "@/components/recipes/ProposalCard";
import type { TraceEntry } from "@/hooks/useChat";
import { useRecipeProposals } from "@/hooks/useRecipes";
import { currentToolName, recipeIdOf } from "@/lib/legacyTools";

/**
 * What a `propose_` tool created, as a card that links to it.
 *
 * The propose tools each write a row somebody still has to decide about — a Recipe
 * version, a Recipe's whole first version, a version's grade, a profile draft, an
 * insight waiting to be added, an insight waiting to be deleted — and the
 * difference between "the chat suggested" and "the chat created" is exactly
 * what a reader has to be able to see. So the result is read out of the tool's
 * own output rather than parsed out of the prose, and it is rendered outside
 * the collapsed trace.
 */

export type Proposal = {
  kind: "recipe_version" | "initial_recipe" | "draft" | "insight" | "insight_deletion" | "outcome";
  label: string;
  detail: string;
  href: string;
  icon: LucideIcon;
  /** A proposed Recipe change or first recipe: which Recipe, and which row on it. */
  recipeId?: number;
  proposalId?: number;
  /** A proposed insight: which, and what it says until the live row is read. */
  insightId?: number;
  insightText?: string;
  /** A proposed deletion: which Recipe and insight, and its text until the live row is read. */
  deletion?: { insightId: number; text: string };
  /** A proposed grade: what the tool said, shown until the live row is read. */
  grade?: { version: string; outcome: string; countedShots: number };
  /** A proposed profile version: which draft, and the name the call gave it, if it gave one. */
  draftId?: number;
  proposedAs?: string | null;
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
  const name = currentToolName(entry.name);

  if (name === "propose_recipe_version") {
    const proposalId = output.proposal_id;
    const recipeId = recipeIdOf(output);
    if (proposalId === undefined || recipeId === undefined) return null;
    const changed = Array.isArray(output.changed) ? (output.changed as string[]) : [];
    return {
      kind: "recipe_version",
      // "Proposed", not "created": the version does not exist until the person
      // presses Accept, and this card is where they press it.
      label: `A change to this Recipe${changed.length > 0 ? `: ${changed.join(", ")}` : ""}`,
      detail: String(output.change_summary ?? "waiting for you"),
      href: `/recipes/${String(recipeId)}`,
      icon: Layers,
      recipeId: Number(recipeId),
      proposalId: Number(proposalId),
    };
  }

  if (name === "propose_initial_recipe") {
    const proposalId = output.proposal_id;
    const recipeId = recipeIdOf(output);
    if (proposalId === undefined || recipeId === undefined) return null;
    const versionFields =
      output.recipe && typeof output.recipe === "object"
        ? (output.recipe as Record<string, unknown>)
        : {};
    const figures = [
      versionFields.profile_label ? String(versionFields.profile_label) : null,
      versionFields.grind_setting ? `grind ${String(versionFields.grind_setting)}` : null,
      versionFields.dose_g ? `${String(versionFields.dose_g)} g in` : null,
      versionFields.target_yield_g ? `${String(versionFields.target_yield_g)} g out` : null,
    ].filter(Boolean);
    return {
      kind: "initial_recipe",
      // The same live card as a change, read back from the Recipe's proposals:
      // this is only what shows until that read lands.
      label: "Version 1 of this Recipe",
      detail: figures.length > 0 ? figures.join(" · ") : "waiting for you",
      href: `/recipes/${String(recipeId)}`,
      icon: Sparkles,
      recipeId: Number(recipeId),
      proposalId: Number(proposalId),
      // The name the agent gave the profile, to say "proposed as" if the person renames it.
      proposedAs: versionFields.profile_label ? String(versionFields.profile_label) : null,
    };
  }

  if (name === "propose_outcome") {
    const proposalId = output.proposal_id;
    const recipeId = recipeIdOf(output);
    if (proposalId === undefined || recipeId === undefined) return null;
    const version = String(output.version ?? "this version");
    const outcome = String(output.outcome ?? "");
    const countedShots = Number(output.counted_shots ?? 0);
    return {
      kind: "outcome",
      // "Proposed", not "recorded": the outcome is the person's until they
      // press Accept, and this card is where they press it.
      label: `A grade for ${version}`,
      detail: `${outcome.replace("_", " ")} — waiting for you`,
      href: `/recipes/${String(recipeId)}`,
      icon: Gauge,
      recipeId: Number(recipeId),
      proposalId: Number(proposalId),
      grade: { version, outcome, countedShots },
    };
  }

  if (name === "draft_profile") {
    const draftId = output.draft_id;
    if (draftId === undefined) return null;
    const patch = entry.arguments.patch;
    const named =
      patch && typeof patch === "object" ? (patch as Record<string, unknown>).label : undefined;
    return {
      kind: "draft",
      label: "Proposed profile change",
      // Shown only until the card's own read lands.
      detail: String(output.change_summary ?? ""),
      href: "/profiles#staged",
      icon: FilePen,
      draftId: Number(draftId),
      // The tool's output does not carry the name, but the call that made a new profile does.
      proposedAs: typeof named === "string" && named.trim() !== "" ? named.trim() : null,
    };
  }

  if (name === "propose_insight_deletion") {
    const proposalId = output.proposal_id;
    const recipeId = recipeIdOf(output);
    if (proposalId === undefined || recipeId === undefined) return null;
    const text = String(output.insight_text ?? "");
    return {
      kind: "insight_deletion",
      // "Proposed", not "deleted": the insight stays until the person presses
      // Delete, and this card is where they press it.
      label: "Deleting an insight",
      detail: `${text} — waiting for you to delete or keep it`,
      href: `/recipes/${String(recipeId)}`,
      icon: Trash2,
      recipeId: Number(recipeId),
      proposalId: Number(proposalId),
      deletion: { insightId: Number(output.insight_id ?? 0), text },
    };
  }

  if (name === "record_insight") {
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
      // where a Recipe's insight lives.
      href: "/recipes",
      icon: Lightbulb,
      insightId: Number(insightId),
      insightText: String(output.text ?? ""),
    };
  }

  return null;
}

/**
 * A proposed Recipe change or first recipe, read back live so the buttons tell the truth.
 *
 * The tool's own output is a snapshot of the moment it was called, and this
 * card is read again every time the conversation is scrolled to — days later,
 * after the person has answered it on the Recipe page. So the row is fetched
 * rather than rendered from the transcript, and a proposal that has since been
 * accepted says so here instead of offering to accept it a second time.
 *
 * Until the fetch lands, the summary from the transcript is what is shown: a
 * card that flashed empty would be worse than one that starts as a line of
 * text.
 */
function ProposedChange({ proposal }: { proposal: Proposal }) {
  const recipeId = proposal.recipeId as number;
  const proposals = useRecipeProposals(recipeId);
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
      <ProposalCard
        recipeId={recipeId}
        proposal={row}
        proposedProfileAs={proposal.proposedAs ?? null}
      />
    </div>
  );
}

export function ProposeCard({ proposal }: { proposal: Proposal }) {
  const Icon = proposal.icon;
  if (proposal.kind === "draft" && proposal.draftId !== undefined) {
    return (
      <div className="m-2 mt-0" data-testid={`propose-card-${proposal.kind}`}>
        <ProfileProposalCard
          draftId={proposal.draftId}
          place="chat"
          summary={proposal.detail}
          proposedAs={proposal.proposedAs ?? null}
        />
      </div>
    );
  }
  if (proposal.kind === "insight" && proposal.insightId !== undefined) {
    return <ChatInsightCard insightId={proposal.insightId} text={proposal.insightText ?? ""} />;
  }
  if (
    proposal.kind === "insight_deletion" &&
    proposal.recipeId &&
    proposal.proposalId &&
    proposal.deletion
  ) {
    return (
      <InsightDeletionCard
        recipeId={proposal.recipeId}
        proposalId={proposal.proposalId}
        fallback={proposal.deletion}
      />
    );
  }
  if (proposal.kind === "outcome" && proposal.recipeId && proposal.proposalId && proposal.grade) {
    return (
      <OutcomeCard
        recipeId={proposal.recipeId}
        proposalId={proposal.proposalId}
        fallback={proposal.grade}
      />
    );
  }
  if (
    (proposal.kind === "recipe_version" || proposal.kind === "initial_recipe") &&
    proposal.recipeId &&
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
