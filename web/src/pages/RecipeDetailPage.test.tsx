import { screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { RecipeDetailPage } from "@/pages/RecipeDetailPage";
import { knowledgeInsight } from "@/test/knowledgeFixtures";
import {
  designingDetail,
  designingRecipe,
  designProposal,
  labelCounts,
  minorDetail,
  point,
  proposal,
  recipeDetail,
  recipeRow,
  trackRecord,
  trends,
  vocabulary,
} from "@/test/recipesFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import {
  overrideInForce,
  signatureInForce,
  signatureMixed,
  signatureNone,
} from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

/**
 * The options the chart was handed.
 *
 * `<Line>` is replaced rather than inspected: the real component draws to a
 * canvas, which is opaque to a test, and what is under test here is which axis
 * a dataset was assigned — a decision made before anything is drawn.
 */
type ChartProps = {
  data?: { datasets?: Array<{ label?: string; yAxisID?: string }> };
  options?: { plugins?: { annotation?: { annotations?: Record<string, { xMin?: number }> } } };
  "aria-label"?: string;
};
let lastChartProps: ChartProps | null = null;
vi.mock("react-chartjs-2", () => ({
  Line: (props: ChartProps) => {
    lastChartProps = props;
    return <canvas aria-label={props["aria-label"]} />;
  },
}));

/** The route param, so one test can make it something that is not a number. */
let recipeId = "3";
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useParams: () => ({ recipeId }),
}));

/**
 * Which y axis each dataset was put on.
 *
 * The chart is a canvas, so this reads the options Chart.js was handed rather
 * than anything drawn. `react-chartjs-2` is mocked below to capture them.
 */
function axisOf(labels: string[]): Record<string, string | undefined> {
  const datasets = lastChartProps?.data?.datasets ?? [];
  return Object.fromEntries(
    labels.map((label) => [label, datasets.find((dataset) => dataset.label === label)?.yAxisID]),
  );
}

const {
  getRecipe,
  getRecipeTrends,
  addRecipeVersion,
  archiveRecipe,
  getVocabulary,
  getKnowledgeInsights,
  rollbackRecipe,
  getProfileVersions,
  getProfileChoices,
  createProfileDraft,
  putOnBoard,
  acceptRecipeProposal,
  discardDesign,
  getSignature,
  getSignatureOverrides,
} = vi.hoisted(() => ({
  getRecipe: vi.fn(),
  getRecipeTrends: vi.fn(),
  addRecipeVersion: vi.fn(),
  archiveRecipe: vi.fn(),
  getVocabulary: vi.fn(),
  getKnowledgeInsights: vi.fn(),
  rollbackRecipe: vi.fn(),
  // Mocked rather than left to the real fetch: the version form's profile
  // picker asks for them, and an unmocked call is a rejected promise and a
  // console full of noise that hides a real failure.
  getProfileVersions: vi.fn(),
  getProfileChoices: vi.fn(),
  // Spied only so the tests can assert this path never reaches them: recording
  // a profile on a version is bookkeeping, and the machine is written from one
  // place by one person.
  createProfileDraft: vi.fn(),
  putOnBoard: vi.fn(),
  acceptRecipeProposal: vi.fn(),
  discardDesign: vi.fn(),
  getSignature: vi.fn(),
  getSignatureOverrides: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getRecipe,
  getRecipeTrends,
  addRecipeVersion,
  archiveRecipe,
  getVocabulary,
  getKnowledgeInsights,
  rollbackRecipe,
  getProfileVersions,
  getProfileChoices,
  createProfileDraft,
  putOnBoard,
  acceptRecipeProposal,
  discardDesign,
  getSignature,
  getSignatureOverrides,
}));

beforeEach(() => {
  vi.clearAllMocks();
  recipeId = "3";
  getRecipe.mockResolvedValue(recipeDetail());
  getRecipeTrends.mockResolvedValue(trends());
  addRecipeVersion.mockResolvedValue(recipeDetail().versions[0].version);
  archiveRecipe.mockResolvedValue({ ...recipeDetail().recipe, archived: true, automatch: false });
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  rollbackRecipe.mockResolvedValue(recipeDetail().versions[0].version);
  getSignature.mockResolvedValue(signatureNone);
  getSignatureOverrides.mockResolvedValue({ items: [] });
  // The design brief names the profile it forks by the profile's name.
  getProfileVersions.mockResolvedValue({
    items: [
      {
        id: 7,
        label: "9 Bar Espresso (first stored)",
        profile_name: "9 Bar Espresso",
        target_yield_g: 36,
        temperature_c: 93,
      },
    ],
    total: 1,
  });
});

