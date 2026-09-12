import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HardwarePage } from "@/pages/HardwarePage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { grinder, vocabulary } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getGrinders, createGrinder, updateGrinder, getMachines, patchMachine, getVocabulary } =
  vi.hoisted(() => ({
    getGrinders: vi.fn(),
    createGrinder: vi.fn(),
    updateGrinder: vi.fn(),
    getMachines: vi.fn(),
    patchMachine: vi.fn(),
    getVocabulary: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getGrinders,
  createGrinder,
  updateGrinder,
  getMachines,
  patchMachine,
  getVocabulary,
}));

const machine = {
  id: 1,
  host: "kitchen.local",
  name: "",
  hardware_string: "GaggiMate Pro",
  display_version: "1.4.2",
  controller_version: "1.4.2",
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
};

beforeEach(() => {
  vi.clearAllMocks();
  getGrinders.mockResolvedValue({ items: [grinder()] });
  getVocabulary.mockResolvedValue(vocabulary);
  createGrinder.mockResolvedValue(grinder({ id: 2, name: "DF64" }));
  updateGrinder.mockResolvedValue(grinder({ name: "Niche Zero" }));
  patchMachine.mockResolvedValue({ ...machine, name: "the kitchen one" });
  getMachines.mockResolvedValue({
    items: [
      {
        machine,
        counts: {
          total: 120,
          quarantined: 0,
          deleted_on_device: 0,
          incomplete: 0,
          samples: 14_000,
          needs_set: 0,
        },
      },
    ],
  });
});

describe("HardwarePage", () => {
  it("lists a grinder with the two facts advice is given in", async () => {
    renderWithQueryClient(<HardwarePage />);

    const list = await screen.findByTestId("grinder-list");
    expect(list).toHaveTextContent("Niche Zero");
    // Burrs decide the profile; the step unit is what stops the analyser
    // inventing a scale.
    expect(list).toHaveTextContent("conical burrs");
    expect(list).toHaveTextContent("steps in numbers");
  });

  it("records a grinder from the served vocabularies", async () => {
    const user = setupUser();
    renderWithQueryClient(<HardwarePage />);

    await user.click(await screen.findByRole("button", { name: /Add a grinder/ }));
    // Scoped to the form: the machine card below has a "Name" field too.
    const form = await screen.findByTestId("grinder-form");
    await user.type(within(form).getByLabelText("Name"), "DF64");
    await screen.findByRole("option", { name: "flat" });
    await user.selectOptions(within(form).getByLabelText("Burrs"), "flat");
    await user.selectOptions(within(form).getByLabelText("Steps are"), "microns");
    await user.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(createGrinder).toHaveBeenCalled());
    expect(createGrinder.mock.calls[0][0]).toEqual(
      expect.objectContaining({ name: "DF64", burr_type: "flat", step_unit: "microns" }),
    );
  });

  it("shows the machine's own account of itself, and says it is not editable", async () => {
    renderWithQueryClient(<HardwarePage />);

    const list = await screen.findByTestId("machine-list");
    expect(list).toHaveTextContent("GaggiMate Pro");
    expect(list).toHaveTextContent("pressure sensor");
    expect(list).toHaveTextContent(/rewritten by the next sync pass/);
  });

  it("sends only the name and the notes", async () => {
    const user = setupUser();
    renderWithQueryClient(<HardwarePage />);

    const form = await screen.findByTestId("machine-form");
    await user.type(within(form).getByLabelText("Name"), "the kitchen one");
    await user.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchMachine).toHaveBeenCalled());
    expect(patchMachine.mock.calls[0].slice(0, 2)).toEqual([
      1,
      { name: "the kitchen one", notes: "" },
    ]);
  });

  it("explains an empty machine list rather than showing nothing", async () => {
    getMachines.mockResolvedValue({ items: [] });
    renderWithQueryClient(<HardwarePage />);

    expect(await screen.findByText("No machine has been seen")).toBeInTheDocument();
  });
});
