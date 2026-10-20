import { act, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { DuplicateDialog } from "@/components/sets/DuplicateDialog";
import { noAnswerSentence } from "@/lib/mutations";
import { duplicateAnswer, duplicatePreview } from "@/test/duplicateFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { bean, grinder } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getDuplicatePreview, duplicateVersion, getBeans, getGrinders } = vi.hoisted(() => ({
  getDuplicatePreview: vi.fn(),
  duplicateVersion: vi.fn(),
  getBeans: vi.fn(),
  getGrinders: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getDuplicatePreview,
  duplicateVersion,
  getBeans,
  getGrinders,
}));

const V12 = { id: 22, version_label: "v1.2" };

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function Search() {
  return <p data-testid="search">{useLocation().search}</p>;
}

function WhereWithBack() {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <>
      <p data-testid="where">{location.pathname}</p>
      <button type="button" onClick={() => navigate(-1)}>
        back
      </button>
    </>
  );
}

function renderDialog(onOpenChange = vi.fn(), initialEntries = ["/"]) {
  renderWithQueryClient(
    <Routes>
      <Route
        path="/"
        element={
          <>
            <DuplicateDialog
              open
              setId={3}
              setName="Ethiopia Guji · Blooming"
              version={V12}
              onOpenChange={onOpenChange}
            />
            <Search />
          </>
        }
      />
      <Route path="/sets/:setId" element={<WhereWithBack />} />
      <Route path="/profiles" element={<Where />} />
    </Routes>,
    { initialEntries },
  );
  return onOpenChange;
}

async function ready() {
  await screen.findByTestId("duplicate-recipe");
  // The pickers' options are in, so the prefilled bean is the selected one.
  await waitFor(() => expect(field("Bean").value).toBe("1"));
}

function field(name: string): HTMLInputElement {
  return screen.getByLabelText(name) as HTMLInputElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  getDuplicatePreview.mockResolvedValue(duplicatePreview());
  duplicateVersion.mockResolvedValue(duplicateAnswer());
  getBeans.mockResolvedValue({
    items: [bean({ id: 1, name: "Ethiopia Guji" }), bean({ id: 2, name: "Kenya AA" })],
  });
  getGrinders.mockResolvedValue({
    items: [grinder({ id: 1, name: "Niche Zero" }), grinder({ id: 2, name: "Eureka" })],
  });
});

describe("the dialog's default state", () => {
  it("says what is being duplicated and prefills everything but the profile name", async () => {
    renderDialog();
    await ready();

    expect(screen.getByRole("dialog")).toHaveTextContent(
      'Duplicate "Ethiopia Guji · Blooming" from v1.2',
    );
    expect(field("Set name").value).toBe("Guji on the Niche (copy)");
    expect(field("Profile name").value).toBe("");
    expect(screen.getByTestId("duplicate-profile-note")).toHaveTextContent(
      "a new profile, same content as v1.2's; it goes on the machine",
    );
    await waitFor(() => expect(field("Bean").value).toBe("1"));
    expect(field("Grinder").value).toBe("1");
    expect(screen.getByTestId("duplicate-recipe")).toHaveTextContent(
      "Blooming · grind 2.2 · 18 g → 40 g (v1.2's; change it later)",
    );
    // Duplicate waits for the profile name.
    expect(screen.getByRole("button", { name: "Duplicate" })).toBeDisabled();
  });

  it("enables Duplicate once both names are given, and sends what is on the form", async () => {
    const user = setupUser();
    renderDialog();
    await ready();

    await user.type(field("Profile name"), "  Blooming milk ");
    await user.clear(field("Set name"));
    await user.type(field("Set name"), "Guji milk");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    await waitFor(() => expect(duplicateVersion).toHaveBeenCalledTimes(1));
    expect(duplicateVersion).toHaveBeenCalledWith(3, 22, {
      recipe_name: "Guji milk",
      profile_name: "Blooming milk",
      bean_id: 1,
      grinder_id: 1,
      insight_ids: [11, 12, 13, 14, 15],
    });
  });

  it("goes to the new Set on success", async () => {
    const user = setupUser();
    renderDialog();
    await ready();

    await user.type(field("Profile name"), "Blooming milk");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    expect(await screen.findByTestId("where")).toHaveTextContent("/sets/9");
  });

  it("takes ?duplicate= out of its history entry, so Back from the new Set does not reopen it", async () => {
    const user = setupUser();
    renderDialog(vi.fn(), ["/profiles", "/?duplicate=22"]);
    await ready();

    await user.type(field("Profile name"), "Blooming milk");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(await screen.findByTestId("where")).toHaveTextContent("/sets/9");
    await user.click(screen.getByRole("button", { name: "back" }));

    // Back lands on the Set page's own address, with no parameter to open the dialog again.
    await waitFor(() => expect(screen.getByTestId("search")).toBeInTheDocument());
    expect(screen.getByTestId("search")).toHaveTextContent("");
    expect(screen.getByTestId("search").textContent).toBe("");
  });

  it("sends the bean and the grinder the person chose, and a cleared grinder as none", async () => {
    const user = setupUser();
    renderDialog();
    await ready();
    await waitFor(() => expect(field("Bean").value).toBe("1"));

    await user.type(field("Profile name"), "P");
    await user.selectOptions(field("Bean"), "2");
    await user.selectOptions(field("Grinder"), "");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    await waitFor(() => expect(duplicateVersion).toHaveBeenCalled());
    expect(duplicateVersion.mock.calls[0]?.[2]).toMatchObject({ bean_id: 2, grinder_id: null });
  });
});

