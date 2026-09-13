import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ProfileVersionListData,
  ShotListData,
  ShotListRow,
  ShotSamplesData,
  SyncStatusData,
} from "@/api/types";
import { EVENT_INVALIDATIONS } from "@/lib/invalidate";
import { ShotsPage } from "@/pages/ShotsPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { judgement, setRow } from "@/test/setsFixtures";
import { shot129, syntheticSamples } from "@/test/shotFixture";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  getShots,
  getSyncStatus,
  getProfileVersions,
  getShotSamples,
  getSets,
  getShot,
  putJudgement,
  putShotSetVersion,
  getDeviceStatus,
  runSync,
  importFiles,
} = vi.hoisted(() => ({
  getShots: vi.fn(),
  getSyncStatus: vi.fn(),
  getProfileVersions: vi.fn(),
  getShotSamples: vi.fn(),
  getSets: vi.fn(),
  getShot: vi.fn(),
  putJudgement: vi.fn(),
  putShotSetVersion: vi.fn(),
  getDeviceStatus: vi.fn(),
  runSync: vi.fn(),
  importFiles: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getShots,
  getSyncStatus,
  getProfileVersions,
  getShotSamples,
  getSets,
  getShot,
  putJudgement,
  putShotSetVersion,
  getDeviceStatus,
  runSync,
  importFiles,
}));

/** Shaped exactly like `ShotListRow` in gaggiclanker/db/repos/shots.py. */
function shot(overrides: Partial<ShotListRow> = {}): ShotListRow {
  return {
    id: 1,
    device_id: "000101",
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
    analysis_state: "none",
    rating: 4,
    has_notes: true,
    has_judgement: false,
    set_version_id: null,
    set_badge: null,
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

/**
 * The list has loaded. Not "the profile name is on screen", which it used to
 * be: the Profile column is off by default now, and a test that waited for a
 * string in a column nobody asked for would be testing the column chooser.
 */
async function listed(): Promise<HTMLElement> {
  return screen.findByTestId("shot-rows");
}

/** The filters live behind a button, so a test that sets one opens it first. */
async function openFilters(user: ReturnType<typeof setupUser>): Promise<void> {
  await user.click(await screen.findByTestId("filters-button"));
  await screen.findByTestId("shot-filters");
}

/** Turn a column on through the chooser, the way a reader would. */
async function showColumn(user: ReturnType<typeof setupUser>, label: string): Promise<void> {
  await user.click(await screen.findByTestId("columns-button"));
  await user.click(await screen.findByRole("checkbox", { name: label }));
  await user.keyboard("{Escape}");
}

beforeEach(() => {
  vi.clearAllMocks();
  // The visible columns are stored per browser, so one test's chooser must not
  // decide what the next one renders.
  window.localStorage.clear();
  getSyncStatus.mockResolvedValue(statusData());
  getProfileVersions.mockResolvedValue(versions);
  getShotSamples.mockResolvedValue(samplesData);
  getSets.mockResolvedValue({ items: [setRow()] });
  getShot.mockResolvedValue({ ...shot129, judgement: judgement() });
  putJudgement.mockImplementation((_id: number, body: unknown) => Promise.resolve(body));
  putShotSetVersion.mockResolvedValue(shot());
  getDeviceStatus.mockResolvedValue({
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: null,
    last_status: null,
  });
  runSync.mockResolvedValue({ queued: ["shots", "profiles", "identity"] });
  importFiles.mockResolvedValue({
    created: 2,
    updated: 0,
    skipped: 1,
    failed: 0,
    items: [],
  });
});

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

describe("ShotsPage", () => {
  it("renders a row per shot with what the default columns need", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    await listed();
    expect(screen.getByText("28.4 s")).toBeInTheDocument();
    expect(screen.getByText("36.4 g")).toBeInTheDocument();
    expect(screen.getByTestId("score-badge")).toHaveTextContent("8.3");
    expect(screen.getByTestId("rating-stars")).toHaveAttribute("data-rating", "4");
    // The row is a link, because the shot page is where everything else is —
    // one stretched link across the row rather than an `<a>` wrapped around
    // the cells, because two of those cells hold buttons.
    expect(screen.getByRole("link", { name: "Open shot 000101" })).toHaveAttribute(
      "href",
      "/shots/1",
    );
  });

  it("keeps the row's controls out of its link", async () => {
    // Interactive content inside an `<a>` is invalid HTML, and five stars plus
    // an editor button inside one is six extra tab stops per row that all
    // announce as part of the link.
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    const link = screen.getByRole("link", { name: "Open shot 000101" });
    for (const control of [
      screen.getByRole("checkbox", { name: "Compare shot 000101" }),
      screen.getByRole("button", { name: "Edit shot 000101" }),
      screen.getByRole("button", { name: /^needs a Set/ }),
      ...screen.getAllByRole("button", { name: /^(Rate|Clear the rating)/ }),
    ]) {
      expect(link.contains(control)).toBe(false);
    }
  });

  it("leads with the Set and centres every heading and every cell", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    const headings = screen.getAllByTestId(/^header-/);
    expect(headings[0]).toHaveAttribute("data-testid", "header-set");
    for (const heading of headings) expect(heading).toHaveClass("text-center");
    // The sort buttons centre their label and arrow as one group; nothing is
    // pushed to the right for being a number any more.
    expect(screen.getByTestId("sort-score")).toHaveClass("justify-center");
    expect(screen.getByTestId("sort-score")).not.toHaveClass("flex-row-reverse");

    const row = screen.getByTestId("shot-row");
    const cells = row.querySelectorAll("[data-column]");
    expect(cells[0]).toHaveAttribute("data-column", "set");
    for (const cell of Array.from(cells)) {
      expect(cell).toHaveClass("text-center");
      expect(cell).not.toHaveClass("text-right");
    }
  });

  it("leaves Profile and Curve out until somebody asks for them", async () => {
    // A sparkline per row is a request and a canvas per row, and the profile
    // name is the same string on almost every row of an archive built around
    // three profiles. Both cost a lot and say little, so neither is the
    // default; what a reader does want — which Set, and whether it was any
    // good — is.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    expect(screen.queryByText("9 Bar Espresso")).not.toBeInTheDocument();
    expect(screen.queryByTestId("shot-sparkline")).not.toBeInTheDocument();
    expect(getShotSamples).not.toHaveBeenCalled();

    await showColumn(user, "Profile");
    expect(await screen.findByText("9 Bar Espresso")).toBeInTheDocument();
  });

  it("remembers the chosen columns in this browser", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    const first = renderWithQueryClient(<ShotsPage />);
    await listed();
    await showColumn(user, "Profile");
    await screen.findByText("9 Bar Espresso");
    first.unmount();

    renderWithQueryClient(<ShotsPage />);
    expect(await screen.findByText("9 Bar Espresso")).toBeInTheDocument();
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

  it("shows the Set badge and the analysis state on every row", async () => {
    // Both are states with something to do behind them rather than absences,
    // which is why "not analysed" is rendered rather than left blank.
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByTestId("set-badge-slot")).toBeInTheDocument();
    expect(screen.getByTestId("analysis-slot")).toHaveTextContent("not analysed");
  });

  it.each([
    ["ok", "analysed"],
    ["running", "analysing"],
    // An interrupted run is reported as `failed` by the server: four states on
    // a list, not five.
    ["failed", "analysis failed"],
  ])("renders the %s analysis state", async (state, label) => {
    getShots.mockResolvedValue(listData([shot({ analysis_state: state })]));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByTestId("analysis-slot")).toHaveTextContent(label);
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

  it("points an empty archive at the two ways of filling it", async () => {
    getShots.mockResolvedValue(listData([]));
    getSyncStatus.mockResolvedValue(statusData({ configured: false, connected: false }));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText("No shots archived yet")).toBeInTheDocument();
    expect(screen.getByText(/Set the machine's address in Settings/)).toBeInTheDocument();
    expect(screen.getByTestId("shots-dropzone")).toBeInTheDocument();
  });

  it("says the archive has never been pulled into", async () => {
    // Nothing fills the archive on its own, so "never pulled" next to a
    // configured machine is the sentence that answers "where are my shots".
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText(/Never pulled/)).toBeInTheDocument();
  });

  it("fetches a sparkline per row, thinned, once the Curve column is on", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();
    await showColumn(user, "Curve");

    await waitFor(() => expect(getShotSamples).toHaveBeenCalledWith(1, 40));
    expect(await screen.findByTestId("shot-sparkline")).toBeInTheDocument();
  });
});

