import { screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { SetDetailPage } from "@/pages/SetDetailPage";
import { knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import {
  designingDetail,
  designingSet,
  designProposal,
  labelCounts,
  minorDetail,
  point,
  proposal,
  setDetail,
  trackRecord,
  trends,
  vocabulary,
} from "@/test/setsFixtures";
import {
  overrideConfirmed,
  signatureConfirmed,
  signatureNone,
  signatureProposed,
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
  getVocabulary,
  getKnowledgeInsights,
  rollbackSet,
  getProfileVersions,
  createProfileDraft,
  putOnBoard,
  acceptSetProposal,
  discardDesign,
  getSignature,
  getSignatureOverrides,
} = vi.hoisted(() => ({
  getSet: vi.fn(),
  getSetTrends: vi.fn(),
  addSetVersion: vi.fn(),
  archiveSet: vi.fn(),
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
  putOnBoard: vi.fn(),
  acceptSetProposal: vi.fn(),
  discardDesign: vi.fn(),
  getSignature: vi.fn(),
  getSignatureOverrides: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSet,
  getSetTrends,
  addSetVersion,
  archiveSet,
  getVocabulary,
  getKnowledgeInsights,
  rollbackSet,
  getProfileVersions,
  createProfileDraft,
  putOnBoard,
  acceptSetProposal,
  discardDesign,
  getSignature,
  getSignatureOverrides,
}));

beforeEach(() => {
  vi.clearAllMocks();
  setId = "3";
  getSet.mockResolvedValue(setDetail());
  getSetTrends.mockResolvedValue(trends());
  addSetVersion.mockResolvedValue(setDetail().versions[0].version);
  archiveSet.mockResolvedValue({ ...setDetail().set, archived: true, automatch: false });
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  rollbackSet.mockResolvedValue(setDetail().versions[0].version);
  getSignature.mockResolvedValue(signatureNone);
  getSignatureOverrides.mockResolvedValue({ items: [] });
  getProfileVersions.mockResolvedValue({
    items: [
      {
        id: 7,
        label: "9 Bar Espresso",
        target_yield_g: 36,
        temperature_c: 93,
        profile_entry_id: 1,
      },
      { id: 8, label: "Turbo", target_yield_g: 45, temperature_c: 90, profile_entry_id: 2 },
      // A newer version of the same list entry as 7: the same profile.
      {
        id: 9,
        label: "9 Bar Espresso (tuned)",
        target_yield_g: 36,
        temperature_c: 92,
        profile_entry_id: 1,
      },
      // In no list entry: its own profile, so a different one from 7.
      { id: 10, label: "Loose", target_yield_g: 36, temperature_c: 92, profile_entry_id: null },
    ],
    total: 4,
  });
});

describe("SetDetailPage", () => {
  it("names a minor version by its name, never by its ordinal", async () => {
    getSet.mockResolvedValue(minorDetail());
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByText("Now brewing: v1.2")).toBeInTheDocument();
    const page = document.body;
    expect(page).toHaveTextContent("v1.1");
    expect(page).not.toHaveTextContent(/\bv3\b/);
    expect(page).not.toHaveTextContent(/\bv2\b/);
  });

  it("puts a waiting change above the log, with both answers", async () => {
    getSet.mockResolvedValue(setDetail({ proposal: proposal() }));
    renderWithQueryClient(<SetDetailPage />);

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
    getSet.mockResolvedValue(setDetail({ proposal: proposal() }));
    acceptSetProposal.mockResolvedValue({
      proposal: proposal({ status: "accepted", resulting_version_label: "v3" }),
      version: { version_label: "v3" },
    });
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Accept/ }));

    await waitFor(() => expect(acceptSetProposal).toHaveBeenCalledWith(3, 5, { major: false }));
    // Accepting is not "add a version with these fields": the server owns the
    // staleness and outcome checks, and a client-side append would skip them.
    expect(addSetVersion).not.toHaveBeenCalled();
  });

  it("shows nothing where nothing has been proposed", async () => {
    renderWithQueryClient(<SetDetailPage />);
    await screen.findByTestId("version-timeline");
    expect(screen.queryByTestId("proposal-card")).not.toBeInTheDocument();
  });

  it("links an accepted version's log entry back to the room it was argued in", async () => {
    const detail = setDetail();
    detail.versions[0].chat_thread_id = 9;
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);

    const link = (await screen.findByTestId("version-proposed-in")).querySelector("a");
    expect(link).toHaveAttribute("href", "/chat?thread=9");
  });

  it("draws the trend chart for a Set with several versions and its shots", async () => {
    renderWithQueryClient(<SetDetailPage />);

    // The canvas is invisible to a test, so each chart renders what it drew as
    // text (see web/README.md).
    const summary = await screen.findByTestId("set-trend-summary");
    // A shot with no rating is a gap, not a zero.
    expect(summary).toHaveTextContent("Your rating: 3 points");
    expect(summary).toHaveTextContent("v2 begins at shot 3");
  });

  it("shows the current recipe and the version history", async () => {
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByText("Now brewing: v2")).toBeInTheDocument();
    expect(screen.getAllByTestId("version-entry")).toHaveLength(2);
  });

  it("discusses the current version, not the Set in general", async () => {
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByText("Now brewing: v2");

    // The version is what makes it open or continue one conversation rather
    // than pointing vaguely at a folder.
    expect(screen.getByTestId("discuss-in-chat")).toHaveAttribute(
      "href",
      expect.stringContaining("version="),
    );
  });

  it("sends only the fields that changed when a version is recorded", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(await screen.findByLabelText("Grind"), "20");
    await user.type(screen.getByLabelText("What are you trying?"), "finer still");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

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
      // What the Major change box showed: a grind change is a minor version.
      major: false,
    });
  });

  it("sends the prediction and what it is compared to with a new version", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await user.type(screen.getByLabelText("What are you trying?"), "one finer");
    await user.type(screen.getByLabelText("Version prediction"), "less sour");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

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
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

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
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

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
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    // Untouched means inherited, like every other recipe field on this form.
    expect(addSetVersion.mock.calls[0][1]).not.toHaveProperty("profile_version_id");
  });

  it("records a change of profile, and says it changes nothing on the machine", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));

    // The one rule this help text protects: only a person puts a profile on the board, and only
    // from the Profiles page.
    expect(screen.getByTestId("profile-help")).toHaveTextContent("changes nothing on the machine");
    expect(screen.getByTestId("profile-help")).toHaveTextContent("Profiles page");

    await user.selectOptions(screen.getByLabelText("Profile"), "8");
    await user.type(screen.getByLabelText("What are you trying?"), "switched to the turbo");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0][1]).toMatchObject({ profile_version_id: 8 });
    // Nothing on this path goes near a draft, the board or the machine.
    expect(putOnBoard).not.toHaveBeenCalled();
    expect(createProfileDraft).not.toHaveBeenCalled();
  });

  it("names the version it will record, and marks a change of profile major", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));
    const box = screen.getByRole("checkbox", { name: "Major change" });
    // The profile is the current one, so the rule says minor: v2 → v2.1.
    expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Record it as v2.1" })).toBeInTheDocument();
    expect(screen.getByTestId("major-choice-result")).toHaveTextContent("v2.1");

    // A different profile is a major version by default…
    await user.selectOptions(screen.getByLabelText("Profile"), "8");
    expect(box).toBeChecked();
    expect(screen.getByRole("button", { name: "Record it as v3" })).toBeInTheDocument();
    // …and back again when the profile goes back.
    await user.selectOptions(screen.getByLabelText("Profile"), "7");
    expect(box).not.toBeChecked();

    // The person has the last word: a grind change marked major.
    await user.type(screen.getByLabelText("Grind"), "20");
    await user.click(box);
    expect(screen.getByRole("button", { name: "Record it as v3" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("What are you trying?"), "a new direction");
    await user.click(screen.getByRole("button", { name: "Record it as v3" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0][1]).toMatchObject({ grind_setting: "20", major: true });
  });

  it("starts the Major change box by the profile's list entry, not by its version", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));
    const box = screen.getByRole("checkbox", { name: "Major change" });

    // Another version of the same profile is not a new profile: minor.
    await user.selectOptions(screen.getByLabelText("Profile"), "9");
    expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Record it as v2.1" })).toBeInTheDocument();
    // A different list entry is: major.
    await user.selectOptions(screen.getByLabelText("Profile"), "8");
    expect(box).toBeChecked();
    // A version in no entry is its own profile: major too.
    await user.selectOptions(screen.getByLabelText("Profile"), "10");
    expect(box).toBeChecked();

    // The person's toggle wins over the default, on the same entry as well.
    await user.selectOptions(screen.getByLabelText("Profile"), "9");
    await user.click(box);
    expect(box).toBeChecked();
    await user.type(screen.getByLabelText("What are you trying?"), "a bigger rethink");
    await user.click(screen.getByRole("button", { name: "Record it as v3" }));
    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0][1]).toMatchObject({ profile_version_id: 9, major: true });
  });

  it("marks the version the Set is on, and reads the current one from the flag", async () => {
    const detail = setDetail();
    // The Set went back: the older version is the current one.
    detail.versions[0].version = { ...detail.versions[0].version, is_current: false };
    detail.versions[1].version = { ...detail.versions[1].version, is_current: true };
    getSet.mockResolvedValue(detail);
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    // The form's "compared to" starts on the current version, v1, not the newest.
    await waitFor(() => expect(screen.getByLabelText("Compared to")).toHaveValue("21"));
  });

  it("starts the box on a changed profile when the current version is in no list entry", async () => {
    const detail = setDetail();
    detail.versions[0].version = {
      ...detail.versions[0].version,
      profile_version_id: 10,
      profile_entry_id: null,
    };
    getSet.mockResolvedValue(detail);
    getProfileVersions.mockResolvedValue({
      items: [
        { id: 10, label: "Loose", target_yield_g: 36, temperature_c: 92, profile_entry_id: null },
        {
          id: 11,
          label: "Other loose",
          target_yield_g: 36,
          temperature_c: 92,
          profile_entry_id: null,
        },
      ],
      total: 2,
    });
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("10"));
    const box = screen.getByRole("checkbox", { name: "Major change" });
    expect(box).not.toBeChecked();
    // Two profiles that no list entry holds are two profiles.
    await user.selectOptions(screen.getByLabelText("Profile"), "11");
    expect(box).toBeChecked();
  });

  it("reads the header and the Discuss link from the current version, not the newest", async () => {
    // The Set went back: v1 (id 21, grind 22) is current, v2 (id 22, grind 21) is newest.
    const detail = setDetail();
    detail.versions[0].version = { ...detail.versions[0].version, is_current: false };
    detail.versions[1].version = { ...detail.versions[1].version, is_current: true };
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("version-timeline");
    expect(screen.getByText("18 g in · 36 g out · grind 22 · 93 °C")).toBeInTheDocument();
    expect(screen.getByTestId("discuss-in-chat").getAttribute("href")).toContain("version=21");
  });

  it("judges the track record by the current version, not the newest", async () => {
    // v1 is current and going badly; v2, the newest, has a Keep shot. A page
    // that read the first row would see no struggle and offer no way back.
    const detail = setDetail({ rollback_target_version_id: 22 });
    detail.versions[0].version = { ...detail.versions[0].version, is_current: false };
    detail.versions[1].version = { ...detail.versions[1].version, is_current: true };
    detail.versions[0].labels = labelCounts({ keep: 3 });
    detail.versions[1].labels = labelCounts({ improve: 2 });
    getSet.mockResolvedValue(detail);
    renderWithQueryClient(<SetDetailPage />);

    expect(
      await screen.findByRole("button", {
        name: "Go back to v2, the last version with Keep shots",
      }),
    ).toBeInTheDocument();
  });

  it("sends the person's minor on a change of profile they unticked", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));
    await user.selectOptions(screen.getByLabelText("Profile"), "8");
    await user.click(screen.getByRole("checkbox", { name: "Major change" }));
    // Their answer sticks when the profile moves again.
    await user.selectOptions(screen.getByLabelText("Profile"), "7");
    await user.selectOptions(screen.getByLabelText("Profile"), "8");
    expect(screen.getByRole("checkbox", { name: "Major change" })).not.toBeChecked();
    await user.type(screen.getByLabelText("What are you trying?"), "a tuned copy");
    await user.click(screen.getByRole("button", { name: "Record it as v2.1" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0][1]).toMatchObject({ profile_version_id: 8, major: false });
  });

  it("carries the new profile's target yield across, without overwriting a typed one", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));

    await user.selectOptions(screen.getByLabelText("Profile"), "8");
    expect(screen.getByLabelText("Target yield (g)")).toHaveValue("45");
    expect(screen.getByTestId("version-from-profile")).toHaveTextContent("from Turbo");

    // Typed by hand: the next profile must not take it back.
    const yieldField = screen.getByLabelText("Target yield (g)");
    await user.clear(yieldField);
    await user.type(yieldField, "50");
    await user.selectOptions(screen.getByLabelText("Profile"), "7");

    expect(screen.getByLabelText("Target yield (g)")).toHaveValue("50");
    expect(screen.queryByTestId("version-from-profile")).not.toBeInTheDocument();
  });

  it("shows the profile's temperature where the Temperature field used to be", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));

    // Read-only: it is read out with its own name, and there is no field to
    // type one into.
    expect(screen.queryByRole("textbox", { name: "Temperature (°C)" })).not.toBeInTheDocument();
    const cell = () => screen.getByRole("group", { name: "Temperature (°C)" });
    expect(cell()).toHaveTextContent("93 °C");
    expect(screen.getByTestId("temperature-from-profile")).toHaveTextContent(
      "The machine brews at the temperature 9 Bar Espresso states.",
    );

    // Switching the profile switches the temperature with it, which is the
    // only way a version's temperature ever changes.
    await user.selectOptions(screen.getByLabelText("Profile"), "8");
    expect(cell()).toHaveTextContent("90 °C");

    await user.type(screen.getByLabelText("What are you trying?"), "the turbo, cooler");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0][1]).not.toHaveProperty("target_temperature_c");
  });

  it("can take the profile off a version entirely", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));

    await user.selectOptions(screen.getByLabelText("Profile"), "");
    await user.type(screen.getByLabelText("What are you trying?"), "any profile now");
    await user.click(screen.getByRole("button", { name: /^Record it as/ }));

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

  it("puts the spread above the log, where the comparisons are", async () => {
    renderWithQueryClient(<SetDetailPage />);

    // The labels come from /api/vocab, so the line reads its slug until that
    // query lands and the words afterwards.
    await screen.findByText("Shot time ±1.8 s · from 9 repeat shots of 4 recipes");
    const spread = screen.getByTestId("set-spread");
    expect(spread).toHaveTextContent("Arithmetic, not a model.");
    // The log follows it: three seconds means nothing until you know this.
    const log = screen.getByTestId("version-timeline");
    expect(spread.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
      name: "Go back to v1, the last version with Keep shots",
    });
    await user.click(offer);
    await user.click(screen.getAllByRole("button", { name: "Go back to v1" })[0]);

    await waitFor(() => expect(rollbackSet).toHaveBeenCalled());
    expect(rollbackSet.mock.calls[0][1]).toEqual({
      to_version_id: 21,
      note: "",
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

  it("draws a boundary at every change of version, a returned-to version twice", async () => {
    // After a revert one version's shots come in two runs: v1, v1.1, v2, v1.1.
    getSetTrends.mockResolvedValue(
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
    renderWithQueryClient(<SetDetailPage />);

    const summary = await screen.findByTestId("set-trend-summary");
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

  it("starts the Add a version form again from the version the Set moved to", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByRole("button", { name: /Change something/ }));
    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("7"));
    await user.type(screen.getByLabelText("Grind"), "20");
    await user.type(screen.getByLabelText("What are you trying?"), "half typed");

    // The Set goes back to v1, which names another profile; the page refetches.
    const back = setDetail();
    back.versions[0].version = { ...back.versions[0].version, is_current: false };
    back.versions[1].version = {
      ...back.versions[1].version,
      is_current: true,
      profile_version_id: 8,
      profile_entry_id: 2,
    };
    getSet.mockResolvedValue(back);
    await user.click(screen.getByRole("button", { name: "Go back to this version" }));
    await user.click(screen.getByRole("button", { name: "Go back to v1" }));

    await waitFor(() => expect(screen.getByLabelText("Profile")).toHaveValue("8"));
    expect(screen.getByLabelText("Grind")).toHaveValue("");
    expect(screen.getByLabelText("What are you trying?")).toHaveValue("");
  });

  it("keeps the ratio off the duration axis so it is not a flat line", async () => {
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("set-trend-summary");
    // The ratio lives around 2 while duration lives around 28; sharing an axis
    // squashes the one series that answers "did the recipe change" onto the
    // baseline.
    const axes = axisOf(["Duration (s)", "Ratio", "Your rating"]);
    expect(axes.Ratio).not.toBe(axes["Duration (s)"]);
    expect(axes["Your rating"]).not.toBe(axes["Duration (s)"]);
  });

  it("says so when a Set has collected nothing yet", async () => {
    getSetTrends.mockResolvedValue({ set_id: 3, versions: [], shots: [] });
    renderWithQueryClient(<SetDetailPage />);

    expect(
      await screen.findByText(
        "No shots yet. The next one pulled with this Set's profile files itself here.",
      ),
    ).toBeInTheDocument();
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

describe("SetDetailPage has no model-reading surface", () => {
  it("offers no suggestions, no batch analysis and no review of the Set", async () => {
    // A model reads a shot only when a person presses Review on that shot's
    // page, and what it writes stays there: nothing on the Set page lists
    // advice, queues provider calls or shows a review.
    renderWithQueryClient(<SetDetailPage />);

    await screen.findAllByText("Guji on the Niche");
    expect(screen.queryByText("Suggestions")).toBeNull();
    expect(screen.queryByTestId("set-suggestions")).toBeNull();
    expect(screen.queryByTestId("analyse-set")).toBeNull();
    expect(screen.queryByRole("button", { name: /analyse|review/i })).toBeNull();
    expect(screen.queryByTestId("large-batch-warning")).toBeNull();
    expect(screen.queryByTestId("review-box")).toBeNull();
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
    // The server does the scope matching, through the same call the chat
    // makes — so the page cannot show a different answer from the prompt.
    expect(getKnowledgeInsights).toHaveBeenCalledWith({ set_id: 3 });
  });

  it("lists what was learned in this Set under its version, waiting ones with their buttons", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [
        knowledgeInsight({
          id: 5,
          text: "Finer helps this bag.",
          set_id: 3,
          set_version_id: 22,
          set_version_label: "v2",
          general: false,
          scope: {},
          confirmed: false,
        }),
      ],
      scope_keys: [],
    });
    renderWithQueryClient(<SetDetailPage />);

    const group = await screen.findByTestId("set-insights-version");
    expect(group).toHaveAttribute("data-version", "v2");
    expect(group).toHaveTextContent("Finer helps this bag.");
    expect(within(group).getByRole("button", { name: "Add" })).toBeInTheDocument();
  });

  it("says nothing at all when nothing has been learned about this Set", async () => {
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("set-trend-summary");
    expect(screen.queryByTestId("set-insights")).not.toBeInTheDocument();
  });
});

/** Where the page sent the person, for a test to read. */
function Location() {
  const location = useLocation();
  return <p data-testid="location">{location.pathname}</p>;
}

describe("SetDetailPage, a Set being designed", () => {
  beforeEach(() => {
    setId = "6";
    getSet.mockResolvedValue(designingDetail());
    getSetTrends.mockResolvedValue(trends({ set_id: 6, versions: [], shots: [] }));
    discardDesign.mockResolvedValue({ set_id: 6, discarded: true });
  });

  it("carries the badge, and Continue designing where Discuss was", async () => {
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByTestId("set-designing")).toHaveTextContent("Designing");
    // Back into version 1's own conversation, through the open-or-continue
    // link Discuss uses, with nothing prefilled.
    expect(screen.getByTestId("continue-designing")).toHaveAttribute(
      "href",
      "/chat?set=6&version=60",
    );
    expect(screen.queryByTestId("discuss-in-chat")).not.toBeInTheDocument();
  });

  it("says version 1 is being designed in the log, not a row of empty fields", async () => {
    renderWithQueryClient(<SetDetailPage />);

    const entry = await screen.findByTestId("version-entry");
    expect(entry).toHaveAttribute("data-designing", "yes");
    expect(within(entry).getByTestId("version-being-designed")).toHaveTextContent(
      "being designed — no recipe yet",
    );
    expect(within(entry).queryByText("No prediction.")).not.toBeInTheDocument();
    expect(within(entry).queryByRole("button", { name: /prediction/ })).not.toBeInTheDocument();
  });

  it("shows a waiting first recipe above the log, as a waiting change is", async () => {
    getSet.mockResolvedValue(designingDetail({ proposal: designProposal() }));
    renderWithQueryClient(<SetDetailPage />);

    const card = await screen.findByTestId("proposal-card");
    expect(card).toHaveAttribute("data-kind", "design");
    expect(card).toHaveTextContent("The first recipe is waiting for you");
    expect(within(card).getByRole("button", { name: /Accept/ })).toBeInTheDocument();
    const log = screen.getByTestId("version-timeline");
    expect(card.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps the Add a version form, saying it sets the first recipe by hand", async () => {
    const user = setupUser();
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByText("No recipe yet: v1 is being designed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Set the first recipe by hand/ }));

    expect(screen.getByTestId("new-version-designing")).toHaveTextContent(
      "Set the first recipe by hand",
    );
    // Nothing to compare a version 1 against, and the server ignores one there.
    expect(screen.queryByLabelText("Version prediction")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Dose (g)"), "18");
    await user.type(screen.getByLabelText("What are you trying?"), "the roaster's card");
    await user.click(screen.getByRole("button", { name: "Set version 1" }));

    await waitFor(() => expect(addSetVersion).toHaveBeenCalled());
    expect(addSetVersion.mock.calls[0][0]).toBe(6);
    expect(addSetVersion.mock.calls[0][1]).toEqual(
      expect.objectContaining({ dose_g: 18, intent: "the roaster's card", prediction: "" }),
    );
    expect(addSetVersion.mock.calls[0][1]).not.toHaveProperty("compares_to_version_id");
  });

  it("discards only once confirmed, then goes to the Sets list", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <>
        <SetDetailPage />
        <Location />
      </>,
      { initialEntries: ["/sets/6"] },
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
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/sets$/));
  });

  it("says in words why a design with a shot filed on it is kept", async () => {
    const user = setupUser();
    discardDesign.mockRejectedValue(
      new ApiClientError("This Set is being designed and a shot is already filed on it", {
        status: 409,
        code: "DESIGN_HAS_SHOTS",
      }),
    );
    renderWithQueryClient(
      <>
        <SetDetailPage />
        <Location />
      </>,
      { initialEntries: ["/sets/6"] },
    );

    await user.click(await screen.findByTestId("discard-design"));
    await user.click(screen.getByRole("button", { name: "Discard it" }));

    expect(await screen.findByTestId("discard-refused")).toHaveTextContent(
      "A shot is filed on this Set, so it is kept",
    );
    expect(screen.getByTestId("location")).toHaveTextContent("/sets/6");
  });

  it("says in words why a Set that has a recipe now is not discarded", async () => {
    const user = setupUser();
    discardDesign.mockRejectedValue(
      new ApiClientError("This Set is not being designed", {
        status: 409,
        code: "NOT_DESIGNING",
      }),
    );
    renderWithQueryClient(<SetDetailPage />);

    await user.click(await screen.findByTestId("discard-design"));
    await user.click(screen.getByRole("button", { name: "Discard it" }));

    expect(await screen.findByTestId("discard-refused")).toHaveTextContent("Archive it instead");
  });

  it("shows what the design was asked for: the fork, the usual grind and the goal", async () => {
    renderWithQueryClient(<SetDetailPage />);

    const brief = await screen.findByTestId("design-brief");
    // The fork by its label, read from the profile versions the page has.
    await waitFor(() =>
      expect(within(brief).getByTestId("design-brief-fork")).toHaveTextContent("9 Bar Espresso"),
    );
    expect(within(brief).getByTestId("design-brief-grind")).toHaveTextContent("22");
    const goal = within(brief).getByTestId("design-brief-goal");
    expect(goal).toHaveTextContent("“more body”");
    // Clamped to a few lines in the notice, the whole text on hover.
    expect(goal).toHaveClass("line-clamp-3");
    expect(goal).toHaveAttribute("title", "more body");
  });

  it("shows only the parts of the brief that were given", async () => {
    getSet.mockResolvedValue(
      designingDetail({
        set: designingSet({
          design_brief: { fork_profile_version_id: null, usual_grind: "", goal: "try a bloom" },
        }),
      }),
    );
    renderWithQueryClient(<SetDetailPage />);

    const brief = await screen.findByTestId("design-brief");
    expect(within(brief).getByTestId("design-brief-goal")).toHaveTextContent("try a bloom");
    expect(within(brief).queryByTestId("design-brief-fork")).not.toBeInTheDocument();
    expect(within(brief).queryByTestId("design-brief-grind")).not.toBeInTheDocument();
  });

  it("shows no brief at all when nothing was asked for", async () => {
    getSet.mockResolvedValue(
      designingDetail({
        set: designingSet({
          design_brief: { fork_profile_version_id: null, usual_grind: "", goal: "" },
        }),
      }),
    );
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("discard-design");
    expect(screen.queryByTestId("design-brief")).not.toBeInTheDocument();
  });

  it("says when shots will arrive, since there is no profile to file them by yet", async () => {
    renderWithQueryClient(<SetDetailPage />);

    expect(
      await screen.findByText(
        "No shots yet. Once the first recipe is accepted and its profile on the machine, shots brewed on it are filed here.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/pulled with this Set's profile/)).not.toBeInTheDocument();
  });

  it("offers no automatch while there is no profile to match on", async () => {
    // The fixture Set collects shots, as every new Set does.
    renderWithQueryClient(<SetDetailPage />);

    await screen.findByTestId("set-designing");
    expect(screen.queryByTestId("set-automatch")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Stop filing shots here|File matching shots here/ }),
    ).not.toBeInTheDocument();
  });

  it("shows none of it on a Set that is not being designed", async () => {
    setId = "3";
    getSet.mockResolvedValue(setDetail());
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByText("Now brewing: v2")).toBeInTheDocument();
    expect(screen.queryByTestId("set-designing")).not.toBeInTheDocument();
    expect(screen.queryByTestId("continue-designing")).not.toBeInTheDocument();
    expect(screen.queryByTestId("discard-design")).not.toBeInTheDocument();
    expect(screen.queryByTestId("version-being-designed")).not.toBeInTheDocument();
    expect(screen.getByTestId("discuss-in-chat")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Change something/ })).toBeInTheDocument();
    expect(screen.queryByTestId("design-brief")).not.toBeInTheDocument();
    expect(screen.getByTestId("set-automatch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Stop filing shots here/ })).toBeInTheDocument();
  });
});

