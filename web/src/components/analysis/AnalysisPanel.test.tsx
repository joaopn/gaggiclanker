import { screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnalysisPanel } from "@/components/analysis/AnalysisPanel";
import { analysis, knowledgeInsight, suggestion } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

/** Where the panel sent the reader, since jsdom has no address bar. */
function LocationProbe() {
  const { pathname, hash } = useLocation();
  return <span data-testid="location">{`${pathname}${hash}`}</span>;
}

import { vocabulary } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const {
  getVocabulary,
  runAnalysis,
  getLlmCalls,
  acceptSuggestion,
  rejectSuggestion,
  createProfileDraft,
  getKnowledgeInsights,
  patchKnowledgeInsight,
} = vi.hoisted(() => ({
  getVocabulary: vi.fn(),
  runAnalysis: vi.fn(),
  getLlmCalls: vi.fn(),
  acceptSuggestion: vi.fn(),
  rejectSuggestion: vi.fn(),
  createProfileDraft: vi.fn(),
  getKnowledgeInsights: vi.fn(),
  patchKnowledgeInsight: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
  runAnalysis,
  getLlmCalls,
  acceptSuggestion,
  rejectSuggestion,
  createProfileDraft,
  getKnowledgeInsights,
  patchKnowledgeInsight,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  getLlmCalls.mockResolvedValue({ calls: [], running: 0 });
  runAnalysis.mockResolvedValue(analysis());
  acceptSuggestion.mockResolvedValue({
    suggestion: suggestion({ status: "accepted" }),
    version: {},
  });
  rejectSuggestion.mockResolvedValue(suggestion({ status: "rejected" }));
  createProfileDraft.mockResolvedValue({ id: 5, status: "draft" });
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  patchKnowledgeInsight.mockResolvedValue(knowledgeInsight({ confirmed: true }));
});

describe("AnalysisPanel", () => {
  it("offers to analyse a shot that has never been analysed", async () => {
    const user = setupUser();
    renderWithQueryClient(<AnalysisPanel shotId={6} analyses={[]} hasSet />);

    expect(screen.getByTestId("analysis-empty")).toHaveTextContent("Not analysed yet");
    await user.click(screen.getByTestId("run-analysis"));

    await waitFor(() =>
      expect(runAnalysis).toHaveBeenCalledWith(6, { model: undefined, force: false }),
    );
  });

  it("sends the model override and forces a re-run when one already exists", async () => {
    const user = setupUser();
    renderWithQueryClient(<AnalysisPanel shotId={6} analyses={[analysis()]} hasSet />);

    await user.type(screen.getByLabelText("Model override"), "haiku");
    await user.click(screen.getByTestId("run-analysis"));

    await waitFor(() =>
      expect(runAnalysis).toHaveBeenCalledWith(6, { model: "haiku", force: true }),
    );
  });

  it("renders the diagnosis, the issues, the questions and the profile patch", () => {
    renderWithQueryClient(<AnalysisPanel shotId={6} analyses={[analysis()]} hasSet />);

    expect(screen.getByTestId("analysis-result")).toHaveTextContent("ran four seconds fast");
    expect(screen.getByTestId("execution-issues")).toHaveTextContent("flow_adherence");
    expect(screen.getByTestId("taste-prediction")).toHaveTextContent("predicts sour, thin body");
    expect(screen.getByTestId("questions")).toHaveTextContent("What did the last shot taste like");
    // Still recorded rather than applied — but there is now a button,
    // and it makes a draft rather than touching the machine.
    expect(screen.getByTestId("profile-patch")).toHaveTextContent("phase 1.duration");
    expect(screen.getByText(/Recorded, not applied/)).toBeInTheDocument();
  });

  it("links each cited rule to the Knowledge page", () => {
    renderWithQueryClient(<AnalysisPanel shotId={6} analyses={[analysis()]} hasSet />);

    const link = screen.getByRole("link", { name: "hierarchy" });
    expect(link).toHaveAttribute("href", "/knowledge?rule=hierarchy");
  });

  it("shows a failed analysis with its error rather than hiding it", () => {
    renderWithQueryClient(
      <AnalysisPanel
        shotId={6}
        analyses={[analysis({ status: "failed", error: "rate_limited: slow down", output: null })]}
        hasSet
      />,
    );

    expect(screen.getByTestId("analysis-failed")).toHaveTextContent("rate_limited: slow down");
  });

  it("names an interrupted run for what it was", () => {
    renderWithQueryClient(
      <AnalysisPanel
        shotId={6}
        analyses={[analysis({ status: "interrupted", output: null })]}
        hasSet
      />,
    );

    expect(screen.getByTestId("analysis-failed")).toHaveTextContent("Interrupted");
  });

  it("warns when there is no Set to suggest changes to", () => {
    renderWithQueryClient(<AnalysisPanel shotId={6} analyses={[]} hasSet={false} />);

    expect(screen.getByText(/not in a Set/)).toBeInTheDocument();
  });

  it("shows a running row as running, and disables the button", () => {
    renderWithQueryClient(
      <AnalysisPanel
        shotId={6}
        analyses={[analysis({ status: "running", output: null })]}
        hasSet
      />,
    );

    expect(screen.getByTestId("analysis-running")).toBeInTheDocument();
    expect(screen.getByTestId("run-analysis")).toBeDisabled();
  });

  it("is not disabled by another shot's analysis", () => {
    // The regression: the panel used to watch the live-call list, so one Set
    // batch froze the button on every other shot's page.
    getLlmCalls.mockResolvedValue({
      calls: [{ id: "x", status: "running", purpose: "analysis", subject: "#000104" }],
      running: 1,
    });

    renderWithQueryClient(<AnalysisPanel shotId={6} analyses={[analysis()]} hasSet />);

    expect(screen.getByTestId("run-analysis")).not.toBeDisabled();
  });

  it("keeps earlier analyses behind a disclosure", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <AnalysisPanel
        shotId={6}
        analyses={[analysis({ id: 2 }), analysis({ id: 1, suggestions: [] })]}
        hasSet
      />,
    );

    expect(screen.queryByTestId("older-analyses")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /1 earlier analysis/ }));
    expect(screen.getByTestId("older-analyses")).toBeInTheDocument();
  });

  it("turns a profile patch into a draft rather than into a device write", async () => {
    // The one route from an analysis to the machine, and it stops at a draft:
    // the diff, the clamps and any stop-condition change are seen by a person
    // before the machine hears about it.
    const user = setupUser();
    renderWithQueryClient(
      <AnalysisPanel shotId={6} analyses={[analysis()]} hasSet profileVersionId={7} />,
    );

    await user.click(screen.getByTestId("draft-profile"));

    await waitFor(() =>
      expect(createProfileDraft).toHaveBeenCalledWith({ base_version_id: 7, analysis_id: 1 }),
    );
  });

  it("lands on the staging section once the draft exists", async () => {
    // The queue is a section of the profiles page, and the anchor is what
    // makes "I made you a draft" land on the draft rather than at the top.
    const user = setupUser();
    renderWithQueryClient(
      <>
        <AnalysisPanel shotId={6} analyses={[analysis()]} hasSet profileVersionId={7} />
        <LocationProbe />
      </>,
    );

    await user.click(screen.getByTestId("draft-profile"));

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/profiles#staged"),
    );
  });

  it("says why there is no draft button when the profile was never mirrored", () => {
    // The machine had deleted the profile by the time we synced it, so there is
    // nothing to derive an edit from. Better than a button that 404s.
    renderWithQueryClient(
      <AnalysisPanel shotId={6} analyses={[analysis()]} hasSet profileVersionId={null} />,
    );
    expect(screen.getByTestId("draft-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("draft-profile")).not.toBeInTheDocument();
  });
});

