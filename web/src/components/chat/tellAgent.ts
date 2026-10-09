import { createContext, useContext } from "react";
import type { SetProposalDecision } from "@/api/types";
import turns from "@/lib/profileTurns.json";

/**
 * How a button inside a conversation says something to the agent in it.
 *
 * A card the agent proposed is answered by a person pressing Accept, and the
 * route that records it knows nothing about the conversation: the agent would
 * go on talking about a card that is already a version, and the person would
 * never hear that the new version is argued in a new conversation. So the
 * Chat page provides this for the conversation on screen, and the card sends
 * a message through it after an accept or a decline succeeds. Outside the chat
 * — the same card on the Set page — there is no provider and nothing is sent.
 */
export type TellAgent = (message: string) => void;

export const TellAgentContext = createContext<TellAgent | null>(null);

export function useTellAgent(): TellAgent | null {
  return useContext(TellAgentContext);
}

/**
 * Which conversation is on screen, for the cards in it.
 *
 * A card that answers something the agent proposed changes what that
 * conversation's transcript shows, and the card itself is drawn from the
 * transcript: so the answer has to invalidate that thread, and the card has to
 * know which it is in. Provided by the Chat page; `null` outside it.
 */
export const ChatThreadContext = createContext<number | null>(null);

export function useChatThreadId(): number | null {
  return useContext(ChatThreadContext);
}

/**
 * A turn's text with its `<placeholders>` filled, in one pass: a name the person typed that
 * happens to contain another placeholder is never filled in a second time.
 */
function fill(template: string, values: Record<string, string>): string {
  return template.replace(/<[^<>]+>/g, (placeholder) => values[placeholder] ?? placeholder);
}

/** The ending of a turn about a profile the person renamed, in place of the full stop. */
function renamedEnding(turn: string, oldName: string): string {
  const ending = fill(turns.renamed_ending, { "<old name>": oldName });
  return turn.endsWith(".") ? turn.slice(0, -1) + ending : turn;
}

/**
 * The message an accept sends, or `null` when there is nothing to tell.
 *
 * It starts with "Accepted:" because that is what the Set prompt tells the
 * agent to recognise: the turn is the button's, and the answer to it is to say
 * that the new version is brewed and analysed in a new conversation. A change
 * names what it recorded, in the card's own before → after words, so the
 * agent answers about the change that was accepted — whether the profile moved
 * is what decides if anything is to be done on the machine.
 *
 * A first recipe's accept also approves its new profile under the name on the card; when the
 * person changed that name, the message says what it is now and what the agent proposed.
 */
export function acceptedMessage(
  decision: SetProposalDecision,
  renamed?: { name: string; proposedAs: string },
): string | null {
  // Only an accept records a version; a decline answers with none.
  const version = decision.version;
  if (!version) return null;
  if (decision.proposal.kind === "design") {
    const base = fill(turns.accepted_first_recipe, { "<version label>": version.version_label });
    if (!renamed) return base;
    const clause = fill(turns.accepted_first_recipe_renamed_clause, { "<name>": renamed.name });
    return renamedEnding(`${base.slice(0, -1)}${clause}.`, renamed.proposedAs);
  }
  const changes = decision.proposal.changes
    .map((change) => `${change.label} ${change.before ?? "not set"} → ${change.after ?? "cleared"}`)
    .join("; ");
  return changes
    ? `Accepted: your proposed change (${changes}) is now ${version.version_label} of this Set.`
    : `Accepted: your proposed change is now ${version.version_label} of this Set.`;
}

/**
 * The message a decline sends: the person's reason, which is the useful half.
 *
 * It starts with "Declined:" for the same reason an accept's starts with
 * "Accepted:". With no reason given it says so, and the prompts tell the agent
 * to ask what was wrong rather than guess and propose again. `reason` is the
 * note exactly as the decline sent it, already trimmed.
 */
export function declinedMessage(reason: string): string {
  return reason ? fill(turns.declined, { "<note>": reason }) : turns.declined_no_reason;
}

/**
 * The message an approved profile proposal sends: which profile, and for a Set which of its
 * versions, as the server's standing read names them. `proposedAs` is the name the agent gave
 * it, only when the person typed another.
 */
export function approvedProfileMessage(approval: {
  name: string;
  /** The Set version this approval is recorded as, with its Set: a profile proposed for a Set. */
  forSet?: { versionLabel: string; setName: string } | null;
  /** A change to a profile that exists, not a profile of its own. */
  newVersion: boolean;
  proposedAs?: string | null;
}): string {
  const template = approval.forSet
    ? turns.approved_for_set
    : approval.newVersion
      ? turns.approved_new_version
      : turns.approved_new_profile;
  const turn = fill(template, {
    "<name>": approval.name,
    "<version label>": approval.forSet?.versionLabel ?? "",
    "<Set name>": approval.forSet?.setName ?? "",
  });
  return approval.proposedAs ? renamedEnding(turn, approval.proposedAs) : turn;
}
