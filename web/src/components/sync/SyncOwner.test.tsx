import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SyncStatusData } from "@/api/types";
import { PullButton } from "@/components/PullButton";
import { SyncOwnerProvider, useSyncOwner } from "@/components/sync/SyncOwner";
import { EVENT_INVALIDATIONS } from "@/lib/invalidate";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getSyncStatus, getDeviceStatus, runSync, getSyncRunsAfter } = vi.hoisted(() => ({
  getSyncStatus: vi.fn(),
  getSyncRunsAfter: vi.fn(),
  getDeviceStatus: vi.fn(),
  runSync: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSyncStatus,
  getDeviceStatus,
  runSync,
  getSyncRunsAfter,
}));

function run(id: number, kind: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    kind,
    status: "ok",
    trigger: "manual",
    started_at: "2026-03-04T08:00:00.000Z",
    finished_at: "2026-03-04T08:00:20.000Z",
    shots_seen: 0,
    shots_inserted: 0,
    shots_updated: 0,
    shots_quarantined: 0,
    profiles_changed: 0,
    notes_synced: 0,
    errors: 0,
    error: null,
    summary: kind === "profiles" ? { profiles_read: 4, writes: 0 } : null,
    ...overrides,
  };
}

function status(lastRuns: Record<string, unknown>): SyncStatusData {
  return {
    configured: true,
    connected: true,
    running: false,
    last_runs: lastRuns as never,
    last_error: null,
    counts: {
      total: 2,
      quarantined: 0,
      deleted_on_device: 0,
      incomplete: 0,
      samples: 2,
      needs_set: 0,
    },
    recent_events: [],
  };
}

/** What a proposal card does: ask under its own key and say whether this sync is its own. */
function Asker({ name }: { name: string }) {
  const owner = useSyncOwner();
  return (
    <>
      <button type="button" onClick={() => void owner.startSync(name)}>
        ask {name}
      </button>
      <span data-testid={`${name}-syncing`}>{owner.waitingFor(name) ? "yes" : "no"}</span>
    </>
  );
}

/** A card's standing read, under the drafts prefix as the real one is, held by the test. */
function StandingProbe({ read }: { read: () => Promise<string> }) {
  const reads = useRef(0);
  const query = useQuery<string>({
    queryKey: ["drafts", "standing", 1],
    queryFn: () => {
      reads.current += 1;
      return reads.current === 1 ? Promise.resolve("approved") : read();
    },
    staleTime: Number.POSITIVE_INFINITY,
  });
  return (
    <span data-testid="probe">
      {query.isFetching && reads.current > 1 ? "reading" : query.data}
    </span>
  );
}

/** A page that comes and goes while the sync it started goes on. */
function Page() {
  const [shown, setShown] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setShown(false)}>
        leave
      </button>
      {shown ? <Asker name="card" /> : null}
    </>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getSyncStatus.mockResolvedValue(
    status({ backfill: run(6, "backfill"), profiles: run(5, "profiles") }),
  );
  getDeviceStatus.mockResolvedValue({
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: null,
    last_status: null,
  });
  runSync.mockResolvedValue({ queued: ["shots", "profiles", "identity"] });
  getSyncRunsAfter.mockImplementation(async (after: number) => {
    const current = (await getSyncStatus()) as SyncStatusData;
    const runs = Object.values(current.last_runs ?? {}).filter((entry) => entry.id > after);
    return { runs: runs.sort((left, right) => left.id - right.id), truncated: false };
  });
});

async function land(queryClient: import("@tanstack/react-query").QueryClient) {
  getSyncStatus.mockResolvedValue(
    status({
      backfill: run(7, "backfill", { shots_inserted: 1 }),
      profiles: run(8, "profiles"),
    }),
  );
  for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
    await queryClient.invalidateQueries({ queryKey });
  }
}

