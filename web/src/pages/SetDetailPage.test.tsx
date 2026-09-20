import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetDetailPage } from "@/pages/SetDetailPage";
import { knowledgeInsight, suggestion } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { labelCounts, setDetail, trackRecord, trends, vocabulary } from "@/test/setsFixtures";

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
let setId = "3";
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useParams: () => ({ setId }),
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
  getSet,
  getSetTrends,
  addSetVersion,
  archiveSet,
  getSetSuggestions,
  analyseSet,
  getVocabulary,
  getKnowledgeInsights,
  rollbackSet,
  getProfileVersions,
  createProfileDraft,
  pushProfileDraft,
} = vi.hoisted(() => ({
  getSet: vi.fn(),
  getSetTrends: vi.fn(),
  addSetVersion: vi.fn(),
  archiveSet: vi.fn(),
  getSetSuggestions: vi.fn(),
  analyseSet: vi.fn(),
  getVocabulary: vi.fn(),
  getKnowledgeInsights: vi.fn(),
  rollbackSet: vi.fn(),
  // Mocked rather than left to the real fetch: the version form's profile
  // picker asks for them, and an unmocked call is a rejected promise and a
  // console full of noise that hides a real failure.
  getProfileVersions: vi.fn(),
  // Spied only so the tests can assert this path never reaches them: recording
  // a profile on a version is bookkeeping, and the machine is written from one
  // place by one person.
  createProfileDraft: vi.fn(),
  pushProfileDraft: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSet,
  getSetTrends,
  addSetVersion,
  archiveSet,
  getSetSuggestions,
  analyseSet,
  getVocabulary,
  getKnowledgeInsights,
  rollbackSet,
  getProfileVersions,
  createProfileDraft,
  pushProfileDraft,
}));

beforeEach(() => {
  vi.clearAllMocks();
  setId = "3";
  getSet.mockResolvedValue(setDetail());
  getSetTrends.mockResolvedValue(trends());
  addSetVersion.mockResolvedValue(setDetail().versions[0].version);
  archiveSet.mockResolvedValue({ ...setDetail().set, status: "archived", active: false });
  getSetSuggestions.mockResolvedValue({ items: [] });
  analyseSet.mockResolvedValue({
    set_id: 3,
    requested: 2,
    succeeded: 2,
    failed: 0,
    stopped: false,
    analysis_ids: [1, 2],
  });
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  rollbackSet.mockResolvedValue(setDetail().versions[0].version);
  getProfileVersions.mockResolvedValue({
    items: [
      { id: 7, label: "9 Bar Espresso", target_yield_g: 36, temperature_c: 93 },
      { id: 8, label: "Turbo", target_yield_g: 45, temperature_c: 90 },
    ],
    total: 2,
  });
});

