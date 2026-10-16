import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetsPage } from "@/pages/SetsPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { designingSet, setRow } from "@/test/setsFixtures";

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

describe("SetsPage, a Set being designed", () => {
  it("carries the badge and Continue designing in place of the recipe", async () => {
    getSets.mockResolvedValue({ items: [designingSet(), setRow()] });
    renderWithQueryClient(<SetsPage />);

    const cards = await screen.findAllByTestId("set-card");
    expect(within(cards[0]).getByTestId("set-designing")).toHaveTextContent("Designing");
    expect(within(cards[0]).getByTestId("set-design-summary")).toHaveTextContent(
      "Ethiopia Guji · Niche Zero · no recipe yet",
    );
    // v1's own conversation, through the route Discuss opens or continues.
    expect(within(cards[0]).getByTestId("continue-designing")).toHaveAttribute(
      "href",
      "/chat?set=6&version=60",
    );

    // The Set beside it has a recipe, and none of this.
    expect(within(cards[1]).queryByTestId("set-designing")).not.toBeInTheDocument();
    expect(within(cards[1]).queryByTestId("continue-designing")).not.toBeInTheDocument();
    expect(cards[1]).toHaveTextContent("Ethiopia Guji · Niche Zero · 9 Bar Espresso v2");
  });

  it("offers no automatch on a Set with no profile to match on yet", async () => {
    // Both collect shots, as every new Set does; only one has a profile.
    getSets.mockResolvedValue({ items: [designingSet({ automatch: true }), setRow()] });
    renderWithQueryClient(<SetsPage />);

    const cards = await screen.findAllByTestId("set-card");
    expect(within(cards[0]).queryByTestId("set-automatch")).not.toBeInTheDocument();
    expect(
      within(cards[0]).queryByRole("button", { name: /filing shots here|matching shots here/ }),
    ).not.toBeInTheDocument();
    expect(within(cards[1]).getByTestId("set-automatch")).toBeInTheDocument();
    expect(
      within(cards[1]).getByRole("button", { name: /Stop filing shots here/ }),
    ).toBeInTheDocument();
  });

  describe("How a Set works", () => {
    function explainer() {
      const section = screen.getByTestId("sets-explainer");
      const button = within(section).getByRole("button", { name: "How a Set works" });
      const region = document.getElementById(String(button.getAttribute("aria-controls")));
      return { button, region: region as HTMLElement };
    }

    it("is open while nothing is being dialled in", async () => {
      getSets.mockResolvedValue({ items: [] });
      renderWithQueryClient(<SetsPage />);

      await screen.findByText("No Sets yet");
      const { button, region } = explainer();
      expect(button).toHaveAttribute("aria-expanded", "true");
      expect(region).not.toHaveAttribute("hidden");
      // Per bean, not per bag: a second bag of the same coffee is the same Set.
      expect(region).toHaveTextContent("A Set is how you dial in one bean.");
      expect(region).toHaveTextContent("The Set doesn't copy any of that. It follows the profile.");
    });

    it("counts only Sets that are not archived as something being dialled in", async () => {
      getSets.mockResolvedValue({ items: [setRow({ archived: true, automatch: false })] });
      renderWithQueryClient(<SetsPage />);

      await screen.findAllByTestId("set-card");
      expect(explainer().button).toHaveAttribute("aria-expanded", "true");
    });

    it("is closed once a Set exists, and its text is still there to open", async () => {
      renderWithQueryClient(<SetsPage />);

      await screen.findAllByTestId("set-card");
      const { button, region } = explainer();
      expect(button).toHaveAttribute("aria-expanded", "false");
      // Always rendered, toggled with `hidden`, so aria-controls resolves.
      expect(region).toHaveAttribute("hidden");
    });

    it("remembers a choice made by hand, in either direction", async () => {
      const user = setupUser();
      const { unmount } = renderWithQueryClient(<SetsPage />);
      await screen.findAllByTestId("set-card");
      await user.click(explainer().button);
      expect(explainer().button).toHaveAttribute("aria-expanded", "true");
      expect(window.localStorage.getItem("sets.explainer.v1")).toBe("1");
      unmount();

      renderWithQueryClient(<SetsPage />);
      await screen.findAllByTestId("set-card");
      expect(explainer().button).toHaveAttribute("aria-expanded", "true");
      await user.click(explainer().button);
      expect(window.localStorage.getItem("sets.explainer.v1")).toBe("0");
    });

    it("keeps a closed choice even when there are no Sets", async () => {
      window.localStorage.setItem("sets.explainer.v1", "0");
      getSets.mockResolvedValue({ items: [] });
      renderWithQueryClient(<SetsPage />);

      await screen.findByText("No Sets yet");
      expect(explainer().button).toHaveAttribute("aria-expanded", "false");
    });
  });
});
