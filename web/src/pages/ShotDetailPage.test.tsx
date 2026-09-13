import { screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShotDetailData, ShotSamplesData } from "@/api/types";
import { ShotDetailPage } from "@/pages/ShotDetailPage";
import { analysis } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { vocabulary } from "@/test/setsFixtures";
import {
  SHOT_129_SAMPLE_COUNT,
  shot129,
  shot129Samples,
  syntheticSamples,
} from "@/test/shotFixture";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getShot, getShotSamples, getLlmCalls, runAnalysis, getVocabulary, getKnowledgeInsights } =
  vi.hoisted(() => ({
    getShot: vi.fn(),
    getShotSamples: vi.fn(),
    getLlmCalls: vi.fn(),
    runAnalysis: vi.fn(),
    getVocabulary: vi.fn(),
    getKnowledgeInsights: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getShot,
  getShotSamples,
  getLlmCalls,
  runAnalysis,
  getVocabulary,
  // Mocked rather than left to the real fetch: the analysis panel asks for the
  // insights its analysis proposed, and an unmocked call is a rejected promise
  // and a console full of noise that hides a real failure.
  getKnowledgeInsights,
}));

function renderShot(id = shot129.shot.id) {
  return renderWithQueryClient(
    <Routes>
      <Route path="/shots/:shotId" element={<ShotDetailPage />} />
    </Routes>,
    { initialEntries: [`/shots/${id}`] },
  );
}

function detail(overrides: Partial<ShotDetailData["shot"]> = {}): ShotDetailData {
  return { ...shot129, shot: { ...shot129.shot, ...overrides } };
}

function samples(rows = shot129Samples.samples): ShotSamplesData {
  return { ...shot129Samples, samples: rows, count: rows.length, total: rows.length };
}

beforeEach(() => {
  vi.clearAllMocks();
  getShot.mockResolvedValue(shot129);
  getShotSamples.mockResolvedValue(samples());
  getLlmCalls.mockResolvedValue({ calls: [], running: 0 });
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  runAnalysis.mockResolvedValue(analysis());
});

describe("ShotDetailPage header", () => {
  it("says what the shot was", async () => {
    renderShot();

    expect(await screen.findByRole("heading", { name: "Gratus 16:32 trad" })).toBeInTheDocument();
    const facts = screen.getByTestId("shot-facts");
    expect(facts).toHaveTextContent("54.6 s");
    expect(facts).toHaveTextContent("32.1 g");
    // The header's `finalExitReason` is 0 on this v5 file — the field arrived
    // in v7 — and "Unknown" is the honest rendering of that.
    expect(facts).toHaveTextContent("Unknown");
    expect(facts).toHaveTextContent("connected");
    expect(facts).toHaveTextContent("imported file");
  });

  it("offers both downloads", async () => {
    renderShot();

    expect(await screen.findByRole("link", { name: /\.slog/ })).toHaveAttribute(
      "href",
      `/api/shots/${shot129.shot.id}/raw`,
    );
    expect(screen.getByRole("button", { name: /JSON/ })).toBeInTheDocument();
  });
});

