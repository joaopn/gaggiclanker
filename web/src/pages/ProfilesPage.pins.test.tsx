import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { selectedSuccessorSentence } from "@/lib/board";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { boardRowView, boardView, conflictView } from "@/test/boardFixtures";
import { boardRowPin, pinDetail } from "@/test/pinFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { signatureNone } from "@/test/signatureFixtures";
import lmleva from "../../../tests/fixtures/profiles/firmware-lmleva.json";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const drawn = vi.hoisted(() => ({ calls: 0 }));
vi.mock("react-chartjs-2", () => ({
  Line: () => {
    drawn.calls += 1;
    return <canvas />;
  },
}));

const api = vi.hoisted(() => ({
  getProfileBoard: vi.fn(),
  getBoardVersions: vi.fn(),
  getBoardConflict: vi.fn(),
  setBoardOnMachine: vi.fn(),
  setBoardStarred: vi.fn(),
  setBoardActiveVersion: vi.fn(),
  resolveBoardConflict: vi.fn(),
  getSignature: vi.fn(),
  unpinPin: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  ...api,
}));

/** A pinned copy as the profile list serves it: row 7, a pro profile, a pin on it. */
function pinned(overrides: Parameters<typeof boardRowView>[0] = {}) {
  return boardRowView({
    row: { id: 7, label: "Blooming v1.2", current_version_id: 901 },
    pin: boardRowPin(),
    active_version: {
      version_id: 901,
      short_hash: "c0ffee00",
      label: "Blooming v1.2",
      type: "pro",
      utility: false,
      source: "machine",
      created_at: "2026-10-03T09:00:00Z",
      shots_brewed: 14,
      profile: lmleva as Record<string, unknown>,
    },
    ...overrides,
  });
}

function rowNamed(name: string): HTMLElement {
  return screen.getByText(name).closest("li") as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  drawn.calls = 0;
  api.getProfileBoard.mockResolvedValue(
    boardView({ rows: [boardRowView({ row: { id: 1, label: "9 Bar Espresso" } }), pinned()] }),
  );
  api.getBoardVersions.mockResolvedValue({ row_id: 7, versions: [], proposed: [] });
  api.getBoardConflict.mockResolvedValue(conflictView({ row_id: 7, label: "Blooming v1.2" }));
  api.getSignature.mockResolvedValue(signatureNone);
  api.setBoardStarred.mockResolvedValue({});
  api.unpinPin.mockResolvedValue({ pin: pinDetail(), board_id: 7 });
});

