import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ProfileVersionListData,
  ShotListData,
  ShotListRow,
  ShotSamplesData,
  SyncStatusData,
} from "@/api/types";
import { EVENT_INVALIDATIONS } from "@/lib/invalidate";
import { publishLiveStatus, resetLiveStatus } from "@/lib/liveStatus";
import { ShotsPage } from "@/pages/ShotsPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { syntheticSamples } from "@/test/shotFixture";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getShots, getSyncStatus, getProfileVersions, getShotSamples } = vi.hoisted(() => ({
  getShots: vi.fn(),
  getSyncStatus: vi.fn(),
  getProfileVersions: vi.fn(),
  getShotSamples: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getShots,
  getSyncStatus,
  getProfileVersions,
  getShotSamples,
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

function listData(items: ShotListRow[], overrides: Partial<ShotListData> = {}): ShotListData {
  return { items, total: items.length, limit: 50, offset: null, next_cursor: null, ...overrides };
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

const versions: ProfileVersionListData = {
  items: [
    {
      id: 7,
      content_hash: "abcdef0123456789",
      // Deliberately not the label any shot row carries: the filter's options
      // and the table's rows share a document, and a test that cannot tell
      // them apart is testing neither.
      label: "Nine Bar (mirrored)",
      type: "standard",
      utility: false,
      source: "device",
      created_at: "2026-03-01T00:00:00.000Z",
      mirrored: true,
      shot_count: 12,
    },
    {
      id: 9,
      content_hash: "99887766",
      label: "Cremina v2 (imported)",
      type: "pro",
      utility: false,
      source: "import",
      created_at: "2026-02-01T00:00:00.000Z",
      mirrored: false,
      shot_count: 1,
    },
  ],
  total: 2,
  limit: 200,
  offset: 0,
};

const samplesData: ShotSamplesData = {
  shot_id: 1,
  count: 40,
  total: 213,
  sample_interval_ms: 250,
  downsampled: true,
  samples: syntheticSamples(40),
};

beforeEach(() => {
  vi.clearAllMocks();
  resetLiveStatus();
  getSyncStatus.mockResolvedValue(statusData());
  getProfileVersions.mockResolvedValue(versions);
  getShotSamples.mockResolvedValue(samplesData);
});

describe("ShotsPage", () => {
  it("renders a row per shot with what a table column needs", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText("9 Bar Espresso")).toBeInTheDocument();
    expect(screen.getByText("28.4 s")).toBeInTheDocument();
    expect(screen.getByText("36.4 g")).toBeInTheDocument();
    expect(screen.getByTestId("score-badge")).toHaveTextContent("8.3");
    expect(screen.getByTestId("rating-stars")).toHaveAttribute("data-rating", "4");
    // The row is a link, because the shot page is where everything else is.
    expect(screen.getByRole("link", { name: /9 Bar Espresso/ })).toHaveAttribute(
      "href",
      "/shots/1",
    );
  });

  it("colours the score by band rather than linearly", async () => {
    // The score is ten minus penalties, so the interesting ground is the top
    // half; a linear ramp would paint every shot green.
    getShots.mockResolvedValue(
      listData([shot({ id: 1, execution_score: 9.1 }), shot({ id: 2, execution_score: 3.2 })]),
    );

    renderWithQueryClient(<ShotsPage />);

    const badges = await screen.findAllByTestId("score-badge");
    expect(badges[0]).toHaveAttribute("data-tone", "good");
    expect(badges[1]).toHaveAttribute("data-tone", "bad");
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
    // No curve to draw, so no request for one.
    expect(screen.queryByTestId("shot-sparkline")).not.toBeInTheDocument();
  });

  it("leaves a slot for the Set badge and the analysis state", async () => {
    // Sets and the analyzer fill these in. The column exists now so that "no Set" reads
    // as a state rather than a missing feature.
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByTestId("set-badge-slot")).toBeInTheDocument();
    expect(screen.getByTestId("analysis-slot")).toBeInTheDocument();
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

  it("fetches a sparkline per row, thinned", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    await waitFor(() => expect(getShotSamples).toHaveBeenCalledWith(1, 40));
    expect(await screen.findByTestId("shot-sparkline")).toBeInTheDocument();
  });
});

