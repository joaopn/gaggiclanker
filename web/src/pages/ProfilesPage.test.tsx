import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BoardProposal } from "@/api/types";
import { ProfilesPage } from "@/pages/ProfilesPage";
import {
  boardAction,
  boardRowView,
  boardView,
  conflictView,
  landing,
  listedVersion,
  profileWith,
  versionsView,
} from "@/test/boardFixtures";
import { draft, draftDetail, draftProfile, yieldChange } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { signatureConfirmed, signatureNone } from "@/test/signatureFixtures";
import lmleva from "../../../tests/fixtures/profiles/firmware-lmleva.json";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

// The canvas is opaque to jsdom; what the curves are handed is what the page decides.
const drawn = vi.hoisted(() => ({ data: new Set<object>(), calls: 0 }));
vi.mock("react-chartjs-2", () => ({
  Line: (props: { data: object }) => {
    drawn.data.add(props.data);
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
  resumeBoard: vi.fn(),
  putOnBoard: vi.fn(),
  discardProfileDraft: vi.fn(),
  getProfileDraft: vi.fn(),
  importFiles: vi.fn(),
  previewProfileDraft: vi.fn(),
  createProfileDraft: vi.fn(),
  getSignature: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  ...api,
}));

const row = (overrides: Parameters<typeof boardRowView>[0] = {}) => boardRowView(overrides);

function proposal(overrides: Partial<BoardProposal> & { id?: number } = {}): BoardProposal {
  const { id = 11, ...rest } = overrides;
  return {
    draft: draft({ id, draft_version_id: 40 + id }),
    landing: landing({ draft_id: id }),
    row_id: 1,
    ...rest,
  };
}

function rowNamed(name: string): HTMLElement {
  const found = screen
    .getAllByTestId("profile-row")
    .find((el) => within(el).queryByText(name, { selector: "span" }));
  if (!found) throw new Error(`no row named ${name}`);
  return found;
}

beforeEach(() => {
  vi.clearAllMocks();
  drawn.data.clear();
  drawn.calls = 0;
  window.localStorage.clear();
  api.getProfileBoard.mockResolvedValue(boardView());
  api.getBoardVersions.mockResolvedValue(
    versionsView([listedVersion({ version_id: 7, is_active: true })]),
  );
  api.getBoardConflict.mockResolvedValue(null);
  api.getSignature.mockResolvedValue(signatureNone);
  api.getProfileDraft.mockResolvedValue(draftDetail());
  for (const name of [
    "setBoardOnMachine",
    "setBoardStarred",
    "setBoardActiveVersion",
    "resolveBoardConflict",
  ] as const) {
    api[name].mockResolvedValue(row().row);
  }
  api.resumeBoard.mockResolvedValue({ resumed: true });
  api.putOnBoard.mockResolvedValue(row().row);
  api.discardProfileDraft.mockResolvedValue(draft());
});

describe("the list", () => {
  it("shows a profile with what it brews and where it stands on the machine", async () => {
    renderWithQueryClient(<ProfilesPage />);

    const profile = await screen.findByTestId("profile-row");
    expect(within(profile).getByText("9 Bar Espresso")).toBeInTheDocument();
    expect(profile).toHaveTextContent("standard · 1 phase · target 36 g");
    expect(within(profile).getByTestId("profile-state")).toHaveTextContent("On the machine");
  });

  it.each([
    [
      "will be put on the machine",
      { machine: { present: false, holds_current: false }, planned: [boardAction({ row_id: 1 })] },
      "Will be put on the machine at the next sync",
    ],
    [
      "will be removed",
      {
        row: { on_machine: false },
        planned: [boardAction({ kind: "remove", row_id: 1, reason: "off", device_id: "9bar" })],
      },
      "Will be removed at the next sync",
    ],
    [
      "not on the machine",
      { row: { on_machine: false }, machine: { present: false, holds_current: false } },
      "Not on the machine",
    ],
  ])("says a profile that %s", async (_name, overrides, text) => {
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row(overrides as Parameters<typeof boardRowView>[0])] }),
    );
    // Off profiles are listed only when asked for.
    window.localStorage.setItem("gaggiclanker.profiles.showOff", "1");
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("profile-state")).toHaveTextContent(text);
  });

  it("marks a conflict, a waiting proposal and the selected profile, and nothing else", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row({ in_conflict: true, proposed_versions: 1 }),
          row({
            row: { id: 2, label: "Londinium", device_profile_id: "lond" },
            machine: { present: true, holds_current: true, selected: true },
          }),
          row({ row: { id: 3, label: "Plain", device_profile_id: "plain" } }),
        ],
        proposals: [proposal({ row_id: 1 })],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await screen.findAllByTestId("profile-row");
    expect(within(rowNamed("9 Bar Espresso")).getByText("Conflict")).toBeInTheDocument();
    expect(within(rowNamed("9 Bar Espresso")).getByTestId("proposed-badge")).toHaveTextContent(
      "Proposed",
    );
    expect(within(rowNamed("Londinium")).getByText("Selected")).toBeInTheDocument();
    const plain = rowNamed("Plain");
    for (const word of ["Conflict", "Proposed", "Selected", "New"]) {
      expect(within(plain).queryByText(word)).not.toBeInTheDocument();
    }
  });

  it("counts several waiting proposals on one profile", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [row({ proposed_versions: 2 })],
        proposals: [proposal({ id: 11 }), proposal({ id: 12 })],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("proposed-badge")).toHaveTextContent("Proposed (2)");
  });

  it("says in one line that the machine will follow when writes are off", async () => {
    api.getProfileBoard.mockResolvedValue(boardView({ writes_enabled: false }));
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("writes-line")).toHaveTextContent(
      "Writes are off: the machine will follow when they are on.",
    );
  });

  it("hides profiles that are off until asked, remembers the choice, and keeps the ones that need a person", async () => {
    const user = setupUser();
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row(),
          row({
            row: { id: 2, label: "Old one", on_machine: false },
            machine: { present: false, holds_current: false },
          }),
          row({
            row: { id: 3, label: "Off but in conflict", on_machine: false },
            in_conflict: true,
          }),
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await screen.findAllByTestId("profile-row");
    expect(screen.queryByText("Old one")).not.toBeInTheDocument();
    expect(screen.getByText("Off but in conflict")).toBeInTheDocument();
    expect(screen.getByTestId("hidden-count")).toHaveTextContent("1 profile is off and hidden");

    await user.click(screen.getByTestId("show-off"));
    expect(screen.getByText("Old one")).toBeInTheDocument();
    expect(window.localStorage.getItem("gaggiclanker.profiles.showOff")).toBe("1");
  });

  it("starts with the filter on when the viewer chose that before", async () => {
    window.localStorage.setItem("gaggiclanker.profiles.showOff", "1");
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ row: { id: 2, label: "Old one", on_machine: false } })] }),
    );
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByText("Old one")).toBeInTheDocument();
    expect(screen.getByTestId("show-off")).toBeChecked();
  });

  it("keeps a profile on screen after it is switched off, so it does not vanish under the cursor", async () => {
    const user = setupUser();
    const first = boardView({ rows: [row()] });
    api.getProfileBoard.mockResolvedValueOnce(first);
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ row: { on_machine: false } })] }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await user.click(await screen.findByRole("switch", { name: "9 Bar Espresso on the machine" }));

    await waitFor(() => expect(screen.getByTestId("profile-row")).toHaveAttribute("data-on", "no"));
    expect(screen.getByText("9 Bar Espresso")).toBeInTheDocument();
  });
});

