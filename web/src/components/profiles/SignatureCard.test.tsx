import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SignatureData } from "@/api/types";
import { SignatureCard } from "@/components/profiles/SignatureCard";
import { listedVersion } from "@/test/boardFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import {
  expectationOf,
  SIGNATURE_THREAD_ID,
  signatureCarried,
  signatureConfirmed,
  signatureMixed,
  signatureNone,
  signatureProposed,
} from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const api = vi.hoisted(() => ({
  getSignature: vi.fn(),
  confirmExpectation: vi.fn(),
  rejectExpectation: vi.fn(),
  setExpectationTier: vi.fn(),
  confirmAllExpectations: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  ...api,
}));

/** The versions of the profile as its dropdown lists them: newest first. */
const VERSIONS = [
  listedVersion({ version_id: 2, added_at: "2026-03-05T10:00:00.000Z", previous_version_id: 1 }),
  listedVersion({ version_id: 1, added_at: "2026-03-01T10:00:00.000Z" }),
];

function renderCard(data: SignatureData, versionId = data.profile_version_id) {
  api.getSignature.mockResolvedValue(data);
  return renderWithQueryClient(<SignatureCard versionId={versionId} versions={VERSIONS} />);
}

const waitingAnswer = (data: SignatureData) => ({ changed: [], signature: data });

async function item(id: number): Promise<HTMLElement> {
  const items = await screen.findAllByTestId("expectation");
  const found = items.find((el) => el.getAttribute("data-expectation-id") === String(id));
  if (!found) throw new Error(`no expectation ${id}`);
  return found;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.confirmExpectation.mockResolvedValue(waitingAnswer(signatureConfirmed));
  api.rejectExpectation.mockResolvedValue(waitingAnswer(signatureMixed));
  api.setExpectationTier.mockResolvedValue(waitingAnswer(signatureProposed));
  api.confirmAllExpectations.mockResolvedValue(waitingAnswer(signatureConfirmed));
});

