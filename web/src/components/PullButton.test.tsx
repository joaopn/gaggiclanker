import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SyncStatusData } from "@/api/types";
import { PullButton } from "@/components/PullButton";
import { EVENT_INVALIDATIONS } from "@/lib/invalidate";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getSyncStatus, getDeviceStatus, runSync } = vi.hoisted(() => ({
  getSyncStatus: vi.fn(),
  getDeviceStatus: vi.fn(),
  runSync: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSyncStatus,
  getDeviceStatus,
  runSync,
}));

function statusData(overrides: Partial<SyncStatusData> = {}): SyncStatusData {
  return {
    configured: true,
    connected: true,
    running: false,
    last_runs: {},
    last_error: null,
    counts: {
      total: 2,
      quarantined: 1,
      deleted_on_device: 0,
      incomplete: 0,
      samples: 236,
      needs_set: 0,
    },
    recent_events: [],
    ...overrides,
  };
}

/** One finished shot pass, as the ledger records it. */
function shotRun(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    kind: "backfill",
    status: "ok",
    trigger: "manual",
    started_at: "2026-03-04T08:00:00.000Z",
    finished_at: "2026-03-04T08:00:20.000Z",
    shots_seen: 12,
    shots_inserted: 3,
    shots_updated: 1,
    shots_quarantined: 0,
    profiles_changed: 0,
    notes_synced: 0,
    errors: 0,
    error: null,
    ...overrides,
  };
}

/** One finished profile pass; `summary` is what the server records (the count, then the board). */
function profileRun(
  summary: Record<string, unknown> | null,
  overrides: Record<string, unknown> = {},
) {
  return shotRun({
    id: 8,
    kind: "profiles",
    shots_seen: 0,
    shots_inserted: 0,
    shots_updated: 0,
    summary,
    ...overrides,
  });
}

/** The board's own keys, all empty, as the write phase records them. */
function boardSummary(overrides: Record<string, unknown> = {}) {
  return {
    profiles_read: 9,
    adopted: [],
    pushed: [],
    removed: [],
    left: [],
    home_screen: [],
    failures: [],
    writes: 0,
    paused: null,
    ...overrides,
  };
}

const item = (label: string) => ({ label, reason: "", detail: "" });

/** The button once the machine's status has loaded and says it can sync. */
async function ready(): Promise<void> {
  await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
}

beforeEach(() => {
  vi.clearAllMocks();
  getSyncStatus.mockResolvedValue(statusData());
  getDeviceStatus.mockResolvedValue({
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: null,
    last_status: null,
  });
  runSync.mockResolvedValue({ queued: ["shots", "profiles", "identity"] });
});