describe("ShotsPage filters", () => {
  it("sends each filter to the API and keeps the rest alone", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();
    await openFilters(user);

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

    // …and the button says how many are on, which is the whole point of
    // hiding them: the page can be scanned for "why am I seeing so few rows".
    expect(screen.getByTestId("filters-count")).toHaveTextContent("3");
  });

  it("clears the filters without clearing the sort", async () => {
    // "Clear all" is about which shots are listed. Somebody who chose "worst
    // executed first" and then cleared their filters did not ask to be put
    // back at newest-first.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();
    await user.click(screen.getByTestId("sort-score"));
    await openFilters(user);
    await user.selectOptions(screen.getByLabelText("Rating"), "4");

    await user.click(screen.getByRole("button", { name: "Clear all" }));

    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "execution_score", min_rating: undefined }),
      ),
    );
    expect(screen.queryByTestId("filters-count")).not.toBeInTheDocument();
  });

  it("offers every stored profile version, imported ones included", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await openFilters(user);

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
    await listed();

    // A column that is not the active one takes over descending; clicking the
    // active one reverses it.
    await user.click(screen.getByTestId("sort-score"));
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "execution_score", order: "desc" }),
      ),
    );
    expect(screen.getByTestId("header-score")).toHaveAttribute("aria-sort", "descending");

    await user.click(screen.getByTestId("sort-score"));
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "execution_score", order: "asc" }),
      ),
    );
    expect(screen.getByTestId("header-score")).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByTestId("header-time")).toHaveAttribute("aria-sort", "none");

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
    await listed();

    await user.click(screen.getByTestId("sort-time"));
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "started_at", order: "asc" }),
      ),
    );

    await user.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Showing 2 of 3")).toBeInTheDocument();
    expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ order: "asc", offset: 1 }));
  });

  it("says nothing matches rather than pretending the archive is empty", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([]));

    renderWithQueryClient(<ShotsPage />);
    await screen.findByText("No shots archived yet");

    await openFilters(user);
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
    await listed();

    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Showing 2 of 2")).toBeInTheDocument();
    expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "cursor-1" }));
  });
});

