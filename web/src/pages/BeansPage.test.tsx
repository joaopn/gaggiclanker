import { screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { BeansPage, beanExport, beanFileName, readText } from "@/pages/BeansPage";
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
  deleteBean,
  getVocabulary,
  getGrinders,
  getMachines,
  getProfileVersions,
  getSimilarSets,
  importBean,
} = vi.hoisted(() => ({
  getBeans: vi.fn(),
  createBean: vi.fn(),
  updateBean: vi.fn(),
  setBeanArchived: vi.fn(),
  deleteBean: vi.fn(),
  getVocabulary: vi.fn(),
  getGrinders: vi.fn(),
  getMachines: vi.fn(),
  getProfileVersions: vi.fn(),
  getSimilarSets: vi.fn(),
  importBean: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getBeans,
  createBean,
  updateBean,
  setBeanArchived,
  deleteBean,
  getVocabulary,
  getGrinders,
  getMachines,
  getProfileVersions,
  getSimilarSets,
  importBean,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getBeans.mockResolvedValue({ items: [bean()] });
  getVocabulary.mockResolvedValue(vocabulary);
  createBean.mockResolvedValue(bean({ id: 2, name: "Kenya Kiambu" }));
  updateBean.mockResolvedValue(bean({ name: "Kenya Kiambu" }));
  setBeanArchived.mockResolvedValue(bean({ archived: true }));
  deleteBean.mockResolvedValue({ deleted: true });
  // The "Start a Set" shortcut mounts the New Set dialog, which asks for these.
  getGrinders.mockResolvedValue({ items: [grinder()] });
  getMachines.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
  getSimilarSets.mockResolvedValue({ bean_id: 1, grinder_id: null, items: [] });
});

describe("BeansPage", () => {
  it("describes the coffee and nothing about a bag of it", async () => {
    // A bean is a type: the roaster, the origin, the process and the roast
    // level stay true of every bag you ever buy of it. Nothing here ages.
    renderWithQueryClient(<BeansPage />);

    await screen.findByTestId("bean-list");
    expect(
      screen.getByText("Every coffee you have brewed: roaster, origin, process and roast level."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("freshness-pill")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Roast date")).not.toBeInTheDocument();
  });

  it("has no roast date on the form", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: /Add a coffee/ }));

    await screen.findByTestId("bean-form");
    expect(screen.queryByLabelText("Roast date")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Roast level")).toBeInTheDocument();
  });

  it("has a multi-line description, decaf among the fields, and no variety", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: /Add a coffee/ }));

    const form = await screen.findByTestId("bean-form");
    expect(within(form).queryByLabelText("Variety")).not.toBeInTheDocument();
    expect(within(form).queryByText(/What the bag claims/)).not.toBeInTheDocument();
    const description = within(form).getByLabelText("Description");
    expect(description.tagName).toBe("TEXTAREA");
    expect(description).toHaveAttribute("rows", "3");
    // Decaf is a field like the others: in the grid, with its label above it.
    const decaf = within(form).getByLabelText("Decaf");
    expect(decaf).toHaveAttribute("type", "checkbox");
    expect(decaf.closest(".grid")).toBe(within(form).getByLabelText("Name").closest(".grid"));

    await user.type(await screen.findByLabelText("Name"), "Kenya Kiambu");
    await user.type(description, "Blackcurrant and tomato.{Enter}Washed at Kiambu.");
    await user.click(decaf);
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(createBean).toHaveBeenCalled());
    expect(createBean.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        description: "Blackcurrant and tomato.\nWashed at Kiambu.",
        decaf: true,
      }),
    );
    expect(createBean.mock.calls[0][0]).not.toHaveProperty("variety");
  });

  it("shows the description on the card as written", async () => {
    getBeans.mockResolvedValue({
      items: [bean({ description: "Peach and jasmine.\nFrom the Guji zone." })],
    });
    renderWithQueryClient(<BeansPage />);

    const text = await screen.findByText(/Peach and jasmine\./);
    expect(text).toHaveClass("whitespace-pre-line");
    expect(text.textContent).toBe("Peach and jasmine.\nFrom the Guji zone.");
    expect(screen.queryByText(/The bag says/)).not.toBeInTheDocument();
  });

  it("shows the taste scales that were filled in on the card, and only those", async () => {
    getBeans.mockResolvedValue({ items: [bean({ acidity: 4, sweetness: 2 })] });
    renderWithQueryClient(<BeansPage />);

    const list = await screen.findByTestId("bean-list");
    expect(within(list).getByText("acidity 4/5")).toBeInTheDocument();
    expect(within(list).getByText("sweetness 2/5")).toBeInTheDocument();
    expect(within(list).queryByText(/intensity/)).not.toBeInTheDocument();
  });

  it("records acidity, intensity and sweetness by clicking a 5-point scale", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: /Add a coffee/ }));
    const form = await screen.findByTestId("bean-form");
    for (const name of ["Acidity", "Intensity", "Sweetness"]) {
      expect(within(form).getByRole("group", { name })).toBeInTheDocument();
    }
    await user.type(within(form).getByLabelText("Name"), "Kenya Kiambu");
    await user.click(within(form).getByRole("button", { name: "Acidity 5 of 5" }));
    await user.click(within(form).getByRole("button", { name: "Intensity 2 of 5" }));
    // A second click on the chosen step takes it back to not stated.
    await user.click(within(form).getByRole("button", { name: "Clear the intensity" }));
    await user.click(within(form).getByRole("button", { name: "Sweetness 3 of 5" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(createBean).toHaveBeenCalled());
    expect(createBean.mock.calls[0][0]).toEqual(
      expect.objectContaining({ acidity: 5, intensity: null, sweetness: 3 }),
    );
  });

  it("edits a coffee's scales from what it has, and sends every one back", async () => {
    getBeans.mockResolvedValue({ items: [bean({ acidity: 3, intensity: 4 })] });
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Edit Ethiopia Guji" }));
    const form = await screen.findByTestId("bean-form");
    const acidity = within(form).getByRole("group", { name: "Acidity" });
    expect(within(acidity).getByRole("button", { pressed: true })).toHaveTextContent("3");
    await user.click(within(form).getByRole("button", { name: "Clear the acidity" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateBean).toHaveBeenCalled());
    // A whole-object PUT: the untouched intensity must go back, or it is cleared.
    expect(updateBean.mock.calls[0][1]).toEqual(
      expect.objectContaining({ acidity: null, intensity: 4, sweetness: null }),
    );
  });

  it("suggests roasters and origins already recorded, archived coffees included", async () => {
    getBeans.mockImplementation(async (includeArchived: boolean) => ({
      items: includeArchived
        ? [bean(), bean({ id: 7, roaster: "Assembly", origin: "Kenya", archived: true })]
        : [bean()],
    }));
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: /Add a coffee/ }));
    await user.type(await screen.findByLabelText("Name"), "Kenya Kiambu");

    // The mouse: type, click the suggestion.
    const roaster = screen.getByRole("combobox", { name: "Roaster" });
    await user.type(roaster, "ass");
    await user.click(await screen.findByRole("option", { name: "Assembly" }));
    expect(roaster).toHaveValue("Assembly");

    // The keyboard: type, arrow down, Enter picks it and does not submit.
    const origin = screen.getByRole("combobox", { name: "Origin" });
    await user.type(origin, "ken");
    expect(screen.getByRole("option", { name: "Kenya" })).toBeInTheDocument();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(origin).toHaveValue("Kenya");
    expect(createBean).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(createBean).toHaveBeenCalled());
    expect(createBean.mock.calls[0][0]).toEqual(
      expect.objectContaining({ roaster: "Assembly", origin: "Kenya" }),
    );
  });

  it("saves a roaster nobody has recorded yet exactly as typed", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: /Add a coffee/ }));
    await user.type(await screen.findByLabelText("Name"), "Kenya Kiambu");
    await user.type(screen.getByRole("combobox", { name: "Roaster" }), "Has Bean Coffee");
    // Enter with no suggestion highlighted submits the form, as it always did.
    await user.keyboard("{Enter}");

    await waitFor(() => expect(createBean).toHaveBeenCalled());
    expect(createBean.mock.calls[0][0]).toEqual(
      expect.objectContaining({ roaster: "Has Bean Coffee" }),
    );
  });

  it("records a coffee with the vocabularies the server serves", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: /Add a coffee/ }));
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

  it("exports a coffee as a JSON file of its id and the fields the form edits", async () => {
    const created: Blob[] = [];
    URL.createObjectURL = vi.fn((blob: Blob) => {
      created.push(blob);
      return "blob:bean";
    });
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    getBeans.mockResolvedValue({ items: [bean({ acidity: 4, archived: true, set_count: 3 })] });
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Export Ethiopia Guji as JSON" }));

    expect(click).toHaveBeenCalledTimes(1);
    expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe(
      "ethiopia-guji.json",
    );
    expect(JSON.parse(await readText(created[0]))).toEqual({
      id: 1,
      name: "Ethiopia Guji",
      roaster: "Hasbean",
      origin: "Ethiopia",
      process: "natural",
      roast_level: "light",
      decaf: false,
      acidity: 4,
      intensity: null,
      sweetness: null,
      description: "blueberry, jasmine",
      notes: "",
    });
    click.mockRestore();
  });

  it("names the file after the coffee, falling back to the id", () => {
    expect(beanFileName(bean({ name: "Café São Paulo #2" }))).toBe("cafe-sao-paulo-2.json");
    expect(beanFileName(bean({ id: 7, name: "☕" }))).toBe("bean-7.json");
    expect(Object.keys(beanExport(bean()))).not.toContain("set_count");
  });

  it("imports a JSON file as read and says whether it added or updated", async () => {
    importBean.mockResolvedValue({ bean: bean({ id: 9, name: "Kenya AA" }), created: true });
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);
    await screen.findByTestId("bean-list");
    const file = new File(['{"name": "Kenya AA", "process": "washed"}'], "kenya.json", {
      type: "application/json",
    });

    await user.upload(screen.getByTestId("bean-import-input"), file);

    await waitFor(() => expect(importBean).toHaveBeenCalledTimes(1));
    expect(importBean.mock.calls[0][0]).toEqual({ name: "Kenya AA", process: "washed" });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added Kenya AA"));
    // The list is read again, so the new coffee shows.
    await waitFor(() => expect(getBeans.mock.calls.length).toBeGreaterThan(1));
  });

  it("says Updated when the file's id named an existing coffee", async () => {
    importBean.mockResolvedValue({ bean: bean(), created: false });
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);
    await screen.findByTestId("bean-list");

    await user.upload(
      screen.getByTestId("bean-import-input"),
      new File(['{"id": 1, "notes": "x"}'], "guji.json", { type: "application/json" }),
    );

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Updated Ethiopia Guji"));
  });

  it("refuses a file that is not one JSON object without calling the server", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);
    await screen.findByTestId("bean-list");
    const input = screen.getByTestId("bean-import-input");

    await user.upload(input, new File(["{nope"], "broken.json", { type: "application/json" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Could not import broken.json: it is not valid JSON",
      ),
    );
    await user.upload(input, new File(["[{}]"], "list.json", { type: "application/json" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Could not import list.json: the file must hold one coffee",
      ),
    );
    expect(importBean).not.toHaveBeenCalled();
  });

  it("names the file and the field the server refused", async () => {
    importBean.mockRejectedValue(
      new ApiClientError("Validation failed for BeanWrite", {
        status: 422,
        details: [{ field: "process", message: "Input should be 'washed'", type: "enum" }],
      }),
    );
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);
    await screen.findByTestId("bean-list");

    await user.upload(
      screen.getByTestId("bean-import-input"),
      new File(['{"name": "x", "process": "boiled"}'], "x.json", { type: "application/json" }),
    );

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Could not import x.json: Validation failed for BeanWrite (process: Input should be 'washed')",
      ),
    );
  });

  it("archives a coffee you have stopped buying rather than deleting it", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Archive Ethiopia Guji" }));

    await waitFor(() => expect(setBeanArchived).toHaveBeenCalled());
    expect(setBeanArchived.mock.calls[0].slice(0, 2)).toEqual([1, true]);
  });

  it("deletes a coffee nobody used, after asking", async () => {
    getBeans.mockResolvedValue({ items: [bean({ set_count: 0 })] });
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Delete Ethiopia Guji" }));
    const confirm = screen.getByTestId("bean-delete-confirm");
    expect(confirm).toHaveTextContent("Delete Ethiopia Guji? This cannot be undone.");
    expect(deleteBean).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteBean).toHaveBeenCalled());
    expect(deleteBean.mock.calls[0][0]).toBe(1);
    await waitFor(() =>
      expect(screen.queryByTestId("bean-delete-confirm")).not.toBeInTheDocument(),
    );
  });

  it("does not delete when the question is cancelled", async () => {
    getBeans.mockResolvedValue({ items: [bean({ set_count: 0 })] });
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Delete Ethiopia Guji" }));
    await user.click(
      within(screen.getByTestId("bean-delete-confirm")).getByRole("button", { name: "Cancel" }),
    );

    expect(screen.queryByTestId("bean-delete-confirm")).not.toBeInTheDocument();
    expect(deleteBean).not.toHaveBeenCalled();
  });

  it("says why a coffee a Set uses cannot be deleted, and offers archiving", async () => {
    getBeans.mockResolvedValue({ items: [bean({ set_count: 2 })] });
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Delete Ethiopia Guji" }));
    const confirm = screen.getByTestId("bean-delete-confirm");

    expect(confirm).toHaveTextContent("2 Sets use this coffee, so it cannot be deleted.");
    expect(within(confirm).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Archive" }));

    await waitFor(() => expect(setBeanArchived).toHaveBeenCalled());
    expect(setBeanArchived.mock.calls[0].slice(0, 2)).toEqual([1, true]);
    expect(deleteBean).not.toHaveBeenCalled();
  });

  it("closes the form when the coffee it is editing is deleted", async () => {
    getBeans.mockResolvedValue({ items: [bean({ set_count: 0 })] });
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Edit Ethiopia Guji" }));
    expect(screen.getByTestId("bean-form")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete Ethiopia Guji" }));
    await user.click(
      within(screen.getByTestId("bean-delete-confirm")).getByRole("button", { name: "Delete" }),
    );

    await waitFor(() => expect(screen.queryByTestId("bean-form")).not.toBeInTheDocument());
  });

  it("opens the New Set dialog on the coffee you pressed it from", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Start a Set from Ethiopia Guji" }));

    // Pre-selected, because the whole point of the shortcut is not having to
    // find the coffee you are looking at in a picker.
    const dialog = await screen.findByTestId("new-set-dialog");
    expect(dialog).toHaveTextContent("Start the Set");
    await waitFor(() => expect(screen.getByLabelText("Bean")).toHaveValue("1"));
  });

  it("offers designing it with the agent from the same dialog, on that coffee", async () => {
    const user = setupUser();
    renderWithQueryClient(<BeansPage />);

    await user.click(await screen.findByRole("button", { name: "Start a Set from Ethiopia Guji" }));
    await waitFor(() => expect(screen.getByLabelText("Bean")).toHaveValue("1"));
    await user.click(screen.getByTestId("design-with-agent"));

    // The bag is picked; the grinder is the one thing left before it can start.
    expect(await screen.findByTestId("design-needs-grinder")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Grinder"), "1");
    expect(screen.getByTestId("start-designing")).toBeEnabled();
  });

  it("does not offer the shortcut on an archived coffee", async () => {
    // Archiving is how a coffee you have stopped buying leaves the pickers,
    // and a shortcut that put it back in one would be the single path around
    // that.
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
