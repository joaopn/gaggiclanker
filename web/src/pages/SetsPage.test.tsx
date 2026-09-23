import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetsPage } from "@/pages/SetsPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { setRow } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getSets, setAutomatch, getBeans, getGrinders, getMachines, getProfileVersions } =
  vi.hoisted(() => ({
    getSets: vi.fn(),
    setAutomatch: vi.fn(),
    getBeans: vi.fn(),
    getGrinders: vi.fn(),
    getMachines: vi.fn(),
    getProfileVersions: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSets,
  setAutomatch,
  getBeans,
  getGrinders,
  getMachines,
  getProfileVersions,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getSets.mockResolvedValue({
    items: [
      setRow(),
      setRow({
        id: 4,
        name: "Kenya on the DF64",
        automatch: false,
        shot_count: 0,
        bean_name: "Kenya",
      }),
    ],
  });
  setAutomatch.mockResolvedValue(setRow({ id: 4, automatch: true }));
  getBeans.mockResolvedValue({ items: [] });
  getGrinders.mockResolvedValue({ items: [] });
  getMachines.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
});

describe("SetsPage", () => {
  it("cards the identity, the counts and which ones collect new shots", async () => {
    renderWithQueryClient(<SetsPage />);

    const cards = await screen.findAllByTestId("set-card");
    expect(cards).toHaveLength(2);
    // bean · grinder · profile vN, which is what identifies a Set at a glance.
    expect(cards[0]).toHaveTextContent("Ethiopia Guji · Niche Zero · 9 Bar Espresso v2");
    expect(cards[0]).toHaveTextContent("4 shots");
    expect(within(cards[0]).getByTestId("set-automatch")).toBeInTheDocument();
    expect(within(cards[1]).queryByTestId("set-automatch")).not.toBeInTheDocument();
  });

  it("turns matching on for one Set and off for another, touching nothing else", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetsPage />);

    const cards = await screen.findAllByTestId("set-card");
    await user.click(within(cards[1]).getByRole("button", { name: /File matching shots here/ }));

    await waitFor(() => expect(setAutomatch).toHaveBeenCalled());
    expect(setAutomatch.mock.calls[0]).toEqual([4, true]);

    // The Set already collecting offers the other direction, and no Set is
    // switched off by another being switched on.
    await user.click(within(cards[0]).getByRole("button", { name: /Stop filing shots here/ }));
    await waitFor(() => expect(setAutomatch).toHaveBeenCalledTimes(2));
    expect(setAutomatch.mock.calls[1]).toEqual([3, false]);
  });

  it("explains what a shot with no Set costs when there are none", async () => {
    getSets.mockResolvedValue({ items: [] });
    renderWithQueryClient(<SetsPage />);

    expect(await screen.findByText("No Sets yet")).toBeInTheDocument();
    expect(screen.getByText(/needs a Set/)).toBeInTheDocument();
  });

  it("opens the New Set dialog from the header", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetsPage />);

    await user.click(await screen.findByRole("button", { name: /New Set/ }));

    expect(await screen.findByTestId("new-set-dialog")).toBeInTheDocument();
  });
});
