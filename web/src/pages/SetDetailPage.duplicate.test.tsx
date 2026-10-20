import { screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SetDetailData } from "@/api/types";
import { SetDetailPage } from "@/pages/SetDetailPage";
import { boardRowView, boardView } from "@/test/boardFixtures";
import { duplicatePreview } from "@/test/duplicateFixtures";
import { setPin } from "@/test/pinFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import {
  bean,
  grinder,
  labelCounts,
  setDetail,
  setRow,
  trends,
  version,
  vocabulary,
} from "@/test/setsFixtures";
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
  getVocabulary,
  getKnowledgeInsights,
  getProfileVersions,
  getSignature,
  getSignatureOverrides,
  getProfileBoard,
  getDuplicatePreview,
  getBeans,
  getGrinders,
} = vi.hoisted(() => ({
  getSet: vi.fn(),
  getSetTrends: vi.fn(),
  getVocabulary: vi.fn(),
  getKnowledgeInsights: vi.fn(),
  getProfileVersions: vi.fn(),
  getSignature: vi.fn(),
  getSignatureOverrides: vi.fn(),
  getProfileBoard: vi.fn(),
  getDuplicatePreview: vi.fn(),
  getBeans: vi.fn(),
  getGrinders: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSet,
  getSetTrends,
  getVocabulary,
  getKnowledgeInsights,
  getProfileVersions,
  getSignature,
  getSignatureOverrides,
  getProfileBoard,
  getDuplicatePreview,
  getBeans,
  getGrinders,
}));

