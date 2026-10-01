import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SyncRunRow, SyncStatusData } from "@/api/types";
import { SyncPage } from "@/pages/SyncPage";
import { boardAction, boardView } from "@/test/boardFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { getDeviceStatus, getSyncStatus, getDeviceWrites, getProfileBoard, resumeBoard } =
  vi.hoisted(() => ({
    getDeviceStatus: vi.fn(),
    getSyncStatus: vi.fn(),
    getDeviceWrites: vi.fn(),
    getProfileBoard: vi.fn(),
    resumeBoard: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getDeviceStatus,
  getSyncStatus,
  getDeviceWrites,
  getProfileBoard,
  resumeBoard,
}));

function profilesRun(summary: unknown): SyncRunRow {
  return {
    id: 5,
    kind: "profiles",
    status: "ok",
    trigger: "manual",
    started_at: "2026-03-04T08:00:00.000Z",
    finished_at: "2026-03-04T08:00:04.000Z",
    shots_seen: 0,
    shots_inserted: 0,
    shots_updated: 0,
    shots_quarantined: 0,
    profiles_changed: 3,
    notes_synced: 0,
    errors: 0,
    error: null,
    summary,
  } as SyncRunRow;
}

function status(summary: unknown): SyncStatusData {
  return {
    configured: true,
    connected: true,
    running: false,
    last_runs: { profiles: profilesRun(summary) },
    last_error: null,
    counts: {
      total: 1,
      quarantined: 0,
      deleted_on_device: 0,
      incomplete: 0,
      samples: 10,
      needs_set: 0,
    },
    recent_events: [],
  } as SyncStatusData;
}

const item = (label: string, extra: Record<string, unknown> = {}) => ({
  row_id: 1,
  label,
  device_id: "x1",
  reason: "",
  detail: "",
  on: null,
  ...extra,
});

beforeEach(() => {
  vi.clearAllMocks();
  getDeviceStatus.mockResolvedValue({
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: null,
    last_status: null,
  });
  getDeviceWrites.mockResolvedValue({ enabled: true, items: [] });
  getProfileBoard.mockResolvedValue(boardView());
  resumeBoard.mockResolvedValue({ resumed: true });
  getSyncStatus.mockResolvedValue(status(null));
});

describe("the Sync page's profile board", () => {
  it("shows what the last sync pushed, removed, left, moved and failed", async () => {
    getSyncStatus.mockResolvedValue(
      status({
        pushed: [item("Londinium [AI]", { reason: "missing" })],
        removed: [item("Old [AI]", { reason: "superseded" })],
        left: [item("Mine", { detail: "it is not a profile the app wrote" })],
        home_screen: [item("9 Bar", { reason: "off", on: false })],
        failures: [item("Broken [AI]", { reason: "round_trip", detail: "read back differently" })],
        writes: 5,
        paused: null,
      }),
    );
    renderWithQueryClient(<SyncPage />);

    const summary = await screen.findByTestId("board-summary");
    expect(within(summary).getByTestId("board-summary-pushed")).toHaveTextContent(
      "Londinium [AI]: It was not on the machine.",
    );
    expect(within(summary).getByTestId("board-summary-removed")).toHaveTextContent(
      "Old [AI]: The old copy went after a newer version was put on.",
    );
    expect(within(summary).getByTestId("board-summary-left")).toHaveTextContent(
      "Mine: It is not a profile the app wrote.",
    );
    expect(within(summary).getByTestId("board-summary-homeScreen")).toHaveTextContent(
      "9 Bar: taken off the home screen",
    );
    expect(within(summary).getByTestId("board-summary-failures")).toHaveTextContent(
      "Broken [AI]: round trip: read back differently",
    );
    expect(within(summary).getByTestId("board-summary-counts")).toHaveTextContent(
      "1 put on the machine",
    );
  });

  it("lists the profiles a sync would not touch as needing a look", async () => {
    getProfileBoard.mockResolvedValue(
      boardView({
        reports: [
          boardAction({
            kind: "report",
            row_id: 1,
            label: "9 Bar Espresso",
            reason: "edited_on_machine",
            detail: "9bar was changed on the machine since it was recorded",
          }),
        ],
      }),
    );
    renderWithQueryClient(<SyncPage />);

    expect(await screen.findByTestId("board-reports")).toHaveTextContent(
      "9 Bar Espresso: 9bar was changed on the machine since it was recorded",
    );
  });

  it("says a sync that found everything in place changed nothing", async () => {
    getSyncStatus.mockResolvedValue(status({ pushed: [], removed: [], writes: 0 }));
    renderWithQueryClient(<SyncPage />);

    expect(await screen.findByTestId("board-summary-nothing")).toHaveTextContent(
      "already matching the board",
    );
  });

  it("says writes are off when there is no summary to show", async () => {
    getProfileBoard.mockResolvedValue(boardView({ writes_enabled: false }));
    renderWithQueryClient(<SyncPage />);

    expect(await screen.findByTestId("board-summary-none")).toHaveTextContent(
      "Writes are off, so a sync does not change the machine's profiles.",
    );
  });

  it("shows no pause banner when the board is not paused", async () => {
    renderWithQueryClient(<SyncPage />);
    await screen.findByTestId("board-summary-none");
    expect(screen.queryByTestId("board-paused-banner")).toBeNull();
  });
});

