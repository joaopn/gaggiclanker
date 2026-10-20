import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import type { SetDetailData } from "@/api/types";
import { selectedSuccessorSentence, writesLine } from "@/lib/board";
import { SetDetailPage } from "@/pages/SetDetailPage";
import { boardAction, boardRowView, boardView } from "@/test/boardFixtures";
import { pinDetail, pinPreview, setPin } from "@/test/pinFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { labelCounts, setDetail, setRow, trends, version, vocabulary } from "@/test/setsFixtures";
import { signatureNone } from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));
vi.mock("react-chartjs-2", () => ({ Line: () => <canvas /> }));
let routeSetId = "3";
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useParams: () => ({ setId: routeSetId }),
}));

const {
  getSet,
  getSetTrends,
  archiveSet,
  getVocabulary,
  getKnowledgeInsights,
  getProfileVersions,
  getSignature,
  getSignatureOverrides,
  getProfileBoard,
  getPinPreview,
  pinVersion,
  unpinPin,
} = vi.hoisted(() => ({
  getSet: vi.fn(),
  getSetTrends: vi.fn(),
  archiveSet: vi.fn(),
  getVocabulary: vi.fn(),
  getKnowledgeInsights: vi.fn(),
  getProfileVersions: vi.fn(),
  getSignature: vi.fn(),
  getSignatureOverrides: vi.fn(),
  getProfileBoard: vi.fn(),
  getPinPreview: vi.fn(),
  pinVersion: vi.fn(),
  unpinPin: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSet,
  getSetTrends,
  archiveSet,
  getVocabulary,
  getKnowledgeInsights,
  getProfileVersions,
  getSignature,
  getSignatureOverrides,
  getProfileBoard,
  getPinPreview,
  pinVersion,
  unpinPin,
}));

/** Three versions: v1.3 current, v1.2 pinned, v1.1 plain. */
function pinnedDetail(overrides: Partial<SetDetailData> = {}): SetDetailData {
  const entry = (id: number, minor: number, extra: Record<string, unknown> = {}) => ({
    version: version({
      id,
      version_major: 1,
      version_minor: minor,
      shot_count: minor === 2 ? 23 : 3,
      is_current: minor === 3,
      grind_setting: `2.${minor}`,
    }),
    changes: [],
    shots: [],
    dead_end: false,
    labels: labelCounts(),
    outcome_proposal: null,
    ...extra,
  });
  return setDetail({
    set: setRow({ current_version_id: 23, current_version_label: "v1.3", version_count: 3 }),
    versions: [entry(23, 3), entry(22, 2, { pinned: { pin_id: 5, shot_count: 14 } }), entry(21, 1)],
    pins: [setPin()],
    ...overrides,
  });
}

/** The profile-list row of the pinned copy, as the machine holds it. */
function pinRow(overrides: Parameters<typeof boardRowView>[0] = {}) {
  return boardRowView({ row: { id: 7, label: "Blooming v1.2" }, ...overrides });
}

function entryOf(label: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(
    `[data-testid="version-entry"][data-version="${label}"]`,
  );
  if (!found) throw new Error(`no ${label} in the log`);
  return found;
}

beforeEach(() => {
  vi.clearAllMocks();
  routeSetId = "3";
  getSet.mockResolvedValue(pinnedDetail());
  getSetTrends.mockResolvedValue(trends());
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  getSignature.mockResolvedValue(signatureNone);
  getSignatureOverrides.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0 });
  getProfileBoard.mockResolvedValue(boardView({ rows: [pinRow()] }));
  getPinPreview.mockResolvedValue(pinPreview());
  pinVersion.mockResolvedValue({ pin: pinDetail({ version_label: "v1.1" }), board_id: 8 });
  unpinPin.mockResolvedValue({ pin: pinDetail(), board_id: 7 });
  archiveSet.mockResolvedValue(setRow({ archived: true }));
});

