import { screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import type { SetDetailData } from "@/api/types";
import { SetDetailPage } from "@/pages/SetDetailPage";
import { pinDetail, pinPreview, setPin } from "@/test/pinFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { labelCounts, setDetail, setRow, trends, version, vocabulary } from "@/test/setsFixtures";
import { signatureNone } from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));
vi.mock("react-chartjs-2", () => ({ Line: () => <canvas /> }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useParams: () => ({ setId: "3" }),
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

function entryOf(label: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(
    `[data-testid="version-entry"][data-version="${label}"]`,
  );
  if (!found) throw new Error(`no ${label} in the log`);
  return found;
}

beforeEach(() => {
  vi.clearAllMocks();
  getSet.mockResolvedValue(pinnedDetail());
  getSetTrends.mockResolvedValue(trends());
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  getSignature.mockResolvedValue(signatureNone);
  getSignatureOverrides.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0 });
  getProfileBoard.mockResolvedValue({ rows: [], actions: [], reports: [], proposals: [] });
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

  it("renders the row for every version, and an empty one takes no room", async () => {
    const detail = pinnedDetail();
    // The current version, already pinned: nothing applies to it.
    detail.versions[0] = { ...detail.versions[0], pinned: { pin_id: 9, shot_count: 0 } } as never;
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");

    for (const label of ["v1.3", "v1.2", "v1.1"]) {
      expect(within(entryOf(label)).getByTestId("version-actions")).toBeInTheDocument();
    }
    const empty = within(entryOf("v1.3")).getByTestId("version-actions");
    expect(empty).toBeEmptyDOMElement();
    expect(empty).toHaveClass("empty:hidden");
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