describe("a profile that is off while its file is on the machine", () => {
  it("is shown behind the filter and says the next sync removes it", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [row({ row: { id: 2, label: "Still there", on_machine: false } })],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const profile = await screen.findByTestId("profile-row");
    expect(profile).toHaveTextContent("Still there");
    expect(within(profile).getByTestId("profile-state")).toHaveTextContent(
      "On the machine, switched off: the next sync removes it",
    );
    expect(profile).not.toHaveTextContent("Not on the machine");
  });

  it("says so too in the sync that matches a new file to it, before the file is the row's", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row({
            row: { id: 2, label: "Matched", on_machine: false },
            machine: { present: false, holds_current: false },
          }),
        ],
        actions: [boardAction({ kind: "adopt", reason: "attached", row_id: 2, label: "Matched" })],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const profile = await screen.findByTestId("profile-row");
    expect(within(profile).getByTestId("profile-state")).toHaveTextContent(
      "On the machine, switched off: the next sync removes it",
    );
  });
});

describe("a profile that is on while its file is being matched", () => {
  it("reads On the machine, not 'Not on the machine'", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row({
            row: { id: 4, label: "Matched on" },
            machine: { present: false, holds_current: false },
          }),
        ],
        actions: [
          boardAction({ kind: "adopt", reason: "attached", row_id: 4, label: "Matched on" }),
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const profile = await screen.findByTestId("profile-row");
    expect(within(profile).getByTestId("profile-state")).toHaveTextContent("On the machine");
    expect(profile).not.toHaveTextContent("Not on the machine");
  });
});

describe("the two switches", () => {
  it("On the machine sends its own request and a switch back on needs no question", async () => {
    const user = setupUser();
    window.localStorage.setItem("gaggiclanker.profiles.showOff", "1");
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ row: { on_machine: false } })] }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await user.click(await screen.findByRole("switch", { name: "9 Bar Espresso on the machine" }));

    await waitFor(() => expect(api.setBoardOnMachine).toHaveBeenCalledWith(1, true));
    expect(screen.queryByTestId("switch-off-confirm")).not.toBeInTheDocument();
  });

  it("switching off a profile nobody brews and the machine has not selected asks nothing", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);

    await user.click(await screen.findByRole("switch", { name: "9 Bar Espresso on the machine" }));

    await waitFor(() => expect(api.setBoardOnMachine).toHaveBeenCalledWith(1, false));
  });

  it("Starred sends its own request while the profile is on", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);

    const star = await screen.findByRole("switch", { name: "9 Bar Espresso starred" });
    expect(star).toBeEnabled();
    await user.click(star);

    await waitFor(() => expect(api.setBoardStarred).toHaveBeenCalledWith(1, false));
  });

  it("Starred is disabled with its reason while the profile is off, and keeps its value", async () => {
    window.localStorage.setItem("gaggiclanker.profiles.showOff", "1");
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ row: { on_machine: false, on_home_screen: true } })] }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const star = await screen.findByRole("switch", { name: "9 Bar Espresso starred" });
    expect(star).toBeDisabled();
    expect(star).toBeChecked();
    expect(star).toHaveAttribute("title", expect.stringContaining("only while the profile is on"));
  });

  it("asks before switching off a profile a Set brews, naming the Sets, and sends nothing until confirmed", async () => {
    const user = setupUser();
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row({
            sets_brewing: [
              { set_id: 3, name: "Ethiopia washed" },
              { set_id: 4, name: "Daily" },
            ],
          }),
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await user.click(await screen.findByRole("switch", { name: "9 Bar Espresso on the machine" }));

    const strip = await screen.findByTestId("switch-off-confirm");
    expect(within(strip).getByTestId("switch-off-sets")).toHaveTextContent(
      "Ethiopia washed, Daily brew this profile",
    );
    expect(api.setBoardOnMachine).not.toHaveBeenCalled();

    await user.click(within(strip).getByRole("button", { name: "Cancel" }));
    expect(api.setBoardOnMachine).not.toHaveBeenCalled();

    await user.click(screen.getByRole("switch", { name: "9 Bar Espresso on the machine" }));
    await user.click(
      within(await screen.findByTestId("switch-off-confirm")).getByRole("button", {
        name: "Switch off",
      }),
    );
    await waitFor(() => expect(api.setBoardOnMachine).toHaveBeenCalledWith(1, false));
  });

  it("says the machine will select another profile first when the selected one is switched off", async () => {
    const user = setupUser();
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [row({ machine: { present: true, holds_current: true, selected: true } })],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await user.click(await screen.findByRole("switch", { name: "9 Bar Espresso on the machine" }));

    expect(await screen.findByTestId("switch-off-selected")).toHaveTextContent(
      "selects another enabled profile first",
    );
    expect(screen.queryByTestId("switch-off-sets")).not.toBeInTheDocument();
  });
});