describe("the timeline's pins", () => {
  it("offers Pin to the machine on every version without a live pin, and marks the pinned one", async () => {
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");

    expect(
      within(entryOf("v1.3")).getByRole("button", { name: /^Pin to the machine/ }),
    ).toBeVisible();
    expect(
      within(entryOf("v1.1")).getByRole("button", { name: /^Pin to the machine/ }),
    ).toBeVisible();

    const pinned = entryOf("v1.2");
    expect(
      within(pinned).queryByRole("button", { name: /^Pin to the machine/ }),
    ).not.toBeInTheDocument();
    expect(within(pinned).getByTestId("pinned-marker")).toHaveTextContent("pinned");
    // The version's whole count, and how much of it was brewed on the pin.
    expect(within(pinned).getByTestId("version-labels")).toHaveTextContent(
      "23 shots (14 on the pin)",
    );
    expect(within(entryOf("v1.1")).queryByTestId("pinned-marker")).not.toBeInTheDocument();
    expect(within(entryOf("v1.1")).getByTestId("version-labels")).not.toHaveTextContent(
      "on the pin",
    );
  });

  it("offers no pin on an archived Set", async () => {
    getSet.mockResolvedValue(pinnedDetail({ set: setRow({ archived: true }) }));
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");

    expect(screen.queryByRole("button", { name: /^Pin to the machine/ })).not.toBeInTheDocument();
  });

  it("offers no pin on a Set being designed, even when its first version names a profile", async () => {
    const detail = pinnedDetail({ set: setRow({ designing: true }), pins: [] });
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");

    expect(screen.queryByRole("button", { name: /^Pin to the machine/ })).not.toBeInTheDocument();
  });

  it("puts going back and pinning in one row of version actions, with the going-back confirmation below the row", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");
    const entry = entryOf("v1.1");
    const back = within(entry).getByRole("button", { name: "Go back to this version" });
    const pin = within(entry).getByRole("button", { name: /^Pin to the machine/ });
    const row = back.parentElement as HTMLElement;

    expect(pin.parentElement).toBe(row);
    expect(row.parentElement).toBe(within(entry).getByTestId("version-actions"));

    await user.click(back);
    const confirm = within(entry).getByTestId("rollback-confirm");
    expect(row).not.toContainElement(confirm);
    expect(within(entry).getByTestId("version-actions")).toContainElement(confirm);
  });

  it("renders the row for every version, and the current pinned version's holds only Duplicate", async () => {
    const detail = pinnedDetail();
    // The current version, already pinned: no way back and no pin apply to it.
    detail.versions[0] = { ...detail.versions[0], pinned: { pin_id: 9, shot_count: 0 } } as never;
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");

    for (const label of ["v1.3", "v1.2", "v1.1"]) {
      expect(within(entryOf(label)).getByTestId("version-actions")).toBeInTheDocument();
    }
    const lone = within(entryOf("v1.3")).getByTestId("version-actions");
    expect(
      within(lone)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Duplicate Set"]);
  });

  it("offers no pin on a version that names no profile", async () => {
    const detail = pinnedDetail();
    detail.versions[2] = {
      ...detail.versions[2],
      version: { ...detail.versions[2].version, profile_version_id: null },
    } as (typeof detail.versions)[number];
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");

    expect(
      within(entryOf("v1.1")).queryByRole("button", { name: /^Pin to the machine/ }),
    ).not.toBeInTheDocument();
    expect(
      within(entryOf("v1.3")).getByRole("button", { name: /^Pin to the machine/ }),
    ).toBeVisible();
  });
});

