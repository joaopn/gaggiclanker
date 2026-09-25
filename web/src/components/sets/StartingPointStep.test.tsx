import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProfileVersionSummary } from "@/api/types";
import { StartingPointStep } from "@/components/sets/StartingPointStep";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { setRow, similarSet, startingPointOption, startingPointRun } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  getSimilarSets,
  createStartingPoint,
  getStartingPoint,
  acceptStartingPoint,
  getProfileVersions,
} = vi.hoisted(() => ({
  getSimilarSets: vi.fn(),
  createStartingPoint: vi.fn(),
  getStartingPoint: vi.fn(),
  acceptStartingPoint: vi.fn(),
  getProfileVersions: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSimilarSets,
  createStartingPoint,
  getStartingPoint,
  acceptStartingPoint,
  getProfileVersions,
}));

function profileVersion(overrides: Partial<ProfileVersionSummary>): ProfileVersionSummary {
  return {
    id: 7,
    content_hash: "abc",
    label: "9 Bar Espresso",
    type: "standard",
    utility: false,
    source: "device",
    created_at: "2026-03-01T00:00:00.000Z",
    mirrored: true,
    shot_count: 12,
    temperature_c: 93,
    target_yield_g: 36,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  getSimilarSets.mockResolvedValue({
    bean_id: 1,
    grinder_id: 1,
    items: [similarSet()],
  });
  createStartingPoint.mockResolvedValue(startingPointRun({ status: "running", output: null }));
  getStartingPoint.mockResolvedValue(startingPointRun());
  getProfileVersions.mockResolvedValue({
    items: [profileVersion({ id: 7, label: "9 Bar Espresso", temperature_c: 93 })],
    total: 1,
    limit: 200,
    offset: 0,
  });
  acceptStartingPoint.mockResolvedValue({
    run: startingPointRun({ accepted_option: "recommended", accepted_set_id: 3 }),
    set: setRow(),
    version: { id: 21 },
    draft: null,
  });
});

function render(props: Partial<Parameters<typeof StartingPointStep>[0]> = {}) {
  return renderWithQueryClient(
    <StartingPointStep
      beanId={1}
      grinderId={1}
      grindUnit="numbers"
      usualGrind="22"
      doseHint="18"
      onUsualGrindChange={() => {}}
      runId={undefined}
      onRunStarted={() => {}}
      onAccepted={() => {}}
      {...props}
    />,
  );
}

