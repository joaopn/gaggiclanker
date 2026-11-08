import turns from "@/lib/profileTurns.json";

/**
 * Whether a person's stored message is a card's turn ("Accepted: …", "Approved: …",
 * "Declined: …") and not their own words.
 *
 * Pressing a card's button sends the agent a turn and the conversation stores it as the person's
 * message, but it is the button's sentence. A general insight is never saved from one. The
 * prefixes are listed once, in `profileTurns.json`, and the back end's quote check holds the same
 * list (a test keeps the two equal).
 */
export function isAutomaticTurn(content: string): boolean {
  const text = content.trimStart();
  return turns.automatic_turn_prefixes.some((prefix) => text.startsWith(prefix));
}
