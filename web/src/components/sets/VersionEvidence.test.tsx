import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VersionEvidence } from "@/components/sets/VersionEvidence";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { evidence, vocabulary } from "@/test/setsFixtures";

const { getVocabulary } = vi.hoisted(() => ({ getVocabulary: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
});

/** The row for one measure, once the disclosure is open. */
function row(measure: string) {
  return screen
    .getAllByTestId("evidence-row")
    .find((entry) => entry.dataset.measure === measure) as HTMLElement;
}

describe("VersionEvidence", () => {
  it("is closed by default, and the region it names exists anyway", () => {
    renderWithQueryClient(<VersionEvidence evidence={evidence()} versionNo={2} />);

    const button = screen.getByRole("button", { name: "Evidence" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    const region = document.getElementById(button.getAttribute("aria-controls") ?? "");
    expect(region).not.toBeNull();
    expect(region).toHaveAttribute("hidden");
    expect(screen.queryByTestId("evidence-table")).not.toBeInTheDocument();
  });

  it("opens without a click when it is told to", () => {
    renderWithQueryClient(<VersionEvidence evidence={evidence()} versionNo={2} defaultOpen />);

    expect(screen.getByRole("button", { name: "Evidence" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByTestId("evidence-table")).toBeInTheDocument();
  });

  it("lays both sides out in a table with headers", async () => {
    const user = setupUser();
    renderWithQueryClient(<VersionEvidence evidence={evidence()} versionNo={2} />);
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    const table = screen.getByRole("table");
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent);
    expect(headers).toEqual(["Measure", "v2", "v1", "Difference", "What it shows"]);

    const times = within(row("shot_time_s"))
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    // This version, the compared version, the signed difference, the verdict.
    expect(times).toEqual([
      "34.2 s · 3 shots",
      "29.1 s · 4 shots",
      "+5.13 s",
      "beyond the spread · held against 2.41 s",
    ]);
    // The measure's own label is the row's header, from the served vocabulary.
    expect(within(row("shot_time_s")).getByRole("rowheader")).toHaveTextContent("Shot time");
  });

  it("says each verdict in words, with what it was held against", async () => {
    const user = setupUser();
    renderWithQueryClient(<VersionEvidence evidence={evidence()} versionNo={2} />);
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    expect(screen.getAllByTestId("evidence-verdict").map((cell) => cell.textContent)).toEqual([
      "beyond the spread · held against 2.41 s",
      "inside the spread · held against 1.00 s",
      "inside the spread · held against 1.00 g",
      "no data",
      "no data",
      "beyond the spread · held against 0.50",
    ]);
  });

  it("marks a side that recorded nothing rather than printing a dash", async () => {
    const user = setupUser();
    renderWithQueryClient(<VersionEvidence evidence={evidence()} versionNo={2} />);
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    const cells = within(row("peak_pressure_bar"))
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells).toEqual(["not recorded", "not recorded", "—", "no data"]);
  });

  it("shows the balance and label counts for both sides, with no verdict", async () => {
    const user = setupUser();
    renderWithQueryClient(<VersionEvidence evidence={evidence()} versionNo={2} />);
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    const counts = screen.getByTestId("evidence-counts");
    expect(counts).toHaveTextContent("v2: 3 shots · 2 balanced · 1 bitter · 2 Keep · 1 Improve");
    expect(counts).toHaveTextContent(
      "v1: 4 shots · 3 sour · 1 balanced · 3 Improve · 1 not labelled",
    );
  });

  it("writes a difference a decimal finer than the means it came from", async () => {
    const user = setupUser();
    const near = evidence();
    near.measures[0] = {
      ...near.measures[0],
      this: { mean: 32.0, n: 2 },
      other: { mean: 30.0, n: 2 },
      difference: 2.04,
      yardstick: 2.0,
      verdict: "beyond",
    };
    renderWithQueryClient(<VersionEvidence evidence={near} versionNo={2} />);
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    // "+2 s … held against 2 s … beyond the spread" would read as a row
    // arguing with its own verdict.
    const cells = within(row("shot_time_s"))
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells).toEqual([
      "32.0 s · 2 shots",
      "30.0 s · 2 shots",
      "+2.04 s",
      "beyond the spread · held against 2.00 s",
    ]);
  });

  it("says what a difference is held against, in the region itself", async () => {
    const user = setupUser();
    renderWithQueryClient(<VersionEvidence evidence={evidence()} versionNo={2} />);
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    const sentence = screen.getByTestId("evidence-yardstick");
    expect(sentence).toHaveTextContent("two standard errors");
    expect(sentence).toHaveTextContent("never against less than that measure's floor");
    expect(sentence).toHaveTextContent("the floor is the whole yardstick");
  });

  it("shows a compared version that collected nothing as an empty side", async () => {
    const user = setupUser();
    const counts = evidence();
    const empty = evidence({
      measures: counts.measures.map((measure) => ({
        ...measure,
        other: { mean: null, n: 0 },
        difference: null,
        yardstick: null,
        verdict: "no_data" as const,
      })),
      other: {
        ...counts.other!,
        shots: 0,
        sour: 0,
        balanced: 0,
        bitter: 0,
        keep: 0,
        improve: 0,
        unlabelled: 0,
      },
    });
    renderWithQueryClient(<VersionEvidence evidence={empty} versionNo={2} />);
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    // There is a compared version, so there is a compared column — it is the
    // shots that are missing, not the comparison.
    const headers = within(screen.getByRole("table"))
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent);
    expect(headers).toEqual(["Measure", "v2", "v1", "Difference", "What it shows"]);
    expect(
      within(row("shot_time_s"))
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["34.2 s · 3 shots", "not recorded", "—", "no data"]);
    const summary = screen.getByTestId("evidence-counts");
    expect(summary).toHaveTextContent("v1: 0 shots");
    expect(summary).not.toHaveTextContent("Compared against nothing");
  });

  it("gives a version compared against nothing its own side only", async () => {
    const user = setupUser();
    const own = evidence({
      compares_to_version_id: null,
      other: null,
      measures: evidence().measures.map((measure) => ({
        ...measure,
        other: null,
        difference: null,
        yardstick: null,
        verdict: "no_data" as const,
      })),
    });
    renderWithQueryClient(<VersionEvidence evidence={own} versionNo={1} />);
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    const headers = within(screen.getByRole("table"))
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent);
    expect(headers).toEqual(["Measure", "v1"]);
    expect(screen.queryByTestId("evidence-verdict")).not.toBeInTheDocument();
    expect(screen.getByTestId("evidence-counts")).toHaveTextContent(
      "Compared against nothing — these are this version's own numbers.",
    );
  });
});