describe("SetDetailPage signature", () => {
  it("says whether the profile the Set brews has a signature, and links to its card", async () => {
    getSignature.mockResolvedValue(signatureConfirmed);
    renderWithQueryClient(<SetDetailPage />);

    expect(await screen.findByTestId("set-signature-state")).toHaveTextContent(
      "confirmed, 6 expectations",
    );
    // The current version's profile, not another: version 22 is v2 and brews profile version 7.
    expect(getSignature).toHaveBeenCalledWith(7);
    expect(screen.getByTestId("set-signature-link")).toHaveAttribute("href", "/profiles#version-7");
    expect(screen.getByTestId("set-signature-link")).toHaveTextContent("Open it on Profiles");
  });

  it("says a proposed signature is not confirmed, and none says so", async () => {
    getSignature.mockResolvedValue(signatureProposed);
    const first = renderWithQueryClient(<SetDetailPage />);
    expect(await screen.findByTestId("set-signature-state")).toHaveTextContent(
      "6 proposed, none confirmed",
    );
    expect(screen.getByTestId("set-signature-link")).toHaveTextContent("Answer it on Profiles");
    first.unmount();

    getSignature.mockResolvedValue(signatureNone);
    renderWithQueryClient(<SetDetailPage />);
    expect(await screen.findByTestId("set-signature-state")).toHaveTextContent("none yet");
    // Nothing waits on the Profiles card, so it is not offered: the Set's chat is.
    expect(screen.queryByTestId("set-signature-link")).toBeNull();
    expect(screen.queryByText(/Answer it on Profiles/)).toBeNull();
    const chat = screen.getByTestId("set-signature-chat").getAttribute("href") ?? "";
    expect(chat).toContain("set=3");
    expect(chat).toContain("version=22");
  });

  it("shows an override on the version it belongs to and on no other", async () => {
    getSignatureOverrides.mockImplementation(async (_set: number, versionId: number) => ({
      items: versionId === 22 ? [{ ...overrideConfirmed, set_version_id: 22 }] : [],
    }));
    renderWithQueryClient(<SetDetailPage />);

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
