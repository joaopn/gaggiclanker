import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProfileVersionSummary } from "@/api/types";
import { NewSetDialog } from "@/components/sets/NewSetDialog";
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
    temperature_c: null,
    target_yield_g: null,
    ...overrides,
  };
}

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
      profileVersion({ id: 7, label: "9 Bar Espresso", temperature_c: 93, target_yield_g: 36 }),
      profileVersion({ id: 8, label: "Adaptive v2", temperature_c: 94, target_yield_g: 38 }),
      // A profile that heats the group but stops on nothing: no yield to offer.
      profileVersion({ id: 9, label: "Turbo by time", temperature_c: 90, target_yield_g: null }),
    ],
    total: 3,
    limit: 200,
    offset: 0,
  });
  createSet.mockResolvedValue(setRow());
  getSimilarSets.mockResolvedValue({ bean_id: 1, grinder_id: 1, items: [] });
  createStartingPoint.mockResolvedValue(startingPointRun({ status: "running", output: null }));
});

/** The selects render before their queries answer, so the option is the signal. */
async function pickBean() {
  const user = setupUser();
  await screen.findByRole("option", { name: /Ethiopia Guji/ });
  await user.selectOptions(screen.getByLabelText("Bean"), "1");
}

async function pickProfile(id: string) {
  const user = setupUser();
  await screen.findByRole("option", { name: "9 Bar Espresso" });
  await user.selectOptions(screen.getByLabelText("Profile version"), id);
}

describe("NewSetDialog", () => {
  it("starts a Set from one screen", async () => {
    const user = setupUser();
    renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

    // Every manual option is on the first screen: no steps, no Next.
    const form = await screen.findByTestId("new-set-form");
    for (const label of [
      "Bean",
      "Call it",
      "Grinder",
      "Profile version",
      "Grind",
      "Dose (g)",
      "Target yield (g)",
      "Temperature (°C)",
      "What are you trying? (optional)",
    ]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(form).toContainElement(screen.getByRole("button", { name: "Start the Set" }));

    // The bean is the one thing a Set cannot start without.
    expect(screen.getByRole("button", { name: "Start the Set" })).toBeDisabled();
    await pickBean();
    // What the coffee is, not how old a bag of it is: a bean is a type.
    expect(screen.getByTestId("new-set-bean-facts")).toHaveTextContent("light");

    await screen.findByRole("option", { name: "Niche Zero" });
    await user.selectOptions(screen.getByLabelText("Grinder"), "1");
    await pickProfile("7");
    await user.type(screen.getByLabelText("Grind"), "22");
    await user.type(screen.getByLabelText("Dose (g)"), "18");
    await user.type(screen.getByLabelText("Target yield (g)"), "36");
    await user.type(screen.getByLabelText("Temperature (°C)"), "93");
    await user.type(screen.getByLabelText("What are you trying? (optional)"), "baseline");

    await user.click(screen.getByRole("button", { name: "Start the Set" }));

    await waitFor(() => expect(createSet).toHaveBeenCalled());
    expect(createSet.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        name: "Ethiopia Guji",
        bean_id: 1,
        grinder_id: 1,
        // A new Set is what the machine is set up for now, or it would collect
        // nothing and look broken.
        activate: true,
        version: expect.objectContaining({
          profile_version_id: 7,
          grind_setting: "22",
          dose_g: 18,
          target_yield_g: 36,
          target_temperature_c: 93,
          intent: "baseline",
          origin: "manual",
        }),
      }),
    );
  });

  it("names the Set after the bag unless told otherwise", async () => {
    renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

    await pickBean();

    expect(screen.getByLabelText("Call it")).toHaveValue("Ethiopia Guji");
  });

  it("pre-selects the bag the Beans page shortcut named", async () => {
    renderWithQueryClient(<NewSetDialog open initialBeanId={1} onOpenChange={() => {}} />);

    await screen.findByRole("option", { name: /Ethiopia Guji/ });
    expect(screen.getByLabelText("Bean")).toHaveValue("1");
    expect(screen.getByRole("button", { name: "Start the Set" })).toBeEnabled();
  });

  it("keeps the dialog and the draft when the server refuses", async () => {
    const user = setupUser();
    const onOpenChange = vi.fn();
    createSet.mockRejectedValueOnce(new Error("no such bean"));
    renderWithQueryClient(<NewSetDialog open onOpenChange={onOpenChange} />);

    await pickBean();
    await user.type(screen.getByLabelText("Dose (g)"), "18");
    await user.click(screen.getByRole("button", { name: "Start the Set" }));

    await waitFor(() => expect(createSet).toHaveBeenCalled());
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Dose (g)")).toHaveValue("18");
  });

  it("hands the new Set's id to the caller so the page can open it", async () => {
    const user = setupUser();
    const onCreated = vi.fn();
    renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} onCreated={onCreated} />);

    await pickBean();
    await user.click(screen.getByRole("button", { name: "Start the Set" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(3));
  });

  describe("the starting-point shortcut", () => {
    it("is folded away until asked for", async () => {
      renderWithQueryClient(<NewSetDialog open initialBeanId={1} onOpenChange={() => {}} />);

      const toggle = await screen.findByTestId("suggest-starting-point");
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByTestId("ask-for-suggestions")).not.toBeInTheDocument();
      // Folded, it costs nothing: not even the free similar-Set query runs.
      expect(getSimilarSets).not.toHaveBeenCalled();
    });

    it("asks with the bean and grinder from the form, without creating anything", async () => {
      const user = setupUser();
      const onCreated = vi.fn();
      renderWithQueryClient(
        <NewSetDialog open initialBeanId={1} onOpenChange={() => {}} onCreated={onCreated} />,
      );

      await screen.findByRole("option", { name: "Niche Zero" });
      await user.selectOptions(screen.getByLabelText("Grinder"), "1");
      await user.click(screen.getByTestId("suggest-starting-point"));
      expect(screen.getByTestId("suggest-starting-point")).toHaveAttribute("aria-expanded", "true");

      // The button is rendered disabled until the bag has resolved — a
      // suggestion with nothing to anchor it could only be general.
      const ask = await screen.findByTestId("ask-for-suggestions");
      await waitFor(() => expect(ask).toBeEnabled());
      await user.click(ask);

      await waitFor(() => expect(createStartingPoint).toHaveBeenCalled());
      expect(createStartingPoint.mock.calls[0][0]).toEqual(
        expect.objectContaining({ bean_id: 1, grinder_id: 1 }),
      );
      // Nothing exists until somebody takes one of the three: the dialog has
      // not created a Set and has not closed.
      expect(createSet).not.toHaveBeenCalled();
      expect(onCreated).not.toHaveBeenCalled();
    });
  });
});