describe("a pinned row on the Profiles page", () => {
  it("has no On the machine switch, keeps Starred, and badges the Recipe it belongs to", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("Blooming v1.2");

    const pinnedRow = rowNamed("Blooming v1.2");
    expect(within(pinnedRow).queryByTestId("on-machine-switch")).not.toBeInTheDocument();
    expect(
      within(pinnedRow).queryByRole("switch", { name: /on the machine/ }),
    ).not.toBeInTheDocument();
    // An ordinary row beside it still has its switch.
    expect(within(rowNamed("9 Bar Espresso")).getByTestId("on-machine-switch")).toBeInTheDocument();

    const badge = within(pinnedRow).getByTestId("pinned-badge");
    expect(badge).toHaveTextContent("Pinned: Ethiopia Guji · Blooming v1.2");
    expect(badge).toHaveAttribute("href", "/recipes/3");

    // The star still works.
    await user.click(within(pinnedRow).getByRole("switch", { name: "Blooming v1.2 starred" }));
    await waitFor(() => expect(api.setBoardStarred).toHaveBeenCalledWith(7, false));
  });

  it("opens to the frozen sentence, the curve, its shots and its signature, and nothing a version list offers", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("Blooming v1.2");

    await user.click(within(rowNamed("Blooming v1.2")).getByTestId("profile-toggle"));

    const panel = await screen.findByTestId("pinned-panel");
    expect(within(panel).getByTestId("pinned-frozen")).toHaveTextContent(
      "Frozen: this copy of Blooming belongs to v1.2 of Ethiopia Guji · Blooming. It changes only by unpinning.",
    );
    expect(within(panel).getByTestId("pinned-shots-link")).toHaveTextContent("14 shots");
    expect(within(panel).getByTestId("pinned-shots-link")).toHaveAttribute(
      "href",
      "/shots?profile_version_id=901",
    );
    expect(within(panel).getByTestId("pinned-judged-by")).toHaveTextContent(
      "Judged by Blooming's signature",
    );
    expect(within(panel).getByTestId("pinned-judged-by")).toHaveAttribute(
      "href",
      "/profiles#version-40",
    );
    expect(within(panel).getByRole("button", { name: "Unpin v1.2" })).toBeEnabled();
    await waitFor(() => expect(drawn.calls).toBeGreaterThan(0));

    // The version list's controls are not here, and its data is never read.
    expect(screen.queryByTestId("version-list")).not.toBeInTheDocument();
    expect(screen.queryByTestId("make-active")).not.toBeInTheDocument();
    expect(screen.queryByTestId("duplicate-profile")).not.toBeInTheDocument();
    expect(screen.queryByText("Signature")).not.toBeInTheDocument();
    expect(api.getBoardVersions).not.toHaveBeenCalled();
  });

  it("offers Duplicate Recipe, which goes to the Recipe page with the pinned version's dialog to open", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("Blooming v1.2");
    await user.click(within(rowNamed("Blooming v1.2")).getByTestId("profile-toggle"));

    const panel = await screen.findByTestId("pinned-panel");
    const link = within(panel).getByTestId("pinned-duplicate");

    expect(link).toHaveTextContent("Duplicate Recipe");
    expect(link).toHaveAccessibleName("Duplicate Recipe: v1.2 of Ethiopia Guji · Blooming");
    expect(link).toHaveAttribute("href", "/recipes/3?duplicate=22");
    // Beside Unpin, in the panel's one row of actions.
    expect(
      Array.from(within(panel).getByTestId("pinned-actions").children).map((el) => el.textContent),
    ).toEqual(["Duplicate Recipe", "Unpin"]);
    // An ordinary row has no such button.
    expect(within(rowNamed("9 Bar Espresso")).queryByTestId("pinned-duplicate")).toBeNull();
  });

  it("counts the shots the link lists: those brewed on the copy, filed or waiting", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          pinned({
            pin: boardRowPin({ shot_count: 0 }),
            active_version: { ...pinned().active_version, shots_brewed: 1 },
          }),
        ],
      }),
    );
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("Blooming v1.2");
    await user.click(within(rowNamed("Blooming v1.2")).getByTestId("profile-toggle"));

    expect(await screen.findByTestId("pinned-shots-link")).toHaveTextContent("1 shot");
  });

  it("asks before unpinning, in the unpin words, and unpins once", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("Blooming v1.2");
    await user.click(within(rowNamed("Blooming v1.2")).getByTestId("profile-toggle"));
    const panel = await screen.findByTestId("pinned-panel");

    await user.click(within(panel).getByRole("button", { name: "Unpin v1.2" }));

    const confirm = within(panel).getByTestId("unpin-confirm");
    expect(confirm).toHaveTextContent(
      'The next sync takes "Blooming v1.2" off the machine. Its 14 shots stay filed under v1.2.',
    );
    expect(within(confirm).queryByTestId("unpin-selected")).not.toBeInTheDocument();
    expect(api.unpinPin).not.toHaveBeenCalled();

    await user.dblClick(within(confirm).getByRole("button", { name: "Unpin" }));
    await waitFor(() => expect(api.unpinPin).toHaveBeenCalledTimes(1));
    expect(api.unpinPin).toHaveBeenCalledWith(5);
  });

  it("adds the shared successor sentence when the copy is the machine's selected profile", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          pinned({
            machine: {
              device_id: "p1",
              present: true,
              holds_current: true,
              favorite: true,
              selected: true,
            },
          }),
        ],
      }),
    );
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("Blooming v1.2");
    await user.click(within(rowNamed("Blooming v1.2")).getByTestId("profile-toggle"));
    const panel = await screen.findByTestId("pinned-panel");

    await user.click(within(panel).getByRole("button", { name: "Unpin v1.2" }));

    expect(within(panel).getByTestId("unpin-selected")).toHaveTextContent(
      selectedSuccessorSentence(),
    );
  });

  it("in conflict, offers both sides and Unpin", async () => {
    api.getProfileBoard.mockResolvedValue(boardView({ rows: [pinned({ in_conflict: true })] }));
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByText("Blooming v1.2");
    await user.click(within(rowNamed("Blooming v1.2")).getByTestId("profile-toggle"));

    const conflict = await screen.findByTestId("conflict-panel");
    expect(within(conflict).getByTestId("keep-app")).toBeEnabled();
    expect(within(conflict).getByTestId("keep-machine")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Unpin v1.2" })).toBeEnabled();
    expect(conflict).toHaveTextContent("Either way the machine's version is kept");
    expect(within(conflict).getByTestId("keep-machine-help")).toHaveTextContent(
      "The Recipe gets a new version with the machine's brew, and the next sync puts it on the machine.",
    );

    await user.click(within(conflict).getByTestId("keep-app"));
    await waitFor(() => expect(api.resolveBoardConflict).toHaveBeenCalledTimes(1));
    expect(api.resolveBoardConflict.mock.calls[0]?.[1]).toBe("app");
  });

  it("an ordinary row in conflict still offers both sides", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [boardRowView({ in_conflict: true })] }),
    );
    api.getBoardConflict.mockResolvedValue(conflictView());
    api.getBoardVersions.mockResolvedValue({ row_id: 1, versions: [], proposed: [] });
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));

    expect(await screen.findByTestId("keep-machine")).toBeInTheDocument();
    expect(screen.getByTestId("keep-app")).toBeInTheDocument();
  });
});
