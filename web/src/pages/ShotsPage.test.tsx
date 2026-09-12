import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShotListData, ShotListRow, SyncStatusData } from "@/api/types";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { ShotsPage } from "@/pages/ShotsPage";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getShots, getSyncStatus, getProfiles } = vi.hoisted(() => ({
  getShots: vi.fn(),
  getSyncStatus: vi.fn(),
  getProfiles: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getShots,
  getSyncStatus,
  getProfiles,
}));

/** Shaped exactly like `ShotListRow` in gaggiclanker/db/repos/shots.py. */
function shot(overrides: Partial<ShotListRow> = {}): ShotListRow {
  return {
    id: 1,
    device_id: "000101",
    machine_id: 1,
    started_at: "2026-03-04T08:15:00.000Z",
    start_epoch: 1_772_611_200,
    duration_ms: 28_400,
    profile_version_id: 7,
    profile_id_on_device: "9bar",
    profile_name_on_device: "9 Bar Espresso",
    profile_label: "9 Bar Espresso",
    final_weight_g: 36.4,
    volume_g: 36.4,
    index_rating: null,
    index_avg_temp_c: 93.1,
    index_max_pressure_bar: 9.2,
    index_avg_flow_ml_s: 1.9,
    execution_score: 8.3,
    execution_reason: "Clean extraction.",
    sample_count: 118,
    scale_connected: true,
    incomplete: false,
    quarantined: false,
    quarantine_reason: null,
    deleted_on_device: false,
    rating: 4,
    has_notes: true,
    source: "device",
    synced_at: "2026-03-04T08:15:30.000Z",
    ...overrides,
  };
}

function listData(items: ShotListRow[]): ShotListData {
  return { items, total: items.length, limit: 50, offset: null, next_cursor: null };
}

function statusData(overrides: Partial<SyncStatusData> = {}): SyncStatusData {
  return {
    configured: true,
    connected: true,
    machine_id: 1,
    running: false,
    last_runs: {},
    last_error: null,
    counts: { total: 2, quarantined: 1, deleted_on_device: 0, incomplete: 0, samples: 236 },
    recent_events: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  getSyncStatus.mockResolvedValue(statusData());
});

describe("ShotsPage", () => {
  it("renders a row per shot with what a table column needs", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText("9 Bar Espresso")).toBeInTheDocument();
    expect(screen.getByText("28.4 s")).toBeInTheDocument();
    expect(screen.getByText("36.4 g")).toBeInTheDocument();
    expect(screen.getByText("8.3")).toBeInTheDocument();
  });

  it("flags a quarantined shot rather than hiding it", async () => {
    // The archive keeps bytes it cannot parse, so the list has to say so:
    // a shot that silently vanished would look like a shot that was never
    // pulled, which is the one thing this project must never do.
    getShots.mockResolvedValue(
      listData([shot({ id: 2, quarantined: true, execution_score: null, volume_g: null })]),
    );

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText("quarantined")).toBeInTheDocument();
  });

  it("says so when a machine has no clock", async () => {
    getShots.mockResolvedValue(listData([shot({ started_at: null, start_epoch: 0 })]));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText("no clock")).toBeInTheDocument();
  });

  it("shows the archive counts from the sync status", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    await waitFor(() => {
      expect(screen.getByText(/2 archived/)).toBeInTheDocument();
    });
    expect(screen.getByText(/1 quarantined/)).toBeInTheDocument();
  });

  it("points an empty archive at the setting that fills it", async () => {
    getShots.mockResolvedValue(listData([]));
    getSyncStatus.mockResolvedValue(statusData({ configured: false, connected: false }));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText("No shots archived yet")).toBeInTheDocument();
    expect(screen.getByText(/gaggimateHost/)).toBeInTheDocument();
  });
});

describe("ProfilesPage", () => {
  it("lists the mirror with its version hash", async () => {
    getProfiles.mockResolvedValue({
      items: [
        {
          device_id: "9bar",
          machine_id: 1,
          current_version_id: 7,
          favorite: true,
          selected: true,
          position: 0,
          first_seen_at: "2026-03-01T00:00:00.000Z",
          last_seen_at: "2026-03-04T00:00:00.000Z",
          deleted_at: null,
          label: "9 Bar Espresso",
          type: "standard",
          utility: false,
          content_hash: "abcdef0123456789",
          shot_count: 12,
        },
      ],
    });

    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByText("9 Bar Espresso")).toBeInTheDocument();
    expect(screen.getByText("selected")).toBeInTheDocument();
    expect(screen.getByText("abcdef01")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });
});
