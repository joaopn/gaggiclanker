import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import {
  droppedWords,
  groupBySet,
  PatternsSection,
  patternFailure,
  scopeWords,
  sinceSentence,
} from "@/components/knowledge/PatternsSection";
import { patternProposal, patternRun, patternsData } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { bean, grinder } from "@/test/setsFixtures";

const {
  getPatterns,
  getBeans,
  getGrinders,
  startPatternRun,
  approvePatternProposal,
  dismissPatternProposal,
} = vi.hoisted(() => ({
  getPatterns: vi.fn(),
  getBeans: vi.fn(),
  getGrinders: vi.fn(),
  startPatternRun: vi.fn(),
  approvePatternProposal: vi.fn(),
  dismissPatternProposal: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getPatterns,
  getBeans,
  getGrinders,
  startPatternRun,
  approvePatternProposal,
  dismissPatternProposal,
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getPatterns.mockResolvedValue(patternsData());
  getBeans.mockResolvedValue({ items: [bean({ id: 3, name: "Kenya Kiambu" })] });
  getGrinders.mockResolvedValue({ items: [grinder({ id: 1, name: "Niche Zero" })] });
  startPatternRun.mockResolvedValue(patternRun({ status: "running", finished_at: null }));
});

function card() {
  return screen.findByTestId("pattern-proposal");
}

describe("the button and the sentence under it", () => {
  it("is disabled with a sentence while fewer than two Sets have a confirmed insight", async () => {
    getPatterns.mockResolvedValue(
      patternsData({ run: null, proposals: [], sets_with_insights: 1, new_since_last_run: 1 }),
    );
    renderWithQueryClient(<PatternsSection />);

    const button = await screen.findByTestId("find-patterns");
    expect(button).toBeDisabled();
    expect(screen.getByTestId("patterns-since")).toHaveTextContent(
      "Needs confirmed insights in at least 2 Sets, and one Set has them.",
    );
  });

  it("says how many confirmed Set insights arrived since the last run", async () => {
    renderWithQueryClient(<PatternsSection />);

    expect(await screen.findByTestId("find-patterns")).toBeEnabled();
    expect(screen.getByTestId("patterns-since")).toHaveTextContent(
      "2 confirmed Set insights have arrived since the last run.",
    );
  });

  it("before any run it says what a first run would read", () => {
    expect(sinceSentence(4, 3, 2, null, null)).toBe(
      "No run yet: 4 confirmed Set insights across 3 Sets to read.",
    );
    expect(sinceSentence(1, 3, 2, patternRun(), "2026-10-03T09:00:00.000Z")).toBe(
      "1 confirmed Set insight has arrived since the last run.",
    );
    expect(sinceSentence(0, 0, 2, null, null)).toContain("none have");
    expect(sinceSentence(4, 3, 2, patternRun({ status: "running" }), null)).toContain(
      "No run has finished yet",
    );
  });

  it("starts one run per press, however fast the second click comes", async () => {
    const user = setupUser();
    getPatterns.mockResolvedValue(patternsData({ run: null, proposals: [] }));
    let resolve: (value: unknown) => void = () => undefined;
    startPatternRun.mockReturnValue(new Promise((done) => (resolve = done)));
    renderWithQueryClient(<PatternsSection />);

    await user.dblClick(await screen.findByTestId("find-patterns"));

    expect(startPatternRun).toHaveBeenCalledTimes(1);
    resolve(patternRun({ status: "running", finished_at: null }));
  });
});

describe("what the newest run did", () => {
  it("shows a running run and keeps the button off", async () => {
    getPatterns.mockResolvedValue(
      patternsData({ run: patternRun({ status: "running", finished_at: null }), proposals: [] }),
    );
    renderWithQueryClient(<PatternsSection />);

    expect(await screen.findByTestId("patterns-running")).toHaveTextContent(
      "Reading 4 Set insights from 3 Sets",
    );
    expect(screen.getByTestId("find-patterns")).toBeDisabled();
    expect(screen.getByTestId("find-patterns")).toHaveTextContent("Finding patterns");
  });

  it("says in words why a run failed, and that nothing was changed", async () => {
    getPatterns.mockResolvedValue(
      patternsData({
        run: patternRun({ status: "failed", error: "auth: the key was refused" }),
        proposals: [],
      }),
    );
    renderWithQueryClient(<PatternsSection />);

    const failed = await screen.findByTestId("patterns-failed");
    expect(failed).toHaveTextContent("The last run failed: auth: the key was refused.");
    expect(failed).toHaveTextContent("Nothing was changed");
    expect(screen.getByTestId("find-patterns")).toBeEnabled();
  });

  it("keeps the earlier proposals under a failed run and says so", async () => {
    getPatterns.mockResolvedValue(
      patternsData({ run: patternRun({ id: 6, status: "failed", error: "timeout: slow" }) }),
    );
    renderWithQueryClient(<PatternsSection />);

    expect(await screen.findByTestId("patterns-failed")).toHaveTextContent(
      "the proposals from the run before it are still below",
    );
    expect(await card()).toBeInTheDocument();
  });

  it("says an interrupted run changed nothing", async () => {
    getPatterns.mockResolvedValue(
      patternsData({ run: patternRun({ status: "interrupted" }), proposals: [] }),
    );
    renderWithQueryClient(<PatternsSection />);

    expect(await screen.findByTestId("patterns-interrupted")).toHaveTextContent(
      "interrupted when the app stopped",
    );
  });

  it("counts from the earlier finished run when the newest one failed or is running", async () => {
    // The newest run failed; an earlier one finished, so "No run has finished yet" would lie.
    getPatterns.mockResolvedValue(
      patternsData({
        run: patternRun({ id: 6, status: "failed", error: "timeout: slow" }),
        counted_from: "2026-10-03T09:00:00.000Z",
        new_since_last_run: 3,
      }),
    );
    renderWithQueryClient(<PatternsSection />);

    const since = await screen.findByTestId("patterns-since");
    expect(since).toHaveTextContent(
      "3 confirmed Set insights have arrived since the last run that finished.",
    );
    expect(since).not.toHaveTextContent("No run has finished yet");
  });

  it("says a finished run found nothing, and what the checks dropped", async () => {
    getPatterns.mockResolvedValue(
      patternsData({
        run: patternRun({
          proposals_kept: 0,
          proposals_dropped: 3,
          dropped: { one_set: 2, invented_source: 1 },
        }),
        proposals: [],
      }),
    );
    renderWithQueryClient(<PatternsSection />);

    expect(await screen.findByTestId("patterns-none")).toHaveTextContent(
      "found nothing worth proposing",
    );
    expect(screen.getByTestId("patterns-dropped")).toHaveTextContent(
      "3 proposals were dropped before you saw them: 2 rested on one Set; 1 named an insight it was not given.",
    );
    expect(droppedWords(patternRun())).toBeNull();
  });
});

describe("a proposal's card", () => {
  it("shows the text, the scope in words and the sources grouped by Set, linked", async () => {
    renderWithQueryClient(<PatternsSection />);

    const proposal = await card();
    expect(within(proposal).getByTestId("pattern-text")).toHaveTextContent(
      "The Niche channels below 9 clicks with light roasts.",
    );
    // Named, not numbered: the grinder's name arrives with the catalogue.
    await waitFor(() =>
      expect(within(proposal).getByTestId("pattern-scope")).toHaveTextContent(
        "Applies to Sets with roast level light, grinder Niche Zero.",
      ),
    );
    const sets = within(proposal).getAllByTestId("pattern-source-set");
    expect(sets).toHaveLength(2);
    expect(
      within(sets[0] as HTMLElement).getByRole("link", { name: "Guji daily" }),
    ).toHaveAttribute("href", "/sets/3");
    expect(sets[1]).toHaveTextContent("It gushes under 9.");
    expect(within(proposal).getByTestId("pattern-consequence")).toHaveTextContent(
      "Approving deletes these 2 Set insights.",
    );
  });

  it("says the general insight it replaces will be deleted too", async () => {
    getPatterns.mockResolvedValue(
      patternsData({
        proposals: [patternProposal({ replaces_id: 7, replaces_text: "Old: below 10." })],
      }),
    );
    renderWithQueryClient(<PatternsSection />);

    const proposal = await card();
    expect(within(proposal).getByTestId("pattern-consequence")).toHaveTextContent(
      "and replaces this general insight (it is deleted too)",
    );
    expect(within(proposal).getByTestId("pattern-replaces")).toHaveTextContent("Old: below 10.");
  });

  it("says no longer there only once the catalogue has loaded without the item", async () => {
    let release: (value: unknown) => void = () => undefined;
    getBeans.mockReturnValue(new Promise((done) => (release = done)));
    getGrinders.mockReturnValue(new Promise(() => undefined));
    getPatterns.mockResolvedValue(
      patternsData({ proposals: [patternProposal({ scope: { bean_id: 9, grinder_id: 1 } })] }),
    );
    renderWithQueryClient(<PatternsSection />);

    const scope = await screen.findByTestId("pattern-scope");
    expect(scope).toHaveTextContent("Applies to Sets with bean …, grinder ….");
    expect(scope).not.toHaveTextContent("no longer there");

    // The beans arrive without bean 9 (the grinders are still pending).
    release({ items: [bean({ id: 3, name: "Kenya Kiambu" })] });
    await waitFor(() => expect(scope).toHaveTextContent("bean #9 (no longer there)"));
    expect(scope).toHaveTextContent("grinder …");
    expect(scope).not.toHaveTextContent("grinder #1");
  });

  it("shows the id, not an ellipsis for ever, when a catalogue failed to load", async () => {
    getBeans.mockRejectedValue(new Error("network down"));
    getGrinders.mockResolvedValue({ items: [grinder({ id: 1, name: "Niche Zero" })] });
    getPatterns.mockResolvedValue(
      patternsData({ proposals: [patternProposal({ scope: { bean_id: 9, grinder_id: 1 } })] }),
    );
    renderWithQueryClient(<PatternsSection />);

    const scope = await screen.findByTestId("pattern-scope");
    await waitFor(() => expect(scope).toHaveTextContent("bean #9, grinder Niche Zero"));
    expect(scope).not.toHaveTextContent("…");
    expect(scope).not.toHaveTextContent("no longer there");
  });

  it("words an empty scope as every Set", () => {
    expect(scopeWords({})).toBe("Applies to every Set.");
    const names = {
      beans: new Map([[3, "Kenya Kiambu"]]),
      grinders: new Map([[1, "Niche Zero"]]),
    };
    expect(scopeWords({ process: "washed", bean_id: 3, grinder_id: 1 }, names)).toBe(
      "Applies to Sets with process washed, bean Kenya Kiambu, grinder Niche Zero.",
    );
    // While a catalogue loads the thing is unknown, not gone.
    expect(
      scopeWords({ bean_id: 9 }, { ...names, loaded: { beans: "loading", grinders: "loaded" } }),
    ).toBe("Applies to Sets with bean ….");
    // A catalogue that failed to load leaves the id and claims nothing.
    expect(
      scopeWords({ bean_id: 9 }, { ...names, loaded: { beans: "failed", grinders: "loaded" } }),
    ).toBe("Applies to Sets with bean #9.");
    // An id is shown only when the thing is gone.
    expect(scopeWords({ bean_id: 9 }, names)).toBe(
      "Applies to Sets with bean #9 (no longer there).",
    );
  });

  it("groups sources by Set in the order the Sets first appear", () => {
    const groups = groupBySet([
      { insight_id: 1, set_id: 9, set_name: "B", text: "x" },
      { insight_id: 2, set_id: 4, set_name: "A", text: "y" },
      { insight_id: 3, set_id: 9, set_name: "B", text: "z" },
    ]);
    expect(groups.map((g) => [g.setId, g.items.length])).toEqual([
      [9, 2],
      [4, 1],
    ]);
  });
});

describe("while a run is going", () => {
  it("disables both answers and says why", async () => {
    getPatterns.mockResolvedValue(
      patternsData({ run: patternRun({ id: 6, status: "running", finished_at: null }) }),
    );
    renderWithQueryClient(<PatternsSection />);

    const proposal = await card();
    expect(within(proposal).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(within(proposal).getByRole("button", { name: "Dismiss" })).toBeDisabled();
    expect(within(proposal).getByTestId("pattern-run-going")).toHaveTextContent(
      "A run is reading the insights now",
    );
  });

  it("enables them again, with no sentence, once the run is over", async () => {
    renderWithQueryClient(<PatternsSection />);

    const proposal = await card();
    expect(within(proposal).getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(within(proposal).getByRole("button", { name: "Dismiss" })).toBeEnabled();
    expect(within(proposal).queryByTestId("pattern-run-going")).not.toBeInTheDocument();
  });

  it("words the server's refusal if one still gets through", () => {
    expect(
      patternFailure(new ApiClientError("x", { status: 409, code: "PATTERNS_RUNNING" })),
    ).toContain("A run is going");
  });
});

describe("answering a proposal", () => {
  it("approve sends the id once, and the card then says what was deleted", async () => {
    const user = setupUser();
    approvePatternProposal.mockResolvedValue({
      proposal: patternProposal({ status: "approved", insight_id: 30 }),
      skipped: [],
      deleted_insight_ids: [21, 22],
    });
    renderWithQueryClient(<PatternsSection />);

    await user.dblClick(await screen.findByRole("button", { name: "Approve" }));

    expect(approvePatternProposal).toHaveBeenCalledTimes(1);
    expect(approvePatternProposal).toHaveBeenCalledWith(11);
    const decided = await screen.findByTestId("pattern-decided");
    expect(decided).toHaveTextContent("Approved: the general insight is on this page");
    expect(decided).toHaveTextContent("2 Set insights were deleted");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("names a skipped source and counts only what was deleted", async () => {
    const user = setupUser();
    approvePatternProposal.mockResolvedValue({
      proposal: patternProposal({ status: "approved", insight_id: 30 }),
      skipped: [
        { insight_id: 22, set_id: 4, text: "It gushes under 9.", reason: "not_confirmed" },
        { insight_id: null, set_id: null, text: "Old: below 10.", reason: "replaced_gone" },
      ],
      deleted_insight_ids: [21],
    });
    renderWithQueryClient(<PatternsSection />);

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    const skipped = await screen.findByTestId("pattern-skipped");
    expect(skipped).toHaveTextContent(
      'A Set insight, "It gushes under 9." was taken back since, so it stays.',
    );
    expect(skipped).toHaveTextContent(
      'The general insight it was to replace "Old: below 10." was already gone.',
    );
    expect(screen.getByTestId("pattern-decided")).toHaveTextContent("1 Set insight was deleted");
  });

  it("dismiss sends the id and says the next run is told", async () => {
    const user = setupUser();
    dismissPatternProposal.mockResolvedValue({
      proposal: patternProposal({ status: "dismissed" }),
      skipped: [],
      deleted_insight_ids: [],
    });
    renderWithQueryClient(<PatternsSection />);

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    expect(dismissPatternProposal).toHaveBeenCalledWith(11);
    expect(await screen.findByTestId("pattern-decided")).toHaveTextContent(
      "the next run is told you declined it",
    );
  });

  it("says in words why an approval failed, naming no input", async () => {
    const user = setupUser();
    approvePatternProposal.mockRejectedValue(
      new ApiClientError("Fewer", { status: 409, code: "PATTERN_TOO_FEW_SETS" }),
    );
    renderWithQueryClient(<PatternsSection />);

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(screen.getByTestId("pattern-error")).toHaveTextContent("fewer than two Sets"),
    );
    // The card is still a question: nothing was written.
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("words the two refusals and passes any other message through", () => {
    expect(
      patternFailure(new ApiClientError("x", { status: 409, code: "PATTERN_PROPOSAL_DECIDED" })),
    ).toContain("already answered");
    expect(patternFailure(new Error("network down"))).toBe("network down");
  });
});
