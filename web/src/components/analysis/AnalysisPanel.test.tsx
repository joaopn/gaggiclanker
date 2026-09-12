import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnalysisPanel } from "@/components/analysis/AnalysisPanel";
import { analysis, suggestion } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { vocabulary } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { getVocabulary, runAnalysis, getLlmCalls, acceptSuggestion, rejectSuggestion } = vi.hoisted(
  () => ({
    getVocabulary: vi.fn(),
    runAnalysis: vi.fn(),
    getLlmCalls: vi.fn(),
    acceptSuggestion: vi.fn(),
    rejectSuggestion: vi.fn(),
  }),
);
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
  runAnalysis,
  getLlmCalls,
  acceptSuggestion,
  rejectSuggestion,
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
    // Recorded, never applied: the panel has to say so where somebody would
    // otherwise look for a button.
    expect(screen.getByTestId("profile-patch")).toHaveTextContent("phase 1.duration");
    expect(screen.getByText(/writes nothing to the machine/)).toBeInTheDocument();
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
});