describe("the reset guard", () => {
  const preview = (overrides = {}) => ({
    push: 3,
    remove: 1,
    star: 0,
    join: 0,
    lines: [
      boardAction({ kind: "push", label: "Londinium" }),
      boardAction({ kind: "remove", label: "Old default", device_id: "d1", reason: "off" }),
    ],
    ...overrides,
  });
  const paused = (overrides = {}) =>
    boardView({
      paused: "the machine looks reset",
      machine_source: "machine",
      resume_preview: preview(overrides),
    });
  /** The mirror says nothing is missing (an empty list never wipes it); the machine answers live. */
  const live = (answer: () => Promise<unknown>) =>
    api.getProfileBoard.mockImplementation(async (isLive?: boolean) =>
      isLive
        ? answer()
        : boardView({
            paused: "the machine looks reset",
            resume_preview: { push: 0, remove: 0, star: 0, join: 0, lines: [] },
          }),
    );

  it("asks once, with the numbers from the machine, and one button that says what it does", async () => {
    const user = setupUser();
    live(async () => paused());
    renderWithQueryClient(<ProfilesPage />);

    await waitFor(() =>
      expect(screen.getByTestId("reset-question")).toHaveTextContent(
        "The machine looks reset: put back 3 profiles and remove 1?",
      ),
    );
    expect(api.getProfileBoard).toHaveBeenCalledWith(true);
    const button = screen.getByTestId("reset-resume");
    expect(button).toHaveTextContent(
      "Resume syncing: the next sync puts back 3 profiles and removes 1",
    );
    await user.click(button);

    await waitFor(() => expect(api.resumeBoard).toHaveBeenCalledTimes(1));
  });

  it("has a line for everything the question counts: a file matched to a profile that is off is removed", async () => {
    const user = setupUser();
    live(async () => ({
      ...paused({
        push: 1,
        remove: 2,
        lines: [
          boardAction({ kind: "push", label: "Londinium" }),
          boardAction({ kind: "remove", label: "Old default", device_id: "d1", reason: "off" }),
          boardAction({
            kind: "adopt",
            label: "Display copy",
            reason: "attached",
            row_id: 2,
            device_id: "d2",
          }),
        ],
      }),
      rows: [row(), row({ row: { id: 2, label: "Display copy", on_machine: false } })],
    }));
    renderWithQueryClient(<ProfilesPage />);
    await waitFor(() =>
      expect(screen.getByTestId("reset-question")).toHaveTextContent(
        "put back 1 profile and remove 2?",
      ),
    );

    await user.click(screen.getByTestId("reset-lines-toggle"));

    const lines = screen
      .getAllByRole("listitem", { hidden: false })
      .filter((li) => screen.getByTestId("reset-lines").contains(li));
    const removing = lines.filter((li) => /remove/i.test(li.textContent ?? ""));
    expect(removing).toHaveLength(2);
    expect(screen.getByTestId("reset-lines")).toHaveTextContent(
      "matched to Display copy, which is off: the next sync removes it",
    );
  });

  it("names the star changes too when there are any", async () => {
    live(async () => paused({ push: 1, remove: 0, star: 9 }));
    renderWithQueryClient(<ProfilesPage />);

    await waitFor(() =>
      expect(screen.getByTestId("reset-question")).toHaveTextContent(
        "put back 1 profile and change 9 stars?",
      ),
    );
    expect(screen.getByTestId("reset-resume")).toHaveTextContent("and changes 9 stars");
  });

  it("leaves out 'remove 0'", async () => {
    live(async () => paused({ remove: 0, push: 9 }));
    renderWithQueryClient(<ProfilesPage />);

    await waitFor(() =>
      expect(screen.getByTestId("reset-question")).toHaveTextContent(
        "The machine looks reset: put back 9 profiles?",
      ),
    );
    expect(screen.getByTestId("reset-resume")).toHaveTextContent(
      "Resume syncing: the next sync puts back 9 profiles",
    );
    expect(screen.getByTestId("reset-banner")).not.toHaveTextContent("remove 0");
  });

  it("says plainly that the machine could not be read, never the stored copy's numbers", async () => {
    live(async () => {
      throw new Error("no machine");
    });
    renderWithQueryClient(<ProfilesPage />);

    await waitFor(() =>
      expect(screen.getByTestId("reset-question")).toHaveTextContent("could not be read just now"),
    );
    expect(screen.getByTestId("reset-banner")).not.toHaveTextContent("put back");
    expect(screen.getByTestId("reset-resume")).toHaveTextContent("Resume syncing");
  });

  it("does not take a read that fell back to the mirror for the machine's answer", async () => {
    live(async () => ({ ...paused(), machine_source: "mirror" }));
    renderWithQueryClient(<ProfilesPage />);

    await waitFor(() =>
      expect(screen.getByTestId("reset-question")).toHaveTextContent("could not be read just now"),
    );
    expect(screen.getByTestId("reset-banner")).not.toHaveTextContent("back 3");
    expect(screen.getByTestId("reset-resume")).toHaveTextContent(/^Resume syncing$/);
  });

  it("does not read the machine when the sync is not paused", async () => {
    renderWithQueryClient(<ProfilesPage />);

    await screen.findByTestId("profile-row");
    expect(screen.queryByTestId("reset-banner")).not.toBeInTheDocument();
    expect(api.getProfileBoard).not.toHaveBeenCalledWith(true);
  });

  it("does not read the machine again when a board write refreshes the page", async () => {
    const user = setupUser();
    live(async () => paused());
    renderWithQueryClient(<ProfilesPage />);
    await waitFor(() =>
      expect(screen.getByTestId("reset-question")).toHaveTextContent("put back 3"),
    );
    const liveReads = () =>
      api.getProfileBoard.mock.calls.filter((call: unknown[]) => call[0] === true).length;
    expect(liveReads()).toBe(1);

    await user.click(await screen.findByRole("switch", { name: "9 Bar Espresso starred" }));
    await waitFor(() => expect(api.setBoardStarred).toHaveBeenCalled());
    await waitFor(() =>
      expect(
        api.getProfileBoard.mock.calls.filter((call: unknown[]) => call[0] !== true).length,
      ).toBeGreaterThan(1),
    );

    expect(liveReads()).toBe(1);
  });

  it("sends one resume per double-click", async () => {
    const user = setupUser();
    live(async () => paused());
    let release: (value: unknown) => void = () => {};
    api.resumeBoard.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    renderWithQueryClient(<ProfilesPage />);
    await waitFor(() =>
      expect(screen.getByTestId("reset-question")).toHaveTextContent("put back 3"),
    );

    await user.dblClick(screen.getByTestId("reset-resume"));
    release({ resumed: true });

    await waitFor(() => expect(api.resumeBoard).toHaveBeenCalledTimes(1));
  });

  it("keeps the per-profile lines one click away", async () => {
    const user = setupUser();
    live(async () => paused());
    renderWithQueryClient(<ProfilesPage />);

    await waitFor(() => expect(screen.getByTestId("reset-lines-toggle")).toBeInTheDocument());
    expect(screen.queryByTestId("reset-lines")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("reset-lines-toggle"));
    const lines = screen.getByTestId("reset-lines");
    expect(lines).toHaveTextContent("Put Londinium on the machine");
    expect(lines).toHaveTextContent("Remove Old default from the machine");
  });

  it("is not there when the sync is not paused", async () => {
    renderWithQueryClient(<ProfilesPage />);

    await screen.findByTestId("profile-row");
    expect(screen.queryByTestId("reset-banner")).not.toBeInTheDocument();
  });
});

