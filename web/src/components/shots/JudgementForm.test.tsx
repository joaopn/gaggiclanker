import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { JudgementForm } from "@/components/shots/JudgementForm";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { flavorPicks, judgement, vocabulary } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getVocabulary, getFlavorPicks, putJudgement, deleteJudgement } = vi.hoisted(() => ({
  getVocabulary: vi.fn(),
  getFlavorPicks: vi.fn(),
  putJudgement: vi.fn(),
  deleteJudgement: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
  getFlavorPicks,
  putJudgement,
  deleteJudgement,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  getFlavorPicks.mockResolvedValue(flavorPicks());
  putJudgement.mockImplementation(async (_id: number, body: unknown) => ({
    ...judgement(),
    ...(body as object),
  }));
  deleteJudgement.mockResolvedValue({ deleted: true });
});

describe("JudgementForm", () => {
  it("renders the vocabularies the server serves rather than its own", async () => {
    renderWithQueryClient(<JudgementForm shotId={1} judgement={null} />);

    // Balance and the decisions come from /api/vocab, labels and all.
    expect(await screen.findByRole("button", { name: "Sour" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Improve" })).toBeInTheDocument();
  });

  it("computes the ratio from the two doses as they are typed", async () => {
    const user = setupUser();
    renderWithQueryClient(<JudgementForm shotId={1} judgement={null} />);

    expect(await screen.findByTestId("judgement-ratio")).toHaveTextContent("needs both doses");
    await user.type(screen.getByLabelText("Dose in (g)"), "18");
    await user.type(screen.getByLabelText("Dose out (g)"), "36");

    expect(screen.getByTestId("judgement-ratio")).toHaveTextContent("1:2.0");
  });

  it("counts the notes against the machine's own 200-character limit", async () => {
    const user = setupUser();
    renderWithQueryClient(<JudgementForm shotId={1} judgement={null} />);

    const notes = await screen.findByLabelText("Notes");
    expect(notes).toHaveAttribute("maxLength", "200");
    await user.type(notes, "sharp");

    expect(screen.getByTestId("notes-counter")).toHaveTextContent("5/200");
  });

  it("puts the notes in a column of their own, to the right of the verdict", async () => {
    renderWithQueryClient(<JudgementForm shotId={1} judgement={null} />);

    const notes = await screen.findByLabelText("Notes");
    const columns = screen.getByTestId("judgement-columns");
    const [left, right] = Array.from(columns.children);
    expect(columns.children).toHaveLength(2);
    expect(right).toContainElement(notes);
    expect(right).toContainElement(screen.getByTestId("notes-counter"));
    expect(left).not.toContainElement(notes);
    expect(left).toContainElement(screen.getByLabelText("Dose in (g)"));
    expect(left).toHaveTextContent("Rating");
  });

  it("sends what was picked, and clears a value when it is picked again", async () => {
    const user = setupUser();
    renderWithQueryClient(<JudgementForm shotId={1} judgement={null} />);

    await user.click(await screen.findByRole("button", { name: "4 stars" }));
    await user.click(screen.getByRole("button", { name: "Sour" }));
    // "Not rated" and "not decided" are real answers, and the second click is
    // the only place to say them.
    await user.click(screen.getByRole("button", { name: "Keep" }));
    await user.click(screen.getByRole("button", { name: "Keep" }));
    await user.click(screen.getByRole("button", { name: "Save judgement" }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
    expect(putJudgement).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ rating: 4, balance: "sour", decision: null }),
    );
  });

  it("edits the aroma and taste notes with the panel's chips, saved with the form", async () => {
    const user = setupUser();
    renderWithQueryClient(<JudgementForm shotId={1} judgement={judgement()} />);

    const taste = within(await screen.findByRole("group", { name: "Taste notes" }));
    const aroma = within(screen.getByRole("group", { name: "Aroma notes" }));
    // The recorded note that is not a pick is there to be taken off.
    expect(taste.getByRole("button", { name: "Sour/Fermented › Sour" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(taste.getByRole("button", { name: "Sour/Fermented › Sour" }));
    await user.click(taste.getByRole("button", { name: "Other › Chemical › Bitter" }));
    await user.click(aroma.getByRole("button", { name: "Floral" }));
    // Nothing is written until the form is saved.
    expect(putJudgement).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Save judgement" }));

    await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
    expect(putJudgement.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        taste_notes: ["other.chemical.bitter"],
        aroma_notes: ["fruity.berry", "floral"],
        decision: "improve",
      }),
    );
  });

  it("says when a judgement came from the machine's notes card", async () => {
    renderWithQueryClient(
      <JudgementForm shotId={1} judgement={judgement({ seeded_from_device_note: true })} />,
    );

    expect(await screen.findByTestId("seeded-badge")).toHaveTextContent("from the machine");
  });

  it("loads an existing verdict into the form", async () => {
    renderWithQueryClient(<JudgementForm shotId={1} judgement={judgement()} />);

    expect(await screen.findByLabelText("Dose in (g)")).toHaveValue("18");
    expect(screen.getByRole("button", { name: "4 stars" })).toHaveAttribute("aria-pressed", "true");
    // The balance only renders once /api/vocab has answered, so this one waits.
    expect(await screen.findByRole("button", { name: "Sour" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("judgement-ratio")).toHaveTextContent("1:2.0");
  });

  it("does not offer to withdraw a verdict that does not exist", async () => {
    renderWithQueryClient(<JudgementForm shotId={1} judgement={null} />);

    expect(await screen.findByRole("button", { name: "Save judgement" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Withdraw/ })).not.toBeInTheDocument();
  });

  it("withdraws a verdict rather than making the user invent a rating", async () => {
    const user = setupUser();
    renderWithQueryClient(<JudgementForm shotId={1} judgement={judgement()} />);

    await user.click(await screen.findByRole("button", { name: /Withdraw/ }));

    // TanStack hands the mutation function a context object as its second
    // argument, so the assertion is on the first one.
    await waitFor(() => expect(deleteJudgement).toHaveBeenCalled());
    expect(deleteJudgement.mock.calls[0][0]).toBe(1);
  });
});