describe("RecipeDetailPage", () => {
  it("names a minor version by its name, never by its ordinal", async () => {
    getRecipe.mockResolvedValue(minorDetail());
    renderWithQueryClient(<RecipeDetailPage />);

    expect(await screen.findByText("Now brewing: v1.2")).toBeInTheDocument();
    const page = document.body;
    expect(page).toHaveTextContent("v1.1");
    expect(page).not.toHaveTextContent(/\bv3\b/);
    expect(page).not.toHaveTextContent(/\bv2\b/);
  });

  it("puts a waiting change above the log, with both answers", async () => {
    getRecipe.mockResolvedValue(recipeDetail({ proposal: proposal() }));
    renderWithQueryClient(<RecipeDetailPage />);

    const card = await screen.findByTestId("proposal-card");
    expect(card).toHaveTextContent("A change is waiting for you");
    expect(card).toHaveTextContent("a touch more body");
    expect(within(card).getByRole("button", { name: /Accept/ })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Decline/ })).toBeInTheDocument();
    // And it is above the log: the log is a record, this is a question.
    const log = screen.getByTestId("version-timeline");
    expect(card.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("accepts a waiting change through the route, not through a version write", async () => {
    const user = setupUser();
    getRecipe.mockResolvedValue(recipeDetail({ proposal: proposal() }));
    acceptRecipeProposal.mockResolvedValue({
      proposal: proposal({ status: "accepted", resulting_version_label: "v3" }),
      version: { version_label: "v3" },
    });
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Accept/ }));

    await waitFor(() => expect(acceptRecipeProposal).toHaveBeenCalledWith(3, 5, { major: false }));
    // Accepting is not "add a version with these fields": the server owns the
    // staleness and outcome checks, and a client-side append would skip them.
    expect(addRecipeVersion).not.toHaveBeenCalled();
  });

  it("shows nothing where nothing has been proposed", async () => {
    renderWithQueryClient(<RecipeDetailPage />);
    await screen.findByTestId("version-timeline");
    expect(screen.queryByTestId("proposal-card")).not.toBeInTheDocument();
  });

  it("links an accepted version's log entry back to the room it was argued in", async () => {
    const detail = recipeDetail();
    detail.versions[0].chat_thread_id = 9;
    getRecipe.mockResolvedValue(detail);
    renderWithQueryClient(<RecipeDetailPage />);

    const link = (await screen.findByTestId("version-proposed-in")).querySelector("a");
    expect(link).toHaveAttribute("href", "/chat?thread=9");
  });

  it("draws the trend chart for a Recipe with several versions and its shots", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    // The canvas is invisible to a test, so each chart renders what it drew as
    // text (see web/README.md).
    const summary = await screen.findByTestId("recipe-trend-summary");
    // A shot with no rating is a gap, not a zero.
    expect(summary).toHaveTextContent("Your rating: 3 points");
    expect(summary).toHaveTextContent("v2 begins at shot 3");
  });

  it("shows the current recipe and the version history", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    expect(await screen.findByText("Now brewing: v2")).toBeInTheDocument();
    expect(screen.getAllByTestId("version-entry")).toHaveLength(2);
  });

  it("sends only the fields that changed when a version is recorded", async () => {
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(await screen.findByLabelText("Grind"), "20");
    await user.type(screen.getByLabelText("What are you trying?"), "finer still");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

    await waitFor(() => expect(addRecipeVersion).toHaveBeenCalled());
    const [recipeId, patch] = addRecipeVersion.mock.calls[0];
    expect(recipeId).toBe(3);
    // Not sent means inherited: a body carrying every field would record all of
    // them as changed and the timeline's diff would say nothing.
    expect(patch).toEqual({
      intent: "finer still",
      prediction: "",
      origin: "manual",
      grind_setting: "20",
      grind_value: 20,
      // What the Major change box showed: a grind change is a minor version.
      major: false,
    });
  });

  it("asks a Recipe with no profile for one, from the profiles no Recipe has, and sends it", async () => {
    getRecipe.mockResolvedValue(
      recipeDetail({
        recipe: recipeRow({ board_id: null, profile_version_id: null, profile_label: null }),
      }),
    );
    getProfileChoices.mockResolvedValue({
      items: [{ profile_version_id: 50, label: "Free" }],
    });
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    const picker = await screen.findByLabelText("Profile");
    await screen.findByRole("option", { name: "Free" });
    expect(within(picker).queryByRole("option", { name: "Held" })).not.toBeInTheDocument();
    await user.selectOptions(picker, "50");
    await user.type(screen.getByLabelText("What are you trying?"), "settle on a profile");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

    await waitFor(() => expect(addRecipeVersion).toHaveBeenCalled());
    expect(addRecipeVersion.mock.calls[0][1]).toEqual(
      expect.objectContaining({ profile_version_id: 50 }),
    );
  });

  it("sends the prediction and what it is compared to with a new version", async () => {
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(screen.getByLabelText("What are you trying?"), "one finer");
    await user.type(screen.getByLabelText("Version prediction"), "less sour");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

    await waitFor(() => expect(addRecipeVersion).toHaveBeenCalled());
    const [, patch] = addRecipeVersion.mock.calls[0];
    // The comparison defaults to the current version, which is what "less sour"
    // means when you have just changed something.
    expect(patch).toMatchObject({
      intent: "one finer",
      prediction: "less sour",
      compares_to_version_id: 22,
    });
  });

  it("sends an explicit null when a new version is compared to nothing", async () => {
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(screen.getByLabelText("What are you trying?"), "a fresh baseline");
    await user.type(screen.getByLabelText("Version prediction"), "  a clean 1:2  ");
    await user.selectOptions(screen.getByLabelText("Compared to"), "");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

    await waitFor(() => expect(addRecipeVersion).toHaveBeenCalled());
    const [, patch] = addRecipeVersion.mock.calls[0];
    // Null, not omitted: omitted means "against the parent", which is the
    // opposite of what "Nothing" says. The text arrives trimmed.
    expect(patch.compares_to_version_id).toBeNull();
    expect(patch.prediction).toBe("a clean 1:2");
  });

  it("names the version it will record, and the person's tick makes it a major", async () => {
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    const box = screen.getByRole("checkbox", { name: "Major change" });
    // Grind, dose and yield are dialling in: v2 → v2.1.
    expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Record it as v2.1" })).toBeInTheDocument();
    expect(screen.getByTestId("major-choice-result")).toHaveTextContent("v2.1");

    // The person has the last word.
    await user.type(screen.getByLabelText("Grind"), "20");
    await user.click(box);
    expect(screen.getByRole("button", { name: "Record it as v3" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("What are you trying?"), "a new direction");
    await user.click(screen.getByRole("button", { name: "Record it as v3" }));

    await waitFor(() => expect(addRecipeVersion).toHaveBeenCalled());
    expect(addRecipeVersion.mock.calls[0][1]).toMatchObject({ grind_setting: "20", major: true });
  });

  it("offers no profile to choose: a Recipe brews its own, and a different brew is a draft of it", async () => {
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));

    expect(screen.queryByLabelText("Profile")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("What are you trying?"), "one finer");
    await user.type(screen.getByLabelText("Grind"), "20");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));
    await waitFor(() => expect(addRecipeVersion).toHaveBeenCalled());
    expect(addRecipeVersion.mock.calls[0][1]).not.toHaveProperty("profile_version_id");
  });

  it("marks the version the Recipe is on, and reads the current one from the flag", async () => {
    const detail = recipeDetail();
    // The Recipe went back: the older version is the current one.
    detail.versions[0].version = { ...detail.versions[0].version, is_current: false };
    detail.versions[1].version = { ...detail.versions[1].version, is_current: true };
    getRecipe.mockResolvedValue(detail);
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    // The form's "compared to" starts on the current version, v1, not the newest.
    await waitFor(() => expect(screen.getByLabelText("Compared to")).toHaveValue("21"));
  });

  it("reads the header and the Discuss link from the current version, not the newest", async () => {
    // The Recipe went back: v1 (id 21, grind 22) is current, v2 (id 22, grind 21) is newest.
    const detail = recipeDetail();
    detail.versions[0].version = { ...detail.versions[0].version, is_current: false };
    detail.versions[1].version = { ...detail.versions[1].version, is_current: true };
    getRecipe.mockResolvedValue(detail);
    renderWithQueryClient(<RecipeDetailPage />);

    await screen.findByTestId("version-timeline");
    expect(screen.getByText("18 g in · 36 g out · grind 22 · 93 °C")).toBeInTheDocument();
    expect(screen.getByTestId("discuss-in-chat").getAttribute("href")).toContain("version=21");
  });

  it("judges the track record by the current version, not the newest", async () => {
    // v1 is current and going badly; v2, the newest, has a Good shot. A page
    // that read the first row would see no struggle and offer no way back.
    const detail = recipeDetail({ rollback_target_version_id: 22 });
    detail.versions[0].version = { ...detail.versions[0].version, is_current: false };
    detail.versions[1].version = { ...detail.versions[1].version, is_current: true };
    detail.versions[0].labels = labelCounts({ good: 3 });
    detail.versions[1].labels = labelCounts({ bad: 2 });
    getRecipe.mockResolvedValue(detail);
    renderWithQueryClient(<RecipeDetailPage />);

    expect(
      await screen.findByRole("button", {
        name: "Go back to v2, the last version with Good shots",
      }),
    ).toBeInTheDocument();
  });

  it("leads with the track record once something has been graded", async () => {
    getRecipe.mockResolvedValue(
      recipeDetail({ track_record: trackRecord({ held: 2, failed: 1, graded: 3, open: 1 }) }),
    );
    renderWithQueryClient(<RecipeDetailPage />);

    const record = await screen.findByTestId("track-record");
    expect(record).toHaveTextContent("2 of 3 predictions held");
    expect(record).toHaveTextContent("1 open");
  });

  it("puts the spread above the log, where the comparisons are", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    // The labels come from /api/vocab, so the line reads its slug until that
    // query lands and the words afterwards.
    await screen.findByText("Shot time ±1.8 s · from 9 repeat shots at 4 settings");
    const spread = screen.getByTestId("recipe-spread");
    expect(spread).toHaveTextContent("Arithmetic, not a model.");
    // The log follows it: three seconds means nothing until you know this.
    const log = screen.getByTestId("version-timeline");
    expect(spread.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says nothing about a track record before anything is graded", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    await screen.findByTestId("version-timeline");
    // Zero out of zero is an absence, not a modest score.
    expect(screen.queryByTestId("track-record")).not.toBeInTheDocument();
  });

  it("offers the way back when the current version is going badly", async () => {
    const user = setupUser();
    const detail = recipeDetail({ rollback_target_version_id: 21 });
    detail.versions[0].labels = labelCounts({ bad: 2 });
    detail.versions[1].labels = labelCounts({ good: 3 });
    getRecipe.mockResolvedValue(detail);
    renderWithQueryClient(<RecipeDetailPage />);

    const offer = await screen.findByRole("button", {
      name: "Go back to v1, the last version with Good shots",
    });
    await user.click(offer);
    await user.click(screen.getAllByRole("button", { name: "Go back to v1" })[0]);

    await waitFor(() => expect(rollbackRecipe).toHaveBeenCalled());
    expect(rollbackRecipe.mock.calls[0][1]).toEqual({
      to_version_id: 21,
      note: "",
    });
  });

  it("does not nag when the current version has a Good shot of its own", async () => {
    const detail = recipeDetail({ rollback_target_version_id: 21 });
    detail.versions[0].labels = labelCounts({ good: 1, bad: 2 });
    getRecipe.mockResolvedValue(detail);
    renderWithQueryClient(<RecipeDetailPage />);

    await screen.findByTestId("version-timeline");
    expect(
      screen.queryByRole("button", { name: /the last version with Good shots/ }),
    ).not.toBeInTheDocument();
  });

  it("draws a boundary at every change of version, a returned-to version twice", async () => {
    // After a revert one version's shots come in two runs: v1, v1.1, v2, v1.1.
    getRecipeTrends.mockResolvedValue(
      trends({
        shots: [
          point(1, 21, 1, { version_label: "v1" }),
          point(2, 22, 1, { version_label: "v1.1" }),
          point(3, 23, 2, { version_label: "v2" }),
          point(4, 22, 1, { version_label: "v1.1" }),
        ],
      }),
    );
    // Two list items keyed by the same label make React warn; that is the only
    // trace such a key leaves, so the warning is the assertion.
    const warn = vi.spyOn(console, "error").mockImplementation(() => undefined);
    renderWithQueryClient(<RecipeDetailPage />);

    const summary = await screen.findByTestId("recipe-trend-summary");
    expect(warn.mock.calls.flat().join(" ")).not.toContain("same key");
    warn.mockRestore();
    expect(summary).toHaveTextContent("v1.1 begins at shot 2");
    expect(summary).toHaveTextContent("v2 begins at shot 3");
    expect(summary).toHaveTextContent("v1.1 begins at shot 4");
    // The drawn lines are keyed by where they fall, so v1.1's second run is a
    // line of its own and not the first one overwritten.
    const lines = lastChartProps?.options?.plugins?.annotation?.annotations ?? {};
    expect(Object.values(lines).map((line) => line.xMin)).toEqual([0.5, 1.5, 2.5]);
  });

  it("starts the Add a version form again from the version the Recipe moved to", async () => {
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(screen.getByLabelText("Grind"), "20");
    await user.type(screen.getByLabelText("What are you trying?"), "half typed");

    // The Recipe goes back to v1; the page refetches.
    const back = recipeDetail();
    back.versions[0].version = { ...back.versions[0].version, is_current: false };
    back.versions[1].version = {
      ...back.versions[1].version,
      is_current: true,
      profile_version_id: 8,
    };
    getRecipe.mockResolvedValue(back);
    await user.click(screen.getByRole("button", { name: "Go back to this version" }));
    await user.click(screen.getByRole("button", { name: "Go back to v1" }));

    await waitFor(() => expect(screen.getByLabelText("Grind")).toHaveValue(""));
    expect(screen.getByLabelText("What are you trying?")).toHaveValue("");
  });

  it("keeps the ratio off the duration axis so it is not a flat line", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    await screen.findByTestId("recipe-trend-summary");
    // The ratio lives around 2 while duration lives around 28; sharing an axis
    // squashes the one series that answers "did the recipe change" onto the
    // baseline.
    const axes = axisOf(["Duration (s)", "Ratio", "Your rating"]);
    expect(axes.Ratio).not.toBe(axes["Duration (s)"]);
    expect(axes["Your rating"]).not.toBe(axes["Duration (s)"]);
  });

  it("says so when a Recipe has collected nothing yet", async () => {
    getRecipeTrends.mockResolvedValue({ recipe_id: 3, versions: [], shots: [] });
    renderWithQueryClient(<RecipeDetailPage />);

    expect(
      await screen.findByText(
        "No shots yet. The next one pulled with this Recipe's profile files itself here.",
      ),
    ).toBeInTheDocument();
  });

  it("reports a Recipe that is not there rather than rendering an empty page", async () => {
    getRecipe.mockRejectedValue(new Error("No Recipe 3"));
    renderWithQueryClient(<RecipeDetailPage />);

    expect(await screen.findByText("No such Recipe")).toBeInTheDocument();
  });
});