describe("ShotsPage filters", () => {
  it("sends each filter to the API and keeps the rest alone", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("9 Bar Espresso");

    await user.selectOptions(screen.getByLabelText("Score"), "clean");
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ min_score: 8 })),
    );

    await user.selectOptions(screen.getByLabelText("Rating"), "4");
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(
        expect.objectContaining({ min_score: 8, min_rating: 4 }),
      ),
    );

    await user.selectOptions(screen.getByLabelText("Source"), "import");
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ source: "import" })),
    );
  });

  it("offers every stored profile version, imported ones included", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    expect(
      await screen.findByRole("option", { name: "Cremina v2 (imported)" }),
    ).toBeInTheDocument();
    const select = screen.getByLabelText("Profile");
    expect(within(select).getByRole("option", { name: "Nine Bar (mirrored)" })).toBeInTheDocument();
  });

  it("switches to offset paging when the sort is not the cursor's own key", async () => {
    // Only `started_at` supports a cursor, because the cursor *is* that key
    // (gaggiclanker/api/shots.py). Sending one with another sort is a 400.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()], { total: 200, next_cursor: "abc" }));

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("9 Bar Espresso");

    await user.selectOptions(screen.getByLabelText("Sort"), "execution_score:asc");
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "execution_score", order: "asc" }),
      ),
    );

    await user.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 1 })),
    );
    expect(getShots).not.toHaveBeenCalledWith(expect.objectContaining({ cursor: "abc" }));
  });

  it("pages oldest-first by offset, because no cursor is issued walking forwards", async () => {
    // The server only hands back a cursor for `started_at` DESC. Choosing
    // keyset from the sort alone left "oldest first" waiting for a cursor that
    // never came: `hasNextPage` was false after page one and the rest of the
    // archive was silently unreachable.
    const user = setupUser();
    getShots
      .mockResolvedValueOnce(listData([shot({ id: 1 })], { total: 3 }))
      .mockResolvedValueOnce(listData([shot({ id: 1 })], { total: 3 }))
      .mockResolvedValueOnce(
        listData([shot({ id: 2, profile_label: "The second page" })], { total: 3 }),
      );

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("9 Bar Espresso");

    await user.selectOptions(screen.getByLabelText("Sort"), "started_at:asc");
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "started_at", order: "asc" }),
      ),
    );

    await user.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByText("The second page")).toBeInTheDocument();
    expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ order: "asc", offset: 1 }));
  });

  it("says nothing matches rather than pretending the archive is empty", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([]));

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("No shots archived yet");

    await user.selectOptions(screen.getByLabelText("Score"), "poor");

    expect(await screen.findByText("No shots match")).toBeInTheDocument();
  });
});

describe("ShotsPage paging", () => {
  it("loads the next page with the cursor the server handed back", async () => {
    // Keyset, not offset: the sync engine inserts rows above the reader while
    // they scroll, and with offsets they would read the same shot twice.
    const user = setupUser();
    getShots
      .mockResolvedValueOnce(listData([shot({ id: 1 })], { total: 2, next_cursor: "cursor-1" }))
      .mockResolvedValueOnce(
        listData([shot({ id: 2, profile_label: "Cremina v2" })], { total: 2 }),
      );

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("9 Bar Espresso");

    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Cremina v2")).toBeInTheDocument();
    expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "cursor-1" }));
    expect(screen.getByText("Showing 2 of 2")).toBeInTheDocument();
  });
});