describe("a board paused because the machine looks reset", () => {
  beforeEach(() => {
    getProfileBoard.mockResolvedValue(
      boardView({ paused: "the machine looks reset", pause_recorded: true }),
    );
  });

  it("explains why syncs stopped writing and offers Resume", async () => {
    renderWithQueryClient(<SyncPage />);

    const banner = await screen.findByTestId("board-paused-banner");
    expect(banner).toHaveTextContent("Syncs are not writing profiles to the machine");
    expect(banner).toHaveTextContent("The machine looks reset");
    expect(within(banner).getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("asks first, saying the next sync pushes the app's profiles and never yours", async () => {
    const user = setupUser();
    renderWithQueryClient(<SyncPage />);

    await user.click(await screen.findByTestId("board-resume"));

    const confirm = screen.getByTestId("board-resume-confirm");
    expect(confirm).toHaveTextContent(
      "The next sync will push the app's profiles back onto the machine.",
    );
    expect(confirm).toHaveTextContent("Profiles of yours are never pushed.");
    expect(resumeBoard).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("board-resume-confirm")).toBeNull();
    expect(resumeBoard).not.toHaveBeenCalled();
  });

  it("moves focus into the confirm, closes on Escape and gives focus back to Resume", async () => {
    const user = setupUser();
    renderWithQueryClient(<SyncPage />);
    const resume = await screen.findByTestId("board-resume");

    await user.click(resume);
    expect(
      within(screen.getByTestId("board-resume-confirm")).getByRole("button", { name: "Cancel" }),
    ).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("board-resume-confirm")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("board-resume")).toHaveFocus());
    expect(resumeBoard).not.toHaveBeenCalled();
  });

  it("puts focus on the section, not the page, once Resume is confirmed", async () => {
    const user = setupUser();
    renderWithQueryClient(<SyncPage />);
    await user.click(await screen.findByTestId("board-resume"));

    await user.click(
      within(screen.getByTestId("board-resume-confirm")).getByRole("button", { name: "Resume" }),
    );

    await waitFor(() => expect(screen.getByTestId("board-section-body")).toHaveFocus());
  });

  it("resumes on confirmation and refreshes everything the resume changes", async () => {
    const user = setupUser();
    const { queryClient } = renderWithQueryClient(<SyncPage />);
    const keys: unknown[] = [];
    const original = queryClient.invalidateQueries.bind(queryClient);
    vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
      keys.push(filters?.queryKey);
      return original(filters);
    });

    await user.click(await screen.findByTestId("board-resume"));
    await user.click(
      within(screen.getByTestId("board-resume-confirm")).getByRole("button", { name: "Resume" }),
    );

    await waitFor(() => expect(resumeBoard).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(keys).toContainEqual(["board"]);
      expect(keys).toContainEqual(["sync"]);
      expect(keys).toContainEqual(["device"]);
    });
  });

  it("drops the banner once resumed, although the last run's summary still says paused", async () => {
    const user = setupUser();
    getSyncStatus.mockResolvedValue(status({ paused: "the machine looks reset", writes: 0 }));
    resumeBoard.mockImplementation(async () => {
      getProfileBoard.mockResolvedValue(boardView({ paused: null }));
      return { resumed: true };
    });
    renderWithQueryClient(<SyncPage />);

    await user.click(await screen.findByTestId("board-resume"));
    await user.click(
      within(screen.getByTestId("board-resume-confirm")).getByRole("button", { name: "Resume" }),
    );

    await waitFor(() => expect(screen.queryByTestId("board-paused-banner")).toBeNull());
  });

  it("shows the banner from the last run too, when the board itself cannot be read", async () => {
    getProfileBoard.mockRejectedValue(new Error("boom"));
    getSyncStatus.mockResolvedValue(status({ paused: "the machine looks reset", writes: 0 }));
    renderWithQueryClient(<SyncPage />);

    expect(await screen.findByTestId("board-paused-banner")).toBeInTheDocument();
    expect(screen.getByTestId("board-summary-nothing")).toHaveTextContent("looked reset");
  });
});
