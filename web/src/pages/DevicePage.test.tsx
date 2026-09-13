import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DeviceStatusData, SyncStatusData } from "@/api/types";
import { DevicePage } from "@/pages/DevicePage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  getDeviceStatus,
  getSyncStatus,
  runSync,
  getDeviceWrites,
  getCleanupPlan,
  getCleanupRuns,
  getPendingNotes,
} = vi.hoisted(() => ({
  getDeviceStatus: vi.fn(),
  getSyncStatus: vi.fn(),
  runSync: vi.fn(),
  getDeviceWrites: vi.fn(),
  getCleanupPlan: vi.fn(),
  getCleanupRuns: vi.fn(),
  getPendingNotes: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getDeviceStatus,
  getSyncStatus,
  runSync,
  getDeviceWrites,
  getCleanupPlan,
  getCleanupRuns,
  getPendingNotes,
}));

function deviceStatus(overrides: Partial<DeviceStatusData> = {}): DeviceStatusData {
  return {
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: {
      hardware: "GaggiMate Pro",
      displayVersion: "1.8.2",
      controllerVersion: "1.8.2",
      latestVersion: "1.8.3",
      channel: "latest",
      updating: false,
      spiffsTotal: 1_048_576,
      spiffsUsed: 786_432,
      sdTotal: 0,
    },
    last_status: null,
    ...overrides,
  };
}

function syncStatus(overrides: Partial<SyncStatusData> = {}): SyncStatusData {
  return {
    configured: true,
    connected: true,
    machine_id: 1,
    running: false,
    last_runs: {
      shots: {
        id: 1,
        kind: "shots",
        status: "ok",
        trigger: "periodic",
        started_at: "2026-03-04T08:00:00.000Z",
        finished_at: "2026-03-04T08:00:04.000Z",
        shots_seen: 12,
        shots_inserted: 2,
        shots_updated: 0,
        shots_quarantined: 0,
        profiles_changed: 0,
        notes_synced: 0,
        errors: 0,
        error: null,
      },
    },
    last_error: null,
    counts: {
      total: 120,
      quarantined: 1,
      deleted_on_device: 4,
      incomplete: 0,
      samples: 25_000,
      needs_set: 0,
    },
    recent_events: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  getDeviceStatus.mockResolvedValue(deviceStatus());
  getSyncStatus.mockResolvedValue(syncStatus());
  runSync.mockResolvedValue({ queued: ["shots", "profiles", "identity"] });
  getDeviceWrites.mockResolvedValue({ enabled: false, items: [] });
  // The Storage and notes cards are part of this page now.
  // Their own suites cover what they render; here they only have to answer, so
  // the page is not asserting on a card stuck in its error state.
  getCleanupPlan.mockResolvedValue({
    machine_id: 1,
    policy: {
      mode: "off",
      keep_newest: 50,
      min_free_kb: 2048,
      auto: false,
      writes_enabled: false,
    },
    on_device_count: 12,
    free_bytes: 262_144,
    free_source: "spiffs",
    planned: [],
    skipped: [],
    blocked: null,
  });
  getCleanupRuns.mockResolvedValue({ items: [] });
  getPendingNotes.mockResolvedValue({
    enabled: false,
    writes_enabled: false,
    fields: ["rating"],
    shot_ids: [],
  });
});