describe("the sync owner", () => {
  it("tells once when the top bar and a card share one sync", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="card" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());

    await user.click(screen.getByText("ask card"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("card-syncing")).toHaveTextContent("yes"));
    await land(queryClient);

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    // Nothing else tells it again, however the ledger settles.
    await act(async () => {
      await queryClient.invalidateQueries();
    });
    expect(toast.success).toHaveBeenCalledTimes(1);
    expect(toast.error).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("card-syncing")).toHaveTextContent("no"));
  });

  it("still tells when the card that started the sync is gone", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Page />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());

    await user.click(screen.getByText("ask card"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    await user.click(screen.getByText("leave"));
    expect(screen.queryByText("ask card")).not.toBeInTheDocument();
    await land(queryClient);

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
  });

  it("asks the server again for a request made while one is watched, and tells once for both", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <Asker name="two" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());

    await user.click(screen.getByText("ask one"));
    await user.click(screen.getByText("ask two"));

    // The second approval is sent too: the first pass may have been read before it was put.
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("one-syncing")).toHaveTextContent("yes");
    expect(screen.getByTestId("two-syncing")).toHaveTextContent("yes");
    await land(queryClient);

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    // And once it has told, nobody is still syncing, whichever key asked.
    await waitFor(() => expect(screen.getByTestId("two-syncing")).toHaveTextContent("no"));
    expect(screen.getByTestId("one-syncing")).toHaveTextContent("no");
  });

  it("waits past the later pass: the first pass finishing alone does not tell", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <Asker name="two" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    // The first pass is running on the ledger when the second ask is made.
    getSyncStatus.mockResolvedValue(
      status({
        backfill: run(7, "backfill", { status: "running", finished_at: null }),
        profiles: run(5, "profiles"),
      }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeDisabled());
    await user.click(screen.getByText("ask two"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(2));

    // The first pass ends: not the one that carries the second approval.
    getSyncStatus.mockResolvedValue(
      status({ backfill: run(7, "backfill"), profiles: run(8, "profiles") }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(toast.success).not.toHaveBeenCalled();

    getSyncStatus.mockResolvedValue(
      status({ backfill: run(9, "backfill"), profiles: run(10, "profiles") }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
  });

  it("keeps watching the first request when the second is refused", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <Asker name="two" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    runSync.mockRejectedValueOnce(new Error("busy"));
    await user.click(screen.getByText("ask two"));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("busy"));
    expect(screen.getByTestId("one-syncing")).toHaveTextContent("yes");
    await land(queryClient);

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
  });

  it("does not carry an earlier sync's asker into the next sync", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <Asker name="two" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await land(queryClient);
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));

    await user.click(screen.getByText("ask two"));
    await waitFor(() => expect(screen.getByTestId("two-syncing")).toHaveTextContent("yes"));

    expect(screen.getByTestId("one-syncing")).toHaveTextContent("no");
  });

  /** The ledger as the events leave it: these runs, newest per kind. */
  async function ledgerIs(
    queryClient: import("@tanstack/react-query").QueryClient,
    runs: Record<string, unknown>,
  ) {
    getSyncStatus.mockResolvedValue(status(runs));
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }
  }

  const pushed = (writes: number, labels: string[]) => ({
    profiles_read: 9,
    writes,
    pushed: labels.map((label) => ({ label, reason: "missing", detail: "" })),
    removed: [],
    home_screen: [],
    failures: [],
    adopted: [],
    left: [],
  });

  it("keeps the first request's wait when a second is refused after the ledger has moved", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <Asker name="two" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    // The first sync's shot run is on the ledger, running, when the second click lands.
    await ledgerIs(queryClient, {
      backfill: run(7, "backfill", { status: "running", finished_at: null }),
      profiles: run(5, "profiles"),
    });
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeDisabled());
    runSync.mockRejectedValueOnce(new Error("busy"));
    await user.click(screen.getByText("ask two"));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("busy"));

    // Only the card that was refused is taken back.
    expect(screen.getByTestId("one-syncing")).toHaveTextContent("yes");
    expect(screen.getByTestId("two-syncing")).toHaveTextContent("no");
    // The first sync's own run finishing is what the wait is for.
    await ledgerIs(queryClient, {
      backfill: run(7, "backfill", { shots_inserted: 1 }),
      profiles: run(8, "profiles"),
    });

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("one-syncing")).toHaveTextContent("no"));
  });

  it("counts every pass it covers in its one sentence", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <Asker name="two" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    await ledgerIs(queryClient, {
      backfill: run(7, "backfill", { status: "running", finished_at: null }),
      profiles: run(5, "profiles"),
    });
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeDisabled());
    await user.click(screen.getByText("ask two"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(2));

    // The first pass ends having pushed one profile; the later pass pushes the other.
    await ledgerIs(queryClient, {
      backfill: run(7, "backfill", { shots_inserted: 1 }),
      profiles: run(8, "profiles", { summary: pushed(1, ["Join One"]) }),
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(toast.success).not.toHaveBeenCalled();
    // The ledger now shows only the newest pass of each kind; the server has all four.
    getSyncRunsAfter.mockResolvedValue({
      runs: [
        run(7, "backfill", { shots_inserted: 1 }),
        run(8, "profiles", { summary: pushed(1, ["Join One"]) }),
        run(9, "backfill", { shots_inserted: 2 }),
        run(10, "profiles", { summary: pushed(1, ["Join Two"]) }),
      ],
      truncated: false,
    });
    await ledgerIs(queryClient, {
      backfill: run(9, "backfill", { shots_inserted: 2 }),
      profiles: run(10, "profiles", { summary: pushed(1, ["Join Two"]) }),
    });

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(toast.success).toHaveBeenCalledWith(
      "Synced: 3 new shots. Read 9 profiles from the machine; wrote 2 (pushed 2).",
    );
    // Asked once, for everything after the newest run on the ledger when the first start was made.
    expect(getSyncRunsAfter).toHaveBeenCalledTimes(1);
    expect(getSyncRunsAfter).toHaveBeenCalledWith(6);
  });

  it("does not turn a notes run's failed fetch into the sync's failure", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    getSyncRunsAfter.mockResolvedValue({
      runs: [
        run(7, "backfill", { shots_inserted: 2 }),
        run(8, "notes", {
          status: "error",
          errors: 1,
          error: "Could not fetch /api/history/000301.json",
        }),
        run(9, "profiles"),
      ],
      truncated: false,
    });
    await land(queryClient);

    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(toast.success).toHaveBeenCalledWith(
      "Synced: 2 new shots. Read 4 profiles from the machine; no writes needed.",
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("reads the runs after the newest run of any kind, not the newest shot run", async () => {
    const user = setupUser();
    // The previous sync left a notes run (id 12) and an identity run (13) above its shot run.
    getSyncStatus.mockResolvedValue(
      status({
        backfill: run(6, "backfill"),
        profiles: run(5, "profiles"),
        notes: run(12, "notes"),
        identity: run(13, "identity"),
      }),
    );
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await land(queryClient);

    await waitFor(() => expect(getSyncRunsAfter).toHaveBeenCalledTimes(1));
    expect(getSyncRunsAfter).toHaveBeenCalledWith(13);
  });

  it("says only Synced when the server had more runs than one read returns", async () => {
    const user = setupUser();
    getSyncRunsAfter.mockResolvedValue({
      runs: [run(7, "backfill", { shots_inserted: 2 })],
      truncated: true,
    });
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await land(queryClient);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Synced."));
    expect(toast.success).toHaveBeenCalledTimes(1);
  });

  it("says only Synced, with no counts, when the runs cannot be read", async () => {
    const user = setupUser();
    getSyncRunsAfter.mockRejectedValue(new Error("down"));
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await land(queryClient);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Synced."));
    expect(toast.success).toHaveBeenCalledTimes(1);
  });

  it("waits for the later profile pass too, not only the later shot pass", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <Asker name="two" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    await ledgerIs(queryClient, {
      backfill: run(7, "backfill"),
      profiles: run(8, "profiles", { status: "running", finished_at: null }),
    });
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeDisabled());
    await user.click(screen.getByText("ask two"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(2));

    // The later shot pass is done and the first profile pass has finished: the later profile
    // pass has not appeared, and the sentence must wait for it.
    await ledgerIs(queryClient, {
      backfill: run(9, "backfill"),
      profiles: run(8, "profiles", { summary: pushed(1, ["Join One"]) }),
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("does not hang on a refused start after an earlier wait has ended", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <Asker name="two" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await user.click(screen.getByText("ask one"));
    await land(queryClient);
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));

    runSync.mockRejectedValueOnce(new Error("No machine is configured."));
    await user.click(screen.getByText("ask two"));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("No machine is configured."));

    expect(screen.getByTestId("two-syncing")).toHaveTextContent("no");
    expect(screen.getByTestId("pull-button")).toBeEnabled();
  });

  it("keeps saying Syncing until the cards' own reads have landed", async () => {
    const user = setupUser();
    let release: () => void = () => undefined;
    const { queryClient } = renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="one" />
        <StandingProbe
          read={() =>
            new Promise<string>((resolve) => {
              release = () => resolve("on_machine");
            })
          }
        />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("approved"));
    await user.click(screen.getByText("ask one"));
    await land(queryClient);
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));

    // The standing is being read again (held): the card still says Syncing, not its old sentence.
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("reading"));
    expect(screen.getByTestId("one-syncing")).toHaveTextContent("yes");
    release();
    await waitFor(() => expect(screen.getByTestId("one-syncing")).toHaveTextContent("no"));
  });

  it("answers false and says why when the request is refused, and watches nothing", async () => {
    const user = setupUser();
    runSync.mockRejectedValue(new Error("No machine is configured."));
    renderWithQueryClient(
      <SyncOwnerProvider>
        <PullButton />
        <Asker name="card" />
      </SyncOwnerProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeEnabled());

    await user.click(screen.getByText("ask card"));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("No machine is configured."));
    expect(screen.getByTestId("card-syncing")).toHaveTextContent("no");
    // A later request is a fresh one, not joined to the refused one.
    runSync.mockResolvedValue({ queued: ["shots", "profiles"] });
    await user.click(screen.getByText("ask card"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(2));
  });
});
