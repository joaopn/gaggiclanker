import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NewSetWizard } from "@/components/sets/NewSetWizard";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { bean, grinder, setRow } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getBeans, getGrinders, getMachines, getProfileVersions, createSet } = vi.hoisted(() => ({
  getBeans: vi.fn(),
  getGrinders: vi.fn(),
  getMachines: vi.fn(),
  getProfileVersions: vi.fn(),
  createSet: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getBeans,
  getGrinders,
  getMachines,
  getProfileVersions,
  createSet,
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
});

/** The selects render before their queries answer, so the option is the signal. */
async function pickBean() {
  const user = setupUser();
  await screen.findByRole("option", { name: /Ethiopia Guji/ });
  await user.selectOptions(screen.getByLabelText("Bean"), "1");
}

describe("NewSetWizard", () => {
  it("walks bean, hardware, profile, recipe", async () => {
    const user = setupUser();
    renderWithQueryClient(<NewSetWizard open onOpenChange={() => {}} />);

    expect(await screen.findByTestId("wizard-steps")).toHaveTextContent("1. Bean");

    // Step one cannot be left until a bag is chosen: the Set's whole identity
    // starts there.
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await pickBean();
    expect(screen.getByTestId("wizard-freshness")).toHaveTextContent("light");

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

    await pickBean();

    expect(screen.getByLabelText("Call it")).toHaveValue("Ethiopia Guji");
  });

  it("hands the new Set's id to the caller so the page can open it", async () => {
    const user = setupUser();
    const onCreated = vi.fn();
    renderWithQueryClient(<NewSetWizard open onOpenChange={() => {}} onCreated={onCreated} />);

    await pickBean();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Start the Set" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(3));
  });
});
