import { screen, waitFor } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProfileVersionSummary } from "@/api/types";
import { designName, fillFromProfile, NewSetDialog } from "@/components/sets/NewSetDialog";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { bean, grinder, setRow, startingPointRun, version } from "@/test/setsFixtures";

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
  designSet,
  sendChatMessage,
} = vi.hoisted(() => ({
  getBeans: vi.fn(),
  getGrinders: vi.fn(),
  getMachines: vi.fn(),
  getProfileVersions: vi.fn(),
  createSet: vi.fn(),
  getSimilarSets: vi.fn(),
  createStartingPoint: vi.fn(),
  designSet: vi.fn(),
  sendChatMessage: vi.fn(),
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
  designSet,
  sendChatMessage,
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
  designSet.mockResolvedValue({
    set: setRow({ id: 6, name: "Ethiopia Guji on the Niche Zero", designing: true }),
    version: version({ id: 60, set_id: 6, profile_version_id: null, shot_count: 0 }),
    thread_id: 12,
  });
  sendChatMessage.mockResolvedValue({
    run: {
      id: 31,
      thread_id: 12,
      status: "running",
      provider: "anthropic",
      model: "claude",
      error: null,
      usage: null,
      tool_rounds: 0,
      tool_calls: 0,
      started_at: "2026-04-02T00:00:00.000Z",
      finished_at: null,
    },
    message: null,
  });
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
        automatch: true,
        version: expect.objectContaining({
          profile_version_id: 7,
          grind_setting: "22",
          dose_g: 18,
          target_yield_g: 36,
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

  it("says the picked coffee's taste scales that are set, and only those", async () => {
    getBeans.mockResolvedValue({ items: [bean({ acidity: 4, intensity: 2 })] });
    renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

    await pickBean();

    expect(screen.getByTestId("new-set-bean-facts")).toHaveTextContent(
      "light · natural · Ethiopia · acidity 4/5 · intensity 2/5",
    );
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

      const user = setupUser();
      const toggle = await screen.findByTestId("suggest-starting-point");
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      // The controlled region exists folded too, so `aria-controls` resolves.
      const region = screen.getByTestId("suggest-region");
      expect(toggle.getAttribute("aria-controls")).toBe(region.id);
      expect(region).not.toBeVisible();
      expect(region).toBeEmptyDOMElement();
      expect(screen.queryByTestId("starting-point-step")).not.toBeInTheDocument();
      // Folded, it costs nothing: not even the free similar-Set query runs.
      await screen.findByRole("option", { name: "Niche Zero" });
      await user.selectOptions(screen.getByLabelText("Grinder"), "1");
      expect(getSimilarSets).not.toHaveBeenCalled();
      expect(createStartingPoint).not.toHaveBeenCalled();

      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).toBe(region);
      expect(region).toBeVisible();
      expect(await screen.findByTestId("starting-point-step")).toBeInTheDocument();
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

  describe("the recipe fills in from the profile", () => {
    it("fills the target yield when a profile is picked", async () => {
      renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

      await pickProfile("7");

      expect(screen.getByLabelText("Target yield (g)")).toHaveValue("36");
      expect(screen.getByTestId("recipe-from-profile")).toHaveTextContent(
        "Target yield 36 g from 9 Bar Espresso.",
      );
    });

    it("replaces the number a previous profile filled when the profile changes", async () => {
      renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

      await pickProfile("7");
      await pickProfile("8");

      expect(screen.getByLabelText("Target yield (g)")).toHaveValue("38");
      expect(screen.getByTestId("recipe-from-profile")).toHaveTextContent("from Adaptive v2");
    });

    it("never overwrites a value typed by hand", async () => {
      const user = setupUser();
      renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

      await pickProfile("7");
      const yieldField = screen.getByLabelText("Target yield (g)");
      await user.clear(yieldField);
      await user.type(yieldField, "40");
      await pickProfile("8");

      // The typed yield stays, and the hint stops claiming it came from a profile.
      expect(screen.getByLabelText("Target yield (g)")).toHaveValue("40");
      expect(screen.queryByTestId("recipe-from-profile")).not.toBeInTheDocument();
    });

    it("never overwrites a value typed before any profile was picked", async () => {
      const user = setupUser();
      renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

      await user.type(await screen.findByLabelText("Target yield (g)"), "50");
      await pickProfile("7");

      expect(screen.getByLabelText("Target yield (g)")).toHaveValue("50");
    });

    it("shows the picked profile's temperature, read-only, and follows a new pick", async () => {
      renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

      // Nothing to type into: the machine brews at the profile's temperature.
      expect(screen.queryByRole("textbox", { name: "Temperature (°C)" })).not.toBeInTheDocument();
      // Read out with its own name, like every field beside it.
      const cell = () => screen.getByRole("group", { name: "Temperature (°C)" });
      await waitFor(() => expect(cell()).toHaveTextContent("—"));
      expect(screen.getByTestId("temperature-from-profile")).toHaveTextContent(
        "The brew temperature comes from the profile.",
      );

      await pickProfile("7");

      expect(cell()).toHaveTextContent("93 °C");
      expect(screen.getByTestId("temperature-from-profile")).toHaveTextContent(
        "The machine brews at the temperature 9 Bar Espresso states.",
      );
      expect(screen.getByTestId("temperature-from-profile")).toHaveTextContent(
        "edit it on the machine and pick the new version here, or draft one on the Profiles page",
      );

      // A second pick moves it: this number is the profile's, and switching
      // profiles is the only way it ever changes.
      await pickProfile("8");
      expect(cell()).toHaveTextContent("94 °C");
      expect(screen.getByTestId("temperature-from-profile")).toHaveTextContent("Adaptive v2");
    });

    it("sends no temperature when the Set is created", async () => {
      const user = setupUser();
      renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

      await pickBean();
      await pickProfile("7");
      await user.click(screen.getByRole("button", { name: "Start the Set" }));

      await waitFor(() => expect(createSet).toHaveBeenCalled());
      const body = createSet.mock.calls[0][0];
      // Not "is null": the key is gone from the API, and a body carrying it
      // would be refused by the model behind `POST /api/sets`.
      expect(Object.keys(body.version)).not.toContain("target_temperature_c");
      expect(JSON.stringify(body)).not.toContain("temperature");
      expect(body.version).toEqual(
        expect.objectContaining({ profile_version_id: 7, target_yield_g: 36 }),
      );
    });

    it("says so when the picked profile states no temperature", async () => {
      getProfileVersions.mockResolvedValue({
        items: [profileVersion({ id: 7, label: "Says nothing", temperature_c: null })],
        total: 1,
        limit: 200,
        offset: 0,
      });
      const user = setupUser();
      renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

      await screen.findByRole("option", { name: "Says nothing" });
      await user.selectOptions(screen.getByLabelText("Profile version"), "7");

      expect(screen.getByTestId("profile-temperature")).toHaveTextContent("—");
      expect(screen.getByTestId("temperature-from-profile")).toHaveTextContent(
        "Says nothing states no brew temperature",
      );
    });

    it("leaves the yield alone when the profile states none", async () => {
      renderWithQueryClient(<NewSetDialog open onOpenChange={() => {}} />);

      await pickProfile("7");
      await pickProfile("9");

      // No volumetric stop: the yield from the previous pick stays, and still
      // says where it came from.
      expect(screen.getByLabelText("Target yield (g)")).toHaveValue("36");
      expect(screen.getByTestId("recipe-from-profile")).toHaveTextContent(
        "Target yield 36 g from 9 Bar Espresso.",
      );
    });
  });
});

describe("fillFromProfile", () => {
  const empty = { targetYieldG: "" };
  const none = { targetYieldG: null };
  const nineBar = { label: "9 Bar", target_yield_g: 36 };

  it("fills an empty field and remembers what it filled", () => {
    expect(fillFromProfile(empty, none, nineBar)).toEqual({
      values: { targetYieldG: "36" },
      filled: { targetYieldG: { value: "36", from: "9 Bar" } },
    });
  });

  it("changes nothing for Any profile", () => {
    const draft = { targetYieldG: "36" };
    expect(fillFromProfile(draft, none, undefined)).toEqual({ values: draft, filled: none });
  });

  it("gives a decimal back as the profile wrote it", () => {
    const lever = { label: "Lever", target_yield_g: 36.5 };
    expect(fillFromProfile(empty, none, lever).values.targetYieldG).toBe("36.5");
  });
});

/** Where the dialog sent the person, for a test to read. */
function Location() {
  const location = useLocation();
  return <p data-testid="location">{`${location.pathname}${location.search}`}</p>;
}

/** The dialog on a page, beside a readout of where the router is. */
function onPage(props: Partial<React.ComponentProps<typeof NewSetDialog>> = {}) {
  return renderWithQueryClient(
    <Routes>
      <Route
        path="*"
        element={
          <>
            <NewSetDialog open onOpenChange={() => {}} {...props} />
            <Location />
          </>
        }
      />
    </Routes>,
    { initialEntries: ["/sets"] },
  );
}

async function pickGrinder() {
  const user = setupUser();
  await screen.findByRole("option", { name: "Niche Zero" });
  await user.selectOptions(screen.getByLabelText("Grinder"), "1");
}

async function openDesign() {
  const user = setupUser();
  await user.click(await screen.findByTestId("design-with-agent"));
  return screen.findByTestId("design-step");
}

describe("NewSetDialog, designing it with the agent", () => {
  it("is folded away until asked for, and mounts nothing folded", async () => {
    onPage({ initialBeanId: 1 });

    const toggle = await screen.findByTestId("design-with-agent");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const region = screen.getByTestId("design-region");
    expect(toggle.getAttribute("aria-controls")).toBe(region.id);
    expect(region).not.toBeVisible();
    expect(region).toBeEmptyDOMElement();
    expect(screen.queryByLabelText("What do you want from it? (optional)")).not.toBeInTheDocument();

    await openDesign();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(region).toBeVisible();
    expect(screen.getByLabelText("What do you want from it? (optional)")).toHaveAttribute(
      "maxlength",
      "2000",
    );
  });

  it("needs a grinder, and says what pre-ground coffee uses instead", async () => {
    onPage({ initialBeanId: 1 });
    await screen.findByRole("option", { name: /Ethiopia Guji/ });
    await openDesign();

    expect(screen.getByTestId("start-designing")).toBeDisabled();
    expect(screen.getByTestId("design-needs-grinder")).toHaveTextContent("Pick a grinder above");
    expect(screen.getByTestId("design-needs-grinder")).toHaveTextContent("pre-ground coffee");

    await pickGrinder();

    expect(screen.getByTestId("start-designing")).toBeEnabled();
    expect(screen.queryByTestId("design-needs-grinder")).not.toBeInTheDocument();
  });

  it("posts the form's own fields, sends the goal to the new thread and goes there", async () => {
    const user = setupUser();
    const onOpenChange = vi.fn();
    onPage({ onOpenChange });

    await pickBean();
    await pickGrinder();
    await pickProfile("7");
    // The form's recipe fields: typed, and not the design's to send.
    await user.type(screen.getByLabelText("Grind"), "22");
    await user.type(screen.getByLabelText("Dose (g)"), "18");
    await user.type(screen.getByLabelText("What are you trying? (optional)"), "baseline");
    await openDesign();
    expect(screen.getByTestId("design-fork")).toHaveTextContent("9 Bar Espresso");
    await user.type(screen.getByLabelText(/What do you normally grind espresso at/), "21");
    await user.type(
      screen.getByLabelText("What do you want from it? (optional)"),
      "more body, and try a bloom",
    );
    await user.click(screen.getByTestId("start-designing"));

    await waitFor(() => expect(designSet).toHaveBeenCalled());
    // Exactly these keys: the grind, dose, yield and intent above are what the
    // conversation works out, so none of them is sent.
    expect(designSet.mock.calls[0][0]).toEqual({
      bean_id: 1,
      grinder_id: 1,
      // Not typed, so the server names it "<bean> on the <grinder>".
      name: null,
      fork_profile_version_id: 7,
      usual_grind: "21",
      goal: "more body, and try a bloom",
    });
    await waitFor(() =>
      expect(sendChatMessage).toHaveBeenCalledWith(12, "more body, and try a bloom"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/chat?thread=12"),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(createSet).not.toHaveBeenCalled();
  });

  it("sends a typed name, and the fixed opener when there is no goal", async () => {
    const user = setupUser();
    onPage({ initialBeanId: 1 });

    await screen.findByRole("option", { name: /Ethiopia Guji/ });
    await user.type(screen.getByLabelText("Call it"), " bloom trial");
    await pickGrinder();
    await openDesign();
    await user.click(screen.getByTestId("start-designing"));

    await waitFor(() => expect(designSet).toHaveBeenCalled());
    expect(designSet.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        bean_id: 1,
        name: "Ethiopia Guji bloom trial",
        fork_profile_version_id: null,
        goal: "",
      }),
    );
    await waitFor(() =>
      expect(sendChatMessage).toHaveBeenCalledWith(12, "Help me design this Set."),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/chat?thread=12"),
    );
  });

  it("says the name the server will give it, and stops once a name is typed", async () => {
    const user = setupUser();
    onPage({ initialBeanId: 1 });

    await screen.findByRole("option", { name: /Ethiopia Guji/ });
    await openDesign();
    // No grinder yet: nothing to say, since the name is the bag and the grinder.
    expect(screen.queryByTestId("design-name")).not.toBeInTheDocument();

    await pickGrinder();
    // "Call it" shows the bag alone, which is the plain form's default; the
    // design is named after the grinder too, and the section says so.
    expect(screen.getByLabelText("Call it")).toHaveValue("Ethiopia Guji");
    expect(screen.getByTestId("design-name")).toHaveTextContent(
      "Called “Ethiopia Guji on the Niche Zero” unless you name it above.",
    );

    await user.type(screen.getByLabelText("Call it"), " bloom trial");
    expect(screen.queryByTestId("design-name")).not.toBeInTheDocument();
  });

  it("keeps the dialog and the draft when the server refuses", async () => {
    const user = setupUser();
    const onOpenChange = vi.fn();
    designSet.mockRejectedValueOnce(new Error("No such grinder"));
    onPage({ initialBeanId: 1, onOpenChange });

    await screen.findByRole("option", { name: /Ethiopia Guji/ });
    await pickGrinder();
    await openDesign();
    await user.type(screen.getByLabelText("What do you want from it? (optional)"), "more body");
    await user.click(screen.getByTestId("start-designing"));

    await waitFor(() => expect(designSet).toHaveBeenCalled());
    expect(sendChatMessage).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("location")).toHaveTextContent("/sets");
    expect(screen.getByLabelText("What do you want from it? (optional)")).toHaveValue("more body");
    expect(screen.getByLabelText("Grinder")).toHaveValue("1");
  });

  it("still goes to the conversation when the first message fails to send", async () => {
    const user = setupUser();
    const onOpenChange = vi.fn();
    sendChatMessage.mockRejectedValueOnce(new Error("No provider is configured"));
    onPage({ initialBeanId: 1, onOpenChange });

    await screen.findByRole("option", { name: /Ethiopia Guji/ });
    await pickGrinder();
    await openDesign();
    await user.click(screen.getByTestId("start-designing"));

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalled());
    // The Set and its conversation exist; the composer is where to try again.
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/chat?thread=12"),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

/** The name a design gets when none is typed: the server's `set_name`, said on screen. */
describe("designName", () => {
  it("trims the bean and the grinder, and keeps the spaces inside them", () => {
    expect(designName("  Ethiopia Guji  ", " Niche Zero ")).toBe("Ethiopia Guji on the Niche Zero");
  });

  it("caps the name at the 200 characters a Set's name may have", () => {
    const bean = "b".repeat(150);
    const grinder = "g".repeat(100);
    const name = designName(bean, grinder);

    expect(name).toHaveLength(200);
    expect(name).toBe(`${bean} on the ${grinder}`.slice(0, 200));
  });

  it("says nothing until both the bean and the grinder are picked", () => {
    expect(designName("Ethiopia Guji", undefined)).toBeNull();
    expect(designName(undefined, "Niche Zero")).toBeNull();
    expect(designName("  ", "Niche Zero")).toBeNull();
  });
});
