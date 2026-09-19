import { screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShotJudgement } from "@/api/types";
import { QuickJudgement } from "@/components/shots/QuickJudgement";
import { usePatchJudgement } from "@/hooks/useSets";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { flavorPicks, judgement, vocabulary } from "@/test/setsFixtures";
import { shot129 } from "@/test/shotFixture";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getVocabulary, getFlavorPicks, getShot, putJudgement } = vi.hoisted(() => ({
  getVocabulary: vi.fn(),
  getFlavorPicks: vi.fn(),
  getShot: vi.fn(),
  putJudgement: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
  getFlavorPicks,
  getShot,
  putJudgement,
}));

/** The verdict on the server, which every write reads before it merges. */
let stored: ShotJudgement | null;

beforeEach(() => {
  vi.clearAllMocks();
  stored = judgement();
  getVocabulary.mockResolvedValue(vocabulary);
  getFlavorPicks.mockResolvedValue(flavorPicks());
  getShot.mockImplementation(async () => ({ ...shot129, judgement: stored }));
  putJudgement.mockImplementation(async (_id: number, body: object) => {
    stored = { ...judgement(), ...body, updated_at: new Date().toISOString() };
    return stored;
  });
});

async function rendered(verdict: ShotJudgement | null = stored) {
  renderWithQueryClient(<QuickJudgement shotId={1} judgement={verdict} />);
  // The chips need the wheel, so their arrival is the panel being ready.
  await screen.findByTestId("taste-chips");
}

function row(kind: "taste" | "aroma") {
  return within(
    screen.getByRole("group", { name: `${kind === "taste" ? "Taste" : "Aroma"} notes` }),
  );
}

