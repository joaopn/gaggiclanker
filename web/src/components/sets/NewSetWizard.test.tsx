import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NewSetWizard } from "@/components/sets/NewSetWizard";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { bean, grinder, setRow, startingPointRun } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  getBeans,
  getGrinders,
  getMachines,
  getProfileVersions,
  createSet,
  getSimilarSets,
  createStartingPoint,
} = vi.hoisted(() => ({
  getBeans: vi.fn(),
  getGrinders: vi.fn(),
  getMachines: vi.fn(),
  getProfileVersions: vi.fn(),
  createSet: vi.fn(),
  getSimilarSets: vi.fn(),
  createStartingPoint: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getBeans,
  getGrinders,
  getMachines,
  getProfileVersions,
  createSet,
  getSimilarSets,
  createStartingPoint,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getBeans.mockResolvedValue({ items: [bean()] });
  getGrinders.mockResolvedValue({ items: [grinder()] });
  getMachines.mockResolvedValue({
    items: [
      {
        machine: {
          id: 1,
          host: "kitchen.local",
          name: "kitchen",
          hardware_string: null,
          display_version: null,
          controller_version: null,
          has_pressure: true,
          has_dimming: false,
          has_gear_pump: false,
          has_led: false,
          temperature_offset_c: null,
          pid: null,
          brew_delay_ms: null,
          identity: null,
          settings: null,
          notes: "",
          first_seen_at: "2026-04-01T00:00:00.000Z",
          last_seen_at: "2026-04-01T00:00:00.000Z",
          created_at: "2026-04-01T00:00:00.000Z",
        },
        counts: {
          total: 10,
          quarantined: 0,
          deleted_on_device: 0,
          incomplete: 0,
          samples: 1000,
          needs_set: 0,
        },
      },
    ],
  });
  getProfileVersions.mockResolvedValue({
    items: [
      {
        id: 7,
        content_hash: "abc",
        label: "9 Bar Espresso",
        type: "standard",
        utility: false,
        source: "device",
        created_at: "2026-03-01T00:00:00.000Z",
        mirrored: true,
        shot_count: 12,
      },
    ],
    total: 1,
    limit: 200,
    offset: 0,
  });
  createSet.mockResolvedValue(setRow());
  getSimilarSets.mockResolvedValue({ bean_id: 1, grinder_id: 1, machine_id: 1, items: [] });
  createStartingPoint.mockResolvedValue(startingPointRun({ status: "running", output: null }));
});

/** The selects render before their queries answer, so the option is the signal. */
async function pickBean() {
  const user = setupUser();
  await screen.findByRole("option", { name: /Ethiopia Guji/ });
  await user.selectOptions(screen.getByLabelText("Bean"), "1");
}

/**
 * Walk past the shortcut and onto the manual path.
 *
 * Step 0 is the suggestion, and its "next" says what it does rather than
 * "Next" — a button labelled "Next" on a step whose whole point is that you may
 * skip it reads as "there is more of this".
 */
async function skipSuggestion() {
  const user = setupUser();
  await user.click(await screen.findByRole("button", { name: "Set it up by hand" }));
}

describe("NewSetWizard", () => {
  it("walks suggest, bean, hardware, profile, recipe", async () => {
    const user = setupUser();
    renderWithQueryClient(<NewSetWizard open onOpenChange={() => {}} />);

    expect(await screen.findByTestId("wizard-steps")).toHaveTextContent("1. Suggest");
    // The shortcut is never a gate: a shortcut you cannot walk past is a wall.
    await skipSuggestion();

    // The bean step cannot be left until a coffee is chosen: the Set's whole
    // identity starts there.
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await pickBean();
    // What the coffee is, not how old a bag of it is: a bean is a type.
    expect(screen.getByTestId("wizard-bean-facts")).toHaveTextContent("light");

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("option", { name: "Niche Zero" });
    await user.selectOptions(screen.getByLabelText("Grinder"), "1");

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("option", { name: "9 Bar Espresso" });
    await user.selectOptions(screen.getByLabelText("Profile version"), "7");

    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.type(await screen.findByLabelText("Grind"), "22");
    await user.type(screen.getByLabelText("Dose (g)"), "18");
    await user.type(screen.getByLabelText("Target yield (g)"), "36");

    await user.click(screen.getByRole("button", { name: "Start the Set" }));

    await waitFor(() => expect(createSet).toHaveBeenCalled());
    expect(createSet.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        bean_id: 1,
        machine_id: 1,
        grinder_id: 1,
        // A new Set is what the machine is set up for now, or it would collect
        // nothing and look broken.
        activate: true,
        version: expect.objectContaining({
          profile_version_id: 7,
          grind_setting: "22",
          dose_g: 18,
          target_yield_g: 36,
        }),
      }),
    );
  });

  it("names the Set after the bag unless told otherwise", async () => {
    renderWithQueryClient(<NewSetWizard open onOpenChange={() => {}} />);

    await skipSuggestion();
    await pickBean();

    expect(screen.getByLabelText("Call it")).toHaveValue("Ethiopia Guji");
  });

  it("carries a bag chosen on the shortcut step into the manual path", async () => {
    renderWithQueryClient(<NewSetWizard open onOpenChange={() => {}} />);

    // One `Draft` backs both paths, so changing your mind after asking for a
    // suggestion costs nothing.
    await pickBean();
    await skipSuggestion();

    expect(screen.getByLabelText("Bean")).toHaveValue("1");
    expect(screen.getByLabelText("Call it")).toHaveValue("Ethiopia Guji");
  });

  it("pre-selects the bag the Beans page shortcut named", async () => {
    renderWithQueryClient(<NewSetWizard open initialBeanId={1} onOpenChange={() => {}} />);

    await screen.findByRole("option", { name: /Ethiopia Guji/ });
    expect(screen.getByLabelText("Bean")).toHaveValue("1");
  });

  it("asks for suggestions without creating anything", async () => {
    const user = setupUser();
    const onCreated = vi.fn();
    renderWithQueryClient(
      <NewSetWizard open initialBeanId={1} onOpenChange={() => {}} onCreated={onCreated} />,
    );

    // The button is rendered disabled until the bag and the machine have both
    // resolved — a suggestion without the hardware could only be general.
    const ask = await screen.findByTestId("ask-for-suggestions");
    await waitFor(() => expect(ask).toBeEnabled());
    await user.click(ask);

    await waitFor(() => expect(createStartingPoint).toHaveBeenCalled());
    // Nothing exists until somebody takes one of the three: the wizard has not
    // created a Set and has not closed.
    expect(createSet).not.toHaveBeenCalled();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("hands the new Set's id to the caller so the page can open it", async () => {
    const user = setupUser();
    const onCreated = vi.fn();
    renderWithQueryClient(<NewSetWizard open onOpenChange={() => {}} onCreated={onCreated} />);

    await skipSuggestion();
    await pickBean();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Start the Set" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(3));
  });
});