describe("the dropdown", () => {
  const three = () => [
    listedVersion({
      version_id: 9,
      is_active: true,
      source: "edit",
      added_at: "2026-03-03T00:00:00.000Z",
      previous_version_id: 8,
      profile: profileWith(8, 94),
      shots_brewed: 4,
    }),
    listedVersion({
      version_id: 8,
      source: "agent",
      added_at: "2026-03-02T00:00:00.000Z",
      previous_version_id: 7,
      profile: profileWith(8),
    }),
    listedVersion({
      version_id: 7,
      source: "machine",
      is_on_machine: false,
      profile: profileWith(9),
    }),
  ];

  async function open(user: ReturnType<typeof setupUser>) {
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));
    return screen.findAllByTestId("version");
  }

  it("opens in the row, lists the versions in the order served, and says where each came from", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(versionsView(three()));
    const versions = await open(user);

    expect(versions.map((v) => v.getAttribute("data-version-id"))).toEqual(["9", "8", "7"]);
    expect(within(versions[0] as HTMLElement).getByText(/Edited in the app/)).toBeInTheDocument();
    expect(
      within(versions[1] as HTMLElement).getByText(/Proposed by the agent/),
    ).toBeInTheDocument();
    expect(
      within(versions[2] as HTMLElement).getByText(/Read from the machine/),
    ).toBeInTheDocument();
    expect(
      within(versions[0] as HTMLElement).getByRole("link", { name: "4 shots" }),
    ).toHaveAttribute("href", "/shots?profile_version_id=9");
    // In the row, not a modal.
    expect(screen.getByTestId("profile-row")).toContainElement(
      screen.getByTestId("profile-dropdown"),
    );
  });

  it("marks the active version and offers Make active on the others only", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(versionsView(three()));
    const versions = await open(user);

    expect(within(versions[0] as HTMLElement).getByTestId("active-marker")).toBeInTheDocument();
    expect(within(versions[0] as HTMLElement).queryByTestId("make-active")).not.toBeInTheDocument();
    expect(within(versions[1] as HTMLElement).getByTestId("make-active")).toBeInTheDocument();
  });

  it("shows the first version as a summary and every later one as a diff against the one before it, never another", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(versionsView(three()));
    const versions = await open(user);

    // v7: nothing before it, so a summary and no diff.
    expect(within(versions[2] as HTMLElement).getByTestId("profile-summary")).toBeInTheDocument();
    expect(
      within(versions[2] as HTMLElement).queryByTestId("profile-diff"),
    ).not.toBeInTheDocument();
    // v8 against v7: the pump moved 9 -> 8.
    const eight = within(versions[1] as HTMLElement).getByTestId("profile-diff");
    expect(eight).toHaveTextContent("pressure 9 bar");
    expect(eight).toHaveTextContent("pressure 8 bar");
    // v9 against v8 (not v7): only the temperature moved; the pump is the same as v8's.
    const nine = within(versions[0] as HTMLElement).getByTestId("profile-diff");
    expect(nine).toHaveTextContent("temperature");
    expect(nine).toHaveTextContent("93");
    expect(nine).toHaveTextContent("94");
    expect(nine).not.toHaveTextContent("pump");
  });

  it("puts the summary one click away from a diff", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(versionsView(three()));
    const versions = await open(user);
    const second = versions[1] as HTMLElement;

    expect(within(second).queryByTestId("profile-summary")).not.toBeInTheDocument();
    await user.click(within(second).getByTestId("show-whole-profile"));
    expect(within(second).getByTestId("profile-summary")).toBeInTheDocument();
  });

  it("Make active names the version in its request", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(versionsView(three()));
    const versions = await open(user);

    // Not the row's current version (7): the request names the version that was clicked.
    await user.click(within(versions[1] as HTMLElement).getByTestId("make-active"));

    await waitFor(() => expect(api.setBoardActiveVersion).toHaveBeenCalledWith(1, 8));
  });

  it("asks first when a Set brews the profile, naming it", async () => {
    const user = setupUser();
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ sets_brewing: [{ set_id: 3, name: "Ethiopia washed" }] })] }),
    );
    api.getBoardVersions.mockResolvedValue(versionsView(three()));
    const versions = await open(user);

    await user.click(within(versions[2] as HTMLElement).getByTestId("make-active"));

    const strip = await screen.findByTestId("make-active-confirm");
    expect(strip).toHaveTextContent("Ethiopia washed brews this profile");
    expect(api.setBoardActiveVersion).not.toHaveBeenCalled();
    await user.click(within(strip).getByRole("button", { name: "Make active" }));
    await waitFor(() => expect(api.setBoardActiveVersion).toHaveBeenCalledWith(1, 7));
  });

  it("Edit a copy opens the JSON editor on that version", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(versionsView(three()));
    api.previewProfileDraft.mockResolvedValue({ valid: true, profile: profileWith(8) });
    const versions = await open(user);

    await user.click(within(versions[1] as HTMLElement).getByTestId("edit-a-copy"));

    expect(await screen.findByText("Edit a copy of 9 Bar Espresso")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save as a proposal" })).toBeInTheDocument();
  });

  it("opens one row at a time", async () => {
    const user = setupUser();
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row(), row({ row: { id: 2, label: "Londinium" } })] }),
    );
    api.getBoardVersions.mockImplementation(async (id: number) =>
      versionsView([listedVersion({ version_id: 7 + id, is_active: true })], { row_id: id }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const toggles = await screen.findAllByTestId("profile-toggle");
    await user.click(toggles[0] as HTMLElement);
    await screen.findByTestId("profile-dropdown");
    await user.click(screen.getAllByTestId("profile-toggle")[1] as HTMLElement);

    await waitFor(() => expect(screen.getAllByTestId("profile-dropdown")).toHaveLength(1));
    expect(within(rowNamed("Londinium")).getByTestId("profile-dropdown")).toBeInTheDocument();
  });
});

