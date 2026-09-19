import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FlavorPicks } from "@/api/types";
import { TasteWheelPage } from "@/pages/TasteWheelPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { flavorPicks, vocabulary } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getVocabulary, getFlavorPicks, putFlavorPicks } = vi.hoisted(() => ({
  getVocabulary: vi.fn(),
  getFlavorPicks: vi.fn(),
  putFlavorPicks: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
  getFlavorPicks,
  putFlavorPicks,
}));

let stored: FlavorPicks;

beforeEach(() => {
  vi.clearAllMocks();
  stored = flavorPicks();
  getVocabulary.mockResolvedValue(vocabulary);
  getFlavorPicks.mockImplementation(async () => stored);
  // The server's answer is what it stored, so a re-read after the write shows it.
  putFlavorPicks.mockImplementation(async (body: FlavorPicks) => {
    stored = body;
    return body;
  });
});

function chips(kind: "taste" | "aroma"): string[] {
  return within(screen.getByTestId(`picked-${kind}`))
    .queryAllByRole("button")
    .map((button) => button.textContent ?? "");
}

async function rendered(path = "/taste-wheel") {
  renderWithQueryClient(<TasteWheelPage />, { initialEntries: [path] });
  await screen.findByTestId("all-notes");
}

describe("TasteWheelPage", () => {
  it("shows both lists as the shot panel will, in wheel order, with their counts", async () => {
    await rendered();

    expect(screen.getByRole("heading", { name: "Taste (3)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Aroma (2)" })).toBeInTheDocument();
    expect(chips("taste")).toEqual(["Berry", "Bitter", "Chocolate"]);
    expect(chips("aroma")).toEqual(["Floral", "Berry"]);
    // Each chip says where it sits, since "Bitter" alone could be the balance.
    expect(
      screen.getByRole("button", { name: "Remove Other › Chemical › Bitter from taste" }),
    ).toBeInTheDocument();
  });

  it("edits the taste list until switched to aroma", async () => {
    const user = setupUser();
    await rendered();

    // "Floral" the category and "Floral" the group are two notes; the path is
    // what tells them apart.
    const category = screen.getByRole("checkbox", { name: "Floral" });
    const group = screen.getByRole("checkbox", { name: "Floral › Floral" });
    expect(category).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Fruity › Berry" })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Aroma" }));

    expect(screen.getByRole("button", { name: "Aroma" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("checkbox", { name: "Floral" })).toBeChecked();
    expect(group).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Other › Chemical › Bitter" })).not.toBeChecked();
  });

  it("opens on the aroma list when the link says so", async () => {
    await rendered("/taste-wheel?list=aroma");
    expect(screen.getByRole("button", { name: "Aroma" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("checkbox", { name: "Floral" })).toBeChecked();
  });

  it("saves a ticked note into its place on the list, leaving the other list alone", async () => {
    const user = setupUser();
    await rendered();

    await user.click(screen.getByRole("checkbox", { name: "Sweet › Brown sugar › Honey" }));
    await user.click(screen.getByRole("checkbox", { name: "Floral › Black tea" }));

    await waitFor(() => expect(putFlavorPicks).toHaveBeenCalledTimes(2));
    expect(putFlavorPicks.mock.calls[1][0]).toEqual({
      taste: [
        "floral.black_tea",
        "fruity.berry",
        "other.chemical.bitter",
        "nutty_cocoa.cocoa.chocolate",
        "sweet.brown_sugar.honey",
      ],
      aroma: ["floral", "fruity.berry"],
    });
    expect(chips("taste")).toEqual(["Black tea", "Berry", "Bitter", "Chocolate", "Honey"]);
    expect(screen.getByRole("heading", { name: "Taste (5)" })).toBeInTheDocument();
  });

  it("takes a note off with its chip, on either list", async () => {
    const user = setupUser();
    await rendered();

    await user.click(screen.getByRole("button", { name: "Remove Floral from aroma" }));

    await waitFor(() => expect(putFlavorPicks).toHaveBeenCalledTimes(1));
    expect(putFlavorPicks.mock.calls[0][0]).toEqual({
      taste: ["fruity.berry", "other.chemical.bitter", "nutty_cocoa.cocoa.chocolate"],
      aroma: ["fruity.berry"],
    });
    await waitFor(() => expect(chips("aroma")).toEqual(["Berry"]));
  });

  it("toggles a note from its segment on the wheel", async () => {
    await rendered();
    const wheel = screen.getByTestId("flavor-wheel");
    const lemon = wheel.querySelector('[data-note="fruity.citrus_fruit.lemon"]') as Element;
    expect(lemon).not.toHaveAttribute("data-picked");

    fireEvent.click(lemon);

    await waitFor(() => expect(putFlavorPicks).toHaveBeenCalledTimes(1));
    expect(putFlavorPicks.mock.calls[0][0].taste).toContain("fruity.citrus_fruit.lemon");
    await waitFor(() => expect(lemon).toHaveAttribute("data-picked"));
    // The drawing is a pointer control; the list is what a screen reader uses.
    expect(wheel).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("checkbox", { name: "Fruity › Citrus fruit › Lemon" })).toBeChecked();
  });

  it("puts the list back and says so when the save fails", async () => {
    const user = setupUser();
    putFlavorPicks.mockRejectedValueOnce(new Error("disk full"));
    await rendered();

    await user.click(screen.getByRole("checkbox", { name: "Sweet › Vanilla" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("disk full")),
    );
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Sweet › Vanilla" })).not.toBeChecked(),
    );
    expect(chips("taste")).toEqual(["Berry", "Bitter", "Chocolate"]);
  });
});
