import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DeviceStatusData, SyncStatusData } from "@/api/types";
import { publishLiveConnection, publishLiveStatus, resetLiveStatus } from "@/lib/liveStatus";
import { DevicePage } from "@/pages/DevicePage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getDeviceStatus, getSyncStatus, runSync } = vi.hoisted(() => ({
  getDeviceStatus: vi.fn(),
  getSyncStatus: vi.fn(),
  runSync: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getDeviceStatus,
  getSyncStatus,
  runSync,
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
    counts: { total: 120, quarantined: 1, deleted_on_device: 4, incomplete: 0, samples: 25_000 },
    recent_events: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetLiveStatus();
  getDeviceStatus.mockResolvedValue(deviceStatus());
  getSyncStatus.mockResolvedValue(syncStatus());
  runSync.mockResolvedValue({ queued: ["shots", "profiles", "identity"] });
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

  it("reads the telemetry off the live stream, not the status poll", async () => {
    renderWithQueryClient(<DevicePage />);
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    publishLiveStatus({
      m: 1,
      ct: 92.7,
      tt: 93,
      pr: 8.9,
      cp: true,
      bc: true,
      sbat: 74,
      p: "9 Bar",
    });

    await waitFor(() => {
      expect(screen.getByTestId("device-telemetry")).toHaveTextContent("92.7 °C");
    });
    const telemetry = screen.getByTestId("device-telemetry");
    expect(telemetry).toHaveTextContent("Brew");
    expect(telemetry).toHaveTextContent("yes (Pro board)");
    expect(telemetry).toHaveTextContent("connected · 74 %");
  });

  it("says when the board has no pressure sensor", async () => {
    // Every pressure diagnostic is gated on this flag; the device page is
    // where a reader finds out why half of them are missing.
    renderWithQueryClient(<DevicePage />);
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    publishLiveStatus({ cp: false, ct: 90 });

    await waitFor(() => {
      expect(screen.getByTestId("device-telemetry")).toHaveTextContent("no (Standard board)");
    });
  });

  it("shows the machine's active warnings", async () => {
    renderWithQueryClient(<DevicePage />);
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    publishLiveStatus({
      warn: [
        { k: "water_low", l: 1, a: true },
        { k: "scale_lost", l: 2, a: false },
      ],
    });

    const warnings = await screen.findByTestId("device-warnings");
    expect(warnings).toHaveTextContent("water_low");
    // Inactive warnings are history; the firmware sends every kind it knows.
    expect(warnings).not.toHaveTextContent("scale_lost");
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
    publishLiveConnection({ connected: false, configured: false });

    renderWithQueryClient(<DevicePage />);

    expect(await screen.findByText("No machine configured")).toBeInTheDocument();
    expect(screen.getByText(/gaggimateHost/)).toBeInTheDocument();
  });
});