describe("the pin dialog", () => {
  async function open(label = "v1.1") {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");
    await user.click(within(entryOf(label)).getByRole("button", { name: /^Pin to the machine/ }));
    return { user, dialog: await screen.findByTestId("pin-dialog") };
  }

  it("asks the preview first, then says what the machine gets, in the version's own words", async () => {
    getPinPreview.mockResolvedValue(pinPreview({ name: "Blooming v1.1" }));
    const { dialog } = await open();

    const text = await within(dialog).findByTestId("pin-explained");
    expect(getPinPreview).toHaveBeenCalledWith(3, 21);
    expect(text).toHaveTextContent(
      `The machine gets a profile called "Blooming v1.1", frozen at this version's profile.`,
    );
    expect(text).toHaveTextContent("Shots brewed on it are filed under v1.1.");
    expect(text).toHaveTextContent(
      "Grind 2.1, dose 18 g and yield 36 g stay v1.1's: set them at the grinder and scale.",
    );
    expect(within(dialog).queryByLabelText("Name")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Pin" })).toBeEnabled();
  });

  it("pins with the default name, once, closes, and sends no label", async () => {
    const { user, dialog } = await open();
    await within(dialog).findByTestId("pin-explained");

    const pin = within(dialog).getByRole("button", { name: "Pin" });
    await user.dblClick(pin);

    await waitFor(() => expect(pinVersion).toHaveBeenCalledTimes(1));
    expect(pinVersion).toHaveBeenCalledWith(3, 21, undefined);
    await waitFor(() => expect(screen.queryByTestId("pin-dialog")).not.toBeInTheDocument());
  });

  it("shows the server's sentence and a prefilled Name field when the name is taken, and sends what is typed", async () => {
    getPinPreview.mockResolvedValue(
      pinPreview({
        name: "Blooming v1.1",
        taken: true,
        refused: 'There is already a profile called "Blooming v1.1". Choose another name.',
      }),
    );
    const { user, dialog } = await open();

    expect(await within(dialog).findByTestId("pin-name-taken")).toHaveTextContent(
      'There is already a profile called "Blooming v1.1". Choose another name.',
    );
    const name = within(dialog).getByLabelText("Name");
    expect(name).toHaveValue("Blooming v1.1");
    expect(within(dialog).queryByTestId("pin-explained")).not.toBeInTheDocument();

    await user.clear(name);
    expect(within(dialog).getByRole("button", { name: "Pin" })).toBeDisabled();
    await user.type(name, "  Blooming v1.1 Guji ");
    await user.click(within(dialog).getByRole("button", { name: "Pin" }));

    await waitFor(() => expect(pinVersion).toHaveBeenCalledWith(3, 21, "Blooming v1.1 Guji"));
  });

  it("keeps Pin off while the name is still the taken one, however it is spelled", async () => {
    getPinPreview.mockResolvedValue(
      pinPreview({ name: "Blooming v1.1", taken: true, refused: "Taken." }),
    );
    const { user, dialog } = await open();
    const name = await within(dialog).findByLabelText("Name");
    const pin = within(dialog).getByRole("button", { name: "Pin" });
    expect(pin).toBeDisabled();

    await user.clear(name);
    await user.type(name, "  BLOOMING V1.1 ");
    expect(pin).toBeDisabled();
    await user.type(name, "2");
    expect(pin).toBeEnabled();
  });

  it("keeps the taken state's words on screen while the next request is in flight", async () => {
    pinVersion.mockRejectedValueOnce(
      new ApiClientError("Taken just now.", {
        status: 409,
        code: "PIN_NAME_TAKEN",
        details: { name: "Blooming v1.1" },
      }),
    );
    const { user, dialog } = await open();
    await within(dialog).findByTestId("pin-explained");
    await user.click(within(dialog).getByRole("button", { name: "Pin" }));
    const name = await within(dialog).findByLabelText("Name");
    await user.type(name, " b");
    let release: (value: unknown) => void = () => {};
    pinVersion.mockReturnValueOnce(new Promise((resolve) => (release = resolve)));

    await user.click(within(dialog).getByRole("button", { name: "Pin" }));

    await waitFor(() => expect(pinVersion).toHaveBeenCalledTimes(2));
    expect(within(dialog).getByTestId("pin-name-taken")).toHaveTextContent("Taken just now.");
    expect(within(dialog).queryByTestId("pin-explained")).not.toBeInTheDocument();
    release({ pin: pinDetail({ version_label: "v1.1" }), board_id: 8 });
  });

  it("a request that got no answer says so once, in place, with no toast and no claim, and lets the person try again", async () => {
    pinVersion.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { user, dialog } = await open();
    await within(dialog).findByTestId("pin-explained");

    await user.click(within(dialog).getByRole("button", { name: "Pin" }));

    const message = await within(dialog).findByTestId("pin-unreachable");
    expect(message).toHaveTextContent(
      /^No answer came back from the app\. Check the card before trying again\.$/,
    );
    // One message: the dialog's. Not the browser's own words, and not a toast beside it.
    expect(toast.error).not.toHaveBeenCalled();
    expect(dialog).not.toHaveTextContent("Failed to fetch");
    expect(within(dialog).getByRole("button", { name: "Pin" })).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(pinVersion).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId("pin-dialog")).not.toBeInTheDocument());
  });

  it("moves focus to the version's pinned marker once the pin has landed", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");
    await user.click(within(entryOf("v1.1")).getByRole("button", { name: /^Pin to the machine/ }));
    const dialog = await screen.findByTestId("pin-dialog");
    await within(dialog).findByTestId("pin-explained");
    // The Set read after the pin shows the version pinned.
    const after = pinnedDetail();
    after.versions[2] = { ...after.versions[2], pinned: { pin_id: 6, shot_count: 0 } } as never;
    getSet.mockResolvedValue(after);

    await user.click(within(dialog).getByRole("button", { name: "Pin" }));

    await waitFor(() => expect(within(entryOf("v1.1")).getByTestId("pinned-marker")).toHaveFocus());
  });

  it("shows a refusal that is not about the name as the server's sentence, with Close only", async () => {
    const sentence =
      "This profile can't be copied unchanged: the safety policy would move 2 numbers.";
    getPinPreview.mockResolvedValue(
      pinPreview({ refused: sentence, taken: false, clamp_changes: [] }),
    );
    const { user, dialog } = await open();

    expect(await within(dialog).findByTestId("pin-refused")).toHaveTextContent(sentence);
    expect(within(dialog).queryByLabelText("Name")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Pin" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();

    // Radix's own corner button is also called Close; the footer's is the one this state offers.
    const closes = within(dialog).getAllByRole("button", { name: "Close" });
    await user.click(closes[closes.length - 1] as HTMLElement);
    await waitFor(() => expect(screen.queryByTestId("pin-dialog")).not.toBeInTheDocument());
    expect(pinVersion).not.toHaveBeenCalled();
  });

  it("shows the answer's sentence in place when the pin is refused on submit, and stays open", async () => {
    pinVersion.mockRejectedValue(
      new ApiClientError("Blooming v1.1 is already pinned.", {
        status: 409,
        code: "ALREADY_PINNED",
        requestId: "req-1",
      }),
    );
    const { user, dialog } = await open();
    await within(dialog).findByTestId("pin-explained");

    await user.click(within(dialog).getByRole("button", { name: "Pin" }));

    // The sentence alone: a request id is for a bug report, not for the person.
    const refused = await within(dialog).findByTestId("pin-refused");
    expect(refused).toHaveTextContent(/^Blooming v1\.1 is already pinned\.$/);
    expect(screen.getByTestId("pin-dialog")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Pin" })).not.toBeInTheDocument();
  });

  it("a name taken in the meantime turns the form into the taken state, with the answer's sentence", async () => {
    pinVersion.mockRejectedValueOnce(
      new ApiClientError(
        'There is already a profile called "Blooming v1.1". Choose another name.',
        {
          status: 409,
          code: "PIN_NAME_TAKEN",
          details: { name: "Blooming v1.1" },
        },
      ),
    );
    const { user, dialog } = await open();
    await within(dialog).findByTestId("pin-explained");

    await user.click(within(dialog).getByRole("button", { name: "Pin" }));

    expect(await within(dialog).findByTestId("pin-name-taken")).toHaveTextContent(
      'There is already a profile called "Blooming v1.1". Choose another name.',
    );
    await user.clear(within(dialog).getByLabelText("Name"));
    await user.type(within(dialog).getByLabelText("Name"), "Blooming v1.1 b");
    await user.click(within(dialog).getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(pinVersion).toHaveBeenLastCalledWith(3, 21, "Blooming v1.1 b"));
  });

  it("asks the preview afresh at every opening and carries no refusal over", async () => {
    pinVersion.mockRejectedValueOnce(
      new ApiClientError("Blooming v1.1 is already pinned.", {
        status: 409,
        code: "ALREADY_PINNED",
      }),
    );
    const { user, dialog } = await open();
    await within(dialog).findByTestId("pin-explained");
    await user.click(within(dialog).getByRole("button", { name: "Pin" }));
    await within(dialog).findByTestId("pin-refused");
    const closes = within(dialog).getAllByRole("button", { name: "Close" });
    await user.click(closes[closes.length - 1] as HTMLElement);
    await waitFor(() => expect(screen.queryByTestId("pin-dialog")).not.toBeInTheDocument());

    await user.click(within(entryOf("v1.1")).getByRole("button", { name: /^Pin to the machine/ }));

    expect(await screen.findByTestId("pin-explained")).toBeInTheDocument();
    expect(screen.queryByTestId("pin-refused")).not.toBeInTheDocument();
    expect(getPinPreview).toHaveBeenCalledTimes(2);
  });
});

