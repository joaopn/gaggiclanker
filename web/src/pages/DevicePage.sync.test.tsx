import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DeviceStatusData, SyncStatusData } from "@/api/types";
import { DevicePage } from "@/pages/DevicePage";
import { boardView } from "@/test/boardFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getDeviceStatus, getSyncStatus, runSync, getDeviceWrites, getProfileBoard } = vi.hoisted(
  () => ({
    getDeviceStatus: vi.fn(),
    getProfileBoard: vi.fn(),
    getSyncStatus: vi.fn(),
    runSync: vi.fn(),
    getDeviceWrites: vi.fn(),
  }),
);
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getDeviceStatus,
  getSyncStatus,
  runSync,
  getDeviceWrites,
  getProfileBoard,
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
  getProfileBoard.mockResolvedValue(boardView({ writes_enabled: false }));
});

describe("the Device page's sync cards", () => {
  it("holds the sync and the write audit, in order, each with its anchor", async () => {
    const { container } = renderWithQueryClient(<DevicePage />);

    await screen.findByText(/120 shots/);
    const headings = screen
      .getAllByText(/^(Sync with the machine|Recent writes)$/)
      .map((node) => node.textContent);
    expect(headings).toEqual(["Sync with the machine", "Recent writes"]);
    for (const anchor of ["sync", "writes"]) {
      expect(container.querySelector(`#${anchor}`)).not.toBeNull();
    }
  });

  it.each(["#sync", "#pull", "#notes", "#storage", "#cleanup"])(
    "lands on the sync section for the %s anchor",
    async (hash) => {
      const scrolled: string[] = [];
      const original = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = function (this: Element) {
        scrolled.push(this.id);
      };
      try {
        renderWithQueryClient(<DevicePage />, { initialEntries: [`/device${hash}`] });
        await screen.findByText(/120 shots/);
        await waitFor(() => expect(scrolled).toContain("sync"));
      } finally {
        Element.prototype.scrollIntoView = original;
      }
    },
  );

  it("offers no way to send notes to the machine or to delete its shots", async () => {
    renderWithQueryClient(<DevicePage />);

    await screen.findByText(/120 shots/);
    expect(screen.queryByText(/Send notes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Clean up/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });

  it("states the rule: the only thing written to the machine is a profile", async () => {
    renderWithQueryClient(<DevicePage />);
    expect(
      await screen.findByText(/The only thing this box ever writes to it is a profile/),
    ).toBeInTheDocument();
    expect(screen.getByText(/hold exactly the profiles that are on/)).toBeInTheDocument();
    expect(screen.queryByText(/pushed from the Profiles page/)).toBeNull();
  });

  it("lists the last run of each pass and the archive counts", async () => {
    renderWithQueryClient(<DevicePage />);

    const runs = await screen.findByTestId("sync-runs");
    await waitFor(() => expect(runs).toHaveTextContent("shots"));
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

  it("has no Sync button of its own and points at the top bar's", async () => {
    renderWithQueryClient(<DevicePage />);
    expect(await screen.findByText(/presses Sync in the top bar/)).toBeInTheDocument();
    expect(screen.queryByTestId("pull-button")).not.toBeInTheDocument();
  });

  it("keeps the last sync and the audit readable when no machine is configured", async () => {
    getDeviceStatus.mockResolvedValue(
      deviceStatus({ configured: false, connected: false, host: null, identity: null }),
    );
    renderWithQueryClient(<DevicePage />);

    expect(await screen.findByText("No machine configured")).toBeInTheDocument();
    expect(await screen.findByTestId("sync-runs")).toHaveTextContent("shots");
    expect(screen.getByTestId("device-writes-empty")).toBeInTheDocument();
  });

  it("lists every write, refusals included, and says whether writes are on", async () => {
    // The refused rows are the ones worth having: "nothing tried to write" and
    // "something tried and was stopped" look identical in an audit that only
    // records what worked. A `notes_save` row is from before the notes send was
    // removed and still reads as history.
    getDeviceWrites.mockResolvedValue({
      enabled: false,
      items: [
        {
          id: 3,
          kind: "notes_save",
          host: "gaggimate.local",
          device_id: null,
          payload_hash: "",
          result: "refused",
          error: "Writing to the machine is switched off.",
          created_at: "2026-03-01T10:00:00.000Z",
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
    expect(table).toHaveTextContent("notes_save");
    expect(table).toHaveTextContent("refused");
    expect(table).toHaveTextContent("aB3xYz90Pq");
  });

  it("says plainly when this box has never written to the machine", async () => {
    renderWithQueryClient(<DevicePage />);
    expect(await screen.findByTestId("device-writes-empty")).toBeInTheDocument();
  });
});
