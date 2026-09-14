import type { SyncRunRow, SyncStatusData } from "@/api/types";

/**
 * Reading the sync ledger the way a person asks about it.
 *
 * The ledger is keyed by pass kind and the front page only cares about one
 * question — what happened the last time somebody pulled — so the mapping from
 * "what the server records" to "what the button says" lives here rather than
 * inside the button, where it would be untestable without a DOM.
 */

/**
 * The kinds a shot pass has been recorded under.
 *
 * `backfill` is what every pull writes today. `live` is in the list because
 * archives filled before pulls became a request still hold runs under it, and
 * "last pull" on such an archive should say when, not "never".
 */
const SHOT_RUN_KINDS = ["backfill", "live", "shots"];

/** The newest shot pass of any kind, running or finished. */
export function latestShotRun(status: SyncStatusData | undefined): SyncRunRow | undefined {
  if (!status) return undefined;
  const last = status.last_runs ?? {};
  const runs = SHOT_RUN_KINDS.map((kind) => last[kind]).filter(
    (run): run is SyncRunRow => run !== undefined,
  );
  return runs.sort((left, right) => right.id - left.id)[0];
}

/**
 * Whether a pass that pulls anything is in flight.
 *
 * Not `status.running`, which is true for any kind — including the identity
 * read the engine does on every reconnect. That is one frame and one request,
 * it happens whenever the machine's Wi-Fi blinks, and it made the button flash
 * "Pulling…" for half a second at a time while doing nothing of the sort.
 */
export function isPulling(status: SyncStatusData | undefined): boolean {
  if (!status) return false;
  const last = status.last_runs ?? {};
  return Object.entries(last).some(
    ([kind, run]) => kind !== "identity" && run.finished_at === null,
  );
}

/** The newest shot pass that has actually finished. */
export function lastFinishedShotRun(status: SyncStatusData | undefined): SyncRunRow | undefined {
  const run = latestShotRun(status);
  return run?.finished_at ? run : undefined;
}

/**
 * "3 new shots, 1 updated" — what a finished pull is worth saying out loud.
 *
 * Quarantined shots are counted as landed rather than left out: the bytes are
 * in the archive and the row is in the list, which is what the person who
 * pressed the button wanted to know. Why it would not parse is the shot page's
 * business.
 */
export function pullSummary(run: SyncRunRow): string {
  const counts = countsSentence(run);
  if (run.status === "ok") return counts ?? "Nothing new";

  // A failed run is not an empty one. A pass that stored eleven shots and then
  // hit three it could not fetch ends `error`, sometimes with no message at
  // all — the per-shot failures are counted, not raised — and "The pull
  // failed" would be telling somebody nothing happened when most of it did.
  const detail = run.error ?? "The Device page has the details.";
  if (counts === null) return run.error ?? "The pull failed. The Device page has the details.";
  const failed = run.errors > 0 ? `${run.errors} failed` : "some failed";
  return `${counts}, ${failed}. ${detail}`;
}

/**
 * What a run moved, as a sentence, or `null` when it moved nothing.
 *
 * Quarantined shots count as landed: the bytes are in the archive and the row
 * is in the list, which is what the person who pressed the button wanted to
 * know. Why it would not parse is the shot page's business — but it is said
 * here too, because a "new shot" with no curve is otherwise a surprise.
 */
function countsSentence(run: SyncRunRow): string | null {
  const parts: string[] = [];
  const landed = run.shots_inserted + run.shots_quarantined;
  if (landed > 0) parts.push(`${landed} new shot${landed === 1 ? "" : "s"}`);
  if (run.shots_updated > 0) parts.push(`${run.shots_updated} updated`);
  if (run.shots_quarantined > 0) {
    parts.push(`${run.shots_quarantined} could not be parsed`);
  }
  return parts.length > 0 ? parts.join(", ") : null;
}

/**
 * "2 minutes ago", in whatever the browser's language calls it.
 *
 * `Intl.RelativeTimeFormat` rather than a table of English strings: the rest of
 * this UI formats dates and numbers through the platform's own locale, and a
 * hand-rolled "2 minutes ago" would be the one English phrase left in a page
 * that otherwise speaks the reader's language.
 */
export function relativeTime(value: string | null | undefined, now = Date.now()): string {
  if (!value) return "";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((then - now) / 1000);
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 60 * 60 * 24 * 365],
    ["month", 60 * 60 * 24 * 30],
    ["day", 60 * 60 * 24],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return format.format(Math.round(seconds), "second");
}

/** What the Sync page knows about the machine when it decides whether a write can start. */
export type WriteReadiness = {
  configured: boolean;
  connected: boolean;
  writesEnabled: boolean;
};

/**
 * Why a write action on the Sync page cannot start right now, or `null` when it can.
 *
 * The same three checks, in the same order, the server applies before it
 * queues a send or a cleanup: a machine, the write switch, a connection. The
 * page shows the sentence beside the disabled button rather than only
 * disabling it, because "nothing happens when I press it" is the failure these
 * actions are most likely to produce. The server still decides; this only
 * saves a request that would be refused.
 */
export function writeBlocker({
  configured,
  connected,
  writesEnabled,
}: WriteReadiness): string | null {
  if (!configured) return "No machine is configured. Set its address in Settings.";
  if (!writesEnabled) {
    return "Device writes are off. Turn on “Device writes enabled” under Settings → Machine.";
  }
  if (!connected) return "The machine is not connected. Nothing can be written to it until it is.";
  return null;
}
