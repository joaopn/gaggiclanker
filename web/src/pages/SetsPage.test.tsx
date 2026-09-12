import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetsPage } from "@/pages/SetsPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { setRow } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getSets, activateSet, getBeans, getGrinders, getMachines, getProfileVersions } = vi.hoisted(
  () => ({
    getSets: vi.fn(),
    activateSet: vi.fn(),
    getBeans: vi.fn(),
    getGrinders: vi.fn(),
    getMachines: vi.fn(),
    getProfileVersions: vi.fn(),
  }),
);
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSets,
  activateSet,
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
        active: false,
        shot_count: 0,
        bean_name: "Kenya",
      }),
    ],
  });
  activateSet.mockResolvedValue(setRow({ id: 4, active: true }));
  getBeans.mockResolvedValue({ items: [] });
  getGrinders.mockResolvedValue({ items: [] });
  getMachines.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
});

describe("SetsPage", () => {
  it("cards the identity, the counts and which one the machine is set up for", async () => {
    renderWithQueryClient(<SetsPage />);

    const cards = await screen.findAllByTestId("set-card");
    expect(cards).toHaveLength(2);
    // bean · grinder · profile vN, which is what identifies a Set at a glance.
    expect(cards[0]).toHaveTextContent("Ethiopia Guji · Niche Zero · 9 Bar Espresso v2");
    expect(cards[0]).toHaveTextContent("4 shots");
    expect(within(cards[0]).getByTestId("set-active")).toBeInTheDocument();
    expect(within(cards[1]).queryByTestId("set-active")).not.toBeInTheDocument();
  });

  it("switches which Set is loaded without archiving the other", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetsPage />);

    const cards = await screen.findAllByTestId("set-card");
    await user.click(within(cards[1]).getByRole("button", { name: /This is what is loaded/ }));

    await waitFor(() => expect(activateSet).toHaveBeenCalled());
    expect(activateSet.mock.calls[0][0]).toBe(4);
    // The active Set has no such button: it is already the answer.
    expect(
      within(cards[0]).queryByRole("button", { name: /This is what is loaded/ }),
    ).not.toBeInTheDocument();
  });

  it("explains what a shot with no Set costs when there are none", async () => {
    getSets.mockResolvedValue({ items: [] });
    renderWithQueryClient(<SetsPage />);

    expect(await screen.findByText("No Sets yet")).toBeInTheDocument();
    expect(screen.getByText(/needs a Set/)).toBeInTheDocument();
  });

  it("opens the wizard from the header", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetsPage />);

    await user.click(await screen.findByRole("button", { name: /New Set/ }));

    expect(await screen.findByTestId("new-set-wizard")).toBeInTheDocument();
  });
});