describe("a version with no profile", () => {
  it("has no profile field and sends no profile name", async () => {
    getDuplicatePreview.mockResolvedValue(
      duplicatePreview({
        has_profile: false,
        version_fields: { ...duplicatePreview().version_fields, profile_label: null },
      }),
    );
    const user = setupUser();
    renderDialog();
    await ready();

    expect(screen.queryByLabelText("Profile name")).not.toBeInTheDocument();
    expect(screen.queryByTestId("duplicate-profile-note")).not.toBeInTheDocument();
    expect(screen.getByTestId("duplicate-recipe")).toHaveTextContent("grind 2.2 · 18 g → 40 g");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    await waitFor(() => expect(duplicateVersion).toHaveBeenCalled());
    expect(duplicateVersion.mock.calls[0]?.[2]).toMatchObject({ profile_name: null });
  });
});

describe("the insights", () => {
  it("shows the box only when the Set has confirmed insights", async () => {
    getDuplicatePreview.mockResolvedValue(duplicatePreview({ insights: [] }));
    renderDialog();
    await ready();

    expect(screen.queryByTestId("duplicate-insights")).not.toBeInTheDocument();
    expect(screen.queryByText(/Copy insights/)).not.toBeInTheDocument();
  });

  it("starts ticked with every insight, the list closed, and counts the ticked", async () => {
    renderDialog();
    await ready();

    const box = screen.getByRole("checkbox", { name: "Copy insights (5 of 5 chosen)" });
    expect(box).toBeChecked();
    expect(screen.queryByTestId("duplicate-insight-list")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show the insights" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("expands to the list with every insight ticked, and unticking one is a half-ticked box", async () => {
    const user = setupUser();
    renderDialog();
    await ready();

    await user.click(screen.getByRole("button", { name: "Show the insights" }));
    const list = screen.getByTestId("duplicate-insight-list");
    const boxes = within(list).getAllByRole("checkbox");
    expect(boxes).toHaveLength(5);
    expect(boxes.every((b) => (b as HTMLInputElement).checked)).toBe(true);

    await user.click(within(list).getByLabelText("40 g yield tastes thin"));
    await user.click(within(list).getByLabelText("A wet puck means too fine"));

    const master = screen.getByRole("checkbox", { name: "Copy insights (3 of 5 chosen)" });
    expect((master as HTMLInputElement).indeterminate).toBe(true);
    await user.type(field("Profile name"), "P");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(duplicateVersion).toHaveBeenCalled());
    expect(duplicateVersion.mock.calls[0]?.[2]).toMatchObject({ insight_ids: [11, 12, 14] });
  });

  it("unticking the box unticks every insight, and ticking it ticks every one", async () => {
    const user = setupUser();
    renderDialog();
    await ready();
    await user.click(screen.getByRole("button", { name: "Show the insights" }));

    await user.click(screen.getByRole("checkbox", { name: "Copy insights (5 of 5 chosen)" }));
    const list = screen.getByTestId("duplicate-insight-list");
    expect(
      within(list)
        .getAllByRole("checkbox")
        .some((b) => (b as HTMLInputElement).checked),
    ).toBe(false);
    expect(screen.getByLabelText("Copy insights (0 of 5 chosen)")).not.toBeChecked();

    await user.click(screen.getByLabelText("Copy insights (0 of 5 chosen)"));
    expect(
      within(list)
        .getAllByRole("checkbox")
        .every((b) => (b as HTMLInputElement).checked),
    ).toBe(true);

    await user.click(screen.getByLabelText("Copy insights (5 of 5 chosen)"));
    await user.type(field("Profile name"), "P");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(duplicateVersion).toHaveBeenCalled());
    expect(duplicateVersion.mock.calls[0]?.[2]).toMatchObject({ insight_ids: [] });
  });
});

