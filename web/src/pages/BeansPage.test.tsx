import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BeansPage } from "@/pages/BeansPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { bean, grinder, vocabulary } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  getBeans,
  createBean,
  updateBean,
  setBeanArchived,
  getVocabulary,
  getGrinders,
  getMachines,
  getProfileVersions,
  getSimilarSets,
} = vi.hoisted(() => ({
  getBeans: vi.fn(),
  createBean: vi.fn(),
  updateBean: vi.fn(),
  setBeanArchived: vi.fn(),
  getVocabulary: vi.fn(),
  getGrinders: vi.fn(),
  getMachines: vi.fn(),
  getProfileVersions: vi.fn(),
  getSimilarSets: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getBeans,
  createBean,
  updateBean,
  setBeanArchived,
  getVocabulary,
  getGrinders,
  getMachines,
  getProfileVersions,
  getSimilarSets,
}));

/** Freshness is "days since", so the clock has to be pinned. */
const TODAY = new Date("2026-04-08T10:00:00Z");

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(TODAY);
  getBeans.mockResolvedValue({ items: [bean()] });
  getVocabulary.mockResolvedValue(vocabulary);
  createBean.mockResolvedValue(bean({ id: 2, name: "Kenya Kiambu" }));
  updateBean.mockResolvedValue(bean({ name: "Kenya Kiambu" }));
  setBeanArchived.mockResolvedValue(bean({ archived: true }));
  // The "Start a Set" shortcut mounts the wizard, which asks for these.
  getGrinders.mockResolvedValue({ items: [grinder()] });
  getMachines.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
  getSimilarSets.mockResolvedValue({ bean_id: 1, grinder_id: null, machine_id: null, items: [] });
});

describe("BeansPage", () => {
  it("shows days off roast and what that means", async () => {
    renderWithQueryClient(<BeansPage />);

    const pill = await screen.findByTestId("freshness-pill");
    // Roasted on the 1st, read on the 8th: a week, which is the plateau.
    expect(pill).toHaveTextContent("7d — ready");
    expect(pill).toHaveAttribute("data-tone", "good");
  });

  it("warns while a bag is still degassing", async () => {
    getBeans.mockResolvedValue({ items: [bean({ roast_date: "2026-04-07" })] });
    renderWithQueryClient(<BeansPage />);

    const pill = await screen.findByTestId("freshness-pill");
    expect(pill).toHaveTextContent("1d — resting");
    expect(pill).toHaveAttribute("data-tone", "warn");
  });

  it("says nothing rather than guessing when a bag carries no date", async () => {
    getBeans.mockResolvedValue({ items: [bean({ roast_date: null })] });
    renderWithQueryClient(<BeansPage />);

    expect(await screen.findByTestId("freshness-pill")).toHaveTextContent("no roast date");
  });

  it("records a bag with the vocabularies the server serves", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: /Add a bag/ }));
    await user.type(await screen.findByLabelText("Name"), "Kenya Kiambu");
    await screen.findByRole("option", { name: "medium light" });
    await user.selectOptions(screen.getByLabelText("Roast level"), "medium-light");
    await user.selectOptions(screen.getByLabelText("Process"), "washed");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(createBean).toHaveBeenCalled());
    expect(createBean.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        name: "Kenya Kiambu",
        roast_level: "medium-light",
        process: "washed",
      }),
    );
  });

  it("archives a finished bag rather than deleting it", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Archive Ethiopia Guji" }));

    await waitFor(() => expect(setBeanArchived).toHaveBeenCalled());
    expect(setBeanArchived.mock.calls[0].slice(0, 2)).toEqual([1, true]);
  });

  it("opens the Set wizard on the bag you pressed it from", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Start a Set from Ethiopia Guji" }));

    // Pre-selected, because the whole point of the shortcut is not having to
    // find the bag you are looking at in a picker.
    const wizard = await screen.findByTestId("new-set-wizard");
    expect(wizard).toHaveTextContent("1. Suggest");
    await waitFor(() => expect(screen.getByLabelText("Bean")).toHaveValue("1"));
  });

  it("does not offer the shortcut on an archived bag", async () => {
    // Archiving is how a finished bag leaves the pickers, and a shortcut that
    // put it back in one would be the single path around that.
    getBeans.mockResolvedValue({ items: [bean({ archived: true })] });
    renderWithQueryClient(<BeansPage />);

    await screen.findByTestId("bean-list");
    expect(
      screen.queryByRole("button", { name: "Start a Set from Ethiopia Guji" }),
    ).not.toBeInTheDocument();
  });

  it("points at the Beans page when there is nothing to pick", async () => {
    getBeans.mockResolvedValue({ items: [] });
    renderWithQueryClient(<BeansPage />);

    expect(await screen.findByText("No beans recorded")).toBeInTheDocument();
  });
});
