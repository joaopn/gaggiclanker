import { describe, expect, it } from "vitest";
import type { SyncRunRow, SyncStatusData } from "@/api/types";
import {
  isPulling,
  lastFinishedShotRun,
  latestShotRun,
  profilesReadOf,
  profilesSentence,
  pullSummary,
  relativeTime,
  syncSucceeded,
} from "@/lib/sync";

function run(overrides: Partial<SyncRunRow> = {}): SyncRunRow {
  return {
    id: 7,
    kind: "backfill",
    status: "ok",
    trigger: "manual",
    started_at: "2026-03-04T08:00:00.000Z",
    finished_at: "2026-03-04T08:00:20.000Z",
    shots_seen: 12,
    shots_inserted: 0,
    shots_updated: 0,
    shots_quarantined: 0,
    profiles_changed: 0,
    notes_synced: 0,
    errors: 0,
    error: null,
    ...overrides,
  };
}

function status(runs: Record<string, SyncRunRow>): SyncStatusData {
  return {
    configured: true,
    connected: true,
    running: false,
    last_runs: runs,
    last_error: null,
    counts: {
      total: 0,
      quarantined: 0,
      deleted_on_device: 0,
      incomplete: 0,
      samples: 0,
      needs_set: 0,
    },
    recent_events: [],
  };
}

describe("latestShotRun", () => {
  it("ignores the passes that are not about shots", () => {
    const runs = status({ identity: run({ id: 90, kind: "identity" }), backfill: run({ id: 7 }) });
    expect(latestShotRun(runs)?.id).toBe(7);
  });

  it("still finds a pass recorded before syncs became a request", () => {
    // An archive filled by the old push-driven engine holds its shot passes
    // under `live`. "Last sync" on such an archive should say when, not never.
    expect(latestShotRun(status({ live: run({ id: 4, kind: "live" }) }))?.id).toBe(4);
  });

  it("has no last finished run while one is in flight", () => {
    const inflight = status({ backfill: run({ finished_at: null, status: "running" }) });
    expect(latestShotRun(inflight)?.id).toBe(7);
    expect(lastFinishedShotRun(inflight)).toBeUndefined();
  });

  it("says nothing about an archive that has never been asked to sync", () => {
    expect(latestShotRun(undefined)).toBeUndefined();
    expect(lastFinishedShotRun(status({}))).toBeUndefined();
  });
});

describe("isPulling", () => {
  it("is true while a pass that moves data is in flight", () => {
    expect(isPulling(status({ backfill: run({ finished_at: null }) }))).toBe(true);
    expect(isPulling(status({ profiles: run({ kind: "profiles", finished_at: null }) }))).toBe(
      true,
    );
  });

  it("is false for an identity read", () => {
    // One frame and one request, on every reconnect — so whenever the
    // machine's Wi-Fi blinks. The ledger's own `running` is true for any kind,
    // which made the button flash "Syncing…" while pulling nothing.
    expect(isPulling(status({ identity: run({ kind: "identity", finished_at: null }) }))).toBe(
      false,
    );
  });

  it("is false when everything has finished, and for an archive with no runs", () => {
    expect(isPulling(status({ backfill: run() }))).toBe(false);
    expect(isPulling(status({}))).toBe(false);
    expect(isPulling(undefined)).toBe(false);
  });
});

function profilePass(summary: Record<string, unknown> | null, overrides: Partial<SyncRunRow> = {}) {
  return run({ id: 8, kind: "profiles", summary, ...overrides });
}

const entry = (label: string) => ({ label, reason: "", detail: "" });
const boardKeys = (overrides: Record<string, unknown> = {}) => ({
  profiles_read: 9,
  adopted: [],
  pushed: [],
  overwritten: [],
  removed: [],
  left: [],
  home_screen: [],
  failures: [],
  writes: 0,
  paused: null,
  ...overrides,
});