describe("ShotsPage refresh on events", () => {
  it("shows a new shot at the top when the sync engine says one landed", async () => {
    // The acceptance criterion: a shot pulled on the machine appears here
    // without a reload. `shot.ingested` on /api/sync/events maps onto this
    // query's key (lib/invalidate.ts) and the list re-reads.
    getShots.mockResolvedValue(listData([shot({ id: 1 })]));

    const { queryClient } = renderWithQueryClient(<ShotsPage />);
    await screen.findByText("Showing 1 of 1");

    getShots.mockResolvedValue(
      listData([shot({ id: 2, profile_label: "Fresh off the machine" }), shot({ id: 1 })]),
    );
    for (const queryKey of EVENT_INVALIDATIONS["shot.ingested"]) {
      await queryClient.invalidateQueries({ queryKey });
    }

    expect(await screen.findByText("Showing 2 of 2")).toBeInTheDocument();
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
    await listed();

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
    await openFilters(setupUser());
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("9"));
  });
});

describe("ShotsPage and Sets", () => {
  it("badges a shot with the Set it belongs to, and says so when it has none", async () => {
    getShots.mockResolvedValue(
      listData([
        shot({
          id: 1,
          set_version_id: 22,
          set_badge: { set_id: 3, set_name: "Guji on the Niche", version_no: 2 },
        }),
        shot({ id: 2, device_id: "000102", set_version_id: null, set_badge: null }),
      ]),
    );

    renderWithQueryClient(<ShotsPage />);

    const badges = await screen.findAllByTestId("set-badge");
    expect(badges[0]).toHaveTextContent("Guji on the Niche");
    expect(badges[0]).toHaveTextContent("v2");
    // "No Set" is a state with a button behind it, not an absence.
    expect(badges[1]).toHaveAttribute("data-state", "needs-set");
    expect(badges[1]).toHaveTextContent("needs a Set");
  });

  it("filters to the inbox, and to one Set", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();
    await openFilters(user);

    await user.selectOptions(screen.getByLabelText("Set"), "needs");
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ needs_set: true })),
    );

    await user.selectOptions(screen.getByLabelText("Set"), "3");
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ set_id: 3 })),
    );
  });

  it("puts the size of the inbox in the header, as a way into it", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(
      statusData({
        counts: {
          total: 12,
          quarantined: 0,
          deleted_on_device: 0,
          incomplete: 0,
          samples: 900,
          needs_set: 4,
        },
      }),
    );

    renderWithQueryClient(<ShotsPage />);

    const button = await screen.findByTestId("needs-set-count");
    expect(button).toHaveTextContent("4 need a Set");

    await user.click(button);
    await waitFor(() =>
      expect(getShots).toHaveBeenLastCalledWith(expect.objectContaining({ needs_set: true })),
    );
    // Clicking it is how you get into the inbox, so it goes away once you are
    // in it rather than sitting there as a no-op.
    expect(screen.queryByTestId("needs-set-count")).not.toBeInTheDocument();
  });
});