describe("the Pinned on the machine card", () => {
  it("is absent with no live pin", async () => {
    getSet.mockResolvedValue(pinnedDetail({ pins: [] }));
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");

    expect(screen.queryByText("Pinned on the machine")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pinned-card")).not.toBeInTheDocument();
  });

  it("is a line per pin, newest first, with the facts once the machine holds the copy", async () => {
    getSet.mockResolvedValue(
      pinnedDetail({
        pins: [
          setPin({
            pin_id: 6,
            version_label: "v1.3",
            name: "Blooming v1.3",
            board_id: 8,
            shot_count: 1,
          }),
          setPin(),
        ],
      }),
    );
    getProfileBoard.mockResolvedValue(
      boardView({
        rows: [pinRow(), boardRowView({ row: { id: 8, label: "Blooming v1.3" } })],
      }),
    );
    renderWithQueryClient(<SetDetailPage />);

    const lines = await screen.findAllByTestId("pin-line");
    expect(lines.map((line) => line.dataset.pin)).toEqual(["6", "5"]);
    expect(lines[1]).toHaveTextContent('v1.2 as "Blooming v1.2"');
    await waitFor(() =>
      expect(within(lines[1] as HTMLElement).getByTestId("pin-facts")).toHaveTextContent(
        /^pinned .*· 14 shots$/,
      ),
    );
    expect(within(lines[0] as HTMLElement).getByTestId("pin-facts")).toHaveTextContent("· 1 shot");
    // The name goes to the profile's row on the Profiles page.
    expect(within(lines[1] as HTMLElement).getByTestId("pin-profile-link")).toHaveAttribute(
      "href",
      "/profiles#row-7",
    );
  });

  it("shows the profile list's own planned line while the copy is not on the machine yet", async () => {
    getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          pinRow({
            machine: {
              device_id: null,
              present: false,
              holds_current: false,
              favorite: false,
              selected: false,
            },
            planned: [boardAction({ kind: "push", label: "Blooming v1.2" })],
          }),
        ],
      }),
    );
    renderWithQueryClient(<SetDetailPage />);

    const line = await screen.findByTestId("pin-line");
    expect(await within(line).findByTestId("profile-state")).toHaveTextContent(
      "Will be put on the machine at the next sync",
    );
    expect(within(line).queryByTestId("pin-facts")).not.toBeInTheDocument();
  });

  it("shows no second line while the profile list has not answered", async () => {
    getProfileBoard.mockReturnValue(new Promise(() => {}));
    renderWithQueryClient(<SetDetailPage />);

    const line = await screen.findByTestId("pin-line");
    expect(within(line).queryByTestId("pin-facts")).not.toBeInTheDocument();
    expect(within(line).queryByTestId("profile-state")).not.toBeInTheDocument();
  });

  it("shows no second line when the profile list could not be read", async () => {
    getProfileBoard.mockRejectedValue(new Error("down"));
    renderWithQueryClient(<SetDetailPage />);

    const line = await screen.findByTestId("pin-line");
    await waitFor(() => expect(getProfileBoard).toHaveBeenCalled());
    expect(within(line).queryByTestId("pin-facts")).not.toBeInTheDocument();
    expect(within(line).queryByTestId("profile-state")).not.toBeInTheDocument();
  });

  it("shows no second line when the list has no row for the pin", async () => {
    getProfileBoard.mockResolvedValue(boardView({ rows: [] }));
    renderWithQueryClient(<SetDetailPage />);

    const line = await screen.findByTestId("pin-line");
    await waitFor(() => expect(getProfileBoard).toHaveBeenCalled());
    expect(within(line).queryByTestId("pin-facts")).not.toBeInTheDocument();
    expect(within(line).queryByTestId("profile-state")).not.toBeInTheDocument();
  });

  it("says once, under the card, that Writes are off, in the Profiles page's words", async () => {
    getProfileBoard.mockResolvedValue(
      boardView({ writes_enabled: false, rows: [pinRow(), boardRowView({ row: { id: 8 } })] }),
    );
    getSet.mockResolvedValue(
      pinnedDetail({ pins: [setPin(), setPin({ pin_id: 6, board_id: 8, version_label: "v1.3" })] }),
    );
    renderWithQueryClient(<SetDetailPage />);

    const note = await screen.findByTestId("pin-writes-line");
    expect(note).toHaveTextContent(writesLine(false));
    expect(screen.getAllByTestId("pin-writes-line")).toHaveLength(1);
  });

  it("says nothing about Writes when they are on", async () => {
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("pin-line");
    await waitFor(() => expect(getProfileBoard).toHaveBeenCalled());

    expect(screen.queryByTestId("pin-writes-line")).not.toBeInTheDocument();
  });

  it("does not read the profile list for a Set with no pins", async () => {
    getSet.mockResolvedValue(pinnedDetail({ pins: [] }));
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");

    expect(getProfileBoard).not.toHaveBeenCalled();
  });

  it("shows the row's line, not the facts, while the sync is still matching its file (on the machine, but not holding the current version)", async () => {
    getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          pinRow({
            machine: {
              device_id: null,
              present: false,
              holds_current: false,
              favorite: false,
              selected: false,
            },
          }),
        ],
        actions: [
          boardAction({ kind: "adopt", reason: "attached", row_id: 7, label: "Blooming v1.2" }),
        ],
      }),
    );
    renderWithQueryClient(<SetDetailPage />);

    const line = await screen.findByTestId("pin-line");
    // The row's own state reads "ok" (a file is being matched), yet the facts line would claim
    // more than the list does: the machine does not hold the current version yet.
    const state = await within(line).findByTestId("profile-state");
    expect(state).toHaveAttribute("data-tone", "ok");
    expect(within(line).queryByTestId("pin-facts")).not.toBeInTheDocument();
  });

  it("shows the row's own line for a conflict too", async () => {
    getProfileBoard.mockResolvedValue(boardView({ rows: [pinRow({ in_conflict: true })] }));
    renderWithQueryClient(<SetDetailPage />);

    const line = await screen.findByTestId("pin-line");
    expect(await within(line).findByTestId("profile-state")).toHaveTextContent(
      "Edited outside the app: choose a side",
    );
  });
});