describe("QuickJudgement", () => {
  it("offers the picks for each row, plus what the shot already has, in wheel order", async () => {
    await rendered();

    // Sour is recorded on this shot but is not a taste pick: it is shown, and
    // pressed, so it can be taken off.
    expect(
      row("taste")
        .getAllByRole("button")
        .map((chip) => chip.textContent),
    ).toEqual(["Berry", "Sour", "Bitter", "Chocolate"]);
    expect(row("taste").getByRole("button", { name: "Sour/Fermented › Sour" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(row("taste").getByRole("button", { name: "Fruity › Berry" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(row("aroma").getByRole("button", { name: "Fruity › Berry" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("link", { name: "Edit notes on the Taste wheel" })).toHaveAttribute(
      "href",
      "/taste-wheel",
    );
  });

  it("saves a chip at once, changing only that list", async () => {
    const user = setupUser();
    await rendered();
    // Changed somewhere else since the panel was drawn: the panel's copy of the
    // rating and the notes is out of date, and must not be written back.
    stored = judgement({ rating: 2, notes: "typed in the row" });

    await user.click(row("taste").getByRole("button", { name: "Other › Chemical › Bitter" }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
    // Merged into the verdict as the server holds it: only the taste list is
    // the panel's; everything else is what was there.
    expect(putJudgement.mock.calls[0]).toEqual([
      1,
      {
        rating: 2,
        balance: "sour",
        taste_notes: ["sour_fermented.sour", "other.chemical.bitter"],
        aroma_notes: ["fruity.berry"],
        dose_in_g: 18,
        dose_out_g: 36,
        grind_setting: "22",
        notes: "typed in the row",
        decision: "improve",
      },
    ]);
    expect(row("taste").getByRole("button", { name: "Other › Chemical › Bitter" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("keeps a star clicked in the row while a chip is clicked in the panel", async () => {
    const user = setupUser();
    // Another control writing the same verdict, the way the row's stars do.
    function RowStar() {
      const patch = usePatchJudgement(1);
      return (
        <button
          type="button"
          onClick={() => void patch.mutateAsync({ shotId: 1, patch: { rating: 5 } })}
        >
          row star 5
        </button>
      );
    }
    renderWithQueryClient(
      <>
        <RowStar />
        <QuickJudgement shotId={1} judgement={stored} />
      </>,
    );
    await screen.findByTestId("taste-chips");

    // The chip goes before the star's write and re-read have come back.
    await user.click(screen.getByRole("button", { name: "row star 5" }));
    await user.click(row("taste").getByRole("button", { name: "Fruity › Berry" }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(2));
    expect(putJudgement.mock.calls[0][1]).toEqual(expect.objectContaining({ rating: 5 }));
    expect(putJudgement.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        rating: 5,
        taste_notes: ["sour_fermented.sour", "fruity.berry"],
      }),
    );
  });

  it("builds quick clicks on each other rather than on what the server had", async () => {
    const user = setupUser();
    await rendered();

    await user.click(row("aroma").getByRole("button", { name: "Floral" }));
    await user.click(row("taste").getByRole("button", { name: "Nutty/Cocoa › Cocoa › Chocolate" }));
    await user.click(screen.getByRole("button", { name: "5 stars" }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(3));
    expect(putJudgement.mock.calls[2][1]).toEqual(
      expect.objectContaining({
        rating: 5,
        aroma_notes: ["fruity.berry", "floral"],
        taste_notes: ["sour_fermented.sour", "nutty_cocoa.cocoa.chocolate"],
      }),
    );
  });

  it("sets the balance with one click and clears it with a second", async () => {
    const user = setupUser();
    await rendered(judgement({ balance: null }));

    await user.click(screen.getByRole("button", { name: "Bitter" }));
    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
    expect(putJudgement.mock.calls[0][1]).toEqual(expect.objectContaining({ balance: "bitter" }));
    expect(screen.getByRole("button", { name: "Bitter" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Bitter" }));
    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(2));
    expect(putJudgement.mock.calls[1][1]).toEqual(expect.objectContaining({ balance: null }));
  });

  it("saves the notes when the field is left, and says so", async () => {
    const user = setupUser();
    await rendered();

    const notes = screen.getByLabelText("Notes");
    await user.clear(notes);
    await user.type(notes, "sweet, long");
    expect(putJudgement).not.toHaveBeenCalled();
    await user.tab();

    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
    expect(putJudgement.mock.calls[0][1]).toEqual(
      expect.objectContaining({ notes: "sweet, long" }),
    );
    expect(await screen.findByText("Saved")).toBeInTheDocument();

    // Leaving it again without a change writes nothing.
    await user.click(notes);
    await user.tab();
    expect(putJudgement).toHaveBeenCalledTimes(1);
  });

  it("saves the notes on Ctrl+Enter without leaving the field", async () => {
    const user = setupUser();
    await rendered();

    const notes = screen.getByLabelText("Notes");
    await user.type(notes, " and bright");
    await user.keyboard("{Control>}{Enter}{/Control}");

    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
    expect(putJudgement.mock.calls[0][1]).toEqual(
      expect.objectContaining({ notes: "sharp at the end and bright" }),
    );
    expect(notes).toHaveFocus();
  });

  it("writes nothing for a clear on a shot nobody has judged", async () => {
    const user = setupUser();
    stored = null;
    await rendered(null);

    // Nothing is lit, so the only "clear" left is an emptied note.
    const notes = screen.getByLabelText("Notes");
    await user.type(notes, "x");
    await user.clear(notes);
    await user.tab();

    expect(putJudgement).not.toHaveBeenCalled();

    // A real answer does create the verdict.
    await user.click(screen.getByRole("button", { name: "Sour" }));
    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
    expect(putJudgement.mock.calls[0][1]).toEqual(
      expect.objectContaining({ balance: "sour", rating: null, taste_notes: [] }),
    );
  });

  it("points at the Taste wheel when there is nothing to offer", async () => {
    getFlavorPicks.mockResolvedValue(flavorPicks({ taste: [], aroma: [] }));
    await rendered(judgement({ taste_notes: [], aroma_notes: [] }));

    // One line per row, each opening the Taste wheel on that row's list.
    const links = await screen.findAllByRole("link", { name: "Pick some on the Taste wheel" });
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/taste-wheel?list=aroma",
      "/taste-wheel",
    ]);
  });

  it("puts a chip back when its write fails", async () => {
    const user = setupUser();
    putJudgement.mockRejectedValueOnce(new Error("disk full"));
    await rendered();

    await user.click(row("taste").getByRole("button", { name: "Fruity › Berry" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    await waitFor(() =>
      expect(row("taste").getByRole("button", { name: "Fruity › Berry" })).toHaveAttribute(
        "aria-pressed",
        "false",
      ),
    );
  });

  it("says when the verdict came from the machine's notes card", async () => {
    await rendered(judgement({ seeded_from_device_note: true }));
    expect(screen.getByTestId("seeded-badge")).toHaveTextContent("from the machine");
  });
});