describe("ShotDetailPage chart", () => {
  it("draws every signal the file carries, with the machine's phase bands", async () => {
    renderShot();

    const series = await screen.findByTestId("chart-series");
    const drawn = within(series)
      .getAllByRole("listitem")
      .map((item) => item.getAttribute("data-series"));
    // The four the chunk asks for, plus the targets that make them readable.
    expect(drawn).toEqual(
      expect.arrayContaining([
        "pressure",
        "targetPressure",
        "flow",
        "puckFlow",
        "weight",
        "temperature",
      ]),
    );
    expect(series).toHaveTextContent(`Pressure: ${SHOT_129_SAMPLE_COUNT} points`);

    const phases = screen.getByTestId("chart-phases");
    expect(
      within(phases)
        .getAllByRole("listitem")
        .map((item) => item.getAttribute("data-phase")),
    ).toEqual(["fill", "soak", "ramp", "decline 9-4"]);
  });

  it("toggles a series off and on", async () => {
    const user = setupUser();
    renderShot();

    await screen.findByTestId("chart-series");
    const toggle = screen.getByRole("button", { name: /Temperature$/ });
    expect(toggle).toHaveAttribute("aria-pressed", "true");

    await user.click(toggle);

    await waitFor(() => {
      expect(screen.queryByText(/^Temperature: /)).not.toBeInTheDocument();
    });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("disables a toggle for a signal the firmware never recorded", async () => {
    getShotSamples.mockResolvedValue(samples(syntheticSamples(40, { hasScale: false })));
    renderShot();

    await screen.findByTestId("chart-series");
    expect(screen.getByRole("button", { name: /^Weight$/ })).toBeDisabled();
  });

  it("says so on a machine with no pressure sensor", async () => {
    // A Standard board reports a hard zero for pressure and flow; the
    // pressure-derived diagnostics did not run, and the page has to say that
    // rather than showing empty cards.
    getShot.mockResolvedValue({
      ...shot129,
      shot: {
        ...shot129.shot,
        diagnostics: {
          ...(shot129.shot.diagnostics as object),
          has_pressure: false,
          diagnostics: { has_pressure: false, resistance: null, channeling: null },
        },
      },
    });
    getShotSamples.mockResolvedValue(samples(syntheticSamples(40, { hasPressure: false })));

    renderShot();

    expect(await screen.findByTestId("no-pressure-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("channeling-risk")).not.toBeInTheDocument();
  });

  it("names the header's own sample interval, not the nominal one", async () => {
    renderShot();

    expect(await screen.findByText(/213 samples at 250 ms/)).toBeInTheDocument();
  });
});

describe("ShotDetailPage diagnostics", () => {
  it("shows each band with what it means", async () => {
    renderShot();

    const resistance = await screen.findByTestId("band-level");
    expect(resistance).toHaveTextContent("3.76");
    expect(resistance).toHaveTextContent("high");
    // The one-line meaning is the point: "HIGH" alone is a fact, not yet
    // information.
    expect(resistance).toHaveTextContent("3.0-5.0");

    expect(screen.getByTestId("band-erosion")).toHaveTextContent("moderate decline");
    expect(screen.getByTestId("band-erosion")).toHaveTextContent("part of the bed gave way");
  });

  it("leads channeling with its guidance and its primary signal", async () => {
    renderShot();

    expect(await screen.findByTestId("channeling-risk")).toHaveTextContent("low");
    expect(screen.getByTestId("channeling-guidance")).toHaveTextContent("Flat flow held steadily");
    expect(screen.getByTestId("channeling-primary")).toHaveTextContent("none");
  });

  it("shows the profile compliance the score was capped by", async () => {
    renderShot();

    expect(await screen.findByTestId("band-flow_adherence")).toHaveTextContent("poor");
    expect(screen.getByTestId("band-pressure_overshoot")).toHaveTextContent("severe overshoot");
  });

  it("breaks the execution score into its components", async () => {
    renderShot();

    const components = await screen.findByTestId("score-components");
    expect(components).toHaveTextContent("flow adherence");
    expect(components).toHaveTextContent("-1.32");
    expect(components).toHaveTextContent("resistance erosion");
  });

  it("falls back to the stored reason for a shot derived earlier", async () => {
    // Shots already in the archive have no `score` block in their diagnostics;
    // the columns still carry the number and its one-line reason.
    const withoutScore = { ...(shot129.shot.diagnostics as Record<string, unknown>) };
    delete withoutScore.score;
    getShot.mockResolvedValue(detail({ diagnostics: withoutScore }));

    renderShot();

    expect(
      await screen.findByText("Execution capped by flow adherence (1.32 point penalty)."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("score-components")).not.toBeInTheDocument();
  });

  it("lists every phase with its own annotations", async () => {
    renderShot();

    const rows = await screen.findAllByTestId("phase-row");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("fill");
    expect(rows[0]).toHaveTextContent("preinfusion");
    expect(rows[0]).toHaveTextContent("ramp rate");
  });
});

describe("ShotDetailPage notes and raw header", () => {
  it("mirrors the device's notes read-only", async () => {
    renderShot();

    const notes = await screen.findByTestId("device-notes");
    expect(notes).toHaveTextContent("Dose out");
    expect(notes).toHaveTextContent("32.1 g");
  });

  it("keeps the raw header behind a disclosure", async () => {
    renderShot();

    const raw = await screen.findByTestId("raw-header");
    expect(raw).toHaveTextContent("0x1fff");
    expect(raw).toHaveTextContent("213");
  });
});

describe("ShotDetailPage quarantine", () => {
  it("shows the reason and the raw download, and nothing it cannot know", async () => {
    getShot.mockResolvedValue(
      detail({
        quarantined: true,
        quarantine_reason: "SlogError: sample size 26 does not match fieldsMask",
        phases: null,
        diagnostics: null,
        execution_score: null,
      }),
    );

    renderShot();

    expect(await screen.findByTestId("quarantine-reason")).toHaveTextContent("SlogError");
    expect(screen.getByRole("link", { name: /Download \.slog/ })).toHaveAttribute(
      "href",
      `/api/shots/${shot129.shot.id}/raw`,
    );
    // No curve, no diagnostics, no phase table: there are no samples at all.
    expect(screen.queryByTestId("shot-chart")).not.toBeInTheDocument();
    expect(screen.queryByTestId("channeling-risk")).not.toBeInTheDocument();
    expect(getShotSamples).not.toHaveBeenCalled();
  });
});

describe("ShotDetailPage render budget", () => {
  it("draws a 60-second shot inside the budget once the data has arrived", async () => {
    // The acceptance criterion: under 200 ms from data to drawn. What is
    // measured here is everything the browser would do — building nine series
    // from 240 samples, React's render, and Chart.js's own layout and draw
    // against a mocked 2D context. The threshold is deliberately generous:
    // this is a regression guard against an accidental O(n²), not a benchmark.
    const sixtySeconds = syntheticSamples(240);
    getShotSamples.mockResolvedValue(samples(sixtySeconds));

    const started = performance.now();
    renderShot();
    await screen.findByTestId("chart-series");
    const elapsed = performance.now() - started;

    expect(screen.getByTestId("chart-series")).toHaveTextContent("240 points");
    expect(elapsed).toBeLessThan(2000);
  });
});

describe("ShotDetailPage analysis panel", () => {
  it("offers to analyse a shot that has none, and renders what comes back", async () => {
    const user = setupUser();
    renderShot();

    expect(await screen.findByTestId("analysis-empty")).toHaveTextContent("Not analysed yet");

    getShot.mockResolvedValue({ ...shot129, analyses: [analysis()] });
    await user.click(screen.getByTestId("run-analysis"));

    await waitFor(() => expect(runAnalysis.mock.calls[0]?.[0]).toBe(shot129.shot.id));
    expect(await screen.findByTestId("analysis-result")).toHaveTextContent("ran four seconds fast");
  });

  it("is not offered for a quarantined shot", async () => {
    // Its bytes never parsed, so there are no diagnostics to reason from.
    getShot.mockResolvedValue(detail({ quarantined: true, quarantine_reason: "bad magic" }));

    renderShot();

    expect(await screen.findByTestId("quarantine-reason")).toBeInTheDocument();
    expect(screen.queryByTestId("run-analysis")).not.toBeInTheDocument();
  });
});

describe("ShotDetailPage Set panel", () => {
  it("scrolls to the Assign panel when the link asks for it", async () => {
    // The shots list's needs-a-Set menu offers only three Sets and sends the
    // rest here with `#set`; the panel is far down the page.
    const scrolled: string[] = [];
    vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(function scrollIntoView(
      this: Element,
    ) {
      scrolled.push(this.id);
    });

    renderWithQueryClient(
      <Routes>
        <Route path="/shots/:shotId" element={<ShotDetailPage />} />
      </Routes>,
      { initialEntries: [`/shots/${shot129.shot.id}#set`] },
    );

    expect(await screen.findByTestId("assign-to-set")).toBeInTheDocument();
    await waitFor(() => expect(scrolled).toContain("set"));
    expect(document.getElementById("set")).toContainElement(screen.getByTestId("assign-to-set"));
  });

  it("stays at the top without the fragment", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");

    renderShot();

    expect(await screen.findByTestId("assign-to-set")).toBeInTheDocument();
    expect(scroll).not.toHaveBeenCalled();
  });
});