describe("proposed versions", () => {
  const withProposal = (draftOverrides = {}, landingOverrides = {}) => {
    const p = proposal();
    p.draft = draft({
      id: 11,
      draft_version_id: 51,
      draft_label: "9 Bar Espresso [AI]",
      ...draftOverrides,
    });
    p.landing = landing({ draft_id: 11, ...landingOverrides });
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ proposed_versions: 1 })], proposals: [p] }),
    );
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 7, is_active: true })], {
        proposed: [{ draft: p.draft, profile: draftProfile(), compared_to_version_id: 7 }],
      }),
    );
    return p;
  };

  async function open(user: ReturnType<typeof setupUser>) {
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));
    return screen.findByTestId("proposal");
  }

  it("sits above the versions, marked Proposed, with what it changes against the active version", async () => {
    const user = setupUser();
    withProposal();
    const panel = await open(user);

    expect(within(panel).getByText("Proposed")).toBeInTheDocument();
    expect(within(panel).getByTestId("profile-diff")).toHaveTextContent("pressure 9 bar");
    expect(within(panel).getByTestId("profile-diff")).toHaveTextContent("pressure 8 bar");
    const dropdown = screen.getByTestId("profile-dropdown");
    expect(
      panel.compareDocumentPosition(within(dropdown).getByTestId("version-list")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("Make active puts it on the board, with no Set when it has none", async () => {
    const user = setupUser();
    withProposal();
    const panel = await open(user);

    await user.click(within(panel).getByTestId("make-proposal-active"));

    await waitFor(() => expect(api.putOnBoard).toHaveBeenCalledWith({ draftId: 11 }));
  });

  it("says a proposal moves a stop condition and makes it active in one click, asking for nothing", async () => {
    const user = setupUser();
    withProposal({ stop_condition_changes: [yieldChange()] });
    const panel = await open(user);

    const warning = within(panel).getByTestId("stop-condition-warning");
    expect(warning).toHaveTextContent("phase 1 · Pump");
    expect(warning).toHaveTextContent("changed volumetric gte 36 → gte 44");
    expect(within(panel).queryByRole("checkbox")).not.toBeInTheDocument();
    const button = within(panel).getByTestId("make-proposal-active");
    expect(button).toBeEnabled();
    await user.click(button);

    await waitFor(() => expect(api.putOnBoard).toHaveBeenCalledWith({ draftId: 11 }));
  });

  it("records the Set's next version only through the button that says so, with the major choice", async () => {
    const user = setupUser();
    withProposal(
      {
        set_id: 3,
        set_name: "Ethiopia washed",
        set_next_minor_label: "v1.2",
        set_next_major_label: "v2",
        prediction: "Less bitter",
      },
      { for_set: { row_id: 1, row_label: "9 Bar Espresso" } },
    );
    const panel = await open(user);

    expect(within(panel).getByTestId("proposal-prediction")).toHaveTextContent("Less bitter");
    const forSet = within(panel).getByTestId("make-proposal-active-for-set");
    expect(forSet).toHaveTextContent("Make active and record it as v1.2 of Ethiopia washed");
    await user.click(within(panel).getByRole("checkbox", { name: /major/i }));
    expect(forSet).toHaveTextContent("record it as v2 of Ethiopia washed");
    await user.click(forSet);

    await waitFor(() =>
      expect(api.putOnBoard).toHaveBeenCalledWith({ draftId: 11, setId: 3, major: true }),
    );
  });

  it("the other button records nothing on the Set", async () => {
    const user = setupUser();
    withProposal(
      {
        set_id: 3,
        set_name: "Ethiopia washed",
        set_next_minor_label: "v1.2",
        set_next_major_label: "v2",
      },
      { for_set: { row_id: 1, row_label: "9 Bar Espresso" } },
    );
    const panel = await open(user);

    await user.click(within(panel).getByTestId("make-proposal-active"));

    await waitFor(() => expect(api.putOnBoard).toHaveBeenCalledWith({ draftId: 11 }));
  });

  it("says that a profile that is off records the Set's version only after a sync", async () => {
    const user = setupUser();
    window.localStorage.setItem("gaggiclanker.profiles.showOff", "1");
    withProposal(
      {
        set_id: 3,
        set_name: "Ethiopia washed",
        set_next_minor_label: "v1.2",
        set_next_major_label: "v2",
      },
      { for_set: { row_id: 1, row_label: "9 Bar Espresso" } },
    );
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [row({ row: { on_machine: false }, proposed_versions: 1 })],
        proposals: [
          proposal({
            draft: draft({
              id: 11,
              set_id: 3,
              set_name: "Ethiopia washed",
              set_next_minor_label: "v1.2",
              set_next_major_label: "v2",
            }),
            landing: landing({
              draft_id: 11,
              for_set: { row_id: 1, row_label: "x" },
            }),
          }),
        ],
      }),
    );
    const panel = await open(user);

    expect(within(panel).getByTestId("proposal-set-off")).toHaveTextContent("only after a sync");
  });

  it("offers no Make active for a profile that is already in the list, only Decline", async () => {
    const user = setupUser();
    withProposal({}, { already_on_board_label: "9 Bar Espresso" });
    const panel = await open(user);

    expect(within(panel).getByTestId("proposal-already-there")).toBeInTheDocument();
    expect(within(panel).queryByTestId("make-proposal-active")).not.toBeInTheDocument();
    expect(within(panel).getByTestId("decline-proposal")).toBeInTheDocument();
  });

  it("sends one put when Make active is clicked twice at once", async () => {
    const user = setupUser();
    withProposal();
    let release: (value: unknown) => void = () => {};
    api.putOnBoard.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const panel = await open(user);

    const button = within(panel).getByTestId("make-proposal-active");
    await user.dblClick(button);
    release(row().row);

    await waitFor(() => expect(api.putOnBoard).toHaveBeenCalledTimes(1));
  });

  it("shows Make active on every proposal of a profile: one never blocks another", async () => {
    const user = setupUser();
    const one = proposal({ id: 11, row_id: 1 });
    const two = proposal({ id: 12, row_id: 1 });
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ proposed_versions: 2 })], proposals: [one, two] }),
    );
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 7, is_active: true })], {
        proposed: [
          { draft: one.draft, profile: draftProfile(), compared_to_version_id: 7 },
          { draft: two.draft, profile: draftProfile(), compared_to_version_id: 7 },
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));

    const panels = await screen.findAllByTestId("proposal");
    expect(panels).toHaveLength(2);
    for (const panel of panels) {
      expect(within(panel).getByTestId("make-proposal-active")).toBeInTheDocument();
      expect(within(panel).queryByTestId("proposal-blocked")).not.toBeInTheDocument();
    }
  });

  it("offers the Set's button and the plain one together, as they land the same", async () => {
    const user = setupUser();
    withProposal(
      {
        set_id: 3,
        set_name: "Ethiopia washed",
        set_next_minor_label: "v1.2",
        set_next_major_label: "v2",
      },
      { for_set: { row_id: 1, row_label: "9 Bar Espresso" } },
    );
    const panel = await open(user);

    expect(within(panel).getByTestId("make-proposal-active-for-set")).toBeInTheDocument();
    expect(within(panel).getByTestId("make-proposal-active")).toBeInTheDocument();
  });

  it("offers no put until the board has said where it would land", async () => {
    const user = setupUser();
    const p = proposal();
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ proposed_versions: 1 })], proposals: [] }),
    );
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 7, is_active: true })], {
        proposed: [{ draft: p.draft, profile: draftProfile(), compared_to_version_id: 7 }],
      }),
    );
    const panel = await open(user);

    expect(within(panel).getByTestId("proposal-landing-pending")).toBeInTheDocument();
    expect(within(panel).queryByTestId("make-proposal-active")).not.toBeInTheDocument();
  });

  it("Decline discards the draft", async () => {
    const user = setupUser();
    withProposal();
    const panel = await open(user);

    await user.click(within(panel).getByTestId("decline-proposal"));

    await waitFor(() => expect(api.discardProfileDraft).toHaveBeenCalledWith(11));
  });

  it("a proposed new profile is a row at the top, marked New, whose dropdown holds the one proposed version", async () => {
    const user = setupUser();
    const newOne = proposal({
      id: 21,
      row_id: null,
      landing: landing({ draft_id: 21 }, null),
    });
    newOne.draft = draft({ id: 21, draft_label: "Fresh idea [AI]", draft_version_id: 61 });
    api.getProfileBoard.mockResolvedValue(boardView({ proposals: [newOne] }));
    api.getProfileDraft.mockResolvedValue(
      draftDetail({ draft: newOne.draft, base_profile: null, draft_profile: draftProfile() }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const rows = await screen.findAllByTestId(/profile-row|new-profile-row/);
    expect(rows[0]).toHaveAttribute("data-testid", "new-profile-row");
    const fresh = screen.getByTestId("new-profile-row");
    expect(within(fresh).getByText("Fresh idea [AI]")).toBeInTheDocument();
    expect(within(fresh).getByText("New")).toBeInTheDocument();
    expect(within(fresh).queryByRole("switch")).not.toBeInTheDocument();

    await user.click(within(fresh).getByTestId("profile-toggle"));
    const panel = await within(fresh).findByTestId("proposal");
    // Nothing to diff against: its information is a summary.
    await within(panel).findByTestId("profile-summary");
    await user.click(within(panel).getByTestId("make-proposal-active"));
    await waitFor(() => expect(api.putOnBoard).toHaveBeenCalledWith({ draftId: 21 }));
    expect(within(panel).getByTestId("make-proposal-active")).toHaveTextContent("Add to the list");
  });
});

describe("a profile designed from scratch", () => {
  it("shows the profile, never a diff, and is added in one click", async () => {
    const user = setupUser();
    const fresh = proposal({ id: 31, row_id: null, landing: landing({ draft_id: 31 }, null) });
    // Even with stop changes recorded (an old draft), a new profile has no stops it changes.
    fresh.draft = draft({
      id: 31,
      is_new: true,
      base_label: null,
      draft_label: "Fresh idea [AI]",
      stop_condition_changes: [yieldChange()],
    });
    api.getProfileBoard.mockResolvedValue(boardView({ rows: [], proposals: [fresh] }));
    api.getProfileDraft.mockResolvedValue(
      draftDetail({ draft: fresh.draft, base_profile: null, draft_profile: draftProfile() }),
    );
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));

    const panel = await screen.findByTestId("proposal");
    await within(panel).findByTestId("profile-summary");
    expect(within(panel).queryByTestId("profile-diff")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("stop-condition-warning")).not.toBeInTheDocument();
    expect(within(panel).queryByText("What changes")).not.toBeInTheDocument();
    const add = within(panel).getByTestId("make-proposal-active");
    expect(add).toBeEnabled();
    await user.click(add);
    await waitFor(() => expect(api.putOnBoard).toHaveBeenCalledWith({ draftId: 31 }));
  });
});