describe("pullSummary", () => {
  it("counts what landed and what moved", () => {
    expect(pullSummary(run({ shots_inserted: 3, shots_updated: 1 }))).toBe(
      "Synced: 3 new shots, 1 updated.",
    );
    expect(pullSummary(run({ shots_inserted: 1 }))).toBe("Synced: 1 new shot.");
  });

  it("says so when there was nothing", () => {
    expect(pullSummary(run())).toBe("Synced: no new shots.");
  });

  it("counts a shot we could not parse as one that landed", () => {
    // The bytes are in the archive and the row is in the list, which is what
    // the person who pressed the button wanted to know.
    expect(pullSummary(run({ shots_quarantined: 1 }))).toBe(
      "Synced: 1 new shot, 1 could not be parsed.",
    );
  });

  it("repeats what the machine said when the sync failed", () => {
    expect(pullSummary(run({ status: "error", error: "the machine stopped answering" }))).toBe(
      "the machine stopped answering.",
    );
  });

  it("says what landed when a run failed part of the way through", () => {
    // A pass that stored three shots and then hit two it could not fetch ends
    // `error` — sometimes with no message at all, because the per-shot
    // failures are counted rather than raised. "The sync failed" would be
    // telling somebody nothing happened when most of it did.
    expect(pullSummary(run({ status: "error", error: null, shots_inserted: 3, errors: 2 }))).toBe(
      "3 new shots, 2 failed. The Sync page has the details.",
    );
  });

  it("still names the fault when a partial run has one", () => {
    expect(
      pullSummary(
        run({
          status: "error",
          error: "the machine stopped answering",
          shots_inserted: 1,
          errors: 3,
        }),
      ),
    ).toBe("1 new shot, 3 failed. the machine stopped answering.");
  });

  it("still says something when a failure carries no message", () => {
    expect(pullSummary(run({ status: "error", error: null }))).toMatch(/failed/);
  });

  describe("with the profile pass of the same sync", () => {
    const shots = run({ shots_inserted: 2 });

    it("says how many profiles were read and that writes are off", () => {
      expect(pullSummary(shots, profilePass({ profiles_read: 9 }))).toBe(
        "Synced: 2 new shots. Read 9 profiles from the machine; no writes (writes are off).",
      );
    });

    it("says no writes were needed when the switch is on and nothing differed", () => {
      expect(pullSummary(shots, profilePass(boardKeys({ profiles_read: 1 })))).toBe(
        "Synced: 2 new shots. Read 1 profile from the machine; no writes needed.",
      );
    });

    it("counts pushes, removals and home-screen changes, each only when there are some", () => {
      const pass = profilePass(
        boardKeys({
          pushed: [entry("A")],
          removed: [entry("B"), entry("C")],
          home_screen: [entry("D")],
          writes: 7,
        }),
      );
      expect(profilesSentence(pass)).toBe(
        "Read 9 profiles from the machine; wrote 4 (pushed 1, removed 2, home screen 1).",
      );
      expect(profilesSentence(profilePass(boardKeys({ removed: [entry("B")] })))).toBe(
        "Read 9 profiles from the machine; wrote 1 (removed 1).",
      );
    });

    it("counts the profiles written, not the saves and deletes behind them", () => {
      // One push is a save and a delete on the machine: `writes` says 2, the person's number is 1.
      const pass = profilePass(boardKeys({ pushed: [entry("A")], writes: 2 }));
      expect(profilesSentence(pass)).toContain("wrote 1 (pushed 1)");
    });

    it("does not call a first adoption a write", () => {
      const pass = profilePass(boardKeys({ adopted: [entry("A"), entry("B")] }));
      expect(profilesSentence(pass)).toBe(
        "Read 9 profiles from the machine; no writes (took them onto the board).",
      );
    });

    it("says writes are paused, whatever else the summary holds", () => {
      const pass = profilePass(boardKeys({ paused: "reset", pushed: [entry("A")] }));
      expect(profilesSentence(pass)).toBe(
        "Read 9 profiles from the machine; no writes (paused: the machine looks reset).",
      );
    });

    it("adds how many writes failed", () => {
      const pass = profilePass(boardKeys({ pushed: [entry("A")], failures: [entry("B")] }));
      expect(profilesSentence(pass)).toBe(
        "Read 9 profiles from the machine; wrote 1 (pushed 1), 1 failed.",
      );
    });

    it("says nothing for a pass that has not finished, or is not there", () => {
      expect(profilesSentence(undefined)).toBeNull();
      expect(profilesSentence(profilePass({ profiles_read: 9 }, { finished_at: null }))).toBeNull();
      expect(pullSummary(shots)).toBe("Synced: 2 new shots.");
    });

    it("says the profiles could not be read when the pass failed before counting any", () => {
      const failed = profilePass(null, { status: "error", error: "no answer" });
      expect(profilesSentence(failed)).toBe("The machine's profiles could not be read: no answer.");
      expect(syncSucceeded(shots, failed)).toBe(false);
      expect(pullSummary(shots, failed)).toBe(
        "Shots synced: 2 new shots. The machine's profiles could not be read: no answer.",
      );
    });

    it("reads the count from the run, not from what changed", () => {
      expect(profilesReadOf(profilePass({ profiles_read: 9 }, { profiles_changed: 2 }))).toBe(9);
      expect(profilesReadOf(profilePass(null))).toBeNull();
      expect(profilesReadOf(profilePass({ profiles_read: "9" }))).toBeNull();
    });
  });
});

describe("relativeTime", () => {
  const now = Date.parse("2026-03-04T09:00:00.000Z");

  it.each([
    ["2026-03-04T08:59:30.000Z", /second/],
    ["2026-03-04T08:45:00.000Z", /minute/],
    ["2026-03-04T06:00:00.000Z", /hour/],
    ["2026-03-01T09:00:00.000Z", /day/],
  ])("describes %s in the right unit", (value, unit) => {
    expect(relativeTime(value, now)).toMatch(unit);
  });

  it("has nothing to say about a run that never finished", () => {
    expect(relativeTime(null, now)).toBe("");
    expect(relativeTime("not a date", now)).toBe("");
  });
});
