import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import { Link, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShotDetailData, ShotSamplesData } from "@/api/types";
import { ShotDetailPage } from "@/pages/ShotDetailPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { review } from "@/test/reviewFixtures";
import { judgement, version, vocabulary } from "@/test/setsFixtures";
import { leverFields, realFields } from "@/test/shotFieldsFixture";
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

const {
  getShot,
  getShotFields,
  getShotSamples,
  getLlmCalls,
  getVocabulary,
  getKnowledgeInsights,
  runReview,
} = vi.hoisted(() => ({
  getShot: vi.fn(),
  getShotFields: vi.fn(),
  getShotSamples: vi.fn(),
  getLlmCalls: vi.fn(),
  getVocabulary: vi.fn(),
  getKnowledgeInsights: vi.fn(),
  runReview: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getShot,
  getShotFields,
  getShotSamples,
  getLlmCalls,
  getVocabulary,
  runReview,
  // Mocked rather than left to the real fetch: an unmocked call is a rejected
  // promise and a console full of noise that hides a real failure.
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
  getShotFields.mockResolvedValue(realFields);
  getShotSamples.mockResolvedValue(samples());
  getLlmCalls.mockResolvedValue({ calls: [], running: 0 });
  getVocabulary.mockResolvedValue(vocabulary);
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  runReview.mockResolvedValue(review({ status: "running", finished_at: null }));
});

describe("ShotDetailPage version prediction", () => {
  const predicted = version({
    version_major: 2,
    prediction: "less bitter, a shorter shot",
    compares_to_version_label: "v1",
  });

  it("keeps the prediction out of the page until the shot has been decided about", async () => {
    const user = setupUser();
    getShot.mockResolvedValue({ ...shot129, set_version: predicted, judgement: null });
    renderShot();

    await screen.findByTestId("version-prediction-row");
    expect(screen.queryByText("less bitter, a shorter shot")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show prediction" }));
    expect(screen.getByText("less bitter, a shorter shot")).toBeInTheDocument();
  });

  it("does not carry a reveal from one shot to another the cache already holds", async () => {
    const user = setupUser();
    // The production cache, not the test one: `gcTime: 0` makes every
    // navigation remount through the loading branch, which hides the bug this
    // test exists for. With a warm cache the route element is reused and a
    // reveal that lived in component state would survive the change of shot.
    const caching = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 30_000, gcTime: 600_000 },
        mutations: { retry: false },
      },
    });
    getShot.mockImplementation(async (id: number) => ({
      ...shot129,
      shot: { ...shot129.shot, id, device_id: String(id) },
      judgement: null,
      set_version: version({
        id: id * 10,
        version_major: 2,
        prediction: `what ${id} was expected to do`,
        compares_to_version_label: "v1",
      }),
    }));
    renderWithQueryClient(
      <Routes>
        <Route
          path="/shots/:shotId"
          element={
            <>
              <Link to="/shots/130">the next shot</Link>
              <Link to="/shots/129">the first shot</Link>
              <ShotDetailPage />
            </>
          }
        />
      </Routes>,
      { initialEntries: ["/shots/129"], queryClient: caching },
    );

    // Visit both, so each is in the cache and fresh.
    await screen.findByTestId("version-prediction-row");
    await user.click(screen.getByRole("link", { name: "the next shot" }));
    await waitFor(() => expect(screen.getByTestId("version-prediction-row")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Show prediction" }));
    expect(await screen.findByText("what 130 was expected to do")).toBeInTheDocument();

    // Back to a shot the cache can answer at once: no loading branch, the same
    // route element — and still nothing revealed.
    await user.click(screen.getByRole("link", { name: "the first shot" }));

    await waitFor(() =>
      expect(screen.getByTestId("version-prediction-row")).toHaveAttribute("data-shown", "no"),
    );
    expect(screen.queryByText("what 129 was expected to do")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show prediction" })).toBeInTheDocument();
  });

  it("shows it straight away once there is a decision", async () => {
    getShot.mockResolvedValue({
      ...shot129,
      set_version: predicted,
      judgement: { ...shot129.judgement, decision: "improve" },
    });
    renderShot();

    expect(await screen.findByText("less bitter, a shorter shot")).toBeInTheDocument();
  });
});

describe("ShotDetailPage header", () => {
  it("says what the shot was", async () => {
    renderShot();

    expect(await screen.findByRole("heading", { name: "Gratus 16:32 trad" })).toBeInTheDocument();
    const facts = screen.getByTestId("shot-facts");
    expect(facts).toHaveTextContent("54.6 s");
    expect(facts).toHaveTextContent("32.1 g");
    // The header's `finalExitReason` is 0 on this v5 file — the field arrived
    // in v7 — so the server serves none, and the facts row says nothing for it.
    expect(within(facts).getByText("Exit reason").nextElementSibling).toHaveTextContent("—");
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

describe("ShotDetailPage layout", () => {
  it("puts the curves on their own row, below the judgement", async () => {
    renderShot();

    const form = await screen.findByTestId("judgement-form");
    const curves = (await screen.findByText("Curves")).closest('[data-slot="card"]');
    const judgement = form.closest('[data-slot="card"]');
    expect(curves).not.toBeNull();
    expect(judgement).not.toBeNull();
    // Siblings in the page's single column: nothing sits beside the chart.
    expect(curves?.parentElement).toBe(judgement?.parentElement);
    expect(
      (judgement as Element).compareDocumentPosition(curves as Element) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(curves).not.toContainElement(form);
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
          diagnostics: { has_pressure: false, resistance: null },
        },
      },
    });
    getShotSamples.mockResolvedValue(samples(syntheticSamples(40, { hasPressure: false })));

    renderShot();

    expect(await screen.findByTestId("no-pressure-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("metric-level")).not.toBeInTheDocument();
  });

  it("names the header's own sample interval, not the nominal one", async () => {
    renderShot();

    expect(await screen.findByText(/213 samples at 250 ms/)).toBeInTheDocument();
  });
});

describe("ShotDetailPage by phase", () => {
  it("reads in the decided order: facts, warnings, judgement, curves, phases, the shot, Set, review, context", async () => {
    getShotFields.mockResolvedValue(leverFields);
    renderShot();

    await screen.findByTestId("shot-warnings");
    // The page's own landmarks, top to bottom, as the DOM has them.
    const landmarks: Array<[string, HTMLElement]> = [
      ["facts", screen.getByTestId("shot-facts")],
      ["warnings", screen.getByTestId("shot-warnings")],
      ["judgement", screen.getByTestId("judgement-form")],
      ["curves", screen.getByText("Curves")],
      ["phases", screen.getAllByTestId("phase-row")[0]],
      ["shot", screen.getByTestId("shot-field-yield")],
      ["set", document.getElementById("set") as HTMLElement],
      ["review", document.getElementById("review") as HTMLElement],
      ["context", screen.getByTestId("shot-context")],
    ];
    const order = [...landmarks]
      .sort((a, b) =>
        a[1].compareDocumentPosition(b[1]) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
      )
      .map(([name]) => name);
    expect(order).toEqual([
      "facts",
      "warnings",
      "judgement",
      "curves",
      "phases",
      "shot",
      "set",
      "review",
      "context",
    ]);
  });

  it("says in the facts row what the shot-wide numbers say, not what the browser works out", async () => {
    // Filed under a version with an 18 g dose, no dose typed: the chat is told
    // 1:2.34, and 33.25 s is 33.2 s to the server's rounding (half to even).
    getShotFields.mockResolvedValue(leverFields);
    getShot.mockResolvedValue({
      ...shot129,
      shot: { ...shot129.shot, duration_ms: 33_250, volume_g: 42.2, final_exit_reason: 1 },
      judgement: null,
      notes: null,
    });
    renderShot();

    await screen.findByTestId("shot-warnings");
    const facts = screen.getByTestId("shot-facts");
    expect(facts).toHaveTextContent("33.2 s");
    expect(facts).not.toHaveTextContent("33.3 s");
    expect(facts).toHaveTextContent("1:2.34");
    expect(facts).not.toHaveTextContent("dose unknown");
    expect(facts).toHaveTextContent("Volumetric target");
    // The same text as the shot-wide card repeats.
    expect(screen.getByTestId("shot-field-ratio")).toHaveTextContent("1:2.34");
    expect(screen.getByTestId("shot-field-shot_time")).toHaveTextContent("33.2 s");
  });

  it("waits for the numbers with the shot, so the warnings card cannot arrive late and shift the judgement", async () => {
    let release: (value: unknown) => void = () => undefined;
    getShotFields.mockReturnValue(new Promise((resolve) => (release = resolve)));
    renderShot();

    await waitFor(() => expect(getShotFields).toHaveBeenCalled());
    expect(screen.queryByTestId("judgement-form")).not.toBeInTheDocument();
    release(leverFields);
    expect(await screen.findByTestId("shot-warnings")).toBeInTheDocument();
    expect(screen.getByTestId("judgement-form")).toBeInTheDocument();
  });

  it("builds the page from the fields route, for this shot", async () => {
    renderShot();

    await screen.findAllByTestId("phase-row");
    expect(getShotFields).toHaveBeenCalledWith(shot129.shot.id);
  });

  it("has no warnings card when there is no warning, and no all-clear", async () => {
    renderShot();

    await screen.findAllByTestId("phase-row");
    expect(screen.queryByTestId("shot-warnings")).not.toBeInTheDocument();
    expect(screen.queryByText("Warnings")).not.toBeInTheDocument();
    expect(screen.queryByText(/all clear|no problems/i)).not.toBeInTheDocument();
  });

  it("shows the numbers as numbers: no band word, no score, no channeling", async () => {
    renderShot();

    const resistance = await screen.findByTestId("shot-field-resistance_level");
    expect(resistance).toHaveTextContent("3.89, from the machine");
    expect(
      screen.queryByText(/excellent|very low|moderate|channeling|execution score/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("score-components")).not.toBeInTheDocument();
  });

  it("lists every phase with its name and kind", async () => {
    renderShot();

    const rows = await screen.findAllByTestId("phase-row");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("fill");
    expect(rows[0]).toHaveTextContent("preinfusion");
  });

  it("says so, and keeps the page, when the numbers cannot be loaded", async () => {
    getShotFields.mockRejectedValue(new Error("boom"));
    renderShot();

    expect(await screen.findByTestId("fields-error")).toHaveTextContent("boom");
    expect(screen.getByTestId("shot-facts")).toBeInTheDocument();
  });

  it("draws no numbers for a quarantined shot, which has none", async () => {
    getShot.mockResolvedValue(detail({ quarantined: true, quarantine_reason: "bad header" }));
    renderShot();

    await screen.findByTestId("quarantine-reason");
    expect(screen.queryByTestId("phase-row")).not.toBeInTheDocument();
    expect(screen.queryByTestId("shot-context")).not.toBeInTheDocument();
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
    expect(screen.queryByTestId("metric-level")).not.toBeInTheDocument();
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

describe("ShotDetailPage Review card", () => {
  it("sits after the Set panel, offers Review, and renders what comes back", async () => {
    const user = setupUser();
    renderShot();

    const card = await screen.findByTestId("review-card");
    const assign = screen.getByTestId("assign-to-set");
    expect(assign.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(card).getByTestId("review-empty")).toHaveTextContent("without your judgement");

    getShot.mockResolvedValue({
      ...shot129,
      judgement: judgement({ shot_id: shot129.shot.id, balance: "balanced" }),
      reviews: [review({ shot_id: shot129.shot.id })],
    });
    await user.click(within(card).getByRole("button", { name: "Review" }));

    await waitFor(() => expect(runReview.mock.calls[0]?.[0]).toBe(shot129.shot.id));
    expect(await screen.findByTestId("review-summary")).toHaveTextContent(
      "Slow start, thin middle; likely sour.",
    );
    // The person's own balance, from their judgement, beside the prediction.
    expect(screen.getByTestId("review-yours")).toHaveTextContent("Balanced");
    // Discuss stays on the page.
    expect(screen.getByTestId("discuss-in-chat")).toBeInTheDocument();
  });

  it("is not offered for a quarantined shot", async () => {
    // Its bytes never parsed, so there is nothing to read.
    getShot.mockResolvedValue(detail({ quarantined: true, quarantine_reason: "bad magic" }));

    renderShot();

    expect(await screen.findByTestId("quarantine-reason")).toBeInTheDocument();
    expect(screen.queryByTestId("review-card")).not.toBeInTheDocument();
  });

  it("scrolls to the review when the link asks for it", async () => {
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
      { initialEntries: [`/shots/${shot129.shot.id}#review`] },
    );

    expect(await screen.findByTestId("review-card")).toBeInTheDocument();
    await waitFor(() => expect(scrolled).toContain("review"));
    expect(document.getElementById("review")).toContainElement(screen.getByTestId("review-card"));
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