describe("ShotsPage needs-a-Set menu", () => {
  /**
   * Five Sets in the order `GET /api/sets` returns them: the active one first,
   * then newest first. Each at a different latest version, so a menu that
   * showed a version number it did not get from `current_version_no` shows up.
   */
  const fiveSets = [
    setRow({
      id: 5,
      name: "Guji on the Niche",
      active: true,
      current_version_id: 51,
      current_version_no: 4,
    }),
    setRow({
      id: 4,
      name: "Kenya AA",
      active: false,
      current_version_id: 41,
      current_version_no: 2,
      grinder_name: null,
    }),
    setRow({
      id: 3,
      name: "Colombia decaf",
      active: false,
      current_version_id: 31,
      current_version_no: 7,
    }),
    setRow({
      id: 2,
      name: "House blend",
      active: false,
      current_version_id: 21,
      current_version_no: 1,
    }),
    setRow({
      id: 1,
      name: "The first bag",
      active: false,
      current_version_id: 11,
      current_version_no: 3,
    }),
  ];

  /** The list page with somewhere to navigate to, so a navigation is visible. */
  function renderList() {
    return renderWithQueryClient(
      <Routes>
        <Route path="/" element={<ShotsPage />} />
        <Route path="/shots/:shotId" element={<p>the shot page</p>} />
        <Route path="/sets/:setId" element={<p>the Set page</p>} />
      </Routes>,
    );
  }

  function badgeButton(): HTMLElement {
    return screen.getByRole("button", { name: /^needs a Set: choose one for shot 000101/ });
  }

  it("opens the Sets from the badge, three of five, active first, at their latest version", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: fiveSets });

    renderList();
    await listed();

    const button = badgeButton();
    expect(button).toHaveAttribute("data-state", "needs-set");
    expect(button).toHaveAttribute("aria-haspopup", "dialog");
    expect(button).toHaveAttribute("aria-expanded", "false");

    await user.click(button);

    const menu = await screen.findByRole("dialog", { name: "File shot 000101 under a Set" });
    expect(button).toHaveAttribute("aria-expanded", "true");
    const options = await within(menu).findAllByTestId("needs-set-option");
    expect(options).toHaveLength(3);
    expect(options.map((option) => option.getAttribute("data-set"))).toEqual(["5", "4", "3"]);
    expect(options[0]).toHaveTextContent("Guji on the Niche");
    expect(options[0]).toHaveTextContent("v4");
    expect(options[0]).toHaveTextContent("active");
    expect(options[1]).toHaveTextContent("Kenya AA");
    expect(options[1]).toHaveTextContent("v2");
    expect(options[1]).not.toHaveTextContent("active");
    expect(options[2]).toHaveTextContent("v7");
    // The one-line summary comes from the list row; nothing is fetched per Set.
    expect(options[0]).toHaveTextContent("Ethiopia Guji · Niche Zero · 9 Bar Espresso");
    expect(within(menu).queryByText("House blend")).not.toBeInTheDocument();
    // Opening the menu did not follow the row's link.
    expect(screen.queryByText("the shot page")).not.toBeInTheDocument();
  });

  it("files the shot under the chosen Set's latest version without navigating", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: fiveSets });

    renderList();
    await listed();
    await user.click(badgeButton());
    const menu = await screen.findByTestId("needs-set-menu");
    await user.click(await within(menu).findByRole("button", { name: /^Kenya AA/ }));

    await waitFor(() => expect(putShotSetVersion).toHaveBeenCalledWith(1, 41));
    await waitFor(() => expect(screen.queryByTestId("needs-set-menu")).not.toBeInTheDocument());
    expect(screen.queryByText("the shot page")).not.toBeInTheDocument();
    expect(screen.getByTestId("shot-rows")).toBeInTheDocument();
  });

  it("turns the row's badge into the assigned one once the shot is filed", async () => {
    // The assignment invalidates the shots prefix, and the list row is under
    // it: the refetched row carries the badge, not a local patch of the old one.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: fiveSets });
    putShotSetVersion.mockImplementation(async () => {
      getShots.mockResolvedValue(
        listData([
          shot({
            set_version_id: 41,
            set_badge: { set_id: 4, set_name: "Kenya AA", version_no: 2 },
          }),
        ]),
      );
      return shot({
        set_version_id: 41,
        set_badge: { set_id: 4, set_name: "Kenya AA", version_no: 2 },
      });
    });

    renderList();
    await listed();
    await user.click(badgeButton());
    await user.click(await screen.findByRole("button", { name: /^Kenya AA/ }));

    await waitFor(() =>
      expect(screen.getByTestId("set-badge")).toHaveAttribute("data-state", "assigned"),
    );
    expect(screen.getByTestId("set-badge")).toHaveTextContent("Kenya AA");
    expect(screen.getByRole("link", { name: /Kenya AA/ })).toHaveAttribute("href", "/sets/4");
  });

  it("refreshes the header's count of shots that need a Set once one is filed", async () => {
    // The count comes from the sync status, not the list, and an assignment
    // publishes no event: without its own invalidation the header would keep
    // counting the shot that was just filed.
    const user = setupUser();
    const counts = statusData().counts;
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: fiveSets });
    getSyncStatus.mockResolvedValue(statusData({ counts: { ...counts, needs_set: 4 } }));
    putShotSetVersion.mockImplementation(async () => {
      getSyncStatus.mockResolvedValue(statusData({ counts: { ...counts, needs_set: 3 } }));
      return shot({
        set_version_id: 41,
        set_badge: { set_id: 4, set_name: "Kenya AA", version_no: 2 },
      });
    });

    renderList();
    await listed();
    expect(await screen.findByTestId("needs-set-count")).toHaveTextContent("4 need a Set");
    const statusCalls = getSyncStatus.mock.calls.length;

    await user.click(badgeButton());
    await user.click(await screen.findByRole("button", { name: /^Kenya AA/ }));

    await waitFor(() => expect(putShotSetVersion).toHaveBeenCalledWith(1, 41));
    await waitFor(() =>
      expect(screen.getByTestId("needs-set-count")).toHaveTextContent("3 need a Set"),
    );
    expect(getSyncStatus.mock.calls.length).toBeGreaterThan(statusCalls);
  });

  it("keeps the menu open and says so when the assignment fails", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: fiveSets });
    putShotSetVersion.mockRejectedValue(new Error("version is archived"));

    renderList();
    await listed();
    await user.click(badgeButton());
    await user.click(await screen.findByRole("button", { name: /^Guji on the Niche/ }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Could not assign: version is archived"),
    );
    expect(screen.getByTestId("needs-set-menu")).toBeInTheDocument();
    expect(screen.getAllByTestId("needs-set-option")).toHaveLength(3);
  });

  it("points at the Sets page when there is no Set to offer", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: [] });

    renderList();
    await listed();
    await user.click(badgeButton());

    const menu = await screen.findByTestId("needs-set-menu");
    expect(await within(menu).findByRole("link", { name: "Start a Set" })).toHaveAttribute(
      "href",
      "/sets",
    );
    expect(within(menu).queryByTestId("needs-set-option")).not.toBeInTheDocument();
  });

  it("sends the fourth and later Sets to the shot page's Assign panel", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: fiveSets });

    renderList();
    await listed();
    await user.click(badgeButton());

    const menu = await screen.findByTestId("needs-set-menu");
    expect(await within(menu).findByRole("link", { name: "Another Set…" })).toHaveAttribute(
      "href",
      "/shots/1#set",
    );
  });

  it("offers no way out to a longer list when three Sets are all there are", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: fiveSets.slice(0, 3) });

    renderList();
    await listed();
    await user.click(badgeButton());

    const menu = await screen.findByTestId("needs-set-menu");
    expect(await within(menu).findAllByTestId("needs-set-option")).toHaveLength(3);
    expect(within(menu).queryByRole("link", { name: "Another Set…" })).not.toBeInTheDocument();
  });

  it("says so when the Sets cannot be loaded, rather than showing an empty menu", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockRejectedValue(new Error("database is locked"));

    renderList();
    await listed();
    await user.click(badgeButton());

    expect(await screen.findByTestId("needs-set-state")).toHaveTextContent(
      "Could not load the Sets: database is locked",
    );
  });

  it("focuses the first Set once placed, and gives focus back to the badge on Escape", async () => {
    // Focus must wait for the anchored panel to be positioned: jsdom focuses a
    // hidden element, browsers refuse. See the popover's own test.
    const user = setupUser();
    const real = HTMLElement.prototype.focus;
    const visibilityWhenFocused: string[] = [];
    vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function focus(
      this: HTMLElement,
      options?: FocusOptions,
    ) {
      const panel = this.closest<HTMLElement>('[role="dialog"]');
      if (panel !== null) visibilityWhenFocused.push(panel.style.visibility);
      real.call(this, options);
    });
    getShots.mockResolvedValue(listData([shot()]));
    getSets.mockResolvedValue({ items: fiveSets });

    renderList();
    await listed();
    await user.click(badgeButton());

    // The options arrive after the Set query, so the panel itself takes focus
    // first and hands it to the first Set when they land; never while hidden.
    const options = await screen.findAllByTestId("needs-set-option");
    await waitFor(() => expect(options[0]).toHaveFocus());
    expect(visibilityWhenFocused).not.toHaveLength(0);
    expect(visibilityWhenFocused).not.toContain("hidden");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("needs-set-menu")).not.toBeInTheDocument());
    expect(badgeButton()).toHaveFocus();
  });

  it("still links an assigned badge to its Set, above the row's own link", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(
      listData([
        shot({
          set_version_id: 22,
          set_badge: { set_id: 3, set_name: "Guji on the Niche", version_no: 2 },
        }),
      ]),
    );

    renderList();
    await listed();

    const setLink = screen.getByRole("link", { name: /Guji on the Niche/ });
    expect(setLink).toHaveAttribute("href", "/sets/3");
    // Lifted like the other row controls: under the stretched row link, a
    // click on the badge would open the shot instead.
    expect(setLink).toHaveClass("z-[1]");
    expect(screen.queryByTestId("needs-set-menu")).not.toBeInTheDocument();

    await user.click(setLink);
    expect(await screen.findByText("the Set page")).toBeInTheDocument();
  });
});