describe("DevicePage", () => {
  it("names the board, the versions and the storage", async () => {
    renderWithQueryClient(<DevicePage />);

    expect(await screen.findByRole("heading", { name: "GaggiMate Pro" })).toBeInTheDocument();
    expect(screen.getByText(/gaggimate.local/)).toBeInTheDocument();
    expect(screen.getByTestId("device-versions")).toHaveTextContent("1.8.3");
    // SPIFFS only: this machine reports no SD card, and an empty bar for one
    // would be a lie about the hardware.
    const storage = screen.getByTestId("device-storage");
    expect(storage).toHaveTextContent("Internal (SPIFFS)");
    expect(storage).not.toHaveTextContent("SD card");
  });

  it("has no telemetry card, because the machine's own UI has one", async () => {
    // The page used to hold a "Right now" card and a warnings list off a 2 Hz
    // stream. Both are gone with the stream: what is happening at the machine
    // is best read at the machine, and this page answers the question that is
    // not — what this box has pulled off it, and what it has written to it.
    renderWithQueryClient(<DevicePage />);
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    expect(screen.queryByTestId("device-telemetry")).not.toBeInTheDocument();
    expect(screen.queryByTestId("device-warnings")).not.toBeInTheDocument();
  });

  it("lists the last run of each pass and the archive counts", async () => {
    renderWithQueryClient(<DevicePage />);

    const runs = await screen.findByTestId("sync-runs");
    expect(runs).toHaveTextContent("shots");
    expect(runs).toHaveTextContent("ok");
    expect(screen.getByText(/120 shots/)).toBeInTheDocument();
    expect(screen.getByText(/4 gone/)).toBeInTheDocument();
  });

  it("surfaces the last failure rather than a bare red light", async () => {
    getSyncStatus.mockResolvedValue(
      syncStatus({
        last_error: {
          id: 9,
          kind: "shots",
          status: "error",
          trigger: "manual",
          started_at: "2026-03-04T07:00:00.000Z",
          finished_at: "2026-03-04T07:00:01.000Z",
          shots_seen: 0,
          shots_inserted: 0,
          shots_updated: 0,
          shots_quarantined: 0,
          profiles_changed: 0,
          notes_synced: 0,
          errors: 1,
          error: "503 during an OTA update",
        },
      }),
    );

    renderWithQueryClient(<DevicePage />);

    expect(await screen.findByText(/503 during an OTA update/)).toBeInTheDocument();
  });

  it("queues a sync and says what was queued", async () => {
    // 202: the loops are woken, nothing has touched the machine yet.
    const user = setupUser();
    renderWithQueryClient(<DevicePage />);
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    await user.click(screen.getByRole("button", { name: "Sync now" }));

    await waitFor(() => expect(runSync).toHaveBeenCalledWith("all"));
    expect(toast.success).toHaveBeenCalledWith("Queued: shots, profiles, identity");
  });

  it("treats 'no machine configured' as a setup step, not a fault", async () => {
    getDeviceStatus.mockResolvedValue(
      deviceStatus({ configured: false, connected: false, host: null, identity: null }),
    );

    renderWithQueryClient(<DevicePage />);

    expect(await screen.findByText("No machine configured")).toBeInTheDocument();
    expect(screen.getByText(/gaggimateHost/)).toBeInTheDocument();
  });

  it("lists every write, refusals included, and says whether writes are on", async () => {
    // The refused rows are the ones worth having: "nothing tried to write" and
    // "something tried and was stopped" look identical in an audit that only
    // records what worked.
    getDeviceWrites.mockResolvedValue({
      enabled: false,
      items: [
        {
          id: 2,
          kind: "profile_save",
          host: "gaggimate.local",
          device_id: null,
          payload_hash: "abc",
          result: "refused",
          error: "Writing to the machine is switched off.",
          created_at: "2026-03-01T09:00:00.000Z",
        },
        {
          id: 1,
          kind: "profile_delete",
          host: "gaggimate.local",
          device_id: "aB3xYz90Pq",
          payload_hash: "def",
          result: "ok",
          error: "",
          created_at: "2026-02-28T09:00:00.000Z",
        },
      ],
    });
    renderWithQueryClient(<DevicePage />);

    const table = await screen.findByTestId("device-writes");
    expect(table).toHaveTextContent("profile_save");
    expect(table).toHaveTextContent("refused");
    expect(table).toHaveTextContent("aB3xYz90Pq");
    expect(screen.getByText("writes off")).toBeInTheDocument();
  });

  it("says plainly when this box has never written to the machine", async () => {
    renderWithQueryClient(<DevicePage />);
    expect(await screen.findByTestId("device-writes-empty")).toBeInTheDocument();
  });

  it("carries the storage and notes cards, with both switches off by default", async () => {
    // Both are rendered whatever the switches say: a card that disappeared when
    // writes were off would leave nowhere to find out that they are.
    renderWithQueryClient(<DevicePage />);

    await waitFor(() => expect(screen.getByTestId("cleanup-summary")).toHaveTextContent("12"));
    expect(screen.getByText("cleanup off")).toBeInTheDocument();
    expect(screen.getByTestId("cleanup-runs-empty")).toBeInTheDocument();
    expect(screen.getByText("write-back off")).toBeInTheDocument();
  });
});