describe("a pin whose answer was lost", () => {
  it("shows one message in the dialog, no toast, and the pin on the card once the Set is re-read", async () => {
    // The app made the pin, then the answer never arrived.
    pinVersion.mockImplementation(async () => {
      const after = pinnedDetail();
      after.versions[2] = { ...after.versions[2], pinned: { pin_id: 6, shot_count: 0 } } as never;
      after.pins = [
        setPin({ pin_id: 6, version_label: "v1.1", name: "Blooming v1.1", board_id: 8 }),
        setPin(),
      ];
      getSet.mockResolvedValue(after);
      throw new TypeError("Failed to fetch");
    });
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");
    expect(screen.getAllByTestId("pin-line")).toHaveLength(1);
    await user.click(within(entryOf("v1.1")).getByRole("button", { name: /^Pin to the machine/ }));
    const dialog = await screen.findByTestId("pin-dialog");
    await within(dialog).findByTestId("pin-explained");

    await user.click(within(dialog).getByRole("button", { name: "Pin" }));

    expect(await within(dialog).findByTestId("pin-unreachable")).toHaveTextContent(
      "No answer came back from the app. Check the card before trying again.",
    );
    expect(within(dialog).getAllByTestId("pin-unreachable")).toHaveLength(1);
    expect(dialog).not.toHaveTextContent(/nothing was pinned|Failed to fetch/);
    expect(toast.error).not.toHaveBeenCalled();
    // The card behind it now lists the pin: the failure refreshed the Set and the board.
    await waitFor(() => expect(screen.getAllByTestId("pin-line")).toHaveLength(2));
  });
});