describe("ShotsPage row editing", () => {
  it("sets a rating from the row", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot({ judgement_rating: null, rating: null })]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: /^Rate shot 000101 4 of 5/ }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalled());
    expect(putJudgement.mock.calls[0][1]).toMatchObject({ rating: 4 });
  });

  it("clears the rating when the star that is already lit is clicked", async () => {
    // A mis-click has to be undoable where it was made. Without this the only
    // way back is the detail page, for a field that is one click to set.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot({ judgement_rating: 4 })]));

    renderWithQueryClient(<ShotsPage />);
    await listed();
    expect(screen.getByTestId("rating-stars")).toHaveAttribute("data-rating", "4");

    await user.click(screen.getByRole("button", { name: "Clear the rating shot 000101" }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalled());
    expect(putJudgement.mock.calls[0][1]).toMatchObject({ rating: null });
  });

  it("does not navigate when a star is clicked", async () => {
    // The row is a link with the stars above it. A star that followed the link
    // would take you to the shot page every time you rated one.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot({ judgement_rating: null, rating: null })]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: /^Rate shot 000101 3 of 5/ }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalled());
    // Still on the list: the table is here and no shot page replaced it.
    expect(screen.getByTestId("shot-rows")).toBeInTheDocument();
  });

  it("records the machine's own rating rather than clearing it", async () => {
    // A row with no verdict shows the machine's notes-card rating. Clicking
    // that star means "yes, three", not "clear" — and clearing a verdict that
    // does not exist would write an empty one.
    const user = setupUser();
    getShots.mockResolvedValue(
      listData([shot({ judgement_rating: null, rating: 3, has_judgement: false })]),
    );
    getShot.mockResolvedValue({ ...shot129, judgement: null });

    renderWithQueryClient(<ShotsPage />);
    await listed();

    expect(screen.getByTestId("rating-stars")).toHaveAttribute("data-rating", "3");
    await user.click(screen.getByRole("button", { name: /^Rate shot 000101 3 of 5/ }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalled());
    expect(putJudgement.mock.calls[0][1]).toMatchObject({ rating: 3 });
  });

  it("writes nothing when there is no rating to clear", async () => {
    // The one click in the list that could create a verdict out of nothing:
    // an empty row lights up `has_judgement`, dates itself later than the
    // machine's notes card, and puts a null point on the Set's trend.
    const user = setupUser();
    getShots.mockResolvedValue(
      listData([shot({ judgement_rating: null, rating: 3, has_judgement: false })]),
    );

    renderWithQueryClient(<ShotsPage />);
    await listed();

    // Star three is the displayed value but not a verdict, so there is no
    // "clear" button at all; the nearest thing is rating it 3, above. Assert
    // the negative through the editor's Save instead — see the editor tests —
    // and here that no star announces itself as a clear.
    expect(screen.queryByRole("button", { name: /^Clear the rating/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Rate shot 000101 1 of 5/ }));
    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
  });

  it("leaves the rest of a verdict alone when a star is clicked", async () => {
    // `PUT` replaces the whole row, so a rating set from the list has to carry
    // back everything typed on the detail page. Losing somebody's taste tags
    // to a star is exactly the kind of data loss this project must not do.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: /^Rate shot 000101 2 of 5/ }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalled());
    expect(putJudgement.mock.calls[0][1]).toEqual({
      rating: 2,
      balance: "sour",
      taste_tags: ["sour"],
      dose_in_g: 18,
      dose_out_g: 36,
      grind_setting: "22",
      notes: "sharp at the end",
      decision: "adjust",
    });
  });

  it("edits notes and the Set from the row without navigating", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot({ judgement_notes: "sharp at the end" })]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: "Edit shot 000101" }));
    const editor = await screen.findByTestId("row-editor");

    const notes = within(editor).getByLabelText("Notes");
    expect(notes).toHaveValue("sharp at the end");
    await user.clear(notes);
    await user.type(notes, "better, still short");
    await user.selectOptions(within(editor).getByLabelText("Set"), "22");
    await user.click(within(editor).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalled());
    expect(putJudgement.mock.calls[0][1]).toMatchObject({ notes: "better, still short" });
    await waitFor(() => expect(putShotSetVersion).toHaveBeenCalledWith(1, 22));

    // It is a panel over the list, not a page: the row is still there.
    expect(screen.getByTestId("shot-rows")).toBeInTheDocument();
  });

  it("closes the editor when the list scrolls under it", async () => {
    // The panel is fixed, positioned once against a row's rectangle. Scrolling
    // the list moves the row out from under it and, past the overscan,
    // unmounts the row entirely — which would take the panel and anything
    // typed into it with it, silently. It closes on the first pixel instead.
    const user = setupUser();
    getShots.mockResolvedValue(
      listData(
        Array.from({ length: 200 }, (_, index) =>
          shot({ id: index + 1, device_id: String(index + 1).padStart(6, "0") }),
        ),
        { total: 200 },
      ),
    );

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: "Edit shot 000001" }));
    expect(await screen.findByTestId("row-editor")).toBeInTheDocument();

    fireEvent.scroll(screen.getByTestId("shots-scroll"));

    await waitFor(() => expect(screen.queryByTestId("row-editor")).not.toBeInTheDocument());
  });

  it("lets a panel taller than the viewport scroll itself", async () => {
    // The clamp puts a too-tall panel's top at the margin; without this its
    // Save button would still be below the bottom of the screen, and a fixed
    // element cannot be scrolled to.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: "Edit shot 000101" }));

    expect(await screen.findByTestId("row-editor")).toHaveClass("overflow-y-auto");
  });

  it("writes nothing when the editor is cancelled", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: "Edit shot 000101" }));
    const editor = await screen.findByTestId("row-editor");
    await user.type(within(editor).getByLabelText("Notes"), " and thin");
    await user.click(within(editor).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByTestId("row-editor")).not.toBeInTheDocument());
    expect(putJudgement).not.toHaveBeenCalled();
    expect(putShotSetVersion).not.toHaveBeenCalled();
  });

  it("does not write a verdict when only the Set moved", async () => {
    // `PUT` creates a row for `{rating: null, notes: ""}` — the server has no
    // empty-judgement guard, by design — so filing an unjudged shot under a
    // Set would claim somebody had an opinion about it.
    const user = setupUser();
    getShots.mockResolvedValue(
      listData([
        shot({
          judgement_rating: null,
          judgement_notes: null,
          has_judgement: false,
          set_version_id: null,
        }),
      ]),
    );

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: "Edit shot 000101" }));
    const editor = await screen.findByTestId("row-editor");
    await user.selectOptions(within(editor).getByLabelText("Set"), "22");
    await user.click(within(editor).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(putShotSetVersion).toHaveBeenCalledWith(1, 22));
    expect(putJudgement).not.toHaveBeenCalled();
  });

  it("writes nothing at all when Save is pressed on an untouched panel", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(
      listData([shot({ judgement_rating: null, judgement_notes: null, has_judgement: false })]),
    );

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: "Edit shot 000101" }));
    const editor = await screen.findByTestId("row-editor");
    await user.click(within(editor).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByTestId("row-editor")).not.toBeInTheDocument());
    expect(putJudgement).not.toHaveBeenCalled();
    expect(putShotSetVersion).not.toHaveBeenCalled();
  });

  it("says whose rating the row is showing when this box has no verdict", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(
      listData([shot({ judgement_rating: null, rating: 3, has_judgement: false })]),
    );

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: "Edit shot 000101" }));

    // Otherwise a panel showing no stars beside a row showing three looks
    // like a bug rather than like the two different facts they are.
    expect(await screen.findByTestId("device-rating-hint")).toHaveTextContent(
      "The machine's own notes say 3",
    );
  });

  it("does not touch the Set when only the verdict changed", async () => {
    // Assigning is a second write against a second endpoint; sending it every
    // time would put a "Filed under…" toast on screen for somebody who only
    // fixed a typo.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot({ set_version_id: 22 })]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByRole("button", { name: "Edit shot 000101" }));
    const editor = await screen.findByTestId("row-editor");
    await user.type(within(editor).getByLabelText("Notes"), "fine");
    await user.click(within(editor).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalled());
    expect(putShotSetVersion).not.toHaveBeenCalled();
  });
});