describe("StartingPointStep", () => {
  it("says nothing about a match neither bean recorded", async () => {
    getSimilarSets.mockResolvedValue({
      bean_id: 1,
      grinder_id: 1,
      items: [similarSet({ roast_match: "unknown", process_match: null, origin_match: true })],
    });
    render();
    const card = await screen.findByTestId("similar-set-card");
    expect(card).toHaveTextContent("same origin");
    expect(card).not.toHaveTextContent(/roast|process/);
  });

  it("shows what the archive already knows before anything is asked", async () => {
    render();
    const card = await screen.findByTestId("similar-set-card");
    expect(card).toHaveTextContent("Kenya AA");
    // The card has to say *why* it is here, or a reader has no way to weigh it.
    expect(card).toHaveTextContent("same roast, same process, same origin");
    expect(card).toHaveTextContent("grind 21");
    expect(card).toHaveTextContent("5 shots");
    expect(createStartingPoint).not.toHaveBeenCalled();
  });

  it("says why there is nothing to show when no grinder is chosen", async () => {
    getSimilarSets.mockResolvedValue({ bean_id: 1, grinder_id: null, items: [] });
    render({ grinderId: null });
    expect(await screen.findByTestId("similar-sets-empty")).toHaveTextContent(
      /grind number from a different grinder/i,
    );
  });

  it("asks for suggestions with the hardware and the usual grind", async () => {
    const user = setupUser();
    const onRunStarted = vi.fn();
    render({ onRunStarted });

    await user.click(await screen.findByTestId("ask-for-suggestions"));

    await waitFor(() => expect(createStartingPoint).toHaveBeenCalled());
    expect(createStartingPoint.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        bean_id: 1,
        grinder_id: 1,
        usual_grind: "22",
        dose_hint_g: 18,
      }),
    );
    // The row is the handle: the caller holds the id and the step follows it.
    await waitFor(() => expect(onRunStarted).toHaveBeenCalledWith(11));
  });

  it("renders the three options once the run is done", async () => {
    render({ runId: 11 });
    const cards = await screen.findAllByTestId("starting-point-option");
    expect(cards.map((card) => card.dataset.option)).toEqual([
      "conservative",
      "recommended",
      "adventurous",
    ]);
    // No rest note: there is no roast date to date a rest window from.
    expect(screen.queryByTestId("starting-point-rest")).not.toBeInTheDocument();
    expect(cards[1]).toHaveTextContent("45 g (1:2.5)");
    expect(cards[1]).toHaveTextContent("94 °C");
    // The citation line names the Set it leaned on by bean, not by row id.
    expect(cards[1]).toHaveTextContent("from Kenya AA");
  });

  it("marks a grind figure that is not a number on this dial", async () => {
    getStartingPoint.mockResolvedValue(
      startingPointRun({
        output: {
          options: [
            startingPointOption({ option: "conservative" }),
            startingPointOption({
              grind_setting: "a little finer than your usual espresso setting",
              grind_is_absolute: false,
            }),
            startingPointOption({ option: "adventurous" }),
          ],
        },
      }),
    );
    render({ runId: 11 });
    // The one thing on this card that must never be read as a dial position.
    expect(await screen.findByTestId("grind-relative")).toHaveTextContent(/relative/i);
  });

  it("says a hotter option will stage a draft of the profile it picked", async () => {
    // The option brews the library profile one degree hotter than the document
    // does, and accepting redrafts it — a draft appearing unannounced would
    // look like the app writing to the machine.
    getStartingPoint.mockResolvedValue(
      startingPointRun({
        output: {
          options: [
            startingPointOption({
              option: "conservative",
              profile_version_id: 7,
              temperature_c: 94,
            }),
            startingPointOption({ profile_version_id: 7, temperature_c: 93 }),
            startingPointOption({ option: "adventurous" }),
          ],
        },
      }),
    );
    render({ runId: 11 });

    const staged = await screen.findByTestId("option-stages-draft");
    expect(staged).toHaveTextContent("It brews at 93 °C");
    expect(staged).toHaveTextContent("stages a draft of it at 94 °C");
    expect(staged).toHaveTextContent("Nothing is sent to the machine");
    // Exactly one card says it: the second option agrees with the profile, and
    // the third names no profile at all.
    expect(screen.getAllByTestId("option-stages-draft")).toHaveLength(1);
  });

  it("says a draft is coming when the profile states no temperature at all", async () => {
    // The firmware's 0 is "not set". Taking the option redrafts the profile at
    // the suggested temperature, exactly as it would for a profile that states
    // a different one, so the card has to say so here too.
    getProfileVersions.mockResolvedValue({
      items: [profileVersion({ id: 7, label: "Says nothing", temperature_c: null })],
      total: 1,
      limit: 200,
      offset: 0,
    });
    getStartingPoint.mockResolvedValue(
      startingPointRun({
        output: {
          options: [
            startingPointOption({
              option: "conservative",
              profile_version_id: 7,
              temperature_c: 94,
            }),
            startingPointOption({ option: "recommended" }),
            startingPointOption({ option: "adventurous" }),
          ],
        },
      }),
    );
    render({ runId: 11 });

    const staged = await screen.findByTestId("option-stages-draft");
    expect(staged).toHaveTextContent("It states no brew temperature");
    expect(staged).toHaveTextContent("stages a draft of it at 94 °C");
  });

  it("says when an option would create a draft rather than reuse a profile", async () => {
    getStartingPoint.mockResolvedValue(
      startingPointRun({
        output: {
          options: [
            startingPointOption({ option: "conservative", profile_version_id: 7 }),
            startingPointOption({ profile: { label: "New", type: "pro", phases: [] } }),
            startingPointOption({ option: "adventurous" }),
          ],
        },
      }),
    );
    render({ runId: 11 });
    const notes = await screen.findAllByTestId("option-profile");
    expect(notes[0]).toHaveTextContent("the one you already have (9 Bar Espresso)");
    expect(notes[1]).toHaveTextContent("a new draft");
    expect(notes[2]).toHaveTextContent("whatever is selected on the machine");
  });

  it("hands the caller the Set and the draft when an option is taken", async () => {
    const user = setupUser();
    const onAccepted = vi.fn();
    acceptStartingPoint.mockResolvedValue({
      run: startingPointRun({ accepted_option: "recommended" }),
      set: setRow({ id: 9 }),
      version: { id: 21 },
      draft: { id: 4 },
    });
    render({ runId: 11, onAccepted });

    const cards = await screen.findAllByTestId("starting-point-option");
    await user.click(cards[1].querySelector("button") as HTMLButtonElement);

    await waitFor(() => expect(acceptStartingPoint).toHaveBeenCalledWith(11, "recommended"));
    await waitFor(() =>
      expect(onAccepted).toHaveBeenCalledWith(expect.objectContaining({ setId: 9, draftId: 4 })),
    );
  });

  it("shows a failed run's error rather than an empty panel", async () => {
    getStartingPoint.mockResolvedValue(
      startingPointRun({ status: "failed", output: null, error: "auth: bad key" }),
    );
    render({ runId: 11 });
    expect(await screen.findByTestId("starting-point-error")).toHaveTextContent("auth: bad key");
  });
});
