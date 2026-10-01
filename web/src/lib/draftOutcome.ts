import type { ProfileDraft } from "@/api/types";

/**
 * What the sync that put this draft on the machine did, as the sentences the server wrote:
 * what was replaced, what was kept and why. Empty for a draft that has not touched the machine.
 */
export function outcomeLines(draft: ProfileDraft): string[] {
  const lines = (draft.outcome as { lines?: unknown } | null | undefined)?.lines;
  return Array.isArray(lines) ? lines.filter((l): l is string => typeof l === "string") : [];
}
