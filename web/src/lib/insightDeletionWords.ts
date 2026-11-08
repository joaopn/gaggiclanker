/**
 * What a stale deletion proposal says, by the cause the server reports (`stale_cause`): the one
 * place the "changed" sentence lives, for the Recipe card and the general card alike.
 *
 * `changed`: the insight is still there but its words changed or it was taken back since the
 * agent asked, and nothing was deleted. Anything else (`deleted`, or a cause not reported) means
 * the insight went another way, and each card says that in its own words.
 */
export const INSIGHT_CHANGED_WORDS = "It was changed since the agent asked: nothing was deleted.";

export function staleWords(cause: string | null | undefined, goneWords: string): string {
  return cause === "changed" ? INSIGHT_CHANGED_WORDS : goneWords;
}