describe("PullButton", () => {
  /** Press the button on a ledger that holds runs 6 and 5, then let `runs` land as the sync's. */
  async function syncAndFinish(runs: Record<string, unknown>) {
    const user = setupUser();
    getSyncStatus.mockResolvedValue(
      statusData({
        last_runs: { backfill: shotRun({ id: 6 }), profiles: profileRun(null, { id: 5 }) },
      }),
    );
    const { queryClient } = renderWithQueryClient(<PullButton />);
    await ready();
    await user.click(screen.getByTestId("pull-button"));
    await waitFor(() => expect(runSync).toHaveBeenCalledWith("all"));

    // The route answers 202; the runs show up in the ledger afterwards, which is what the
    // `sync.progress` event tells the page to re-read.
    getSyncStatus.mockResolvedValue(statusData({ last_runs: runs as never }));
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }
  }

  it("reads Sync on screen and Sync with machine to a screen reader, and Syncing… while it runs", async () => {
    renderWithQueryClient(<PullButton />);
    await ready();
    const button = screen.getByRole("button", { name: "Sync with machine" });
    expect(button).toBe(screen.getByTestId("pull-button"));
    // The visible word; " with machine" is sr-only, the word itself hides below `sm`.
    expect(within(button).getByText("Sync")).toHaveClass("max-sm:sr-only");
    expect(within(button).getByText("with machine")).toHaveClass("sr-only");
    expect(screen.queryByText(/Pull from/)).not.toBeInTheDocument();
  });

  it("says what landed and what the profiles did when the sync it started finishes", async () => {
    await syncAndFinish({
      backfill: shotRun({ id: 7, shots_inserted: 2, shots_updated: 0 }),
      profiles: profileRun(boardSummary({ pushed: [item("A")], removed: [item("B")], writes: 4 })),
    });

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Synced: 2 new shots. Read 9 profiles from the machine; wrote 2 (pushed 1, removed 1).",
      ),
    );
  });

  it("says no writes when the writes switch is off", async () => {
    await syncAndFinish({
      backfill: shotRun({ id: 7, shots_inserted: 0, shots_updated: 0 }),
      profiles: profileRun({ profiles_read: 9 }),
    });

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Synced: no new shots. Read 9 profiles from the machine; no writes (writes are off).",
      ),
    );
  });

  it("says no writes were needed when the machine already matched the board", async () => {
    await syncAndFinish({
      backfill: shotRun({ id: 7, shots_inserted: 3, shots_updated: 1 }),
      profiles: profileRun(boardSummary({ profiles_read: 1 })),
    });

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Synced: 3 new shots, 1 updated. Read 1 profile from the machine; no writes needed.",
      ),
    );
  });

  it("counts star changes, and says writes were paused after a suspected reset", async () => {
    await syncAndFinish({
      backfill: shotRun({ id: 7, shots_inserted: 0, shots_updated: 0 }),
      profiles: profileRun(boardSummary({ home_screen: [item("A"), item("B")], writes: 2 })),
    });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Synced: no new shots. Read 9 profiles from the machine; wrote 2 (home screen 2).",
      ),
    );
  });

  it("says writes are paused when the machine looked reset", async () => {
    await syncAndFinish({
      backfill: shotRun({ id: 7, shots_inserted: 0, shots_updated: 0 }),
      profiles: profileRun(boardSummary({ paused: "the machine looks reset" })),
    });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Synced: no new shots. Read 9 profiles from the machine; no writes (paused: the machine looks reset).",
      ),
    );
  });

  it("waits for the profile pass before it speaks, and does not use the previous one's", async () => {
    // The shot pass of this sync is done but the profile pass on the ledger is still the one
    // from before the click (run 5): no toast yet, and never one built from run 5's numbers.
    await syncAndFinish({
      backfill: shotRun({ id: 7, shots_inserted: 2, shots_updated: 0 }),
      profiles: profileRun(boardSummary({ profiles_read: 40 }), { id: 5 }),
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("says how many writes failed when the board could not do all of them", async () => {
    await syncAndFinish({
      backfill: shotRun({ id: 7, shots_inserted: 1, shots_updated: 0 }),
      profiles: profileRun(
        boardSummary({ pushed: [item("A")], failures: [item("B")], writes: 2 }),
        { status: "error", errors: 1 },
      ),
    });

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Shots synced: 1 new shot. Read 9 profiles from the machine; wrote 1 (pushed 1), 1 failed.",
      ),
    );
  });

  it("reports a failed sync with what the machine said", async () => {
    await syncAndFinish({
      backfill: shotRun({
        id: 7,
        status: "error",
        error: "the machine stopped answering",
        shots_inserted: 0,
        shots_updated: 0,
      }),
      profiles: profileRun(null, {
        status: "error",
        error: "the machine stopped answering",
      }),
    });

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "the machine stopped answering. The machine's profiles could not be read either.",
      ),
    );
  });

  it("says what landed when a sync failed part of the way through", async () => {
    // The common shape of a failure: eleven shots stored, three the machine
    // would not serve, and no message at all — the per-shot failures are
    // counted, not raised. Saying "the sync failed" would be wrong about the
    // eleven.
    await syncAndFinish({
      backfill: shotRun({
        id: 7,
        status: "error",
        error: null,
        shots_inserted: 11,
        shots_updated: 0,
        errors: 3,
      }),
      profiles: profileRun({ profiles_read: 9 }),
    });

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "11 new shots, 3 failed. The Device page has the details. Read 9 profiles from the machine; no writes (writes are off).",
      ),
    );
  });

  it("still toasts when the ledger moved before the 202 came back", async () => {
    // The regression: the run id to wait past used to be read when the request
    // resolved. The first `sync.progress` event lands well inside that window
    // and refetches the ledger, so by then the newest run was already the one
    // this click started — and the page sat waiting for a run newer than
    // itself, for ever.
    const user = setupUser();
    getSyncStatus.mockResolvedValue(statusData({ last_runs: { backfill: shotRun({ id: 6 }) } }));

    let accept: (value: { queued: string[] }) => void = () => {};
    runSync.mockImplementation(
      () =>
        new Promise<{ queued: string[] }>((resolve) => {
          accept = resolve;
        }),
    );

    const { queryClient } = renderWithQueryClient(<PullButton />);
    await ready();
    await user.click(screen.getByTestId("pull-button"));

    // The engine's "started" event, and the refetch it causes, before the 202.
    getSyncStatus.mockResolvedValue(
      statusData({
        running: true,
        last_runs: { backfill: shotRun({ id: 7, status: "running", finished_at: null }) },
      }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }
    accept({ queued: ["shots"] });

    getSyncStatus.mockResolvedValue(
      statusData({ last_runs: { backfill: shotRun({ id: 7, shots_inserted: 2 }) } }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }

    // Only the shot pass was queued, so it is all there is to say.
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Synced: 2 new shots, 1 updated."),
    );
  });

  /** Press the button on a ledger holding runs 6 and 5, then let `runs` be the ledger. */
  async function pressAndLedger(profileGraceMs: number) {
    const user = setupUser();
    getSyncStatus.mockResolvedValue(
      statusData({
        last_runs: { backfill: shotRun({ id: 6 }), profiles: profileRun(null, { id: 5 }) },
      }),
    );
    const { queryClient } = renderWithQueryClient(<PullButton profileGraceMs={profileGraceMs} />);
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByTestId("pull-button"));
    await waitFor(() => expect(runSync).toHaveBeenCalled());
    const ledger = async (runs: Record<string, unknown>) => {
      getSyncStatus.mockResolvedValue(statusData({ last_runs: runs as never }));
      for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
        await queryClient.invalidateQueries({ queryKey });
      }
    };
    return { user, ledger };
  }

  // Twice: React flushes the effect that follows a ledger update at the end of the first act,
  // so the timer under test only starts there.
  const settle = async (ms: number) => {
    await act(() => new Promise((resolve) => setTimeout(resolve, 1)));
    await act(() => new Promise((resolve) => setTimeout(resolve, ms)));
  };
  const running = () => profileRun(null, { status: "running", finished_at: null });

  it("waits for a profile pass that is running when the shot pass is done, then says its numbers", async () => {
    // The usual order: the shot pass finishes first, and this click's profile pass is already
    // on the ledger, running.
    const { ledger } = await pressAndLedger(40);
    const shots = shotRun({ id: 7, shots_inserted: 2, shots_updated: 0 });
    await ledger({ backfill: shots, profiles: running() });
    await settle(150);
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();

    await ledger({ backfill: shots, profiles: profileRun({ profiles_read: 9 }) });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Synced: 2 new shots. Read 9 profiles from the machine; no writes (writes are off).",
      ),
    );
  });

  it("does not give up on a slow profile pass, and reports it when it fails", async () => {
    const { ledger } = await pressAndLedger(40);
    const shots = shotRun({ id: 7, shots_inserted: 2, shots_updated: 0 });
    await ledger({ backfill: shots, profiles: running() });
    await settle(200); // well past the grace
    expect(toast.success).not.toHaveBeenCalled();

    await ledger({
      backfill: shots,
      profiles: profileRun(null, { status: "error", error: "machine gone", errors: 1 }),
    });
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Shots synced: 2 new shots. The machine's profiles could not be read: machine gone.",
      ),
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("waits for the second click's profile pass instead of reusing the first click's give-up", async () => {
    const first = await pressAndLedger(30);
    await first.ledger({
      backfill: shotRun({ id: 7, shots_inserted: 1, shots_updated: 0 }),
      profiles: profileRun(null, { id: 5 }),
    });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Synced: 1 new shot."));
    vi.mocked(toast.success).mockClear();

    // Second click: the profile pass of this one is not on the ledger yet, and the first
    // click's give-up must not make the toast fire at once.
    await first.user.click(screen.getByTestId("pull-button"));
    await first.ledger({
      backfill: shotRun({ id: 8, shots_inserted: 2, shots_updated: 0 }),
      profiles: profileRun(null, { id: 5 }),
    });
    await settle(5);
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("does not let the first click's grace timer cut the second click's wait short", async () => {
    // Fake time, so the order is exact: the first click's grace timer is still pending when
    // the second click starts, and must not fire into the second click's wait.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user, ledger } = await pressAndLedger(10_000);
      // A first, tiny step lets React flush the effect that follows a ledger update (and so
      // start its timer) before the clock moves.
      const advance = async (ms: number) => {
        await act(() => vi.advanceTimersByTimeAsync(1));
        await act(() => vi.advanceTimersByTimeAsync(ms));
      };

      // First click: its shot pass is done, its profile pass has not appeared; the grace runs.
      await ledger({
        backfill: shotRun({ id: 7, shots_inserted: 1, shots_updated: 0 }),
        profiles: profileRun(null, { id: 5 }),
      });
      await advance(6_000);

      // Second click inside the grace, and its shot pass finishes with no profile pass yet.
      await user.click(screen.getByTestId("pull-button"));
      await ledger({
        backfill: shotRun({ id: 8, shots_inserted: 2, shots_updated: 0 }),
        profiles: profileRun(null, { id: 5 }),
      });
      // The first click's timer was due 4 s ago, this click's is due in about 6 s.
      await advance(4_500);
      expect(toast.success).not.toHaveBeenCalledWith("Synced: 2 new shots.");

      await ledger({
        backfill: shotRun({ id: 8, shots_inserted: 2, shots_updated: 0 }),
        profiles: profileRun({ profiles_read: 3 }),
      });
      await waitFor(() =>
        expect(toast.success).toHaveBeenCalledWith(
          "Synced: 2 new shots. Read 3 profiles from the machine; no writes (writes are off).",
        ),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("holds the toast until the 202 says what was queued when the ledger is quicker", async () => {
    const user = setupUser();
    getSyncStatus.mockResolvedValue(
      statusData({
        last_runs: { backfill: shotRun({ id: 6 }), profiles: profileRun(null, { id: 5 }) },
      }),
    );
    let accept: (value: { queued: string[] }) => void = () => {};
    runSync.mockImplementation(
      () =>
        new Promise<{ queued: string[] }>((resolve) => {
          accept = resolve;
        }),
    );
    const { queryClient } = renderWithQueryClient(<PullButton profileGraceMs={5000} />);
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByTestId("pull-button"));

    // Both passes finish before the 202 comes back.
    getSyncStatus.mockResolvedValue(
      statusData({
        last_runs: {
          backfill: shotRun({ id: 7, shots_inserted: 2, shots_updated: 0 }),
          profiles: profileRun({ profiles_read: 9 }),
        },
      }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }
    await settle(50);
    expect(toast.success).not.toHaveBeenCalled();

    accept({ queued: ["shots", "profiles"] });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Synced: 2 new shots. Read 9 profiles from the machine; no writes (writes are off).",
      ),
    );
  });

  it("does not toast after the button is gone", async () => {
    const { ledger } = await pressAndLedger(30);
    const view = screen.getByTestId("pull-button");
    expect(view).toBeInTheDocument();
    await ledger({ backfill: shotRun({ id: 7, shots_inserted: 2, shots_updated: 0 }) });
    cleanup();
    await settle(100);
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("speaks without the profile pass when it never shows up", async () => {
    const user = setupUser();
    getSyncStatus.mockResolvedValue(statusData({ last_runs: { backfill: shotRun({ id: 6 }) } }));
    const { queryClient } = renderWithQueryClient(<PullButton profileGraceMs={40} />);
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());

    await user.click(screen.getByTestId("pull-button"));
    await waitFor(() => expect(runSync).toHaveBeenCalled());
    getSyncStatus.mockResolvedValue(
      statusData({
        last_runs: { backfill: shotRun({ id: 7, shots_inserted: 2, shots_updated: 0 }) },
      }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Synced: 2 new shots."));
  });

  it("does not call an identity read a sync", async () => {
    // The engine reads identity on every reconnect — one frame and one
    // request, whenever the machine's Wi-Fi blinks. `running` on the ledger is
    // true for any kind, so the button used to flash "Syncing…" and go dead
    // for half a second at a time while nothing was being pulled.
    getSyncStatus.mockResolvedValue(
      statusData({
        running: true,
        last_runs: {
          identity: shotRun({ id: 9, kind: "identity", status: "running", finished_at: null }),
          backfill: shotRun({ id: 7 }),
        },
      }),
    );

    renderWithQueryClient(<PullButton />);
    await ready();

    expect(screen.queryByText("Syncing…")).not.toBeInTheDocument();
    expect(screen.getByTestId("pull-button")).toBeEnabled();
  });

  it("is disabled with no machine configured", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: false,
      connected: false,
      host: null,
      identity: null,
      last_status: null,
    });

    renderWithQueryClient(<PullButton />);

    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeDisabled());
  });

  it("is disabled while the machine is unreachable", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: false,
      host: "gaggimate.local",
      identity: null,
      last_status: null,
    });

    renderWithQueryClient(<PullButton />);

    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeDisabled());
  });

  it("shows a sync that is already running, whoever started it", async () => {
    getSyncStatus.mockResolvedValue(
      statusData({ running: true, last_runs: { backfill: shotRun({ finished_at: null }) } }),
    );

    renderWithQueryClient(<PullButton />);

    expect(await screen.findByText("Syncing…")).toBeInTheDocument();
    expect(screen.getByTestId("pull-button")).toBeDisabled();
  });
});
