import type { SyncRunRow, SyncStatusData } from "@/api/types";
import { boardSummaryOf } from "@/lib/board";

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
 * `backfill` is what every sync writes today. `live` is in the list because
 * archives filled before syncs became a request still hold runs under it, and
 * "last sync" on such an archive should say when, not "never".
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
 * Whether a pass that syncs anything is in flight.
 *
 * Not `status.running`, which is true for any kind — including the identity
 * read the engine does on every reconnect. That is one frame and one request,
 * it happens whenever the machine's Wi-Fi blinks, and it made the button flash
 * "Syncing…" for half a second at a time while doing nothing of the sort.
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

/** The newest profile pass, running or finished (the pass that reads the machine's profiles). */
export function latestProfileRun(status: SyncStatusData | undefined): SyncRunRow | undefined {
  return status?.last_runs?.profiles;
}

/**
 * How many profiles a profile pass read from the machine, or `null` when the run did not
 * record it (a run that could not list them, or one from before the count was kept).
 */
export function profilesReadOf(run: SyncRunRow | undefined): number | null {
  const raw = run?.summary;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const read = (raw as Record<string, unknown>).profiles_read;
  return typeof read === "number" ? read : null;
}

/** `text` as a sentence of its own: the machine's messages come without a closing full stop. */
function endSentence(text: string): string {
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * What a profile pass wrote to the machine, in a phrase: "wrote 2 (pushed 1, removed 1)".
 *
 * Writes are counted per profile action (pushed, removed, home screen), not per request to
 * the machine: a push is a save and a removal there, and it is one profile here. A push that
 * found its identical file already on the machine sent no save, so it is said apart.
 */
function writesPhrase(run: SyncRunRow): string {
  const board = boardSummaryOf(run);
  // No write phase ran: the switch is off, and nothing was sent.
  if (board === null) return "no writes (writes are off)";
  if (board.paused) return "no writes (paused: the machine looks reset)";

  const pushed = board.pushed.filter((item) => !item.reused).length;
  const reused = board.pushed.length - pushed;
  const parts: string[] = [];
  if (pushed > 0) parts.push(`pushed ${pushed}`);
  if (board.removed.length > 0) parts.push(`removed ${board.removed.length}`);
  if (board.homeScreen.length > 0) parts.push(`home screen ${board.homeScreen.length}`);
  const total = pushed + board.removed.length + board.homeScreen.length;
  const failed = board.failures.length;
  const conflicts = board.conflicts.length;

  const phrases: string[] = [];
  if (total > 0)
    phrases.push(`wrote ${total} (${parts.join(", ")})${failed > 0 ? `, ${failed} failed` : ""}`);
  else if (failed > 0) {
    // "No writes needed" would be wrong: something was needed and did not happen.
    phrases.push(`nothing written, ${failed} failed \u2014 the Sync page has the details`);
  } else if (board.adopted.length > 0) phrases.push("no writes (took them into the profile list)");
  else if (reused === 0) phrases.push("no writes needed");
  if (reused > 0) phrases.push(`${reused} ${reused === 1 ? "was" : "were"} already on the machine`);
  if (conflicts > 0) phrases.push(`${plural(conflicts, "profile")} in conflict, left alone`);
  return phrases.join("; ");
}

/**
 * What a finished profile pass did, as a sentence: "Read 9 profiles from the machine; wrote 2
 * (pushed 1, removed 1)." `null` for a pass that has not finished or is not there.
 *
 * Both numbers come from the run row the server recorded, never from the page's own state:
 * the profiles read from `summary.profiles_read`, the writes from the board's summary of the
 * same run, so what the toast says is what the Sync page shows.
 */
export function profilesSentence(run: SyncRunRow | undefined): string | null {
  if (!run?.finished_at) return null;
  const read = profilesReadOf(run);
  if (read === null) {
    return run.status === "ok"
      ? null
      : `The machine's profiles could not be read${run.error ? `: ${run.error}` : ""}.`;
  }
  return `Read ${plural(read, "profile")} from the machine; ${writesPhrase(run)}.`;
}

/**
 * What a finished sync is worth saying out loud: the shots it brought in, then the profiles it
 * read and the writes it made. "Synced: 3 new shots, 1 updated. Read 9 profiles from the
 * machine; wrote 2 (pushed 1, removed 1)."
 *
 * `run` is the shot pass and `profileRun` the profile pass of the same sync (absent when the
 * sync did not include one). Quarantined shots are counted as landed rather than left out: the
 * bytes are in the archive and the row is in the list, which is what the person who pressed the
 * button wanted to know. Why it would not parse is the shot page's business.
 */
export function pullSummary(run: SyncRunRow, profileRun?: SyncRunRow): string {
  const counts = countsSentence(run);
  const profiles = profilesSentence(profileRun);
  // The same message from both passes (the machine went away) is said once.
  const sameFault =
    run.status !== "ok" &&
    profileRun?.status !== "ok" &&
    !!run.error &&
    run.error === profileRun?.error;
  const tail = sameFault
    ? " The machine's profiles could not be read either."
    : profiles
      ? ` ${profiles}`
      : "";
  if (run.status === "ok") {
    // The shots are fine; "Synced" would be wrong only if the profile pass did not end well.
    const lead = syncSucceeded(run, profileRun) ? "Synced" : "Shots synced";
    return `${lead}: ${counts ?? "no new shots"}.${tail}`;
  }

  // A failed run is not an empty one. A pass that stored eleven shots and then
  // hit three it could not fetch ends `error`, sometimes with no message at
  // all — the per-shot failures are counted, not raised — and "The sync
  // failed" would be telling somebody nothing happened when most of it did.
  const detail = endSentence(run.error ?? "The Sync page has the details.");
  if (counts === null) {
    return `${endSentence(run.error ?? "The sync failed. The Sync page has the details.")}${tail}`;
  }
  const failed = run.errors > 0 ? `${run.errors} failed` : "some failed";
  return `${counts}, ${failed}. ${detail}${tail}`;
}

/** Whether a sync, shots and profiles together, ended well enough to say "Synced". */
export function syncSucceeded(run: SyncRunRow, profileRun?: SyncRunRow): boolean {
  return run.status === "ok" && (profileRun === undefined || profileRun.status === "ok");
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
