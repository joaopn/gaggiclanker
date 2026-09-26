import { createContext, useContext } from "react";
import type { SetProposalDecision } from "@/api/types";

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
 * The message an accept sends, or `null` when there is nothing to tell.
 *
 * It starts with "Accepted:" because that is what the Set prompt tells the
 * agent to recognise: the turn is the button's, and the answer to it is to say
 * that the new version is brewed and analysed in a new conversation.
 */
export function acceptedMessage(decision: SetProposalDecision): string | null {
  // Only an accept records a version; a decline answers with none.
  const version = decision.version;
  if (!version) return null;
  return decision.proposal.kind === "design"
    ? `Accepted: your first recipe is now version ${version.version_no} of this Set.`
    : `Accepted: your proposed change is now version ${version.version_no} of this Set.`;
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
  return reason ? `Declined: ${reason}` : "Declined: no reason given.";
}