describe("what the card shows", () => {
  it("lists the expectations in tier order, each with its phase, sentence, fault, kind and status", async () => {
    renderCard(signatureProposed);

    const tiers = await screen.findAllByTestId("signature-tier");
    expect(tiers.map((t) => t.getAttribute("data-tier"))).toEqual([
      "critical",
      "important",
      "context",
    ]);
    const rows = screen.getAllByTestId("expectation");
    expect(rows.map((r) => within(r).getByTestId("expectation-kind").textContent)).toEqual([
      "phase reached",
      "computed",
      "computed",
      "computed",
      "warning expected",
      "checked by the reading",
    ]);
    const ramp = await item(expectationOf(signatureProposed, "ramp", "measure").id);
    expect(within(ramp).getByTestId("expectation-phase")).toHaveTextContent("ramp");
    expect(within(ramp).getByTestId("expectation-fault")).toHaveTextContent("early yield");
    expect(within(ramp).getByTestId("expectation-sentence")).toHaveTextContent(
      "cup weight at the end of the ramp, as a share of the target yield, at most 0.15",
    );
    expect(within(ramp).getByTestId("expectation-status")).toHaveTextContent("Proposed");
    // A shot-wide expectation says so.
    const reading = await item(expectationOf(signatureProposed, null, "free_text").id);
    expect(within(reading).getByTestId("expectation-phase")).toHaveTextContent("Whole shot");
    expect(screen.getByTestId("signature-summary")).toHaveTextContent("6 waiting");
  });

  it("shows each status, and a rejection's reason", async () => {
    renderCard(signatureMixed);

    const decline = await item(expectationOf(signatureMixed, "decline", "reached").id);
    const ramp = await item(expectationOf(signatureMixed, "ramp", "measure").id);
    expect(decline).toHaveAttribute("data-status", "confirmed");
    expect(within(decline).getByTestId("expectation-status")).toHaveTextContent("Confirmed");
    expect(ramp).toHaveAttribute("data-status", "rejected");
    expect(within(ramp).getByTestId("expectation-reject-reason")).toHaveTextContent(
      "too tight for this lever",
    );
    // Answered ones have nothing left to press.
    expect(within(decline).queryByTestId("confirm-expectation")).toBeNull();
    expect(within(ramp).queryByTestId("confirm-expectation")).toBeNull();
    expect(screen.getByTestId("signature-summary")).toHaveTextContent(
      "1 confirmed · 4 waiting · 1 rejected",
    );
  });

  it("is open while something waits and closed once everything is answered, and toggles", async () => {
    const user = setupUser();
    renderCard(signatureConfirmed);

    const card = await screen.findByTestId("signature-card");
    expect(card).toHaveAttribute("data-open", "no");
    expect(screen.queryByTestId("confirm-all")).toBeNull();
    await user.click(screen.getByTestId("signature-toggle"));
    expect(card).toHaveAttribute("data-open", "yes");
    expect(screen.getAllByTestId("expectation")).toHaveLength(6);
  });

  it("marks a carried expectation with the version it came from, and one that needs a phase cannot be confirmed", async () => {
    renderCard(signatureCarried);

    const decline = await item(expectationOf(signatureCarried, "decline", "reached").id);
    expect(within(decline).getByTestId("expectation-carried")).toHaveTextContent(
      /^carried from v1 \(added /,
    );
    expect(within(decline).getByTestId("confirm-expectation")).toBeInTheDocument();

    const soak = await item(expectationOf(signatureCarried, "soak", "measure").id);
    expect(within(soak).getByTestId("expectation-needs-phase")).toHaveTextContent(
      "needs a new phase",
    );
    expect(within(soak).queryByTestId("confirm-expectation")).toBeNull();
    // It can still be turned down, and Confirm all counts only what can be confirmed.
    expect(within(soak).getByTestId("reject-expectation")).toBeInTheDocument();
    expect(screen.getByTestId("confirm-all")).toHaveTextContent("Confirm all (5)");
  });

  it("links the conversation that proposed an expectation", async () => {
    renderCard(signatureProposed);

    const ramp = await item(expectationOf(signatureProposed, "ramp", "measure").id);
    expect(within(ramp).getByTestId("expectation-thread-link")).toHaveAttribute(
      "href",
      `/chat?thread=${SIGNATURE_THREAD_ID}`,
    );
  });

  it("has no signature to show for a version nobody proposed anything for, and links the Set chats", async () => {
    renderCard(signatureNone);

    expect(await screen.findByTestId("signature-none")).toHaveTextContent("No signature yet");
    const link = screen.getByTestId("signature-set-chat");
    expect(link).toHaveTextContent("Constructed lever");
    const href = link.getAttribute("href") ?? "";
    expect(href).toContain(`set=${signatureNone.sets[0].set_id}`);
    expect(href).toContain(`version=${signatureNone.sets[0].version_id}`);
    expect(screen.queryByTestId("confirm-all")).toBeNull();
  });

  it("offers the general chat when no Set brews the version", async () => {
    renderCard({ ...signatureNone, sets: [] });

    expect(await screen.findByTestId("signature-general-chat")).toBeInTheDocument();
    expect(screen.queryByTestId("signature-set-chat")).toBeNull();
  });

  it("cuts a long phase name inside its row instead of widening the card", async () => {
    const long = "a phase named at some length by someone who likes long names";
    const data: SignatureData = {
      ...signatureProposed,
      expectations: signatureProposed.expectations.map((e) =>
        e.phase === "ramp" ? { ...e, phase: long } : e,
      ),
    };
    renderCard(data);

    const phase = within(await item(data.expectations[1].id)).getByTestId("expectation-phase");
    expect(phase).toHaveClass("truncate", "max-w-full", "min-w-0");
    expect(phase).toHaveAttribute("title", long);
  });
});

describe("answering", () => {
  it("confirms one expectation with one call, however fast it is pressed twice", async () => {
    const user = setupUser();
    api.confirmExpectation.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureProposed);
    const id = expectationOf(signatureProposed, "ramp", "measure").id;

    const button = within(await item(id)).getByTestId("confirm-expectation");
    await user.dblClick(button);

    expect(api.confirmExpectation).toHaveBeenCalledTimes(1);
    expect(api.confirmExpectation).toHaveBeenCalledWith(id);
  });

  it("shows the signature the answer returns without waiting for a refetch", async () => {
    const user = setupUser();
    // The server's answer from here on: every expectation confirmed.
    api.confirmExpectation.mockImplementation(async () => {
      api.getSignature.mockResolvedValue(signatureConfirmed);
      return waitingAnswer(signatureConfirmed);
    });
    renderCard(signatureProposed);

    await user.click(within(await item(1)).getByTestId("confirm-expectation"));

    await waitFor(() =>
      expect(screen.getByTestId("signature-summary")).toHaveTextContent("6 confirmed"),
    );
  });

  it("rejects with the reason typed, in one call under a double press", async () => {
    const user = setupUser();
    api.rejectExpectation.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureProposed);
    const id = expectationOf(signatureProposed, "soak", "measure").id;

    await user.click(within(await item(id)).getByTestId("reject-expectation"));
    await user.type(screen.getByLabelText(/why reject the soak expectation/i), "too strict");
    const submit = screen.getByTestId("reason-submit");
    await user.dblClick(submit);

    expect(api.rejectExpectation).toHaveBeenCalledTimes(1);
    expect(api.rejectExpectation).toHaveBeenCalledWith(id, "too strict");
  });

  it("rejects with no reason when none is typed, and Cancel sends nothing", async () => {
    const user = setupUser();
    renderCard(signatureProposed);
    const id = expectationOf(signatureProposed, "soak", "measure").id;

    await user.click(within(await item(id)).getByTestId("reject-expectation"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.rejectExpectation).not.toHaveBeenCalled();
    expect(screen.queryByTestId("reject-form")).toBeNull();

    await user.click(within(await item(id)).getByTestId("reject-expectation"));
    await user.click(screen.getByTestId("reason-submit"));
    expect(api.rejectExpectation).toHaveBeenCalledWith(id, "");
  });

  it("moves an expectation to another tier with one call, and sends nothing for the same tier", async () => {
    api.setExpectationTier.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureProposed);
    const id = expectationOf(signatureProposed, "soak", "measure").id;
    const select = within(await item(id)).getByTestId("expectation-tier");
    expect(select).toHaveValue("important");

    fireEvent.change(select, { target: { value: "important" } });
    expect(api.setExpectationTier).not.toHaveBeenCalled();

    fireEvent.change(select, { target: { value: "context" } });
    fireEvent.change(select, { target: { value: "critical" } });
    await waitFor(() => expect(api.setExpectationTier).toHaveBeenCalledTimes(1));
    expect(api.setExpectationTier).toHaveBeenCalledWith(id, "context");
  });

  it("confirms all with the one call for the version, not one per expectation, under a double press", async () => {
    const user = setupUser();
    api.confirmAllExpectations.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureProposed);

    await user.dblClick(await screen.findByTestId("confirm-all"));

    expect(api.confirmAllExpectations).toHaveBeenCalledTimes(1);
    expect(api.confirmAllExpectations).toHaveBeenCalledWith(signatureProposed.profile_version_id);
    expect(api.confirmExpectation).not.toHaveBeenCalled();
  });
});