describe("Unpin on the card", () => {
  it("asks first, in the card's words, and does nothing on Cancel", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);
    const line = await screen.findByTestId("pin-line");

    await user.click(within(line).getByRole("button", { name: "Unpin v1.2" }));

    const confirm = within(line).getByTestId("unpin-confirm");
    expect(confirm).toHaveTextContent(
      'The next sync takes "Blooming v1.2" off the machine. Its 14 shots stay filed under v1.2.',
    );
    expect(within(confirm).queryByTestId("unpin-selected")).not.toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("unpin-confirm")).not.toBeInTheDocument();
    expect(unpinPin).not.toHaveBeenCalled();
  });

  it("adds the shared successor sentence when the copy is the selected profile", async () => {
    getProfileBoard.mockResolvedValue(
      boardView({
        rows: [
          pinRow({
            machine: {
              device_id: "p1",
              present: true,
              holds_current: true,
              favorite: false,
              selected: true,
            },
          }),
        ],
      }),
    );
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);
    const line = await screen.findByTestId("pin-line");
    await waitFor(() => expect(within(line).getByTestId("pin-facts")).toBeInTheDocument());

    await user.click(within(line).getByRole("button", { name: "Unpin v1.2" }));

    expect(within(line).getByTestId("unpin-selected")).toHaveTextContent(
      selectedSuccessorSentence(),
    );
  });

  it("unpins once however fast it is clicked", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);
    const line = await screen.findByTestId("pin-line");
    await user.click(within(line).getByRole("button", { name: "Unpin v1.2" }));

    await user.dblClick(
      within(within(line).getByTestId("unpin-confirm")).getByRole("button", { name: "Unpin" }),
    );

    await waitFor(() => expect(unpinPin).toHaveBeenCalledTimes(1));
    expect(unpinPin).toHaveBeenCalledWith(5);
  });
});