describe("SetDetailPage", () => {
  it("draws the trend chart for a Set with several versions and its shots", async () => {
    renderWithQueryClient(<SetDetailPage />);

    // The canvas is invisible to a test, so each chart renders what it drew as
    // text (see web/README.md).
    const summary = await screen.findByTestId("set-trend-summary");
    expect(summary).toHaveTextContent("Execution score: 4 points");
    // A shot with no rating is a gap, not a zero.
    expect(summary).toHaveTextContent("Your rating: 3 points");
    expect(summary).toHaveTextContent("Version 2 begins at shot 3");
  });

  it("shows the current recipe and the version history", async () => {
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByText("Now brewing: v2")).toBeInTheDocument();
    expect(screen.getAllByTestId("version-entry")).toHaveLength(2);
  });

  it("sends only the fields that changed when a version is recorded", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(await screen.findByLabelText("Grind"), "20");
    await user.type(screen.getByLabelText("What are you trying?"), "finer still");
    await user.click(screen.getByRole("button", { name: "Record the version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    const [setId, patch] = addSetVersion.mock.calls[0];
    expect(setId).toBe(3);
    // Not sent means inherited: a body carrying every field would record all of
    // them as changed and the timeline's diff would say nothing.
    expect(patch).toEqual({
      intent: "finer still",
      prediction: "",
      origin: "manual",
      grind_setting: "20",
      grind_value: 20,
    });
  });

  it("sends the prediction and what it is compared to with a new version", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(screen.getByLabelText("What are you trying?"), "one finer");
    await user.type(screen.getByLabelText("Version prediction"), "less sour");
    await user.click(screen.getByRole("button", { name: "Record the version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    const [, patch] = addSetVersion.mock.calls[0];
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
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(screen.getByLabelText("What are you trying?"), "a fresh baseline");
    await user.type(screen.getByLabelText("Version prediction"), "  a clean 1:2  ");
    await user.selectOptions(screen.getByLabelText("Compared to"), "");
    await user.click(screen.getByRole("button", { name: "Record the version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    const [, patch] = addSetVersion.mock.calls[0];
    // Null, not omitted: omitted means "against the parent", which is the
    // opposite of what "Nothing" says. The text arrives trimmed.
    expect(patch.compares_to_version_id).toBeNull();
    expect(patch.prediction).toBe("a clean 1:2");
  });

  it("leaves the comparison off entirely when there is no prediction", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(screen.getByLabelText("What are you trying?"), "one finer");
    await user.click(screen.getByRole("button", { name: "Record the version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    const [, patch] = addSetVersion.mock.calls[0];
    expect(patch).not.toHaveProperty("compares_to_version_id");
  });

  it("preselects the current version's profile and sends nothing when it is untouched", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    // The fixture's current version brews with profile version 7.
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));
    await user.type(screen.getByLabelText("What are you trying?"), "one finer");
    await user.type(screen.getByLabelText("Grind"), "20");
    await user.click(screen.getByRole("button", { name: "Record the version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    // Untouched means inherited, like every other recipe field on this form.
    expect(addSetVersion.mock.calls[0][1]).not.toHaveProperty("profile_version_id");
  });

  it("records a change of profile, and says it changes nothing on the machine", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));

    // The one rule this help text protects: only a person pushes, and only
    // from the Profiles page.
    expect(screen.getByTestId("profile-help")).toHaveTextContent("changes nothing on the machine");
    expect(screen.getByTestId("profile-help")).toHaveTextContent("Profiles page");

    await user.selectOptions(screen.getByLabelText("Profile"), "8");
    await user.type(screen.getByLabelText("What are you trying?"), "switched to the turbo");
    await user.click(screen.getByRole("button", { name: "Record the version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0][1]).toMatchObject({ profile_version_id: 8 });
    // Nothing on this path goes near a draft or the machine.
    expect(pushProfileDraft).not.toHaveBeenCalled();
    expect(createProfileDraft).not.toHaveBeenCalled();
  });

  it("carries the new profile's targets across, without overwriting a typed one", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));
    // Typed by hand first: the profile must not take it back.
    await user.type(screen.getByLabelText("Temperature (°C)"), "95");

    await user.selectOptions(screen.getByLabelText("Profile"), "8");

    expect(screen.getByLabelText("Target yield (g)")).toHaveValue("45");
    expect(screen.getByLabelText("Temperature (°C)")).toHaveValue("95");
    expect(screen.getByTestId("version-from-profile")).toHaveTextContent("from Turbo");
  });

  it("can take the profile off a version entirely", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));

    await user.selectOptions(screen.getByLabelText("Profile"), "");
    await user.type(screen.getByLabelText("What are you trying?"), "any profile now");
    await user.click(screen.getByRole("button", { name: "Record the version" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0][1]).toMatchObject({ profile_version_id: null });
  });

  it("leads with the track record once something has been graded", async () => {
    getSet.mockResolvedValue(
      setDetail({ track_record: trackRecord({ held: 2, failed: 1, graded: 3, open: 1 }) }),
    );
    renderWithQueryClient(<SetDetailPage />);

    const record = await screen.findByTestId("track-record");
    expect(record).toHaveTextContent("2 of 3 predictions held");
    expect(record).toHaveTextContent("1 open");
  });

  it("says nothing about a track record before anything is graded", async () => {
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("version-timeline");
    // Zero out of zero is an absence, not a modest score.
    expect(screen.queryByTestId("track-record")).not.toBeInTheDocument();
  });

  it("offers the way back when the current version is going badly", async () => {
    const user = setupUser();
    const detail = setDetail({ rollback_target_version_id: 21 });
    detail.versions[0].labels = labelCounts({ improve: 2 });
    detail.versions[1].labels = labelCounts({ keep: 3 });
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);

    const offer = await screen.findByRole("button", {
      name: "Roll back to v1, the last version with Keep shots",
    });
    await user.click(offer);
    await user.click(screen.getAllByRole("button", { name: "Roll back to v1" })[0]);

    await waitFor(() => expect(rollbackSet).toHaveBeenCalled());
    expect(rollbackSet.mock.calls[0][1]).toEqual({
      to_version_id: 21,
      intent: "",
      prediction: "",
    });
  });

  it("does not nag when the current version has a Keep shot of its own", async () => {
    const detail = setDetail({ rollback_target_version_id: 21 });
    detail.versions[0].labels = labelCounts({ keep: 1, improve: 2 });
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("version-timeline");
    expect(
      screen.queryByRole("button", { name: /the last version with Keep shots/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps the ratio off the duration axis so it is not a flat line", async () => {
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("set-trend-summary");
    // The ratio lives around 2 while duration lives around 28; sharing an axis
    // squashes the one series that answers "did the recipe change" onto the
    // baseline.
    const axes = axisOf(["Duration (s)", "Ratio", "Execution score", "Your rating"]);
    expect(axes.Ratio).not.toBe(axes["Duration (s)"]);
    expect(axes["Execution score"]).toBe(axes["Your rating"]);
  });

  it("says so when a Set has collected nothing yet", async () => {
    getSetTrends.mockResolvedValue({ set_id: 3, versions: [], shots: [] });
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByText(/No shots yet/)).toBeInTheDocument();
  });

  it("reports a Set that is not there rather than rendering an empty page", async () => {
    getSet.mockRejectedValue(new Error("No Set 3"));
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByText("No such Set")).toBeInTheDocument();
  });
});

describe("SetDetailPage with a URL that is not a Set", () => {
  it("says so rather than showing a skeleton for ever", async () => {
    // The query is never enabled for a non-numeric id, so "pending" is a state
    // it can never leave.
    setId = "nonsense";
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByText("No such Set")).toBeInTheDocument();
    expect(screen.getByText("That is not a Set id.")).toBeInTheDocument();
    expect(getSet).not.toHaveBeenCalled();
  });
});

describe("SetDetailPage suggestions", () => {
  it("says so when nothing has been suggested yet", async () => {
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByTestId("no-suggestions")).toHaveTextContent(
      "No analysis has suggested anything",
    );
  });

  it("groups the advice by the version it was about", async () => {
    // A suggestion is a delta from the numbers it was given, so advice about v1
    // and advice about v2 are not one conversation.
    getSetSuggestions.mockResolvedValue({
      items: [
        // 21 and 22 are the fixture Set's v1 and v2.
        suggestion({ id: 1, set_version_id: 21, variable: "grind" }),
        suggestion({ id: 2, set_version_id: 22, variable: "yield", direction: "increase" }),
      ],
    });

    renderWithQueryClient(<SetDetailPage />);

    const group = await screen.findByTestId("set-suggestions");
    expect(group).toHaveTextContent("about v1");
    expect(group).toHaveTextContent("about v2");
  });

  it("runs the batch over the un-analysed shots", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByTestId("analyse-set"));

    await waitFor(() => expect(analyseSet.mock.calls[0]?.[0]).toBe(3));
  });
});

describe("SetDetailPage — what this archive has learned", () => {
  it("shows the confirmed insights that apply to this Set", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [knowledgeInsight({ confirmed: true, confirmed_at: "2026-03-03T10:00:00.000Z" })],
      scope_keys: [],
    });
    renderWithQueryClient(<SetDetailPage />);

    const card = await screen.findByTestId("set-insights");
    expect(card).toHaveTextContent("Naturals on this grinder");
    // The server does the scope matching, through the same call an analysis
    // makes — so the page cannot show a different answer from the prompt.
    expect(getKnowledgeInsights).toHaveBeenCalledWith({ set_id: 3 });
  });

  it("says nothing at all when nothing has been learned about this Set", async () => {
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("set-trend-summary");
    expect(screen.queryByTestId("set-insights")).not.toBeInTheDocument();
  });
});
