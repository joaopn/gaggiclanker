import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import type { ShotInfoItem, ShotInformation } from "@/api/types";
import { findSettingsPage, type SettingsPageInfo } from "@/lib/settingsPages";
import { ShotInformationPage } from "@/pages/settings/ShotInformationPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getShotInformation, putShotInformationTier, resetShotInformation } = vi.hoisted(() => ({
  getShotInformation: vi.fn(),
  putShotInformationTier: vi.fn(),
  resetShotInformation: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getShotInformation,
  putShotInformationTier,
  resetShotInformation,
}));

const PAGE = findSettingsPage("shot-information") as SettingsPageInfo;

function item(overrides: Partial<ShotInfoItem> & Pick<ShotInfoItem, "key" | "name">): ShotInfoItem {
  return {
    label: overrides.name,
    meaning: `What ${overrides.name.toLowerCase()} means.`,
    default_tier: "base",
    tier: overrides.default_tier ?? "base",
    locked: false,
    example: null,
    ...overrides,
  };
}

function documentWith(
  tiers: Record<string, ShotInfoItem["tier"]> = {},
  estimates: Partial<ShotInformation["estimates"]> = {},
): ShotInformation {
  const items = [
    item({ key: "shot_id", name: "Shot id", locked: true, example: "204" }),
    item({ key: "shot_time", name: "Shot time", example: "54.6 s" }),
    item({ key: "rating", name: "Rating", example: "4/5" }),
    item({
      key: "phase_ramp",
      name: "Phase ramp rate",
      default_tier: "extended",
      example: "phase 1 · fill: ramp 0.01 bar/s GENTLE\nphase 2 · soak: ramp 0.00 bar/s",
    }),
    item({ key: "processing_note", name: "Processing note", default_tier: "excluded" }),
  ].map((entry) => ({ ...entry, tier: tiers[entry.key] ?? entry.default_tier }));
  const byKey = Object.fromEntries(items.map((entry) => [entry.key, entry]));
  return {
    groups: [
      {
        name: "Identity and status",
        note: null,
        items: [byKey.shot_id, byKey.shot_time, byKey.rating] as ShotInfoItem[],
      },
      {
        name: "Phases",
        note: "One line per phase, headed by the phase.",
        items: [byKey.phase_ramp] as ShotInfoItem[],
      },
      { name: "Channeling", note: null, items: [byKey.processing_note] as ShotInfoItem[] },
    ],
    example_shot: { shot_id: 204, started_at: "2026-09-10T18:11:00.000Z", judged: true },
    estimates: {
      base_per_shot: 120,
      extended_per_shot: 2900,
      full_per_shot: 3020,
      glossary: 3100,
      autoload: 2400,
      recent_shots: 20,
      ...estimates,
    },
  };
}

function tierGroup(name: string) {
  return screen.getByRole("group", { name: `Tier for ${name}` });
}

function pressed(name: string): string[] {
  return within(tierGroup(name))
    .getAllByRole("button")
    .filter((button) => button.getAttribute("aria-pressed") === "true")
    .map((button) => button.textContent ?? "");
}

async function renderPage() {
  const view = renderWithQueryClient(<ShotInformationPage page={PAGE} />);
  await screen.findByRole("heading", { name: "Identity and status" });
  return view;
}