describe("RecipeDetailPage with a URL that is not a Recipe", () => {
  it("says so rather than showing a skeleton for ever", async () => {
    // The query is never enabled for a non-numeric id, so "pending" is a state
    // it can never leave.
    recipeId = "nonsense";
    renderWithQueryClient(<RecipeDetailPage />);

    expect(await screen.findByText("No such Recipe")).toBeInTheDocument();
    expect(screen.getByText("That is not a Recipe id.")).toBeInTheDocument();
    expect(getRecipe).not.toHaveBeenCalled();
  });
});

describe("RecipeDetailPage has no model-review surface", () => {
  it("offers no suggestions, no batch analysis and no review of the Recipe", async () => {
    // A model reads a shot only when a person presses Review on that shot's
    // page, and what it writes stays there: nothing on the Recipe page lists
    // advice, queues provider calls or shows a review.
    renderWithQueryClient(<RecipeDetailPage />);

    await screen.findAllByText("Guji on the Niche");
    expect(screen.queryByText("Suggestions")).toBeNull();
    expect(screen.queryByTestId("recipe-suggestions")).toBeNull();
    expect(screen.queryByTestId("analyse-recipe")).toBeNull();
    expect(screen.queryByRole("button", { name: /analyse|review/i })).toBeNull();
    expect(screen.queryByTestId("large-batch-warning")).toBeNull();
    expect(screen.queryByTestId("review-box")).toBeNull();
  });
});