/** Three versions: v1.3 current, v1.2 pinned, v1.1 plain. */
function detail(overrides: Partial<SetDetailData> = {}): SetDetailData {
  const entry = (id: number, minor: number, extra: Record<string, unknown> = {}) => ({
    version: version({
      id,
      version_major: 1,
      version_minor: minor,
      is_current: minor === 3,
    }),
    changes: [],
    shots: [],
    dead_end: false,
    labels: labelCounts(),
    outcome_proposal: null,
    ...extra,
  });
  return setDetail({
    recipe: setRow({
      current_version_id: 23,
      current_version_label: "v1.3",
      version_count: 3,
      name: "Ethiopia Guji · Blooming",
    }),
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

function Search() {
  return <p data-testid="search">{useLocation().search}</p>;
}

function renderPage(entry = "/sets/3") {
  return renderWithQueryClient(
    <>
      <SetDetailPage />
      <Search />
    </>,
    { initialEntries: [entry] },
  );
}

// The visible words first, then the version: the name the Pin button follows too.
const DUPLICATE = /^Duplicate Set: v/;

beforeEach(() => {
  vi.clearAllMocks();
  routeSetId = "3";
  getSet.mockResolvedValue(detail());
  getSetTrends.mockResolvedValue(trends());
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  getSignature.mockResolvedValue(signatureNone);
  getSignatureOverrides.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0 });
  getProfileBoard.mockResolvedValue(
    boardView({ rows: [boardRowView({ row: { id: 7, label: "Blooming v1.2" } })] }),
  );
  getDuplicatePreview.mockResolvedValue(duplicatePreview());
  getBeans.mockResolvedValue({ items: [bean()] });
  getGrinders.mockResolvedValue({ items: [grinder()] });
});

describe("the buttons", () => {
  it("offers Duplicate Set once on every version, the current and the pinned ones included", async () => {
    renderPage();
    await screen.findByTestId("version-timeline");

    for (const label of ["v1.3", "v1.2", "v1.1"]) {
      const buttons = within(entryOf(label)).getAllByRole("button", { name: DUPLICATE });
      expect(buttons).toHaveLength(1);
      expect(buttons[0]).toHaveTextContent("Duplicate Set");
      expect(buttons[0]).toHaveAccessibleName(`Duplicate Set: ${label}`);
    }
  });

  it("puts it in the same row as the version's other buttons, below which nothing moves", async () => {
    renderPage();
    await screen.findByTestId("version-timeline");

    const entry = entryOf("v1.1");
    const row = within(entry).getByRole("button", { name: "Go back to this version" })
      .parentElement as HTMLElement;
    expect(row).toHaveClass("flex-wrap");
    expect(within(row).getByRole("button", { name: DUPLICATE })).toBeVisible();
    expect(within(row).getByRole("button", { name: /^Pin to the machine/ })).toBeVisible();
    // The three buttons are one row's children, in this order.
    expect(Array.from(row.children).map((child) => child.textContent?.trim())).toEqual([
      "Go back to this version",
      "Pin to the machine",
      "Duplicate Set",
    ]);
  });

  it("offers it on an archived Set, since a finished bag is the usual starting point", async () => {
    getSet.mockResolvedValue(detail({ recipe: setRow({ archived: true }), pins: [] }));
    renderPage();
    await screen.findByTestId("version-timeline");

    expect(screen.getAllByRole("button", { name: DUPLICATE })).toHaveLength(3);
  });

  it("offers it on no version of a Set that is still being designed", async () => {
    getSet.mockResolvedValue(detail({ recipe: setRow({ designing: true }), pins: [] }));
    renderPage();
    await screen.findByTestId("version-timeline");

    expect(screen.queryByRole("button", { name: DUPLICATE })).not.toBeInTheDocument();
  });

  it("opens the dialog for the version whose button was pressed", async () => {
    const user = setupUser();
    renderPage();
    await screen.findByTestId("version-timeline");

    await user.click(within(entryOf("v1.1")).getByRole("button", { name: DUPLICATE }));

    const dialog = await screen.findByTestId("duplicate-dialog");
    expect(dialog).toHaveTextContent('Duplicate "Ethiopia Guji · Blooming" from v1.1');
    expect(getDuplicatePreview).toHaveBeenCalledWith(3, 21);
    // A button of the page opens it in place: the address does not change.
    expect(screen.getByTestId("search")).toHaveTextContent("");
  });

  it("opens the dialog in place from a line of the Pinned card, for the pinned version", async () => {
    const user = setupUser();
    renderPage();
    const card = await screen.findByTestId("pinned-card");

    // The card's buttons: Duplicate beside Unpin.
    expect(
      within(card)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Duplicate Set", "Unpin"]);
    await user.click(within(card).getByRole("button", { name: "Duplicate Set: v1.2" }));

    const dialog = await screen.findByTestId("duplicate-dialog");
    expect(dialog).toHaveTextContent("from v1.2");
    expect(getDuplicatePreview).toHaveBeenCalledWith(3, 22);
    expect(screen.getByTestId("search")).toHaveTextContent("");
  });

  it("closing a dialog the page opened leaves the page as it was", async () => {
    const user = setupUser();
    renderPage();
    await screen.findByTestId("version-timeline");
    await user.click(within(entryOf("v1.2")).getByRole("button", { name: DUPLICATE }));
    await screen.findByTestId("duplicate-dialog");

    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByTestId("duplicate-dialog")).not.toBeInTheDocument());
    expect(screen.getByTestId("version-timeline")).toBeInTheDocument();
  });
});