describe("Archive", () => {
  it("archives at once, as before, when the Set has no pins", async () => {
    getSet.mockResolvedValue(pinnedDetail({ pins: [] }));
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: "Archive" }));

    await waitFor(() => expect(archiveSet).toHaveBeenCalledWith(3));
    expect(screen.queryByTestId("archive-confirm")).not.toBeInTheDocument();
  });

  it("sends one archive for a double click when the Set has no pins", async () => {
    getSet.mockResolvedValue(pinnedDetail({ pins: [] }));
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.dblClick(await screen.findByRole("button", { name: "Archive" }));

    await waitFor(() => expect(archiveSet).toHaveBeenCalledTimes(1));
  });

  it("finds the question closed when the route goes to another Set and back", async () => {
    const user = setupUser();
    const { rerender } = renderWithQueryClient(<SetDetailPage />);
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    expect(screen.getByTestId("archive-confirm")).toBeInTheDocument();

    routeSetId = "4";
    getSet.mockResolvedValue(pinnedDetail({ set: setRow({ id: 4, name: "Other Set" }), pins: [] }));
    rerender(<SetDetailPage />);
    await screen.findByRole("heading", { name: "Other Set" });
    routeSetId = "3";
    getSet.mockResolvedValue(pinnedDetail());
    rerender(<SetDetailPage />);

    await screen.findByTestId("pin-line");
    expect(screen.queryByTestId("archive-confirm")).not.toBeInTheDocument();
  });

  it("names each pin and says the next sync takes them off, and archives only on confirm", async () => {
    getSet.mockResolvedValue(
      pinnedDetail({
        pins: [setPin({ pin_id: 6, name: "Blooming v1.4", board_id: 8 }), setPin()],
      }),
    );
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: "Archive" }));

    const confirm = screen.getByTestId("archive-confirm");
    expect(confirm).toHaveTextContent(
      'It receives no more shots. Its 2 pins are unpinned: the next sync takes "Blooming v1.4" and "Blooming v1.2" off the machine.',
    );
    expect(archiveSet).not.toHaveBeenCalled();

    await user.dblClick(within(confirm).getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(archiveSet).toHaveBeenCalledTimes(1));
  });

  it("does not carry an open question over to another Set when the route moves", async () => {
    const user = setupUser();
    const { rerender } = renderWithQueryClient(<SetDetailPage />);
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    expect(screen.getByTestId("archive-confirm")).toBeInTheDocument();

    routeSetId = "4";
    getSet.mockResolvedValue(
      pinnedDetail({
        set: setRow({ id: 4, name: "Other Set" }),
        pins: [setPin({ pin_id: 9, name: "Other v1.1" })],
      }),
    );
    rerender(<SetDetailPage />);

    await screen.findByRole("heading", { name: "Other Set" });
    expect(screen.queryByTestId("archive-confirm")).not.toBeInTheDocument();
  });

  it("a single pin is said in the singular, and Cancel archives nothing", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: "Archive" }));

    expect(screen.getByTestId("archive-pins")).toHaveTextContent(
      'Its pin is unpinned: the next sync takes "Blooming v1.2" off the machine.',
    );
    await user.click(
      within(screen.getByTestId("archive-confirm")).getByRole("button", { name: "Cancel" }),
    );
    expect(screen.queryByTestId("archive-confirm")).not.toBeInTheDocument();
    expect(archiveSet).not.toHaveBeenCalled();
  });
});

