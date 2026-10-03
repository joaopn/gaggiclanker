import { act, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { InsightDeletionCard, insightDeletionFailure } from "@/components/chat/InsightDeletionCard";
import { ChatThreadContext } from "@/components/chat/tellAgent";
import { insightDeletion } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getInsightDeletions, acceptInsightDeletion, keepInsightDeletion, toastSuccess } =
  vi.hoisted(() => ({
    getInsightDeletions: vi.fn(),
    acceptInsightDeletion: vi.fn(),
    keepInsightDeletion: vi.fn(),
    toastSuccess: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getInsightDeletions,
  acceptInsightDeletion,
  keepInsightDeletion,
}));
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const FALLBACK = { insightId: 7, text: "Two clicks finer on the Niche." };

beforeEach(() => {
  vi.clearAllMocks();
  getInsightDeletions.mockResolvedValue({ items: [insightDeletion()] });
});

function renderCard() {
  return renderWithQueryClient(
    <ChatThreadContext.Provider value={14}>
      <InsightDeletionCard setId={3} proposalId={9} fallback={FALLBACK} />
    </ChatThreadContext.Provider>,
  );
}

/**
 * The card is the one place an agent's reason becomes a removal, so what it has to get
 * right is the decision: what would go and why, that nothing goes until Delete, what
 * each button does, and what is left of the card once it is no longer a question.
 */
describe("InsightDeletionCard", () => {
  it("shows the tool's own words until the live row has been read", () => {
    getInsightDeletions.mockReturnValue(new Promise(() => undefined));
    renderCard();

    const holder = screen.getByTestId("propose-card-insight_deletion");
    expect(holder).toHaveTextContent("Two clicks finer on the Niche.");
    expect(holder).toHaveTextContent("waiting for you");
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("shows the insight, the reason, and says nothing is deleted until Delete", async () => {
    renderCard();

    const card = await screen.findByTestId("insight-deletion-card");
    expect(getInsightDeletions).toHaveBeenCalledWith(3, 14);
    expect(card).toHaveAttribute("data-status", "proposed");
    expect(within(card).getByTestId("insight-deletion-text")).toHaveTextContent(
      "Two clicks finer on the Niche.",
    );
    expect(within(card).getByTestId("insight-deletion-reason")).toHaveTextContent(
      "The last two shots contradict it on both measures.",
    );
    expect(within(card).getByRole("button", { name: "Delete" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "Keep" })).toBeEnabled();
    expect(card).toHaveTextContent("Nothing is deleted until you press Delete");
    expect(card).toHaveTextContent("still told to this Set's conversations");
    expect(acceptInsightDeletion).not.toHaveBeenCalled();
  });

  it("deletes on Delete and then says what is left of it", async () => {
    const user = setupUser();
    acceptInsightDeletion.mockResolvedValue({
      proposal: insightDeletion({ status: "deleted", insight_id: null }),
    });
    getInsightDeletions
      .mockResolvedValueOnce({ items: [insightDeletion()] })
      .mockResolvedValue({ items: [insightDeletion({ status: "deleted", insight_id: null })] });
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Delete" }));

    expect(acceptInsightDeletion).toHaveBeenCalledWith(3, 9);
    const decided = await screen.findByTestId("insight-deletion-decided");
    expect(decided).toHaveTextContent("Deleted: the insight is gone from this Set.");
    expect(decided).toHaveTextContent("Only this card remembers what it said.");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByTestId("insight-deletion-card")).toHaveAttribute("data-status", "deleted");
  });

  it("keeps on Keep and changes nothing else", async () => {
    const user = setupUser();
    keepInsightDeletion.mockResolvedValue({ proposal: insightDeletion({ status: "kept" }) });
    getInsightDeletions
      .mockResolvedValueOnce({ items: [insightDeletion()] })
      .mockResolvedValue({ items: [insightDeletion({ status: "kept" })] });
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Keep" }));

    expect(keepInsightDeletion).toHaveBeenCalledWith(3, 9);
    expect(acceptInsightDeletion).not.toHaveBeenCalled();
    expect(await screen.findByTestId("insight-deletion-decided")).toHaveTextContent(
      "Kept: the insight stays as it was",
    );
  });

  it.each([
    ["stale", "Already gone: the insight was removed another way"],
    ["superseded", "A newer proposal for this insight replaced this one."],
    ["deleted", "Deleted: the insight is gone"],
    ["kept", "Kept: the insight stays"],
  ] as const)("says %s in words, with no buttons", async (status, words) => {
    getInsightDeletions.mockResolvedValue({ items: [insightDeletion({ status })] });
    renderCard();

    expect(await screen.findByTestId("insight-deletion-decided")).toHaveTextContent(words);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("reads its proposal by conversation, so an old one is found", async () => {
    renderCard();
    await screen.findByTestId("insight-deletion-card");
    expect(getInsightDeletions).toHaveBeenCalledWith(3, 14);
  });

  it("says a stale proposal whose insight is gone has no words left of it", async () => {
    getInsightDeletions.mockResolvedValue({
      items: [insightDeletion({ status: "stale", insight_text: "", insight_id: null })],
    });
    renderCard();

    const card = await screen.findByTestId("insight-deletion-card");
    expect(within(card).getByTestId("insight-deletion-text")).toHaveTextContent(
      "That insight has since been removed.",
    );
    expect(screen.getByTestId("insight-deletion-decided")).toHaveTextContent("Already gone");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("keeps saying a newer proposal replaced a superseded one whose insight was removed", async () => {
    getInsightDeletions.mockResolvedValue({
      items: [insightDeletion({ status: "superseded", insight_text: "", insight_id: null })],
    });
    renderCard();

    expect(await screen.findByTestId("insight-deletion-decided")).toHaveTextContent(
      "A newer proposal for this insight replaced this one.",
    );
    expect(screen.getByTestId("insight-deletion-text")).toHaveTextContent(
      "That insight has since been removed.",
    );
  });

  it("sends one request however fast the second click comes", async () => {
    const user = setupUser();
    let resolve: (value: unknown) => void = () => undefined;
    acceptInsightDeletion.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    renderCard();

    await user.dblClick(await screen.findByRole("button", { name: "Delete" }));
    await act(async () =>
      resolve({ proposal: insightDeletion({ status: "deleted", insight_id: null }) }),
    );

    expect(acceptInsightDeletion).toHaveBeenCalledTimes(1);
  });

  it("says why a press failed, in words", async () => {
    const user = setupUser();
    acceptInsightDeletion.mockRejectedValue(
      new ApiClientError("answered", { status: 409, code: "INSIGHT_DELETION_DECIDED" }),
    );
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Delete" }));

    expect(await screen.findByTestId("insight-deletion-error")).toHaveTextContent(
      "This proposal was already answered",
    );
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("says an insight that was taken back cannot be deleted by this card", () => {
    expect(
      insightDeletionFailure(new ApiClientError("x", { status: 409, code: "INSIGHT_NOT_ADDED" })),
    ).toContain("not added any more");
    expect(insightDeletionFailure(new Error("the box fell over"))).toBe("the box fell over");
  });
});