describe("the server's words, in place", () => {
  it("shows the taken-name sentence under the profile field and waits for another name", async () => {
    const sentence = "There is already a profile called Blooming. Choose another name.";
    duplicateVersion.mockRejectedValueOnce(
      new ApiClientError(sentence, {
        status: 409,
        code: "PROFILE_NAME_TAKEN",
        details: { name: "blooming" },
      }),
    );
    const user = setupUser();
    renderDialog();
    await ready();

    await user.type(field("Profile name"), "blooming");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    const note = await screen.findByTestId("duplicate-name-taken");
    expect(note).toHaveTextContent(sentence);
    expect(field("Profile name")).toHaveAccessibleDescription(sentence);
    // The same name again would be refused again.
    expect(screen.getByRole("button", { name: "Duplicate" })).toBeDisabled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.type(field("Profile name"), " milk");
    expect(screen.getByRole("button", { name: "Duplicate" })).toBeEnabled();
  });

  it("shows a policy refusal as the server's sentence with only Close", async () => {
    const sentence = "outside the current safety bounds: phases.0.pump.pressure 11 -> 9";
    duplicateVersion.mockRejectedValueOnce(
      new ApiClientError(sentence, {
        status: 409,
        code: "PROFILE_POLICY_REFUSED",
        details: { clamp_changes: [] },
      }),
    );
    const user = setupUser();
    const onOpenChange = renderDialog();
    await ready();

    await user.type(field("Profile name"), "Blooming milk");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    expect(await screen.findByTestId("duplicate-refused")).toHaveTextContent(sentence);
    expect(screen.queryByRole("button", { name: "Duplicate" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Profile name")).not.toBeInTheDocument();
    const closers = screen.getAllByRole("button", { name: "Close" });
    await user.click(closers[closers.length - 1] as HTMLElement);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("shows any other refusal in place and keeps the form", async () => {
    duplicateVersion.mockRejectedValueOnce(
      new ApiClientError("No bean 1", { status: 422, code: "UNPROCESSABLE_ENTITY" }),
    );
    const user = setupUser();
    renderDialog();
    await ready();

    await user.type(field("Profile name"), "P");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    expect(await screen.findByTestId("duplicate-other")).toHaveTextContent("No bean 1");
    expect(field("Profile name").value).toBe("P");
  });

  it("shows the server's sentence when the preview itself is refused", async () => {
    getDuplicatePreview.mockRejectedValue(
      new ApiClientError("This Set is still being designed: there is no version to copy yet.", {
        status: 409,
        code: "DESIGNING",
      }),
    );
    renderDialog();

    expect(await screen.findByTestId("duplicate-preview-error")).toHaveTextContent(
      "This Set is still being designed",
    );
    expect(screen.queryByRole("button", { name: "Duplicate" })).not.toBeInTheDocument();
  });

  it("says one thing when no answer came back, and keeps the form for another try", async () => {
    duplicateVersion.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const user = setupUser();
    renderDialog();
    await ready();

    await user.type(field("Profile name"), "P");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    expect(await screen.findByTestId("duplicate-unreachable")).toHaveTextContent(
      noAnswerSentence("the Sets list"),
    );
    expect(screen.getByRole("button", { name: "Duplicate" })).toBeEnabled();
  });
});

describe("the bean and grinder pickers", () => {
  it("keeps Duplicate disabled until the bean and grinder lists have loaded", async () => {
    let giveBeans: (value: unknown) => void = () => undefined;
    getBeans.mockReturnValue(new Promise((done) => (giveBeans = done)));
    const user = setupUser();
    renderDialog();
    await screen.findByTestId("duplicate-recipe");
    await user.type(field("Profile name"), "P");

    expect(screen.getByRole("button", { name: "Duplicate" })).toBeDisabled();
    giveBeans({ items: [bean({ id: 1 })] });

    await waitFor(() => expect(screen.getByRole("button", { name: "Duplicate" })).toBeEnabled());
  });

  it("asks for the archived beans too and marks them, so a finished bag can be picked", async () => {
    getBeans.mockResolvedValue({
      items: [
        bean({ id: 1, name: "Ethiopia Guji", archived: true }),
        bean({ id: 2, name: "Kenya AA" }),
      ],
    });
    renderDialog();
    await ready();

    expect(getBeans).toHaveBeenCalledWith(true);
    const picker = field("Bean");
    expect(
      within(picker).getByRole("option", { name: /Ethiopia Guji.*\(archived\)/ }),
    ).toBeVisible();
    expect(within(picker).getByRole("option", { name: /^Kenya AA/ })).toBeVisible();
    // The source's own bag is archived and still the one selected.
    expect(picker.value).toBe("1");
  });
});

describe("an insight that went while the dialog was open", () => {
  it("shows the server's sentence, asks the preview again and sends only what is still listed", async () => {
    const sentence =
      "One of the chosen insights is no longer one of this Set's confirmed insights.";
    duplicateVersion.mockRejectedValueOnce(
      new ApiClientError(sentence, {
        status: 422,
        code: "UNPROCESSABLE_ENTITY",
        details: { field: "insight_ids" },
      }),
    );
    const user = setupUser();
    renderDialog();
    await ready();
    await user.type(field("Profile name"), "P");
    // Insight 13 is taken back elsewhere: the next preview no longer lists it.
    getDuplicatePreview.mockResolvedValue(
      duplicatePreview({ insights: duplicatePreview().insights.filter((i) => i.id !== 13) }),
    );

    await user.click(screen.getByRole("button", { name: "Duplicate" }));

    expect(await screen.findByTestId("duplicate-other")).toHaveTextContent(sentence);
    await screen.findByRole("checkbox", { name: "Copy insights (4 of 4 chosen)" });
    expect(getDuplicatePreview).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(duplicateVersion).toHaveBeenCalledTimes(2));
    expect(duplicateVersion.mock.calls[1]?.[2]).toMatchObject({ insight_ids: [11, 12, 14, 15] });
  });

  it("does not ask again for any other refusal", async () => {
    duplicateVersion.mockRejectedValueOnce(
      new ApiClientError("A Set needs a name", {
        status: 422,
        code: "UNPROCESSABLE_ENTITY",
        details: { field: "recipe_name" },
      }),
    );
    const user = setupUser();
    renderDialog();
    await ready();
    await user.type(field("Profile name"), "P");
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    await screen.findByTestId("duplicate-other");

    expect(getDuplicatePreview).toHaveBeenCalledTimes(1);
  });
});

describe("one submit at a time", () => {
  it("sends one request however fast the form is submitted", async () => {
    let resolve: (value: ReturnType<typeof duplicateAnswer>) => void = () => undefined;
    duplicateVersion.mockReturnValue(new Promise((done) => (resolve = done)));
    const user = setupUser();
    renderDialog();
    await ready();
    await user.type(field("Profile name"), "P");
    const form = field("Profile name").closest("form") as HTMLFormElement;

    // Two submissions before React has re-rendered with a pending mutation: the button's
    // disabled state cannot stop the second, only the ref can.
    act(() => {
      form.requestSubmit();
      form.requestSubmit();
    });

    await waitFor(() => expect(duplicateVersion).toHaveBeenCalled());
    expect(duplicateVersion).toHaveBeenCalledTimes(1);
    resolve(duplicateAnswer());
    await screen.findByTestId("where");
  });
});
