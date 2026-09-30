import type { ProfileDraft } from "@/api/types";

/**
 * What the last push or rollback did to the machine, as the sentences the server wrote:
 * what was replaced, what was kept and why, and whether the firmware cleared the startup
 * profile. Empty for a draft that has not touched the machine.
 */
export function outcomeLines(draft: ProfileDraft): string[] {
  const lines = (draft.outcome as { lines?: unknown } | null | undefined)?.lines;
  return Array.isArray(lines) ? lines.filter((l): l is string => typeof l === "string") : [];
}

/** Whether a rollback actually took the pushed profile off the machine. */
export function rollbackRemovedProfile(draft: ProfileDraft): boolean {
  const removed = (draft.outcome as { removed_device_profile_id?: unknown } | null | undefined)
    ?.removed_device_profile_id;
  return typeof removed === "string" && removed.length > 0;
}
