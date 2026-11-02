import { act, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import {
  previewWords,
  SignatureRequestCard,
  signatureRequestFailure,
} from "@/components/chat/SignatureRequestCard";
import { ChatThreadContext } from "@/components/chat/tellAgent";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { requestChange, signatureRequest } from "@/test/signatureRequestFixtures";

const { getSignatureRequests, approveSignatureRequest, declineSignatureRequest } = vi.hoisted(
  () => ({
    getSignatureRequests: vi.fn(),
    approveSignatureRequest: vi.fn(),
    declineSignatureRequest: vi.fn(),
  }),
);
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSignatureRequests,
  approveSignatureRequest,
  declineSignatureRequest,
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const FALLBACK = {
  profileLabel: "Regio Cappuccino",
  changes: ["replace #73 “lowest value of temperature in the Hold, at least 93”"],
};

beforeEach(() => {
  vi.clearAllMocks();
  getSignatureRequests.mockResolvedValue({ items: [signatureRequest()] });
});

function renderCard() {
  return renderWithQueryClient(
    <ChatThreadContext.Provider value={14}>
      <SignatureRequestCard requestId={4} profileVersionId={27} fallback={FALLBACK} />
    </ChatThreadContext.Provider>,
  );
}

/**
 * The card is the one place an agent's reason becomes a change to what every shot is checked
 * against: what would change, why, what it does to the version's shots, that nothing changes
 * before Approve, and what is left of it once it is no longer a question.
 */
describe("what a preview says from its numbers", () => {
  const preview = (shots: number, failing_now: number, failing_after: number) => ({
    shots,
    failing_now,
    failing_after,
  });

  it("never says a check fails on 0: it passes on all of them", () => {
    expect(previewWords("replace", preview(9, 3, 0))).toBe(
      "On this version's 9 shots: this check fails on 3 now, and would pass on all 9.",
    );
    expect(previewWords("replace", preview(9, 0, 2))).toBe(
      "On this version's 9 shots: this check passes on all 9 now, and would fail on 2.",
    );
    expect(previewWords("retire", preview(1, 0, 0))).toBe(
      "On this version's 1 shot: this check passes on all 1 now, and nothing is checked once it is retired.",
    );
  });

  it("leaves out the 'now' half of a put back, since the row is not in force", () => {
    expect(previewWords("put_back", preview(9, 0, 4))).toBe(
      "On this version's 9 shots: once it is back, this check would fail on 4.",
    );
    expect(previewWords("put_back", preview(9, 0, 0))).toBe(
      "On this version's 9 shots: once it is back, this check would pass on all 9.",
    );
    expect(previewWords("put_back", preview(9, 0, 4))).not.toMatch(/fails on 0|now/);
  });

  it("says so when there is nothing to count", () => {
    expect(previewWords("replace", preview(0, 0, 0))).toBe(
      "This version has no shots yet, so there is nothing to count.",
    );
  });
});

describe("SignatureRequestCard", () => {
  it("shows the tool's own words until the live row has been read", () => {
    getSignatureRequests.mockReturnValue(new Promise(() => undefined));
    renderCard();

    const holder = screen.getByTestId("propose-card-signature_request");
    expect(holder).toHaveTextContent("Regio Cappuccino");
    expect(holder).toHaveTextContent("replace #73");
    expect(holder).toHaveTextContent("waiting for you");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("reads the conversation's requests, and outside a conversation those of its version", async () => {
    renderCard();
    await screen.findByTestId("signature-request-card");
    expect(getSignatureRequests).toHaveBeenCalledWith({ threadId: 14 });

    getSignatureRequests.mockClear();
    renderWithQueryClient(
      <SignatureRequestCard requestId={4} profileVersionId={27} fallback={FALLBACK} />,
    );
    await screen.findAllByTestId("signature-request-card");
    expect(getSignatureRequests).toHaveBeenCalledWith({ profileVersionId: 27 });
  });

  it("shows the change, the reason, the preview and says nothing changes before Approve", async () => {
    renderCard();

    const card = await screen.findByTestId("signature-request-card");
    expect(card).toHaveAttribute("data-status", "waiting");
    // The profile's version, named as the Profiles page names it, with no date: the Recipe
    // version the loosening line names has a name of its own.
    expect(within(card).getByTestId("request-title")).toHaveTextContent(
      /^Signature change · Regio Cappuccino · profile v3$/,
    );
    expect(card).toHaveTextContent("Replace");
    expect(card).toHaveTextContent("important · Hold");
    expect(within(card).getByTestId("request-limits")).toHaveTextContent(
      "lowest value of temperature in the Hold at least 93 °C → at least 91.5 °C",
    );
    expect(within(card).getByTestId("request-change-reason")).toHaveTextContent(
      "93 was a floor 2 °C under the old 95 °C setpoint",
    );
    expect(within(card).getByTestId("request-preview")).toHaveTextContent(
      "On this version's 9 shots: this check fails on 9 now, and would pass on all 9.",
    );
    expect(within(card).getByTestId("request-stays")).toHaveTextContent(
      "#77 “lowest value of temperature in the Hold, at least 91 °C” stays in force.",
    );
    expect(card).toHaveTextContent("Nothing changes until you press Approve");
    expect(screen.queryByTestId("request-loosens")).not.toBeInTheDocument();
    expect(approveSignatureRequest).not.toHaveBeenCalled();
  });

  it("says a loosening is graded under an open prediction, only while it waits", async () => {
    getSignatureRequests.mockResolvedValue({
      items: [signatureRequest({ loosens_open_prediction: "Regio Cappuccino v2.4" })],
    });
    renderCard();

    expect(await screen.findByTestId("request-loosens")).toHaveTextContent(
      "This loosens a check that Regio Cappuccino v2.4's open prediction is graded under.",
    );
  });

  it("writes a replacement that changes more than the limit as the two sentences", async () => {
    getSignatureRequests.mockResolvedValue({
      items: [
        signatureRequest({
          changes: [
            requestChange({
              new_sentence: "mean of temperature over the Hold, at least 91.5",
              stays_in_force: [],
              preview: null,
            }),
          ],
        }),
      ],
    });
    renderCard();

    const limits = await screen.findByTestId("request-limits");
    expect(limits).toHaveTextContent(
      "lowest value of temperature in the Hold, at least 93 °C → mean of temperature over the Hold, at least 91.5",
    );
    expect(screen.queryByTestId("request-preview")).not.toBeInTheDocument();
  });

  it("shows a tier change as critical → important, and a retirement's preview as nothing checked", async () => {
    getSignatureRequests.mockResolvedValue({
      items: [
        signatureRequest({
          changes: [
            requestChange({
              kind: "tier",
              old_tier: "critical",
              new_tier: "important",
              // The header names the tier the row has now; the line says where it goes.
              tier: "critical",
              preview: null,
              stays_in_force: [],
            }),
            requestChange({
              kind: "retire",
              preview: { shots: 9, failing_now: 9, failing_after: 0 },
            }),
          ],
        }),
      ],
    });
    renderCard();

    const tiers = await screen.findByTestId("request-tiers");
    expect(tiers).toHaveTextContent("critical → important");
    const [tierRow] = screen.getAllByTestId("request-change");
    expect(tierRow).toHaveTextContent("Tier critical · Hold");
    expect(tierRow).not.toHaveTextContent("important · Hold");
    expect(screen.getByTestId("request-preview")).toHaveTextContent(
      "this check fails on 9 now, and nothing is checked once it is retired.",
    );
  });

  it("shows a put back with the person's own rejection and its date", async () => {
    getSignatureRequests.mockResolvedValue({
      items: [
        signatureRequest({
          changes: [
            requestChange({
              kind: "put_back",
              rejected_reason: "too tight for this bean",
              rejected_at: "2026-10-20T09:00:00.000Z",
              preview: null,
            }),
          ],
        }),
      ],
    });
    renderCard();

    const rejected = await screen.findByTestId("request-rejected");
    expect(rejected).toHaveTextContent("you rejected it");
    expect(rejected).toHaveTextContent("“too tight for this bean”");
  });

  it("shows a put back of this version's limit with its unit", async () => {
    getSignatureRequests.mockResolvedValue({
      items: [
        signatureRequest({
          changes: [
            requestChange({
              kind: "put_back_override",
              expectation_id: null,
              override_id: 9,
              new_sentence: "at most 98 °C",
              rejected_reason: "keep the profile's limit",
              rejected_at: "2026-10-20T09:00:00.000Z",
              preview: null,
            }),
          ],
        }),
      ],
    });
    renderCard();

    expect(await screen.findByTestId("signature-request-card")).toHaveTextContent(
      "This version's limit: at most 98 °C",
    );
  });

  it("approves on Approve and then says what became of the rows", async () => {
    const user = setupUser();
    const approved = signatureRequest({
      status: "approved",
      answered_at: "2026-11-01T10:00:00.000Z",
      changes: [requestChange({ written_expectation_id: 81, preview: null })],
    });
    approveSignatureRequest.mockResolvedValue({ request: approved });
    getSignatureRequests
      .mockResolvedValueOnce({ items: [signatureRequest()] })
      .mockResolvedValue({ items: [approved] });
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    expect(approveSignatureRequest).toHaveBeenCalledWith(4);
    const answer = await screen.findByTestId("request-answer");
    expect(answer).toHaveTextContent("Approved");
    expect(answer).toHaveTextContent(
      "“at least 93 °C” is rejected with the agent's reason; “at least 91.5 °C” is in force.",
    );
    expect(answer).toHaveTextContent("Restore it on the Profiles page, under Rejected.");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("declines with an optional reason and changes nothing else", async () => {
    const user = setupUser();
    const declined = signatureRequest({
      status: "declined",
      answer_reason: "keep it, the boiler should hold 93",
      answered_at: "2026-11-01T10:00:00.000Z",
    });
    declineSignatureRequest.mockResolvedValue({ request: declined });
    getSignatureRequests
      .mockResolvedValueOnce({ items: [signatureRequest()] })
      .mockResolvedValue({ items: [declined] });
    renderCard();

    const decline = await screen.findByTestId("decline-request");
    expect(screen.getByTestId("decline-form")).not.toBeVisible();
    await user.click(decline);
    expect(screen.getByTestId("decline-form")).toBeVisible();
    await user.type(
      screen.getByLabelText("Why decline this request"),
      "keep it, the boiler should hold 93",
    );
    await user.click(screen.getByTestId("decline-submit"));

    expect(declineSignatureRequest).toHaveBeenCalledWith(4, "keep it, the boiler should hold 93");
    expect(approveSignatureRequest).not.toHaveBeenCalled();
    // The quote closes its sentence before the next one starts.
    expect(await screen.findByTestId("request-declined")).toHaveTextContent(
      "“keep it, the boiler should hold 93”. Nothing changed, and the agent is told.",
    );
  });

  it("declines with no reason when none is typed", async () => {
    const user = setupUser();
    declineSignatureRequest.mockResolvedValue({
      request: signatureRequest({ status: "declined", answered_at: "2026-11-01T10:00:00.000Z" }),
    });
    renderCard();

    await user.click(await screen.findByTestId("decline-request"));
    await user.click(screen.getByTestId("decline-submit"));

    expect(declineSignatureRequest).toHaveBeenCalledWith(4, "");
  });

  it("says a stale request is no longer open, with why, and offers no buttons", async () => {
    getSignatureRequests.mockResolvedValue({
      items: [
        signatureRequest({
          status: "stale",
          stale_reason: "You rejected “at least 93 °C” on the Profiles page.",
          answered_at: "2026-11-01T10:00:00.000Z",
        }),
      ],
    });
    renderCard();

    const stale = await screen.findByTestId("request-stale");
    expect(stale).toHaveTextContent(
      "No longer open. You rejected “at least 93 °C” on the Profiles page. Nothing was changed.",
    );
    // The server's reason is a whole sentence: nothing is appended to it.
    expect(stale.textContent).not.toContain("..");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("answers with aria-disabled buttons and one request however fast the second click comes", async () => {
    const user = setupUser();
    let resolve: (value: unknown) => void = () => undefined;
    approveSignatureRequest.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    renderCard();

    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).not.toBeDisabled();
    await user.dblClick(approve);
    expect(screen.getByRole("button", { name: "Approve" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("button", { name: "Approve" })).not.toBeDisabled();
    await act(async () =>
      resolve({
        request: signatureRequest({ status: "approved", answered_at: "2026-11-01T10:00:00.000Z" }),
      }),
    );

    expect(approveSignatureRequest).toHaveBeenCalledTimes(1);
  });

  it("says a 409 in words, and the card stays to be refreshed", async () => {
    const user = setupUser();
    approveSignatureRequest.mockRejectedValue(
      new ApiClientError(
        "This request is no longer open. You rejected “at least 93 °C” on the Profiles page.",
        { status: 409, code: "SIGNATURE_REQUEST_STALE", requestId: "9f2c" },
      ),
    );
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    // It says what staled it, never that it was answered in another tab.
    const error = await screen.findByTestId("request-error");
    expect(error).toHaveTextContent(
      "This request is no longer open. You rejected “at least 93 °C” on the Profiles page. Nothing was changed.",
    );
    expect(error).not.toHaveTextContent("another tab");
    // The request id is for a bug report, not for this sentence.
    expect(error).not.toHaveTextContent("request 9f2c");
  });

  it("does not repeat the refusal once the card shows the request as no longer open", async () => {
    const user = setupUser();
    approveSignatureRequest.mockRejectedValue(
      new ApiClientError("This request is no longer open. You rejected it.", {
        status: 409,
        code: "SIGNATURE_REQUEST_STALE",
      }),
    );
    getSignatureRequests.mockResolvedValueOnce({ items: [signatureRequest()] }).mockResolvedValue({
      items: [signatureRequest({ status: "stale", stale_reason: "You rejected it." })],
    });
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByTestId("request-stale")).toHaveTextContent("You rejected it.");
    expect(screen.queryByTestId("request-error")).not.toBeInTheDocument();
  });

  it.each([
    ["SIGNATURE_REQUEST_DECIDED", "already answered"],
    ["SIGNATURE_REQUEST_STALE", "Nothing was changed"],
    ["SIGNATURE_REQUEST_IS_A_DRAFTS", "came with a profile draft"],
  ])("words the %s failure", (code, words) => {
    expect(signatureRequestFailure(new ApiClientError("x", { status: 409, code }))).toContain(
      words,
    );
    expect(signatureRequestFailure(new Error("plain"))).toBe("plain");
  });
});
