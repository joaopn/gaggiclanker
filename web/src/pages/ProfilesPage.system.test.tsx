import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { STAR_LIMIT_NOTE } from "@/lib/board";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { boardAction, boardRowView, boardView } from "@/test/boardFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

vi.mock("react-chartjs-2", () => ({ Line: () => <canvas /> }));

const api = vi.hoisted(() => ({
  getProfileBoard: vi.fn(),
  getBoardVersions: vi.fn(),
  getBoardConflict: vi.fn(),
  setBoardOnMachine: vi.fn(),
  setBoardStarred: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  ...api,
}));

const STAR_LIMIT = "The machine shows at most 10 starred profiles. Take a star off another first.";

function rowNamed(name: string): HTMLElement {
  return screen.getByText(name).closest("li") as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getProfileBoard.mockResolvedValue(
    boardView({
      rows: [
        boardRowView({ row: { id: 1, label: "9 Bar Espresso", on_home_screen: false } }),
        boardRowView({
          row: {
            id: 2,
            label: "[Utility] Backflush",
            device_profile_id: "flush",
            on_home_screen: false,
          },
          utility: true,
          kept_by_machine: true,
        }),
      ],
    }),
  );
  api.getBoardVersions.mockResolvedValue({ row_id: 1, versions: [], proposed: [] });
  api.getBoardConflict.mockResolvedValue(null);
});

describe("a profile the machine keeps for itself", () => {
  it("still reads on the machine, cannot be switched, and says why in one line", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("[Utility] Backflush");

    const kept = rowNamed("[Utility] Backflush");
    const sw = within(kept).getByRole("switch", { name: "[Utility] Backflush on the machine" });
    expect(sw).toBeChecked();
    expect(sw).toBeDisabled();
    // The switch still says what it always says.
    expect(within(kept).getAllByText("On the machine").length).toBeGreaterThan(0);
    expect(within(kept).getByTestId("kept-by-machine")).toHaveTextContent("Kept by the machine");
    await user.click(sw);
    expect(api.setBoardOnMachine).not.toHaveBeenCalled();

    // Another row has its switch, and no such line.
    const other = rowNamed("9 Bar Espresso");
    expect(
      within(other).getByRole("switch", { name: "9 Bar Espresso on the machine" }),
    ).toBeEnabled();
    expect(within(other).queryByTestId("kept-by-machine")).not.toBeInTheDocument();
  });

  it("keeps the star working, as for any profile", async () => {
    const user = setupUser();
    api.setBoardStarred.mockResolvedValue({});
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("[Utility] Backflush");

    await user.click(
      within(rowNamed("[Utility] Backflush")).getByRole("switch", {
        name: "[Utility] Backflush starred",
      }),
    );

    await waitFor(() => expect(api.setBoardStarred).toHaveBeenCalledWith(2, true));
  });
});

describe("an eleventh star", () => {
  it("shows the refusal the server gave, naming the limit, instead of failing silently", async () => {
    const user = setupUser();
    api.setBoardStarred.mockRejectedValue(new Error(STAR_LIMIT));
    const { toast } = await import("sonner");
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("9 Bar Espresso");

    await user.click(
      within(rowNamed("9 Bar Espresso")).getByRole("switch", { name: "9 Bar Espresso starred" }),
    );

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(STAR_LIMIT));
    // Nothing was stored, so the switch reads what the board says.
    expect(
      within(rowNamed("9 Bar Espresso")).getByRole("switch", { name: "9 Bar Espresso starred" }),
    ).not.toBeChecked();
  });
});

describe("a star past the machine's tenth", () => {
  it("says on the row that the sync leaves it unstarred, and only on that row", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          boardRowView({ row: { id: 1, label: "9 Bar Espresso", on_home_screen: true } }),
          boardRowView({
            row: { id: 2, label: "Lever", on_home_screen: true },
            planned: [
              boardAction({
                kind: "report",
                reason: "star_limit",
                label: "Lever",
                detail: STAR_LIMIT,
              }),
            ],
          }),
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("Lever");

    expect(within(rowNamed("Lever")).getByTestId("profile-state")).toHaveTextContent(
      STAR_LIMIT_NOTE,
    );
    expect(within(rowNamed("9 Bar Espresso")).getByTestId("profile-state")).not.toHaveTextContent(
      "at most 10",
    );
  });
});