describe("ShotsPage live refresh", () => {
  it("shows a new shot at the top when the sync engine says one landed", async () => {
    // The acceptance criterion: a shot pulled on the machine appears here
    // without a reload. `shot.ingested` on /api/sync/events maps onto this
    // query's key (lib/invalidate.ts) and the list re-reads.
    getShots.mockResolvedValue(listData([shot({ id: 1 })]));

    const { queryClient } = renderWithQueryClient(<ShotsPage />);
    await screen.findByText("9 Bar Espresso");

    getShots.mockResolvedValue(
      listData([shot({ id: 2, profile_label: "Fresh off the machine" }), shot({ id: 1 })]),
    );
    for (const queryKey of EVENT_INVALIDATIONS["shot.ingested"]) {
      await queryClient.invalidateQueries({ queryKey });
    }

    expect(await screen.findByText("Fresh off the machine")).toBeInTheDocument();
  });

  it("banners a shot in progress and links to the live view", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("9 Bar Espresso");

    publishLiveStatus({ process: { a: 1, l: "Infusion", e: 4500 }, pr: 6.2 });

    const banner = await screen.findByTestId("live-banner");
    expect(banner).toHaveAttribute("href", "/live");
    expect(banner).toHaveTextContent("Infusion");
    expect(banner).toHaveTextContent("0:04");
  });
});

describe("ShotsPage virtualisation", () => {
  it("renders a window of a thousand shots, not a thousand rows", async () => {
    // A year of shots is a thousand rows, each with a sparkline and five
    // badges. Mounting them all is tens of thousands of nodes laid out on
    // every scroll frame.
    const many = Array.from({ length: 1000 }, (_, index) =>
      shot({ id: index + 1, device_id: String(index + 1).padStart(6, "0") }),
    );
    getShots.mockResolvedValue(listData(many, { total: 1000 }));

    renderWithQueryClient(<ShotsPage />);

    await screen.findByText("Showing 1000 of 1000");
    const rows = screen.getAllByTestId("shot-row");
    expect(rows.length).toBeGreaterThan(5);
    expect(rows.length).toBeLessThan(60);
    // And the sparklines follow the rows, so the archive is not fetched whole.
    expect(getShotSamples.mock.calls.length).toBeLessThan(60);
  });
});

describe("ShotsPage compare drawer", () => {
  it("overlays the shots that were ticked", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(
      listData([
        shot({ id: 1 }),
        shot({ id: 2, device_id: "000102", profile_label: "Cremina v2" }),
      ]),
    );

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("9 Bar Espresso");

    await user.click(screen.getByRole("checkbox", { name: "Compare shot 000101" }));
    await user.click(screen.getByRole("checkbox", { name: "Compare shot 000102" }));

    const drawer = await screen.findByTestId("compare-drawer");
    expect(drawer).toHaveTextContent("Comparing 2 shots");
    // Two shots, pressure and puck flow each.
    const series = await screen.findByTestId("compare-series");
    expect(within(series).getAllByRole("listitem")).toHaveLength(4);
  });

  it("stops at three, because a fourth line makes the overlay unreadable", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(
      listData([1, 2, 3, 4].map((id) => shot({ id, device_id: String(id).padStart(6, "0") }))),
    );

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("Showing 4 of 4");

    const checkboxes = screen.getAllByRole("checkbox");
    for (const checkbox of checkboxes.slice(0, 3)) await user.click(checkbox);

    expect(checkboxes[3]).toBeDisabled();
    expect(await screen.findByTestId("compare-drawer")).toHaveTextContent("Comparing 3 shots");
  });
});

describe("ShotsPage deep links", () => {
  it("starts filtered when the URL says so", async () => {
    // The profiles page links a version's shot count straight at this list.
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />, { initialEntries: ["/shots?profile_version_id=9"] });

    await waitFor(() =>
      expect(getShots).toHaveBeenCalledWith(expect.objectContaining({ profile_version_id: 9 })),
    );
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("9"));
  });
});
