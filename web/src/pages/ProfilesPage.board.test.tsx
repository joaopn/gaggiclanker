import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProfileVersionListData } from "@/api/types";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { boardAction, boardRow, boardRowView, boardView } from "@/test/boardFixtures";
import { draft, draftDetail } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const {
  getProfiles,
  getProfileVersions,
  getProfileDrafts,
  getProfileDraft,
  getDeviceWrites,
  getProfileBoard,
  putOnBoard,
  setBoardHomeScreen,
  deleteBoardRow,
  pushProfileDraft,
} = vi.hoisted(() => ({
  getProfiles: vi.fn(),
  getProfileVersions: vi.fn(),
  getProfileDrafts: vi.fn(),
  getProfileDraft: vi.fn(),
  getDeviceWrites: vi.fn(),
  getProfileBoard: vi.fn(),
  putOnBoard: vi.fn(),
  setBoardHomeScreen: vi.fn(),
  deleteBoardRow: vi.fn(),
  pushProfileDraft: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getProfiles,
  getProfileVersions,
  getProfileDrafts,
  getProfileDraft,
  getDeviceWrites,
  getProfileBoard,
  putOnBoard,
  setBoardHomeScreen,
  deleteBoardRow,
  pushProfileDraft,
}));

const versions: ProfileVersionListData = {
  items: [
    {
      id: 7,
      content_hash: "abcdef0123456789",
      label: "9 Bar Espresso",
      type: "standard",
      utility: false,
      source: "device",
      created_at: "2026-03-01T00:00:00.000Z",
      mirrored: true,
      shot_count: 12,
    },
  ],
  total: 1,
  limit: 200,
  offset: 0,
};

const mine = boardRowView({
  row: { id: 1, label: "9 Bar Espresso", origin: "adopted", current_version_id: 7 },
});
const apps = boardRowView({
  row: {
    id: 2,
    label: "Londinium [AI]",
    origin: "draft",
    current_version_id: 11,
    on_home_screen: false,
    device_profile_id: null,
  },
  machine: { present: false, holds_current: false },
  planned: [boardAction({ row_id: 2, label: "Londinium [AI]" })],
});

beforeEach(() => {
  vi.clearAllMocks();
  getProfiles.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue(versions);
  getProfileDrafts.mockResolvedValue({ items: [] });
  getProfileDraft.mockResolvedValue(draftDetail());
  getDeviceWrites.mockResolvedValue({ enabled: true, items: [] });
  getProfileBoard.mockResolvedValue(boardView({ rows: [mine, apps] }));
  putOnBoard.mockResolvedValue(boardRow({ id: 3, label: "9 Bar Espresso [AI]" }));
  setBoardHomeScreen.mockResolvedValue(boardRow());
  deleteBoardRow.mockResolvedValue(boardRow());
  pushProfileDraft.mockResolvedValue({ draft: draft({ status: "pushed" }), set_version: null });
});

async function cards() {
  return await screen.findAllByTestId("board-row");
}