describe("RecipeDetailPage — what this archive has learned", () => {
  it("shows the confirmed insights that apply to this Recipe", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [knowledgeInsight({ confirmed: true, confirmed_at: "2026-03-03T10:00:00.000Z" })],
      scope_keys: [],
    });
    renderWithQueryClient(<RecipeDetailPage />);

    const card = await screen.findByTestId("recipe-insights");
    expect(card).toHaveTextContent("Naturals on this grinder");
    // The server does the scope matching, through the same call the chat
    // makes — so the page cannot show a different answer from the prompt.
    expect(getKnowledgeInsights).toHaveBeenCalledWith({ recipe_id: 3 });
  });

  it("lists what was learned in this Recipe under its version, waiting ones with their buttons", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [
        knowledgeInsight({
          id: 5,
          text: "Finer helps this bag.",
          recipe_id: 3,
          recipe_version_id: 22,
          recipe_version_label: "v2",
          general: false,
          scope: {},
          confirmed: false,
        }),
      ],
      scope_keys: [],
    });
    renderWithQueryClient(<RecipeDetailPage />);

    const group = await screen.findByTestId("recipe-insights-version");
    expect(group).toHaveAttribute("data-version", "v2");
    expect(group).toHaveTextContent("Finer helps this bag.");
    expect(within(group).getByRole("button", { name: "Add" })).toBeInTheDocument();
  });

  it("says nothing at all when nothing has been learned about this Recipe", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    await screen.findByTestId("recipe-trend-summary");
    expect(screen.queryByTestId("recipe-insights")).not.toBeInTheDocument();
  });
});