describe("?duplicate=", () => {
  it("opens the dialog on arrival for a version of this Set", async () => {
    renderPage("/sets/3?duplicate=22");

    const dialog = await screen.findByTestId("duplicate-dialog");

    expect(dialog).toHaveTextContent("from v1.2");
    expect(getDuplicatePreview).toHaveBeenCalledWith(3, 22);
  });

  it("removes the parameter when the dialog closes, and stays on the Set page", async () => {
    const user = setupUser();
    renderPage("/sets/3?duplicate=22&keep=1");
    await screen.findByTestId("duplicate-dialog");
    expect(screen.getByTestId("search")).toHaveTextContent("?duplicate=22&keep=1");

    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByTestId("duplicate-dialog")).not.toBeInTheDocument());
    expect(screen.getByTestId("search")).toHaveTextContent("?keep=1");
    expect(screen.getByTestId("version-timeline")).toBeInTheDocument();
  });

  it.each([
    ["an id no version has", "999"],
    ["an id that is not a number", "abc"],
    ["an empty value", ""],
    ["a number with a tail", "22x"],
    ["a negative number", "-22"],
  ])("opens nothing for %s", async (_name, value) => {
    renderPage(`/sets/3?duplicate=${value}`);
    await screen.findByTestId("version-timeline");

    expect(screen.queryByTestId("duplicate-dialog")).not.toBeInTheDocument();
    expect(getDuplicatePreview).not.toHaveBeenCalled();
  });

  it("opens nothing for another Set's version", async () => {
    // Version 31 belongs to a Set this page is not showing.
    renderPage("/sets/3?duplicate=31");
    await screen.findByTestId("version-timeline");

    expect(screen.queryByTestId("duplicate-dialog")).not.toBeInTheDocument();
    expect(getDuplicatePreview).not.toHaveBeenCalled();
  });

  it("shows the server's sentence when the Set cannot be duplicated", async () => {
    getSet.mockResolvedValue(detail({ recipe: setRow({ designing: true }), pins: [] }));
    getDuplicatePreview.mockRejectedValue(
      Object.assign(new Error("designing"), { sentence: "Still being designed." }),
    );
    renderPage("/sets/3?duplicate=22");

    expect(await screen.findByTestId("duplicate-dialog")).toBeInTheDocument();
    expect(await screen.findByTestId("duplicate-preview-error")).toBeInTheDocument();
  });
});

describe("the page keeps its state when the route moves to another Set", () => {
  it("drops a dialog opened by a button, and does not bring it back with the Set", async () => {
    const user = setupUser();
    const { rerender } = renderPage();
    await screen.findByTestId("version-timeline");
    await user.click(within(entryOf("v1.2")).getByRole("button", { name: DUPLICATE }));
    await screen.findByTestId("duplicate-dialog");

    routeSetId = "4";
    getSet.mockResolvedValue(
      detail({
        recipe: setRow({ id: 4, name: "Kenya" }),
        versions: [
          {
            version: version({ id: 41, recipe_id: 4, version_major: 1, is_current: true }),
            changes: [],
            shots: [],
            dead_end: false,
            labels: labelCounts(),
            outcome_proposal: null,
          },
        ],
        pins: [],
      }),
    );
    rerender(
      <>
        <SetDetailPage />
        <Search />
      </>,
    );

    await screen.findByText("Kenya");
    expect(screen.queryByTestId("duplicate-dialog")).not.toBeInTheDocument();

    // Back to the first Set: the question it was asking is not still open.
    routeSetId = "3";
    getSet.mockResolvedValue(detail());
    rerender(
      <>
        <SetDetailPage />
        <Search />
      </>,
    );
    await screen.findByTestId("version-timeline");
    expect(screen.queryByTestId("duplicate-dialog")).not.toBeInTheDocument();
  });
});

describe("the new Set's header", () => {
  it("says where the Set was copied from, links to that Set, and gives the date", async () => {
    getSet.mockResolvedValue(
      detail({
        recipe: setRow({
          created_at: "2026-10-10T09:00:00Z",
          copied_from: {
            recipe_id: 2,
            recipe_name: "Ethiopia Guji · Blooming",
            version_id: 18,
            version_label: "v1.2",
          },
        }),
      }),
    );
    renderPage();

    const note = await screen.findByTestId("copied-from");

    expect(note).toHaveTextContent("Copied from Ethiopia Guji · Blooming v1.2 ↗ on");
    expect(note).toHaveTextContent(
      new Date("2026-10-10T09:00:00Z").toLocaleDateString(undefined, { dateStyle: "medium" }),
    );
    expect(within(note).getByTestId("copied-from-link")).toHaveAttribute("href", "/sets/2");
  });

  it("has no line at all when the Set was not copied or its source is gone", async () => {
    getSet.mockResolvedValue(detail({ recipe: setRow({ copied_from: null }) }));
    renderPage();
    await screen.findByTestId("version-timeline");

    expect(screen.queryByTestId("copied-from")).not.toBeInTheDocument();
    expect(screen.queryByText(/Copied from/)).not.toBeInTheDocument();
  });
});