describe("focus after a pin", () => {
  it("does not move focus to another Set's pinned marker after the route moves away and back", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 600_000 } },
    });
    const other = () => pinnedDetail({ set: setRow({ id: 4, name: "Other Set" }), pins: [] });
    const user = setupUser();
    // The other Set is read first, so moving to it later finds it cached and the timeline stays
    // mounted (production keeps its cache; a loading skeleton would reset the state by itself).
    routeSetId = "4";
    getSet.mockResolvedValue(other());
    const { rerender } = renderWithQueryClient(<SetDetailPage />, { queryClient: client });
    await screen.findByRole("heading", { name: "Other Set" });

    routeSetId = "3";
    getSet.mockResolvedValue(pinnedDetail());
    rerender(<SetDetailPage />);
    await screen.findByRole("heading", { name: "Guji on the Niche" });
    await user.click(within(entryOf("v1.1")).getByRole("button", { name: /^Pin to the machine/ }));
    const dialog = await screen.findByTestId("pin-dialog");
    await within(dialog).findByTestId("pin-explained");
    // The re-read after the pin does not show it yet, so the wish for focus stays pending.
    await user.click(within(dialog).getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(screen.queryByTestId("pin-dialog")).not.toBeInTheDocument());

    routeSetId = "4";
    getSet.mockResolvedValue(other());
    rerender(<SetDetailPage />);
    await screen.findByRole("heading", { name: "Other Set" });
    routeSetId = "3";
    const later = pinnedDetail();
    later.versions[2] = { ...later.versions[2], pinned: { pin_id: 6, shot_count: 0 } } as never;
    getSet.mockResolvedValue(later);
    rerender(<SetDetailPage />);

    await waitFor(() =>
      expect(within(entryOf("v1.1")).getByTestId("pinned-marker")).toBeInTheDocument(),
    );
    expect(within(entryOf("v1.1")).getByTestId("pinned-marker")).not.toHaveFocus();
  });
});