/** Where the page sent the person, for a test to read. */
function Location() {
  const location = useLocation();
  return <p data-testid="location">{location.pathname}</p>;
}

describe("RecipeDetailPage, a Recipe being designed", () => {
  beforeEach(() => {
    recipeId = "6";
    getRecipe.mockResolvedValue(designingDetail());
    getRecipeTrends.mockResolvedValue(trends({ recipe_id: 6, versions: [], shots: [] }));
    discardDesign.mockResolvedValue({ recipe_id: 6, discarded: true });
  });

  it("carries the badge, and Continue designing where Discuss was", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    expect(await screen.findByTestId("recipe-designing")).toHaveTextContent("Designing");
    // Back into version 1's own conversation, through the open-or-continue
    // link Discuss uses, with nothing prefilled.
    expect(screen.getByTestId("continue-designing")).toHaveAttribute(
      "href",
      "/chat?recipe=6&version=60",
    );
    expect(screen.queryByTestId("discuss-in-chat")).not.toBeInTheDocument();
  });

  it("says version 1 is being designed in the log, not a row of empty fields", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    const entry = await screen.findByTestId("version-entry");
    expect(entry).toHaveAttribute("data-designing", "yes");
    expect(within(entry).getByTestId("version-being-designed")).toHaveTextContent(
      "being designed — no numbers yet",
    );
    expect(within(entry).queryByText("No prediction.")).not.toBeInTheDocument();
    expect(within(entry).queryByRole("button", { name: /prediction/ })).not.toBeInTheDocument();
  });

  it("shows a waiting first recipe above the log, as a waiting change is", async () => {
    getRecipe.mockResolvedValue(designingDetail({ proposal: designProposal() }));
    renderWithQueryClient(<RecipeDetailPage />);

    const card = await screen.findByTestId("proposal-card");
    expect(card).toHaveAttribute("data-kind", "design");
    expect(card).toHaveTextContent("Version 1 is waiting for you");
    expect(within(card).getByRole("button", { name: /Accept/ })).toBeInTheDocument();
    const log = screen.getByTestId("version-timeline");
    expect(card.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps the Add a version form, saying it writes version 1 by hand", async () => {
    getProfileChoices.mockResolvedValue({
      items: [{ profile_version_id: 50, label: "Free" }],
    });
    const user = setupUser();
    renderWithQueryClient(<RecipeDetailPage />);

    expect(await screen.findByText("No numbers yet: v1 is being designed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Write version 1 by hand/ }));

    expect(screen.getByTestId("new-version-designing")).toHaveTextContent(
      "Write version 1 by hand",
    );
    // Nothing to compare a version 1 against, and the server ignores one there.
    expect(screen.queryByLabelText("Version prediction")).not.toBeInTheDocument();
    // The first recipe gives the Recipe its profile, as New Recipe does.
    await screen.findByRole("option", { name: "Free" });
    await user.selectOptions(screen.getByLabelText("Profile"), "50");
    await user.type(screen.getByLabelText("Dose (g)"), "18");
    await user.type(screen.getByLabelText("What are you trying?"), "the roaster's card");
    await user.click(screen.getByRole("button", { name: "Recipe version 1" }));

    await waitFor(() => expect(addRecipeVersion).toHaveBeenCalled());
    expect(addRecipeVersion.mock.calls[0][0]).toBe(6);
    expect(addRecipeVersion.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        dose_g: 18,
        intent: "the roaster's card",
        prediction: "",
        profile_version_id: 50,
      }),
    );
    expect(addRecipeVersion.mock.calls[0][1]).not.toHaveProperty("compares_to_version_id");
  });

  it("discards only once confirmed, then goes to the Recipes list", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <>
        <RecipeDetailPage />
        <Location />
      </>,
      { initialEntries: ["/recipes/6"] },
    );

    const toggle = await screen.findByTestId("discard-design");
    const confirm = screen.getByTestId("discard-confirm");
    expect(toggle.getAttribute("aria-controls")).toBe(confirm.id);
    expect(confirm).toHaveAttribute("hidden");

    await user.click(toggle);
    expect(confirm).not.toHaveAttribute("hidden");
    expect(discardDesign).not.toHaveBeenCalled();
    // Changing one's mind declines nothing.
    await user.click(within(confirm).getByRole("button", { name: "Keep designing" }));
    expect(confirm).toHaveAttribute("hidden");
    expect(discardDesign).not.toHaveBeenCalled();

    await user.click(toggle);
    await user.click(within(confirm).getByRole("button", { name: "Discard it" }));

    await waitFor(() => expect(discardDesign).toHaveBeenCalled());
    expect(discardDesign.mock.calls[0][0]).toBe(6);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/recipes$/));
  });

  it("says in words why a design with a shot filed on it is kept", async () => {
    const user = setupUser();
    discardDesign.mockRejectedValue(
      new ApiClientError("This Recipe is being designed and a shot is already filed on it", {
        status: 409,
        code: "DESIGN_HAS_SHOTS",
      }),
    );
    renderWithQueryClient(
      <>
        <RecipeDetailPage />
        <Location />
      </>,
      { initialEntries: ["/recipes/6"] },
    );

    await user.click(await screen.findByTestId("discard-design"));
    await user.click(screen.getByRole("button", { name: "Discard it" }));

    expect(await screen.findByTestId("discard-refused")).toHaveTextContent(
      "A shot is filed on this Recipe, so it is kept",
    );
    expect(screen.getByTestId("location")).toHaveTextContent("/recipes/6");
  });

  it("says in words why a Recipe that has a first version now is not discarded", async () => {
    const user = setupUser();
    discardDesign.mockRejectedValue(
      new ApiClientError("This Recipe is not being designed", {
        status: 409,
        code: "NOT_DESIGNING",
      }),
    );
    renderWithQueryClient(<RecipeDetailPage />);

    await user.click(await screen.findByTestId("discard-design"));
    await user.click(screen.getByRole("button", { name: "Discard it" }));

    expect(await screen.findByTestId("discard-refused")).toHaveTextContent("Archive it instead");
  });

  it("shows what the design was asked for: the fork, the usual grind and the goal", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    const brief = await screen.findByTestId("design-brief");
    // The fork by its label, read from the profile versions the page has.
    await waitFor(() =>
      expect(within(brief).getByTestId("design-brief-fork")).toHaveTextContent("9 Bar Espresso"),
    );
    expect(within(brief).getByTestId("design-brief-grind")).toHaveTextContent("22");
    const goal = within(brief).getByTestId("design-brief-goal");
    expect(goal).toHaveTextContent("“more body”");
    // The whole text on hover.
    expect(goal).toHaveAttribute("title", "more body");
  });

  it("shows only the parts of the brief that were given", async () => {
    getRecipe.mockResolvedValue(
      designingDetail({
        recipe: designingRecipe({
          design_brief: {
            fork_profile_version_id: null,
            usual_grind: "",
            goal: "try a bloom",
            grind_setting: "",
            drink_notes: "",
          },
        }),
      }),
    );
    renderWithQueryClient(<RecipeDetailPage />);

    const brief = await screen.findByTestId("design-brief");
    expect(within(brief).getByTestId("design-brief-goal")).toHaveTextContent("try a bloom");
    expect(within(brief).queryByTestId("design-brief-fork")).not.toBeInTheDocument();
    expect(within(brief).queryByTestId("design-brief-grind")).not.toBeInTheDocument();
    expect(within(brief).queryByTestId("design-brief-kept")).not.toBeInTheDocument();
  });

  it("names the recipe numbers the agent was told to keep, and only those given", async () => {
    getRecipe.mockResolvedValue(
      designingDetail({
        recipe: designingRecipe({
          design_brief: {
            fork_profile_version_id: null,
            usual_grind: "",
            goal: "",
            grind_setting: "19",
            dose_g: null,
            target_yield_g: 36.5,
            drink_notes: "",
          },
        }),
      }),
    );
    renderWithQueryClient(<RecipeDetailPage />);

    const brief = await screen.findByTestId("design-brief");
    expect(within(brief).getByTestId("design-brief-kept")).toHaveTextContent(
      "grind 19 · 36.5 g out",
    );
  });

  it("shows no brief at all when nothing was asked for", async () => {
    getRecipe.mockResolvedValue(
      designingDetail({
        recipe: designingRecipe({
          design_brief: {
            fork_profile_version_id: null,
            usual_grind: "",
            goal: "",
            grind_setting: "",
            drink_notes: "",
          },
        }),
      }),
    );
    renderWithQueryClient(<RecipeDetailPage />);

    await screen.findByTestId("discard-design");
    expect(screen.queryByTestId("design-brief")).not.toBeInTheDocument();
  });

  it("says when shots will arrive, since there is no profile to file them by yet", async () => {
    renderWithQueryClient(<RecipeDetailPage />);

    expect(
      await screen.findByText(
        "No shots yet. Once version 1 is accepted and its profile on the machine, shots brewed on it are filed here.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/pulled with this Recipe's profile/)).not.toBeInTheDocument();
  });

  it("offers no automatch while there is no profile to match on", async () => {
    // The fixture Recipe collects shots, as every new Recipe does.
    renderWithQueryClient(<RecipeDetailPage />);

    await screen.findByTestId("recipe-designing");
    expect(screen.queryByTestId("recipe-automatch")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Stop filing shots here|File matching shots here/ }),
    ).not.toBeInTheDocument();
  });

  it("shows none of it on a Recipe that is not being designed", async () => {
    recipeId = "3";
    getRecipe.mockResolvedValue(recipeDetail());
    renderWithQueryClient(<RecipeDetailPage />);

    expect(await screen.findByText("Now brewing: v2")).toBeInTheDocument();
    expect(screen.queryByTestId("recipe-designing")).not.toBeInTheDocument();
    expect(screen.queryByTestId("continue-designing")).not.toBeInTheDocument();
    expect(screen.queryByTestId("discard-design")).not.toBeInTheDocument();
    expect(screen.queryByTestId("version-being-designed")).not.toBeInTheDocument();
    expect(screen.getByTestId("discuss-in-chat")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Change something/ })).toBeInTheDocument();
    expect(screen.queryByTestId("design-brief")).not.toBeInTheDocument();
    expect(screen.getByTestId("recipe-automatch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Stop filing shots here/ })).toBeInTheDocument();
  });
});