describe("ShotInformationPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getShotInformation.mockResolvedValue(documentWith());
  });

  it("renders every group and item, with its meaning, tier and example", async () => {
    await renderPage();

    for (const name of ["Identity and status", "Phases", "Channeling"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("One line per phase, headed by the phase.")).toBeInTheDocument();
    for (const name of ["Shot id", "Shot time", "Rating", "Phase ramp rate", "Processing note"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
      expect(screen.getByText(`What ${name.toLowerCase()} means.`)).toBeInTheDocument();
    }
    expect(screen.getByText("54.6 s")).toBeInTheDocument();
    // A phase item is a line per phase, kept as lines.
    expect(screen.getByText(/phase 1 · fill: ramp 0.01 bar\/s GENTLE/)).toHaveClass(
      "whitespace-pre-line",
    );
    expect(
      within(screen.getByTestId("item-processing_note")).getByText("not on this shot"),
    ).toBeInTheDocument();

    expect(pressed("Shot time")).toEqual(["base (default)"]);
    expect(pressed("Phase ramp rate")).toEqual(["extended (default)"]);
    expect(pressed("Processing note")).toEqual(["excluded (default)"]);
  });

  it("keeps each group a list of its items at every width, named by the group", async () => {
    await renderPage();

    const list = screen.getByRole("list", { name: "Identity and status" });
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((row) => row.dataset.testid),
    ).toEqual(["item-shot_id", "item-shot_time", "item-rating"]);
    // No table whose rows a narrow layout could strip of their roles.
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("item-rating")).getByText("Example:")).toBeInTheDocument();
  });

  it("says exactly where an excluded item is left out", async () => {
    await renderPage();

    expect(screen.getByRole("list", { name: "The tiers" })).toHaveTextContent(
      "excluded: left out of the opening context, the shot tools, the search and the glossary (a General chat's SQL tool can still read the archive's views).",
    );
  });

  it("names the example shot and the estimates, with N linking to where it is set", async () => {
    await renderPage();

    const example = screen.getByTestId("example-shot");
    expect(within(example).getByRole("link", { name: "shot 204" })).toHaveAttribute(
      "href",
      "/shots/204",
    );
    expect(example).toHaveTextContent("judged");
    expect(example).not.toHaveTextContent("not judged yet");
    expect(screen.getByTestId("estimate-base")).toHaveTextContent("≈ 120 tokens");
    expect(screen.getByTestId("estimate-extended")).toHaveTextContent("≈ 2,900 tokens");
    expect(screen.getByTestId("estimate-glossary")).toHaveTextContent("≈ 3,100 tokens");
    expect(screen.getByTestId("estimate-autoload")).toHaveTextContent("≈ 2,400 tokens");
    expect(screen.getByRole("link", { name: /^20 shots/ })).toHaveAttribute(
      "href",
      "/settings/llm#chat",
    );
  });

  it("shows a locked item's tier with no control", async () => {
    await renderPage();

    const row = screen.getByTestId("item-shot_id");
    expect(within(row).queryByRole("group")).not.toBeInTheDocument();
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
    expect(within(row).getByTestId("locked-tier")).toHaveTextContent("base, locked");
  });

  it("saves a click at once, shows it pending, then takes the answered document", async () => {
    const user = setupUser();
    let answer: (document: ShotInformation) => void = () => undefined;
    putShotInformationTier.mockImplementation(
      () =>
        new Promise<ShotInformation>((resolve) => {
          answer = resolve;
        }),
    );
    await renderPage();

    await user.click(within(tierGroup("Rating")).getByRole("button", { name: "excluded" }));

    expect(putShotInformationTier).toHaveBeenCalledWith("rating", "excluded");
    expect(pressed("Rating")).toEqual(["excluded"]);
    expect(tierGroup("Rating")).toHaveAttribute("aria-busy", "true");
    expect(within(tierGroup("Rating")).getByRole("status")).toHaveTextContent("Saving");

    answer(documentWith({ rating: "excluded" }, { base_per_shot: 110, autoload: 2200 }));

    await waitFor(() => expect(screen.getByTestId("estimate-base")).toHaveTextContent("≈ 110"));
    expect(screen.getByTestId("estimate-autoload")).toHaveTextContent("≈ 2,200 tokens");
    expect(pressed("Rating")).toEqual(["excluded"]);
    expect(tierGroup("Rating")).toHaveAttribute("aria-busy", "false");
    expect(screen.getByText("1 item is not in the default tier.")).toBeInTheDocument();
  });

  it("runs the writes one at a time, in the order they were clicked", async () => {
    const user = setupUser();
    const answers: Array<(document: ShotInformation) => void> = [];
    putShotInformationTier.mockImplementation(
      () =>
        new Promise<ShotInformation>((resolve) => {
          answers.push(resolve);
        }),
    );
    await renderPage();

    await user.click(within(tierGroup("Rating")).getByRole("button", { name: "excluded" }));
    await user.click(within(tierGroup("Shot time")).getByRole("button", { name: "extended" }));

    // The second waits for the first: each answer is the whole document, so
    // the last to arrive must be the last one sent.
    expect(putShotInformationTier).toHaveBeenCalledTimes(1);
    expect(pressed("Shot time")).toEqual(["extended"]);
    answers[0]?.(documentWith({ rating: "excluded" }));
    await waitFor(() => expect(putShotInformationTier).toHaveBeenCalledTimes(2));
    expect(putShotInformationTier).toHaveBeenLastCalledWith("shot_time", "extended");
    answers[1]?.(documentWith({ rating: "excluded", shot_time: "extended" }));

    await waitFor(() => expect(tierGroup("Shot time")).toHaveAttribute("aria-busy", "false"));
    expect(pressed("Rating")).toEqual(["excluded"]);
    expect(pressed("Shot time")).toEqual(["extended"]);
  });

  it("puts the control back on the stored tier and says why when a save is refused", async () => {
    const user = setupUser();
    putShotInformationTier.mockRejectedValue(
      new ApiClientError("The database is locked", { status: 500, code: "INTERNAL_ERROR" }),
    );
    await renderPage();

    await user.click(within(tierGroup("Shot time")).getByRole("button", { name: "extended" }));

    expect(await within(tierGroup("Shot time")).findByRole("alert")).toHaveTextContent(
      "Not saved: The database is locked",
    );
    expect(pressed("Shot time")).toEqual(["base (default)"]);
    expect(screen.getByTestId("estimate-base")).toHaveTextContent("≈ 120 tokens");
  });

  it("does not send a click on the tier an item is already in", async () => {
    const user = setupUser();
    await renderPage();

    await user.click(within(tierGroup("Rating")).getByRole("button", { name: "base (default)" }));

    expect(putShotInformationTier).not.toHaveBeenCalled();
  });

  it("asks inline before a reset, and only then sends it", async () => {
    const user = setupUser();
    getShotInformation.mockResolvedValue(documentWith({ rating: "excluded", phase_ramp: "base" }));
    resetShotInformation.mockResolvedValue(documentWith());
    await renderPage();
    expect(screen.getByText("2 items are not in the default tier.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset to defaults" }));
    const confirm = screen.getByTestId("reset-confirm");
    expect(confirm).toHaveTextContent("The 2 items you moved go back");
    expect(resetShotInformation).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("reset-confirm")).not.toBeInTheDocument();
    expect(resetShotInformation).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Reset to defaults" }));
    await user.click(
      within(screen.getByTestId("reset-confirm")).getByRole("button", {
        name: "Reset to defaults",
      }),
    );

    expect(resetShotInformation).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(pressed("Rating")).toEqual(["base (default)"]));
    expect(pressed("Phase ramp rate")).toEqual(["extended (default)"]);
    expect(screen.getByText("Every item is in its default tier.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset to defaults" })).toBeDisabled();
  });

  it("says there is no shot yet and leaves the examples empty on an empty archive", async () => {
    getShotInformation.mockResolvedValue({
      ...documentWith({}, { base_per_shot: null, extended_per_shot: null, autoload: null }),
      example_shot: null,
      groups: documentWith().groups.map((group) => ({
        ...group,
        items: group.items.map((entry) => ({ ...entry, example: null })),
      })),
    });
    await renderPage();

    expect(screen.getByTestId("example-shot")).toHaveTextContent(
      "No shot in the archive yet, so there are no examples.",
    );
    expect(screen.queryByRole("link", { name: /^shot / })).not.toBeInTheDocument();
    expect(screen.queryByText("not on this shot")).not.toBeInTheDocument();
    expect(screen.getByTestId("estimate-base")).toHaveTextContent("—");
    expect(screen.getByTestId("estimate-autoload")).toHaveTextContent("—");
    expect(screen.getByTestId("estimate-glossary")).toHaveTextContent("≈ 3,100 tokens");
    // The tiers are still there to be chosen.
    expect(pressed("Rating")).toEqual(["base (default)"]);
  });
});