describe("ShotsPage pull button", () => {
  it("pulls, and says what landed when the run it started finishes", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(statusData({ last_runs: { backfill: shotRun({ id: 6 }) } }));

    const { queryClient } = renderWithQueryClient(<ShotsPage />);
    await listed();

    await user.click(screen.getByTestId("pull-button"));
    await waitFor(() => expect(runSync).toHaveBeenCalledWith("all"));

    // The route answers 202; the run shows up in the ledger afterwards, which
    // is what the `sync.progress` event tells the page to re-read.
    getSyncStatus.mockResolvedValue(
      statusData({ last_runs: { backfill: shotRun({ id: 7, shots_inserted: 3 }) } }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("3 new shots, 1 updated"));
  });

  it("says nothing new when a pull found nothing", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(statusData({ last_runs: { backfill: shotRun({ id: 6 }) } }));

    const { queryClient } = renderWithQueryClient(<ShotsPage />);
    await listed();
    await user.click(screen.getByTestId("pull-button"));
    await waitFor(() => expect(runSync).toHaveBeenCalled());

    getSyncStatus.mockResolvedValue(
      statusData({
        last_runs: { backfill: shotRun({ id: 7, shots_inserted: 0, shots_updated: 0 }) },
      }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Nothing new"));
  });

  it("reports a failed pull with what the machine said", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(statusData({ last_runs: { backfill: shotRun({ id: 6 }) } }));

    const { queryClient } = renderWithQueryClient(<ShotsPage />);
    await listed();
    await user.click(screen.getByTestId("pull-button"));
    await waitFor(() => expect(runSync).toHaveBeenCalled());

    getSyncStatus.mockResolvedValue(
      statusData({
        last_runs: {
          backfill: shotRun({
            id: 7,
            status: "error",
            error: "the machine stopped answering",
            shots_inserted: 0,
            shots_updated: 0,
          }),
        },
      }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("the machine stopped answering"));
  });

  it("says what landed when a pull failed part of the way through", async () => {
    // The common shape of a failure: eleven shots stored, three the machine
    // would not serve, and no message at all — the per-shot failures are
    // counted, not raised. Saying "the pull failed" would be wrong about the
    // eleven.
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(statusData({ last_runs: { backfill: shotRun({ id: 6 }) } }));

    const { queryClient } = renderWithQueryClient(<ShotsPage />);
    await listed();
    await user.click(screen.getByTestId("pull-button"));
    await waitFor(() => expect(runSync).toHaveBeenCalled());

    getSyncStatus.mockResolvedValue(
      statusData({
        last_runs: {
          backfill: shotRun({
            id: 7,
            status: "error",
            error: null,
            shots_inserted: 11,
            shots_updated: 0,
            errors: 3,
          }),
        },
      }),
    );
    for (const queryKey of EVENT_INVALIDATIONS["sync.progress"]) {
      await queryClient.invalidateQueries({ queryKey });
    }

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "11 new shots, 3 failed. The Device page has the details.",
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
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(statusData({ last_runs: { backfill: shotRun({ id: 6 }) } }));

    let accept: (value: { queued: string[] }) => void = () => {};
    runSync.mockImplementation(
      () =>
        new Promise<{ queued: string[] }>((resolve) => {
          accept = resolve;
        }),
    );

    const { queryClient } = renderWithQueryClient(<ShotsPage />);
    await listed();
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

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("2 new shots, 1 updated"));
  });

  it("does not call an identity read a pull", async () => {
    // The engine reads identity on every reconnect — one frame and one
    // request, whenever the machine's Wi-Fi blinks. `running` on the ledger is
    // true for any kind, so the button used to flash "Pulling…" and go dead
    // for half a second at a time while nothing was being pulled.
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(
      statusData({
        running: true,
        last_runs: {
          identity: shotRun({ id: 9, kind: "identity", status: "running", finished_at: null }),
          backfill: shotRun({ id: 7 }),
        },
      }),
    );

    renderWithQueryClient(<ShotsPage />);
    await listed();

    expect(screen.queryByText("Pulling…")).not.toBeInTheDocument();
    expect(screen.getByTestId("pull-button")).toBeEnabled();
  });

  it("is disabled with no machine configured", async () => {
    getShots.mockResolvedValue(listData([shot()]));
    getDeviceStatus.mockResolvedValue({
      configured: false,
      connected: false,
      host: null,
      identity: null,
      last_status: null,
    });

    renderWithQueryClient(<ShotsPage />);

    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeDisabled());
  });

  it("is disabled while the machine is unreachable", async () => {
    getShots.mockResolvedValue(listData([shot()]));
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: false,
      host: "gaggimate.local",
      identity: null,
      last_status: null,
    });

    renderWithQueryClient(<ShotsPage />);

    await waitFor(() => expect(screen.getByTestId("pull-button")).toBeDisabled());
  });

  it("shows a pull that is already running, whoever started it", async () => {
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(
      statusData({ running: true, last_runs: { backfill: shotRun({ finished_at: null }) } }),
    );

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText("Pulling…")).toBeInTheDocument();
    expect(screen.getByTestId("pull-button")).toBeDisabled();
  });

  it("says when the archive was last pulled into", async () => {
    getShots.mockResolvedValue(listData([shot()]));
    getSyncStatus.mockResolvedValue(statusData({ last_runs: { backfill: shotRun() } }));

    renderWithQueryClient(<ShotsPage />);

    expect(await screen.findByText(/Last pull/)).toBeInTheDocument();
  });
});