describe("an is_new draft shown inside a profile", () => {
  it("is still a profile written from scratch: the summary, never a diff", async () => {
    const user = setupUser();
    const p = proposal({ id: 41, row_id: 1 });
    p.draft = draft({
      id: 41,
      is_new: true,
      base_label: null,
      draft_label: "9 Bar Espresso",
      stop_condition_changes: [yieldChange()],
    });
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ proposed_versions: 1 })], proposals: [p] }),
    );
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 7, is_active: true })], {
        proposed: [{ draft: p.draft, profile: draftProfile(), compared_to_version_id: 7 }],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));

    const panel = await screen.findByTestId("proposal");
    expect(within(panel).queryByTestId("profile-diff")).not.toBeInTheDocument();
    expect(within(panel).getByTestId("profile-summary")).toBeInTheDocument();
    expect(within(panel).queryByTestId("stop-condition-warning")).not.toBeInTheDocument();
    expect(within(panel).getByTestId("make-proposal-active")).toBeEnabled();
  });
});

describe("a conflict", () => {
  const conflicted = () =>
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row({
            in_conflict: true,
            conflict: {
              device_id: "9bar",
              version_id: 31,
              content_hash: "feedface00",
              short_hash: "feedface",
            },
          }),
        ],
      }),
    );

  async function open(user: ReturnType<typeof setupUser>) {
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));
    return screen.findByTestId("conflict-panel");
  }

  it("shows the app's version and the machine's side by side with the differences marked on both", async () => {
    const user = setupUser();
    conflicted();
    api.getBoardConflict.mockResolvedValue(conflictView());
    const panel = await open(user);

    const app = within(panel).getByTestId("conflict-app");
    const machine = within(panel).getByTestId("conflict-machine");
    expect(app).toHaveTextContent("The app's version");
    expect(machine).toHaveTextContent("The machine's version");
    expect(within(app).getByTestId("conflict-difference")).toHaveTextContent("pressure 9 bar");
    expect(within(machine).getByTestId("conflict-difference")).toHaveTextContent("pressure 7 bar");
    // No content hashes: the marked differences say what differs.
    expect(panel.textContent).not.toMatch(/abcdef01|feedface/);
    // Two columns from md up, stacked below.
    expect(within(panel).getByTestId("conflict-sides").className).toMatch(/md:grid-cols-2/);
  });

  it("opens above the versions", async () => {
    const user = setupUser();
    conflicted();
    api.getBoardConflict.mockResolvedValue(conflictView());
    await open(user);

    const dropdown = screen.getByTestId("profile-dropdown");
    const first = dropdown.firstElementChild as HTMLElement;
    expect(first).toHaveAttribute("data-testid", "conflict-panel");
  });

  it("each button carries the hash the person saw and says what the next sync does", async () => {
    const user = setupUser();
    conflicted();
    api.getBoardConflict.mockResolvedValue(conflictView());
    const panel = await open(user);

    expect(panel).toHaveTextContent("The next sync replaces the machine's file");
    expect(panel).toHaveTextContent("Nothing is sent to the machine");
    await user.click(within(panel).getByTestId("keep-app"));
    await waitFor(() =>
      expect(api.resolveBoardConflict).toHaveBeenCalledWith(1, "app", "feedface00"),
    );
    await user.click(within(panel).getByTestId("keep-machine"));
    await waitFor(() =>
      expect(api.resolveBoardConflict).toHaveBeenCalledWith(1, "machine", "feedface00"),
    );
  });

  it("says the machine changed again after a stale refusal, and shows the file as it is now", async () => {
    const user = setupUser();
    conflicted();
    const { ApiClientError } = await import("@/api/client");
    api.getBoardConflict.mockResolvedValueOnce(conflictView());
    api.getBoardConflict.mockResolvedValue(
      conflictView({
        machine: {
          device_id: "9bar",
          version_id: 32,
          content_hash: "0ddba11",
          short_hash: "0ddba11",
        },
        machine_profile: profileWith(6),
      }),
    );
    api.resolveBoardConflict.mockRejectedValueOnce(
      new ApiClientError("changed", {
        status: 409,
        code: "CONFLICT",
        details: { reason: "stale_conflict" },
      }),
    );
    const panel = await open(user);

    await user.click(within(panel).getByTestId("keep-app"));

    expect(await screen.findByTestId("conflict-stale")).toHaveTextContent("changed again");
    await waitFor(() =>
      expect(
        within(screen.getByTestId("conflict-panel")).getByTestId("conflict-machine"),
      ).toHaveTextContent("pressure 6 bar"),
    );
    // The next choice carries the new hash.
    await user.click(screen.getByTestId("keep-machine"));
    await waitFor(() =>
      expect(api.resolveBoardConflict).toHaveBeenLastCalledWith(1, "machine", "0ddba11"),
    );
  });
});

describe("links into the page", () => {
  it("#staged opens the profile holding the newest proposal", async () => {
    const p = proposal({ id: 11, row_id: 2 });
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [row(), row({ row: { id: 2, label: "Londinium" }, proposed_versions: 1 })],
        proposals: [p],
      }),
    );
    api.getBoardVersions.mockImplementation(async (id: number) =>
      versionsView([listedVersion({ version_id: 7 + id, is_active: true })], { row_id: id }),
    );
    renderWithQueryClient(<ProfilesPage />, { initialEntries: ["/profiles#staged"] });

    await waitFor(() =>
      expect(within(rowNamed("Londinium")).getByTestId("profile-dropdown")).toBeInTheDocument(),
    );
    expect(
      within(rowNamed("9 Bar Espresso")).queryByTestId("profile-dropdown"),
    ).not.toBeInTheDocument();
  });

  it("#staged opens the newest of several proposals, whichever profile holds it", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row({ proposed_versions: 1 }),
          row({ row: { id: 2, label: "Londinium" }, proposed_versions: 1 }),
        ],
        proposals: [proposal({ id: 11, row_id: 1 }), proposal({ id: 12, row_id: 2 })],
      }),
    );
    api.getBoardVersions.mockImplementation(async (id: number) =>
      versionsView([listedVersion({ version_id: 7 + id, is_active: true })], { row_id: id }),
    );
    renderWithQueryClient(<ProfilesPage />, { initialEntries: ["/profiles#staged"] });

    await waitFor(() =>
      expect(within(rowNamed("Londinium")).getByTestId("profile-dropdown")).toBeInTheDocument(),
    );
    expect(
      within(rowNamed("9 Bar Espresso")).queryByTestId("profile-dropdown"),
    ).not.toBeInTheDocument();
  });

  it("#staged opens a proposed new profile's row", async () => {
    const newOne = proposal({ id: 21, row_id: null, landing: landing({ draft_id: 21 }, null) });
    api.getProfileBoard.mockResolvedValue(boardView({ proposals: [newOne] }));
    api.getProfileDraft.mockResolvedValue(draftDetail({ draft_profile: draftProfile() }));
    renderWithQueryClient(<ProfilesPage />, { initialEntries: ["/profiles#staged"] });

    expect(await screen.findByTestId("proposal")).toBeInTheDocument();
  });

  it("#version-N opens the profile that has that version, even one that is off", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [row(), row({ row: { id: 2, label: "Old one", on_machine: false } })],
      }),
    );
    api.getBoardVersions.mockImplementation(async (id: number) =>
      versionsView([listedVersion({ version_id: id === 2 ? 99 : 7, is_active: true })], {
        row_id: id,
      }),
    );
    renderWithQueryClient(<ProfilesPage />, { initialEntries: ["/profiles#version-99"] });

    const old = await screen.findByText("Old one");
    const li = old.closest("li") as HTMLElement;
    expect(await within(li).findByTestId("profile-dropdown")).toBeInTheDocument();
  });

  it("#version-N opens that version's Signature card, which a shot read without one links to", async () => {
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 99, is_active: true })]),
    );
    api.getSignature.mockResolvedValue({ ...signatureConfirmed, profile_version_id: 99 });
    renderWithQueryClient(<ProfilesPage />, { initialEntries: ["/profiles#version-99"] });

    const card = await screen.findByTestId("signature-card");
    expect(api.getSignature).toHaveBeenCalledWith(99);
    expect(card).toHaveAttribute("data-open", "yes");
  });

  it("shows each version's Signature card in its dropdown, closed when nothing waits", async () => {
    const user = setupUser();
    api.getSignature.mockResolvedValue(signatureConfirmed);
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));

    const card = await screen.findByTestId("signature-card");
    expect(card).toHaveAttribute("data-open", "no");
    expect(within(card).getByTestId("signature-summary")).toHaveTextContent("6 confirmed");
  });
});