describe("the Profiles page around the board", () => {
  it("shows one entry per board row with its label, version, owner and state", async () => {
    renderWithQueryClient(<ProfilesPage />);

    const [first, second] = await cards();
    expect(first).toHaveTextContent("9 Bar Espresso");
    expect(first).toHaveTextContent("version abcdef01");
    expect(first).toHaveAttribute("data-owner", "yours");
    expect(first).toHaveTextContent("yours");
    expect(within(first).getByTestId("board-state")).toHaveTextContent("On the machine");

    expect(second).toHaveTextContent("Londinium [AI]");
    expect(second).toHaveAttribute("data-owner", "app");
    expect(second).toHaveTextContent("the app's");
    // A version the page does not hold is named by its number, not left blank.
    expect(second).toHaveTextContent("version 11");
    expect(within(second).getByTestId("board-state")).toHaveTextContent(
      "Will be pushed on the next pull",
    );
  });

  it("reads the board from the mirror, the cheap read", async () => {
    renderWithQueryClient(<ProfilesPage />);
    await cards();
    expect(getProfileBoard).toHaveBeenCalledWith(false);
  });

  it("says profiles reach the machine on the next pull, and drops the mirror table", async () => {
    renderWithQueryClient(<ProfilesPage />);
    await cards();
    expect(screen.getByText(/they reach the machine on the next pull/)).toBeInTheDocument();
    expect(screen.queryByText("The mirror, as of the last pull.")).toBeNull();
  });

  it("puts each home-screen flag in a toggle and changes it through the board", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    const [first, second] = await cards();

    const on = within(first).getByRole("checkbox", {
      name: /9 Bar Espresso on the machine's home screen/,
    });
    const off = within(second).getByRole("checkbox", {
      name: /Londinium \[AI\] on the machine's home screen/,
    });
    expect(on).toBeChecked();
    expect(off).not.toBeChecked();

    await user.click(on);
    await waitFor(() => expect(setBoardHomeScreen).toHaveBeenCalledWith(1, false));
    await user.click(off);
    await waitFor(() => expect(setBoardHomeScreen).toHaveBeenCalledWith(2, true));
  });

  it("asks before deleting, says a profile of yours is never removed, and deletes on confirm", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    const [first, second] = await cards();

    await user.click(within(first).getByTestId("board-delete"));
    const confirm = within(first).getByTestId("board-delete-confirm");
    expect(confirm).toHaveTextContent("A profile of yours is never removed from the machine.");
    expect(confirm).toHaveTextContent("This profile is yours, so it stays on the machine");
    expect(deleteBoardRow).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(within(first).queryByTestId("board-delete-confirm")).toBeNull();
    expect(deleteBoardRow).not.toHaveBeenCalled();

    // The app's own: the pull removes its copy, and the same promise is made.
    await user.click(within(second).getByTestId("board-delete"));
    const second_confirm = within(second).getByTestId("board-delete-confirm");
    expect(second_confirm).toHaveTextContent("The next pull removes the app's copy");
    expect(second_confirm).toHaveTextContent("A profile of yours is never removed");
    await user.click(within(second_confirm).getByRole("button", { name: "Delete from the board" }));
    await waitFor(() => expect(deleteBoardRow).toHaveBeenCalledWith(2));
  });

  it("lists deleted profiles still on the machine as removed or left", async () => {
    getProfileBoard.mockResolvedValue(
      boardView({
        rows: [],
        pending_removals: [
          boardRow({
            id: 8,
            label: "Old [AI]",
            origin: "draft",
            deleted_at: "2026-03-02T00:00:00Z",
          }),
          boardRow({ id: 9, label: "Mine", deleted_at: "2026-03-02T00:00:00Z" }),
        ],
        actions: [
          boardAction({ kind: "remove", row_id: 8, label: "Old [AI]", device_id: "o1" }),
          boardAction({
            kind: "leave",
            row_id: 9,
            label: "Mine",
            detail: "it is not a profile the app wrote",
          }),
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const removals = await screen.findAllByTestId("board-removal");
    expect(removals[0]).toHaveTextContent("Old [AI]");
    expect(removals[0]).toHaveTextContent("Will be removed from the machine on the next pull");
    expect(removals[1]).toHaveTextContent("Left on the machine");
    expect(removals[1]).toHaveTextContent("It is not a profile the app wrote.");
  });

  it("says each machine state in plain words", async () => {
    const view = boardView({
      rows: [
        boardRowView({
          row: { id: 1, label: "Edited" },
          machine: { present: true, holds_current: false },
        }),
        boardRowView({
          row: { id: 2, label: "Gone" },
          machine: { present: false, holds_current: false },
        }),
        boardRowView({
          row: { id: 3, label: "Unverified", origin: "draft" },
          machine: { present: false, holds_current: false },
        }),
      ],
      reports: [
        boardAction({ kind: "report", row_id: 1, reason: "edited_on_machine" }),
        boardAction({ kind: "report", row_id: 2, reason: "missing" }),
        boardAction({ kind: "report", row_id: 3, reason: "did_not_verify" }),
      ],
    });
    getProfileBoard.mockResolvedValue(view);
    renderWithQueryClient(<ProfilesPage />);

    const states = (await screen.findAllByTestId("board-state")).map((el) => el.textContent);
    expect(states[0]).toContain("Edited on the display");
    expect(states[1]).toContain("Missing from the machine");
    expect(states[2]).toContain("Did not verify");
    for (const text of states) expect(text).not.toMatch(/origin|adopt/i);
  });

  it("says when writes are off, and when pulls are paused, with the way to resume", async () => {
    getProfileBoard.mockResolvedValue(
      boardView({
        writes_enabled: false,
        paused: "the machine looks reset",
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("board-writes-off")).toHaveTextContent(
      "Writes are switched off",
    );
    const paused = screen.getByTestId("board-paused");
    expect(paused).toHaveTextContent("The machine looks reset");
    expect(
      within(paused).getByRole("link", { name: /Resume it on the Sync page/ }),
    ).toHaveAttribute("href", "/sync");
  });
});

describe("drafts once the board is adopted", () => {
  const approved = draft({ status: "approved", draft_label: "Londinium [AI]" });

  beforeEach(() => {
    getProfileDrafts.mockResolvedValue({ items: [approved] });
  });

  it("offers Put on the board and no push", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);

    const button = await screen.findByTestId("put-on-board");
    expect(button).toHaveTextContent("Put on the board");
    expect(screen.queryByTestId("push-draft")).toBeNull();
    expect(screen.queryByTestId("push-draft-for-set")).toBeNull();

    await user.click(button);
    await waitFor(() => expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1 }));
    expect(pushProfileDraft).not.toHaveBeenCalled();
  });

  it("shows no rollback for a pushed draft", async () => {
    getProfileDrafts.mockResolvedValue({
      items: [draft({ status: "pushed", pushed_device_profile_id: "ab12" })],
    });
    renderWithQueryClient(<ProfilesPage />);

    await screen.findByTestId("draft-card");
    expect(screen.queryByTestId("rollback-draft")).toBeNull();
  });

  it("carries what the push carried for a Set: the next version and the major choice", async () => {
    const user = setupUser();
    getProfileDrafts.mockResolvedValue({
      items: [
        draft({
          status: "approved",
          set_id: 3,
          set_name: "Guji on the Niche",
          set_next_version_no: 4,
          set_next_minor_label: "v2.2",
          set_next_major_label: "v3",
          suggest_major: true,
          major_reason: "it changes the ramp",
        }),
      ],
    });
    renderWithQueryClient(<ProfilesPage />);

    const button = await screen.findByTestId("put-on-board-for-set");
    // The agent's suggestion is preselected, with its reason, as for a push.
    expect(screen.getByRole("checkbox", { name: "Major change" })).toBeChecked();
    expect(screen.getByTestId("major-reason")).toHaveTextContent("it changes the ramp");
    expect(button).toHaveTextContent("Put on the board and record it as v3 of Guji on the Niche");
    await user.click(screen.getByRole("checkbox", { name: "Major change" }));
    expect(button).toHaveTextContent("record it as v2.2 of Guji on the Niche");
    await user.click(button);
    await waitFor(() =>
      expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1, setId: 3, major: false }),
    );

    await user.click(screen.getByTestId("put-on-board"));
    await waitFor(() => expect(putOnBoard).toHaveBeenLastCalledWith({ draftId: 1 }));
  });

  it("says a draft already on the board reaches the machine on the next pull", async () => {
    getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          boardRowView({
            row: { id: 2, label: "Londinium [AI]", origin: "draft", pending_draft_id: 1 },
          }),
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("draft-on-board")).toHaveTextContent(
      "It reaches the machine on the next pull",
    );
    expect(screen.queryByTestId("put-on-board")).toBeNull();
  });
});

describe("before the board is adopted the page is as it was", () => {
  beforeEach(() => {
    getProfileBoard.mockResolvedValue(boardView({ adopted: false, rows: [] }));
    getProfileDrafts.mockResolvedValue({ items: [draft({ status: "approved" })] });
    getProfiles.mockResolvedValue({
      items: [
        {
          device_id: "9bar",
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
  });

  it("keeps the staged box, its push and the mirror, and shows no board", async () => {
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("push-draft")).toHaveTextContent("Push to the machine");
    expect(screen.queryByTestId("put-on-board")).toBeNull();
    expect(screen.queryByTestId("board-list")).toBeNull();
    expect(screen.getByText("Staged for the machine")).toBeInTheDocument();
    expect(screen.getByText("The mirror, as of the last pull.")).toBeInTheDocument();
    expect(screen.queryByText(/on the next pull/)).toBeNull();
  });

  it("is the same when the board cannot be read", async () => {
    getProfileBoard.mockRejectedValue(new Error("boom"));
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("push-draft")).toBeInTheDocument();
    expect(screen.queryByTestId("put-on-board")).toBeNull();
  });
});
