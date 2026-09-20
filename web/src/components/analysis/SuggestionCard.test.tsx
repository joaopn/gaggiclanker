import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SuggestionCard } from "@/components/analysis/SuggestionCard";
import { suggestion } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { vocabulary } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { getVocabulary, acceptSuggestion, rejectSuggestion } = vi.hoisted(() => ({
  getVocabulary: vi.fn(),
  acceptSuggestion: vi.fn(),
  rejectSuggestion: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
  acceptSuggestion,
  rejectSuggestion,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  acceptSuggestion.mockResolvedValue({
    suggestion: suggestion({ status: "accepted", resulting_set_version_id: 2 }),
    version: { version_no: 2 },
  });
  rejectSuggestion.mockResolvedValue(suggestion({ status: "rejected" }));
});

describe("SuggestionCard", () => {
  it("writes the change in the grinder's own words", async () => {
    renderWithQueryClient(<SuggestionCard suggestion={suggestion()} />);

    // "Grind" and "grinder steps" both come from /api/vocab.
    expect(await screen.findByText(/Grind finer 2 grinder steps/)).toBeInTheDocument();
    expect(screen.getByText(/28 s target/)).toBeInTheDocument();
  });

  it("accepts and rejects", async () => {
    const user = setupUser();
    renderWithQueryClient(<SuggestionCard suggestion={suggestion({ id: 7 })} />);

    // TanStack hands the mutation function a context object as its second
    // argument, so the id is asserted positionally rather than on the whole
    // call.
    // Accept appears once `/api/vocab` says the grind is one of the variables
    // a Set version records.
    await user.click(await screen.findByTestId("accept-suggestion"));
    await waitFor(() => expect(acceptSuggestion.mock.calls[0]?.[0]).toBe(7));

    await user.click(screen.getByTestId("reject-suggestion"));
    await waitFor(() => expect(rejectSuggestion.mock.calls[0]?.[0]).toBe(7));
  });

  it("offers no accept for a variable no Set version can record", async () => {
    renderWithQueryClient(<SuggestionCard suggestion={suggestion({ variable: "pressure" })} />);

    expect(await screen.findByTestId("not-actionable")).toBeInTheDocument();
    expect(screen.queryByTestId("accept-suggestion")).not.toBeInTheDocument();
    // It points at the draft flow rather than saying "cannot be done": since
    // profile drafts exist, a profile change has a route, it is just not this button.
    expect(screen.getByText(/Draft profile/)).toBeInTheDocument();
    // But it can still be turned down: the advice was read and disagreed with.
    expect(screen.getByTestId("reject-suggestion")).toBeInTheDocument();
  });

  it("treats a temperature like a profile change, as the server does", async () => {
    // The machine brews at the profile's temperature, so accepting one here
    // would be a 409. Which variables can be accepted is `/api/vocab`'s answer,
    // not this component's, and this is the case that proved why.
    renderWithQueryClient(
      <SuggestionCard suggestion={suggestion({ variable: "temperature", unit: "c" })} />,
    );

    expect(await screen.findByTestId("not-actionable")).toHaveTextContent(
      /change to the brew profile/,
    );
    expect(screen.queryByTestId("accept-suggestion")).not.toBeInTheDocument();
    expect(screen.getByTestId("reject-suggestion")).toBeInTheDocument();
  });

  it("offers nothing until the list of what can be accepted has arrived", async () => {
    // Neither claim can be made yet: showing the button would invite a 409 and
    // showing the explanation would call good advice a profile change.
    getVocabulary.mockReturnValue(new Promise(() => {}));
    renderWithQueryClient(<SuggestionCard suggestion={suggestion()} />);

    expect(await screen.findByTestId("reject-suggestion")).toBeInTheDocument();
    expect(screen.queryByTestId("accept-suggestion")).not.toBeInTheDocument();
    expect(screen.queryByTestId("not-actionable")).not.toBeInTheDocument();
  });

  it("shows a resolved suggestion without its buttons", () => {
    renderWithQueryClient(
      <SuggestionCard
        suggestion={suggestion({ status: "accepted", resulting_set_version_id: 4 })}
      />,
    );

    expect(screen.getByText("accepted")).toBeInTheDocument();
    expect(screen.getByText(/new Set version \(#4\)/)).toBeInTheDocument();
    expect(screen.queryByTestId("accept-suggestion")).not.toBeInTheDocument();
  });

  it("calls a superseded suggestion overtaken rather than rejected", () => {
    renderWithQueryClient(<SuggestionCard suggestion={suggestion({ status: "superseded" })} />);

    expect(screen.getByText("overtaken")).toBeInTheDocument();
  });
});