describe("one request per click", () => {
  it("a double-click on On the machine sends one request", async () => {
    const user = setupUser();
    let release: (value: unknown) => void = () => {};
    api.setBoardOnMachine.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await user.dblClick(
      await screen.findByRole("switch", { name: "9 Bar Espresso on the machine" }),
    );
    release(row().row);

    await waitFor(() => expect(api.setBoardOnMachine).toHaveBeenCalledTimes(1));
  });

  it("a double-click on Starred sends one request", async () => {
    const user = setupUser();
    let release: (value: unknown) => void = () => {};
    api.setBoardStarred.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await user.dblClick(await screen.findByRole("switch", { name: "9 Bar Espresso starred" }));
    release(row().row);

    await waitFor(() => expect(api.setBoardStarred).toHaveBeenCalledTimes(1));
  });

  it("a double-click on Make active sends one request", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(
      versionsView([
        listedVersion({ version_id: 8, is_active: true, previous_version_id: 7 }),
        listedVersion({ version_id: 7 }),
      ]),
    );
    let release: (value: unknown) => void = () => {};
    api.setBoardActiveVersion.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));

    await user.dblClick(await screen.findByTestId("make-active"));
    release(row().row);

    await waitFor(() => expect(api.setBoardActiveVersion).toHaveBeenCalledTimes(1));
  });

  it("a double-click on Decline sends one request", async () => {
    const user = setupUser();
    const p = proposal();
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ proposed_versions: 1 })], proposals: [p] }),
    );
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 7, is_active: true })], {
        proposed: [{ draft: p.draft, profile: draftProfile(), compared_to_version_id: 7 }],
      }),
    );
    let release: (value: unknown) => void = () => {};
    api.discardProfileDraft.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));

    await user.dblClick(await screen.findByTestId("decline-proposal"));
    release(draft());

    await waitFor(() => expect(api.discardProfileDraft).toHaveBeenCalledTimes(1));
  });
});

describe("what the page leaves out", () => {
  it("shows no content hashes, and keeps the writes sentence out of the subtitle", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 7, is_active: true, short_hash: "deadbeef" })]),
    );
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));
    await screen.findByTestId("version");

    expect(document.body.textContent).not.toContain("deadbeef");
    expect(screen.getByTestId("writes-line")).toBeInTheDocument();
    expect(screen.queryByText(/makes the machine hold exactly/)).not.toBeInTheDocument();
  });

  it("puts utility profiles last", async () => {
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row({ row: { id: 1, label: "[Utility] Backflush" }, utility: true }),
          row({ row: { id: 2, label: "Zeta" } }),
          row({ row: { id: 3, label: "Alpha" } }),
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    await screen.findAllByTestId("profile-row");
    const names = screen
      .getAllByTestId("profile-row")
      .map((el) => el.querySelector("span.font-medium")?.textContent);
    expect(names).toEqual(["Alpha", "Zeta", "[Utility] Backflush"]);
  });

  it("claims aria-expanded only where its region stays in the page", async () => {
    renderWithQueryClient(<ProfilesPage />);

    const toggle = await screen.findByTestId("profile-toggle");
    expect(toggle).not.toHaveAttribute("aria-expanded");
  });

  it("does not say a toggled-off profile will be put on the machine", async () => {
    const { toast } = await import("sonner");
    const user = setupUser();
    window.localStorage.setItem("gaggiclanker.profiles.showOff", "1");
    api.getProfileBoard.mockResolvedValue(
      boardView({ rows: [row({ row: { on_machine: false } })] }),
    );
    api.getBoardVersions.mockResolvedValue(
      versionsView([
        listedVersion({ version_id: 8, is_active: true, previous_version_id: 7 }),
        listedVersion({ version_id: 7 }),
      ]),
    );
    api.setBoardActiveVersion.mockResolvedValue(row({ row: { on_machine: false } }).row);
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));

    await user.click(await screen.findByTestId("make-active"));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const described = vi.mocked(toast.success).mock.calls.map((call) => JSON.stringify(call[1]));
    expect(described.join(" ")).toContain("switched off");
    expect(described.join(" ")).not.toContain("puts it on the machine");
  });
});

describe("Edit a copy", () => {
  async function openEditor() {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 7, is_active: true })]),
    );
    api.previewProfileDraft.mockResolvedValue({ valid: true, profile: profileWith(9) });
    api.createProfileDraft.mockResolvedValue(draft());
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));
    await user.click(await screen.findByTestId("edit-a-copy"));
    return user;
  }

  it("says the copy is a proposed new version of that profile, whoever made it, and that its name is kept", async () => {
    await openEditor();

    expect(
      await screen.findByText(/proposed new version of 9 Bar Espresso, not active/),
    ).toBeInTheDocument();
    expect(screen.getByText(/a changed name makes a profile of its own/)).toBeInTheDocument();
  });

  it("saves the copy against the version it was opened from, with nothing else naming a profile", async () => {
    const user = await openEditor();

    await waitFor(() => expect(screen.getByTestId("editor-valid")).toBeInTheDocument());
    await user.click(screen.getByTestId("save-as-draft"));

    await waitFor(() => expect(api.createProfileDraft).toHaveBeenCalled());
    const body = api.createProfileDraft.mock.calls[0]?.[0];
    expect(body).toMatchObject({ base_version_id: 7 });
    expect(body).not.toHaveProperty("target_row_id");
  });
});

describe("links into the page, once per arrival", () => {
  it("does not reopen a row by itself after the person answers a proposal", async () => {
    const user = setupUser();
    const first = proposal({ id: 11, row_id: 1 });
    const second = proposal({ id: 12, row_id: 2 });
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          row({ proposed_versions: 1 }),
          row({ row: { id: 2, label: "Londinium" }, proposed_versions: 1 }),
        ],
        proposals: [first, second],
      }),
    );
    api.getBoardVersions.mockImplementation(async (id: number) =>
      versionsView([listedVersion({ version_id: 7 + id, is_active: true })], { row_id: id }),
    );
    const { queryClient } = renderWithQueryClient(<ProfilesPage />, {
      initialEntries: ["/profiles#staged"],
    });
    await waitFor(() =>
      expect(within(rowNamed("Londinium")).getByTestId("profile-dropdown")).toBeInTheDocument(),
    );

    // The person closes it; the newest proposal is then answered elsewhere, so another one is newest.
    await user.click(within(rowNamed("Londinium")).getByTestId("profile-toggle"));
    api.getProfileBoard.mockResolvedValue(
      boardView({
        rows: [row({ proposed_versions: 1 }), row({ row: { id: 2, label: "Londinium" } })],
        proposals: [first],
      }),
    );
    const reads = api.getProfileBoard.mock.calls.length;
    await queryClient.invalidateQueries({ queryKey: ["board"] });
    await waitFor(() => expect(api.getProfileBoard.mock.calls.length).toBeGreaterThan(reads));
    // Wait for the refreshed list to be on screen (Londinium no longer has a proposal), then
    // give an effect that wrongly re-acts the chance to run.
    await waitFor(() =>
      expect(within(rowNamed("Londinium")).queryByTestId("proposed-badge")).not.toBeInTheDocument(),
    );
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(screen.queryAllByTestId("profile-dropdown")).toHaveLength(0);
  });
});