describe("focus", () => {
  it("moves to the answered row when Confirm, Reject or a tier change takes its own control away", async () => {
    const user = setupUser();
    renderCard(signatureProposed);
    const id = expectationOf(signatureProposed, "ramp", "measure").id;
    const row = await item(id);

    await user.click(within(row).getByTestId("confirm-expectation"));
    expect(row).toHaveFocus();
  });

  it("moves to the row on a rejection's submit and on its Cancel", async () => {
    const user = setupUser();
    renderCard(signatureProposed);
    const id = expectationOf(signatureProposed, "soak", "measure").id;
    const row = await item(id);

    await user.click(within(row).getByTestId("reject-expectation"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(row).toHaveFocus();

    await user.click(within(row).getByTestId("reject-expectation"));
    await user.click(screen.getByTestId("reason-submit"));
    expect(row).toHaveFocus();
  });

  it("moves to the row when the tier select is disabled by the call", async () => {
    api.setExpectationTier.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureProposed);
    const row = await item(expectationOf(signatureProposed, "soak", "measure").id);
    const select = within(row).getByTestId("expectation-tier");
    select.focus();

    fireEvent.change(select, { target: { value: "context" } });

    expect(row).toHaveFocus();
  });

  it("follows an expectation to its new tier group, where its row is focused", async () => {
    const id = expectationOf(signatureProposed, "soak", "measure").id;
    api.setExpectationTier.mockImplementation(async () => {
      const moved = {
        ...signatureProposed,
        expectations: [
          ...signatureProposed.expectations.filter((e) => e.id !== id),
          ...signatureProposed.expectations
            .filter((e) => e.id === id)
            .map((e) => ({ ...e, tier: "context" as const })),
        ],
      };
      api.getSignature.mockResolvedValue(moved);
      return { changed: [], signature: moved };
    });
    renderCard(signatureProposed);
    const select = within(await item(id)).getByTestId("expectation-tier");
    select.focus();

    fireEvent.change(select, { target: { value: "context" } });

    await waitFor(() => {
      const row = screen
        .getAllByTestId("expectation")
        .find((el) => el.getAttribute("data-expectation-id") === String(id));
      expect(row).toHaveAttribute("data-tier", "context");
      expect(row?.closest('[data-testid="signature-tier"]')).toHaveAttribute(
        "data-tier",
        "context",
      );
      expect(row).toHaveFocus();
    });
  });

  it("keeps focus on the heading button through a double click on Confirm all", async () => {
    const user = setupUser();
    api.confirmAllExpectations.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureProposed);

    await user.dblClick(await screen.findByTestId("confirm-all"));

    expect(api.confirmAllExpectations).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("signature-toggle")).toHaveFocus();
  });

  it("marks Confirm all aria-disabled while the call is out, and never disables it", async () => {
    const user = setupUser();
    api.confirmAllExpectations.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureProposed);
    const button = await screen.findByTestId("confirm-all");
    expect(button).toHaveAttribute("aria-disabled", "false");

    await user.click(button);

    // A disabled button would take focus away from a second press of a double click.
    await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));
    expect(button).not.toBeDisabled();
  });

  it("moves to the card's heading button when Confirm all goes away with the last waiting one", async () => {
    const user = setupUser();
    renderCard(signatureProposed);

    await user.click(await screen.findByTestId("confirm-all"));

    expect(screen.getByTestId("signature-toggle")).toHaveFocus();
  });
});