describe("ShotsPage drop zone", () => {
  it("imports the files that are dropped on it and reports the batch", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    const file = new File(['{"id":"000101"}'], "shot.json", { type: "application/json" });
    const zone = screen.getByTestId("shots-dropzone");
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(importFiles).toHaveBeenCalled());
    expect(importFiles.mock.calls[0][0]).toEqual([file]);

    const result = await screen.findByTestId("import-result");
    expect(result).toHaveTextContent("2 imported");
    expect(result).toHaveTextContent("1 skipped");
  });

  it("unfolds the per-file results without leaving the page", async () => {
    const user = setupUser();
    getShots.mockResolvedValue(listData([shot()]));
    importFiles.mockResolvedValue({
      created: 1,
      updated: 0,
      skipped: 0,
      failed: 1,
      items: [
        {
          filename: "shot-129.json",
          kind: "shot",
          status: "created",
          message: "213 samples",
          shot_id: 4,
          device_id: "000129",
          profile_version_id: null,
          label: null,
          quarantined: false,
        },
        {
          filename: "broken.json",
          kind: "unknown",
          status: "failed",
          message: "not JSON",
          shot_id: null,
          device_id: null,
          profile_version_id: null,
          label: null,
          quarantined: false,
        },
      ],
    });

    renderWithQueryClient(<ShotsPage />);
    await listed();

    const file = new File(['{"id":"000101"}'], "shot.json", { type: "application/json" });
    fireEvent.drop(screen.getByTestId("shots-dropzone"), { dataTransfer: { files: [file] } });

    const result = await screen.findByTestId("import-result");
    // Collapsed by default: one dropped file plus a table is noise.
    expect(within(result).queryByText("not JSON")).not.toBeInTheDocument();

    await user.click(within(result).getByTestId("import-result-toggle"));

    // Which file did not land, and why — the question the old import page was
    // the only answer to.
    expect(within(result).getByText("not JSON")).toBeInTheDocument();
    expect(within(result).getByText("213 samples")).toBeInTheDocument();
    expect(within(result).getByRole("link", { name: "shot 000129" })).toHaveAttribute(
      "href",
      "/shots/4",
    );
  });

  it("offers a file picker for keyboards and phones", async () => {
    getShots.mockResolvedValue(listData([shot()]));

    renderWithQueryClient(<ShotsPage />);
    await listed();

    const input = screen.getByTestId("shots-import-input");
    expect(input).toHaveAttribute("accept", expect.stringContaining(".slog"));
    expect(screen.getByRole("button", { name: "Choose files" })).toBeInTheDocument();
  });
});
