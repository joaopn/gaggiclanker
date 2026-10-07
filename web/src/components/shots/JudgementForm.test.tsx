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

  it("has no ratio and no grind: the server's ratio is elsewhere, and the grind is the recipe's", async () => {
    renderWithQueryClient(<JudgementForm shotId={1} judgement={null} />);

    await screen.findByLabelText("Dose in (g)");
    expect(screen.queryByTestId("judgement-ratio")).toBeNull();
    expect(screen.queryByLabelText("Grind")).toBeNull();
    expect(screen.queryByText("Ratio")).toBeNull();
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
    // The doses are above the notes, in the same column.
    const doseIn = screen.getByLabelText("Dose in (g)");
    expect(right).toContainElement(doseIn);
    expect(right).toContainElement(screen.getByLabelText("Dose out (g)"));
    expect(doseIn.compareDocumentPosition(notes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

  describe("the doses start where the shot says, and only a save records them", () => {
    it("shows the scale's yield and the Set version's dose as values, not placeholders", async () => {
      renderWithQueryClient(
        <JudgementForm shotId={1} judgement={null} prefill={{ doseIn: 18, doseOut: 36.4 }} />,
      );

      const doseIn = await screen.findByLabelText("Dose in (g)");
      const doseOut = screen.getByLabelText("Dose out (g)");
      expect(doseIn).toHaveValue("18");
      expect(doseOut).toHaveValue("36.4");
      expect(doseIn).not.toHaveAttribute("placeholder");
      expect(doseOut).not.toHaveAttribute("placeholder");
      // Nothing is saved by looking.
      expect(putJudgement).not.toHaveBeenCalled();
    });

    it("leaves dose out empty for a shot with no scale, and dose in empty for an unfiled shot", async () => {
      renderWithQueryClient(
        <JudgementForm shotId={1} judgement={null} prefill={{ doseIn: null, doseOut: null }} />,
      );
      expect(await screen.findByLabelText("Dose in (g)")).toHaveValue("");
      expect(screen.getByLabelText("Dose out (g)")).toHaveValue("");
    });

    it("fills each side on its own: a filed shot with no scale, a scale with no Set", async () => {
      const { unmount } = renderWithQueryClient(
        <JudgementForm shotId={1} judgement={null} prefill={{ doseIn: 18, doseOut: null }} />,
      );
      expect(await screen.findByLabelText("Dose in (g)")).toHaveValue("18");
      expect(screen.getByLabelText("Dose out (g)")).toHaveValue("");
      unmount();
      renderWithQueryClient(
        <JudgementForm shotId={1} judgement={null} prefill={{ doseIn: null, doseOut: 36.4 }} />,
      );
      expect(await screen.findByLabelText("Dose in (g)")).toHaveValue("");
      expect(screen.getByLabelText("Dose out (g)")).toHaveValue("36.4");
    });

    it("lets a saved value win over the prefill, field by field", async () => {
      renderWithQueryClient(
        <JudgementForm
          shotId={1}
          judgement={judgement({ dose_in_g: 17.5, dose_out_g: null })}
          prefill={{ doseIn: 18, doseOut: 36.4 }}
        />,
      );
      expect(await screen.findByLabelText("Dose in (g)")).toHaveValue("17.5");
      // Nothing saved for the dose out, so the scale's yield stands in until the next save.
      expect(screen.getByLabelText("Dose out (g)")).toHaveValue("36.4");
    });

    it("saves what is shown, once the person saves, and a prefill edited is the edit", async () => {
      const user = setupUser();
      renderWithQueryClient(
        <JudgementForm shotId={1} judgement={null} prefill={{ doseIn: 18, doseOut: 36.4 }} />,
      );
      const doseOut = await screen.findByLabelText("Dose out (g)");
      await user.clear(doseOut);
      await user.type(doseOut, "37");
      await user.click(screen.getByRole("button", { name: "Save judgement" }));

      await waitFor(() => expect(putJudgement).toHaveBeenCalledTimes(1));
      const body = putJudgement.mock.calls[0][1];
      expect(body.dose_in_g).toBe(18);
      expect(body.dose_out_g).toBe(37);
      expect(body).not.toHaveProperty("grind_setting");
    });
  });
});