describe("the header", () => {
  it("uploads a profile export and reports what it did", async () => {
    const user = setupUser();
    api.importFiles.mockResolvedValue({
      created: 1,
      updated: 0,
      skipped: 0,
      failed: 0,
      items: [
        {
          filename: "cremina.json",
          kind: "profile",
          status: "created",
          message: "profile version 9",
          shot_id: null,
          device_id: null,
          profile_version_id: 9,
          label: "Cremina v2",
          quarantined: false,
        },
      ],
    });
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByTestId("profile-row");

    const file = new File(['{"label":"Cremina v2"}'], "cremina.json", {
      type: "application/json",
    });
    await user.upload(screen.getByTestId("profile-upload-input"), file);

    await waitFor(() => expect(api.importFiles).toHaveBeenCalled());
    const result = await screen.findByTestId("import-result");
    expect(result).toHaveTextContent("1 imported");
    await user.click(within(result).getByTestId("import-result-toggle"));
    expect(within(result).getByRole("link", { name: "Cremina v2" })).toHaveAttribute(
      "href",
      "/profiles#version-9",
    );
  });

  it("says the board cannot be read, with a way to try again", async () => {
    api.getProfileBoard.mockRejectedValue(new Error("boom"));
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("board-unreadable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("has none of the old page's sections", async () => {
    renderWithQueryClient(<ProfilesPage />);

    await screen.findByTestId("profile-row");
    for (const gone of [
      "Waiting for you",
      "Deleted, still on the machine",
      "On the machine, not on the board",
      "Go back a version",
    ]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });
});

describe("the curve in the dropdown", () => {
  const pro = lmleva as Record<string, unknown>;
  const changed = (version: number) => ({
    ...pro,
    phases: (pro.phases as Record<string, unknown>[]).map((phase, index) =>
      index === 0 ? { ...phase, duration: 3 + version } : phase,
    ),
  });
  const withActive = (profile: Record<string, unknown>, label = "Lever") =>
    boardView({
      rows: [
        row({
          row: { label },
          type: profile.type as string,
          active_version: {
            ...boardRowView().active_version,
            label,
            type: profile.type as string,
            profile,
          },
        }),
      ],
    });

  async function open(user: ReturnType<typeof setupUser>) {
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));
    return screen.findAllByTestId("version");
  }

  it("starts the open row with the active version's curve, and gives every other version its own", async () => {
    const user = setupUser();
    api.getProfileBoard.mockResolvedValue(withActive(changed(2)));
    api.getBoardVersions.mockResolvedValue(
      versionsView([
        listedVersion({
          version_id: 9,
          label: "v1.1",
          type: "pro",
          is_active: true,
          previous_version_id: 7,
          profile: changed(2),
        }),
        listedVersion({ version_id: 7, label: "v1.0", type: "pro", profile: changed(0) }),
      ]),
    );
    const versions = await open(user);

    const top = await screen.findByTestId("active-curve");
    expect(await within(top).findByTestId("profile-curve-title")).toHaveTextContent(
      "Active version",
    );
    // The active version (v1.1) is the top curve, not drawn a second time in its own entry.
    expect(within(versions[0]).queryByTestId("profile-curve")).not.toBeInTheDocument();
    const curve = await within(versions[1]).findByTestId("profile-curve");
    // Above the summary or the diff, not below it.
    const information = within(versions[1]).getByTestId("version-information");
    expect(
      curve.compareDocumentPosition(information) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // The curve is each version's own: the active one's first phase runs two seconds longer.
    const lengths = screen
      .getAllByTestId("profile-curve-summary")
      .map((el) => Number(/over (\d+(?:\.\d)?) s/.exec(el.textContent ?? "")?.[1]));
    expect(lengths).toHaveLength(2);
    expect(lengths[0] - lengths[1]).toBe(2);
  });

  it("takes the top curve from the active version, wherever the list puts it", async () => {
    const user = setupUser();
    api.getProfileBoard.mockResolvedValue(withActive(changed(5)));
    api.getBoardVersions.mockResolvedValue(
      versionsView([
        listedVersion({
          version_id: 9,
          label: "newest",
          type: "pro",
          previous_version_id: 8,
          profile: changed(0),
        }),
        listedVersion({
          version_id: 8,
          label: "chosen",
          type: "pro",
          is_active: true,
          profile: changed(5),
        }),
      ]),
    );
    await open(user);

    const top = await screen.findByTestId("active-curve");
    const lengthOf = (el: HTMLElement) =>
      Number(/over (\d+(?:\.\d)?) s/.exec(el.textContent ?? "")?.[1]);
    const topLength = lengthOf(await within(top).findByTestId("profile-curve-summary"));
    const [newest, chosen] = await screen.findAllByTestId("version");
    const first = lengthOf(within(newest).getByTestId("profile-curve-summary"));
    // The active (second) entry draws no curve of its own; the top curve is its, five seconds
    // longer than the first listed version's, never that one's.
    expect(within(chosen).queryByTestId("profile-curve")).not.toBeInTheDocument();
    expect(topLength - first).toBe(5);
  });

  it("draws nothing for a profile that is not pro, and leaves its summary", async () => {
    const user = setupUser();
    api.getBoardVersions.mockResolvedValue(
      versionsView([listedVersion({ version_id: 7, is_active: true })]),
    );
    const versions = await open(user);

    expect(within(versions[0]).getByTestId("profile-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("profile-curve")).not.toBeInTheDocument();
    // Not even an empty wrapper that would take the dropdown's spacing.
    expect(screen.queryByTestId("active-curve")).not.toBeInTheDocument();
    expect(drawn.calls).toBe(0);
  });

  it("does not draw the curves again for a click that changes none of them", async () => {
    const user = setupUser();
    const ids = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const list = ids.map((id) =>
      listedVersion({
        version_id: id,
        label: `v1.${id}`,
        type: "pro",
        is_active: id === 10,
        previous_version_id: id === 1 ? null : id - 1,
        profile: changed(id),
      }),
    );
    api.getProfileBoard.mockResolvedValue(withActive(changed(10)));
    api.getBoardVersions.mockResolvedValue(versionsView(list.reverse()));
    const versions = await open(user);
    // The top curve and the nine versions that are not active.
    await waitFor(() => expect(screen.getAllByTestId("profile-curve")).toHaveLength(10));
    const before = { charts: drawn.data.size, calls: drawn.calls };

    // Opens a whole profile under a later version: the dropdown renders again.
    await user.click(within(versions[0]).getByTestId("show-whole-profile"));

    expect(drawn.data.size).toBe(before.charts);
    expect(drawn.calls).toBe(before.calls);
  });
});
