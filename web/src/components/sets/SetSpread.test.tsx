import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetSpread } from "@/components/sets/SetSpread";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";
import { spreadReport, vocabulary } from "@/test/setsFixtures";

const { getVocabulary } = vi.hoisted(() => ({ getVocabulary: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
});

describe("SetSpread", () => {
  it("says the number and what it rests on once it is measured", async () => {
    renderWithQueryClient(<SetSpread spread={spreadReport()} />);

    const line = await screen.findByText(/^Shot time/);
    // Nine shots of one recipe and nine of four are very different evidence,
    // so the basis says both. The recipe count is the shots minus the degrees
    // of freedom — one degree spent on each group's own average.
    expect(line).toHaveTextContent("Shot time ±1.8 s · from 9 repeat shots of 4 recipes");
    expect(line.dataset.measured).toBe("yes");
  });

  it("counts one recipe in the singular", async () => {
    const spread = spreadReport({
      shot_time_s: { value: 1.8, measured: true, shots: 4, degrees_of_freedom: 3, recorded: 4 },
    });
    renderWithQueryClient(<SetSpread spread={spread} />);

    expect(
      await screen.findByText("Shot time ±1.8 s · from 4 repeat shots of 1 recipe"),
    ).toBeInTheDocument();
  });

  it("names the floor instead of the figure while it is not measured yet", async () => {
    renderWithQueryClient(<SetSpread spread={spreadReport()} />);

    // The yield's spread is served and real — 0.4 g over two degrees of
    // freedom — and it is deliberately not the number on the page: it is not
    // what a difference is held against yet.
    const line = await screen.findByText(/^Yield/);
    expect(line).toHaveTextContent(
      "Yield: not measured yet · differences under 1.00 g are not counted",
    );
    expect(line).not.toHaveTextContent("0.4");
    expect(line.dataset.measured).toBe("no");
  });

  it("leaves out a measure the archive holds nothing for", async () => {
    renderWithQueryClient(<SetSpread spread={spreadReport()} />);

    await screen.findByText(/^Shot time/);
    const measures = screen.getAllByTestId("spread-line").map((line) => line.dataset.measure);
    expect(measures).toEqual(["shot_time_s", "first_drip_s", "yield_g", "rating"]);
    expect(screen.queryByText(/Peak pressure/)).not.toBeInTheDocument();
  });

  it("writes a measure with no unit without one", async () => {
    renderWithQueryClient(<SetSpread spread={spreadReport()} />);

    expect(
      await screen.findByText("Rating ±0.5 · from 9 repeat shots of 4 recipes"),
    ).toBeInTheDocument();
  });

  it("writes every number to the precision the server serves it in", async () => {
    const spread = spreadReport({
      peak_pressure_bar: {
        value: 0.2,
        measured: true,
        shots: 6,
        degrees_of_freedom: 3,
        recorded: 6,
      },
      brew_flow_ml_s: { recorded: 6 },
    });
    renderWithQueryClient(<SetSpread spread={spread} />);

    // Two decimals for bar, and three for the floor it would be held against:
    // the page never invents a digit and never drops one.
    expect(await screen.findByText(/^Peak pressure/)).toHaveTextContent(
      "Peak pressure ±0.20 bar · from 6 repeat shots of 3 recipes",
    );
    expect(screen.getByText(/^Average brew flow/)).toHaveTextContent(
      "Average brew flow: not measured yet · differences under 0.200 ml/s are not counted",
    );
  });

  it("renders nothing at all for a Set with no shots", () => {
    const empty = spreadReport({
      shot_time_s: { value: null, measured: false, shots: 0, degrees_of_freedom: 0, recorded: 0 },
      first_drip_s: { recorded: 0 },
      yield_g: { value: null, shots: 0, degrees_of_freedom: 0, recorded: 0 },
      rating: { value: null, measured: false, shots: 0, degrees_of_freedom: 0, recorded: 0 },
    });

    renderWithQueryClient(<SetSpread spread={empty} />);

    expect(screen.queryByTestId("set-spread")).not.toBeInTheDocument();
  });

  it("falls back to the slug until the vocabulary has arrived", () => {
    renderWithQueryClient(<SetSpread spread={spreadReport()} />);

    // The labels are the server's, like every other closed list on this page.
    expect(screen.getByText(/^shot_time_s/)).toBeInTheDocument();
  });
});