describe("AnalysisPanel — the knowledge it used", () => {
  it("links each reference excerpt to the passage it came from", async () => {
    renderWithQueryClient(
      <AnalysisPanel
        shotId={6}
        hasSet
        analyses={[
          analysis({
            output: {
              diagnosis: "Sour and fast.",
              excerpts_used: [
                "ESPRESSO_TASTING_GUIDE#sour-vs-bitter",
                "COFFEE_PROCESSING#the-methods/natural-dry-process",
              ],
            },
          }),
        ]}
      />,
    );

    const list = await screen.findByTestId("excerpts-used");
    const [first, second] = within(list).getAllByRole("link");
    // The document slug is the part before the `#`, and the whole path is the
    // anchor — so the link lands on the passage, not on the top of the file.
    expect(first).toHaveAttribute(
      "href",
      "/knowledge?tab=docs&doc=ESPRESSO_TASTING_GUIDE&chunk=ESPRESSO_TASTING_GUIDE%23sour-vs-bitter",
    );
    expect(second).toHaveAttribute(
      "href",
      "/knowledge?tab=docs&doc=COFFEE_PROCESSING&chunk=COFFEE_PROCESSING%23the-methods%2Fnatural-dry-process",
    );
  });

  it("shows nothing where the model cited no excerpts", async () => {
    renderWithQueryClient(
      <AnalysisPanel shotId={6} hasSet analyses={[analysis({ output: { diagnosis: "Fine." } })]} />,
    );

    await screen.findByTestId("analysis-result");
    expect(screen.queryByTestId("excerpts-used")).not.toBeInTheDocument();
  });

  it("offers the analysis's proposals for a one-click confirm", async () => {
    const user = setupUser();
    getKnowledgeInsights.mockResolvedValue({
      items: [knowledgeInsight({ id: 9 })],
      scope_keys: [],
    });
    renderWithQueryClient(<AnalysisPanel shotId={6} hasSet analyses={[analysis()]} />);

    const proposed = await screen.findByTestId("proposed-insights");
    expect(proposed).toHaveTextContent("Naturals on this grinder");
    expect(getKnowledgeInsights).toHaveBeenCalledWith({ analysis_id: 1 });

    await user.click(within(proposed).getByTestId("toggle-insight"));
    await waitFor(() => expect(patchKnowledgeInsight).toHaveBeenCalledWith(9, { confirmed: true }));
  });

  it("says nothing about insights once they have all been confirmed", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [knowledgeInsight({ id: 9, confirmed: true })],
      scope_keys: [],
    });
    renderWithQueryClient(<AnalysisPanel shotId={6} hasSet analyses={[analysis()]} />);

    await screen.findByTestId("analysis-result");
    expect(screen.queryByTestId("proposed-insights")).not.toBeInTheDocument();
  });
});