describe("RecipeDetailPage signature", () => {
  it("says what is in force on the profile the Recipe brews, and links to its card", async () => {
    getSignature.mockResolvedValue(signatureInForce);
    renderWithQueryClient(<RecipeDetailPage />);

    expect(await screen.findByTestId("recipe-signature-state")).toHaveTextContent("6 in force");
    // The current version's profile, not another: version 22 is v2 and brews profile version 7.
    expect(getSignature).toHaveBeenCalledWith(7);
    expect(screen.getByTestId("recipe-signature-link")).toHaveAttribute(
      "href",
      "/profiles#version-7",
    );
    expect(screen.getByTestId("recipe-signature-link")).toHaveTextContent("Open it on Profiles");
  });

  it("counts what the person rejected, and says when none is in force or there is none", async () => {
    getSignature.mockResolvedValue(signatureMixed);
    const first = renderWithQueryClient(<RecipeDetailPage />);
    expect(await screen.findByTestId("recipe-signature-state")).toHaveTextContent(
      "5 in force · 1 rejected",
    );
    first.unmount();

    getSignature.mockResolvedValue({ ...signatureMixed, confirmed: 0, rejected: 6 });
    const second = renderWithQueryClient(<RecipeDetailPage />);
    expect(await screen.findByTestId("recipe-signature-state")).toHaveTextContent(
      "6 rejected, so shots are read without one",
    );
    expect(screen.getByTestId("recipe-signature-link")).toHaveTextContent("Open it on Profiles");
    second.unmount();

    // Rows that are not in force (carried without their phase, or from before) are counted apart.
    getSignature.mockResolvedValue({ ...signatureMixed, confirmed: 4, not_in_force: 1 });
    const third = renderWithQueryClient(<RecipeDetailPage />);
    expect(await screen.findByTestId("recipe-signature-state")).toHaveTextContent(
      "4 in force · 1 not in force · 1 rejected",
    );
    third.unmount();
    getSignature.mockResolvedValue({
      ...signatureMixed,
      confirmed: 0,
      rejected: 0,
      not_in_force: 2,
    });
    const fourth = renderWithQueryClient(<RecipeDetailPage />);
    expect(await screen.findByTestId("recipe-signature-state")).toHaveTextContent(
      "2 not in force, so shots are read without one",
    );
    fourth.unmount();

    getSignature.mockResolvedValue(signatureNone);
    renderWithQueryClient(<RecipeDetailPage />);
    expect(await screen.findByTestId("recipe-signature-state")).toHaveTextContent("none yet");
    // Nothing on the Profiles card yet, so it is not offered: the Recipe's chat is.
    expect(screen.queryByTestId("recipe-signature-link")).toBeNull();
    const chat = screen.getByTestId("recipe-signature-chat").getAttribute("href") ?? "";
    expect(chat).toContain("recipe=3");
    expect(chat).toContain("version=22");
  });

  it("shows an override on the version it belongs to and on no other", async () => {
    getSignatureOverrides.mockImplementation(async (_recipe: number, versionId: number) => ({
      items: versionId === 22 ? [{ ...overrideInForce, recipe_version_id: 22 }] : [],
    }));
    renderWithQueryClient(<RecipeDetailPage />);

    const entries = await screen.findAllByTestId("version-entry");
    const v2 = entries.find((e) => e.dataset.version === "v2") as HTMLElement;
    const v1 = entries.find((e) => e.dataset.version === "v1") as HTMLElement;
    expect(await within(v2).findByTestId("override-limit")).toHaveTextContent(
      "at most 20 % of target here (profile: at most 15 % of target)",
    );
    await waitFor(() => expect(getSignatureOverrides).toHaveBeenCalledWith(3, 21));
    expect(within(v1).queryByTestId("version-overrides")).toBeNull();
  });
});
