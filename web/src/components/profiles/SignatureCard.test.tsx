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
  signatureInForce,
  signatureMixed,
  signatureNone,
} from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const api = vi.hoisted(() => ({
  getSignature: vi.fn(),
  rejectExpectation: vi.fn(),
  restoreExpectation: vi.fn(),
  setExpectationTier: vi.fn(),
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

const answered = (data: SignatureData) => ({ changed: [], signature: data });

/** The card is closed until a link or a row needing a phase opens it: open it as a person does. */
async function openCard(user: ReturnType<typeof setupUser>) {
  const card = await screen.findByTestId("signature-card");
  if (card.getAttribute("data-open") === "no") {
    await user.click(screen.getByTestId("signature-toggle"));
  }
  return card;
}

async function item(id: number): Promise<HTMLElement> {
  const items = await screen.findAllByTestId("expectation");
  const found = items.find((el) => el.getAttribute("data-expectation-id") === String(id));
  if (!found) throw new Error(`no expectation ${id}`);
  return found;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.rejectExpectation.mockResolvedValue(answered(signatureMixed));
  api.restoreExpectation.mockResolvedValue(answered(signatureInForce));
  api.setExpectationTier.mockResolvedValue(answered(signatureInForce));
});

describe("what the card shows", () => {
  it("lists what is in force in tier order, each with its phase, sentence, fault and kind, and counts it", async () => {
    const user = setupUser();
    renderCard(signatureInForce);
    await openCard(user);

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
      "checked by the review",
    ]);
    const ramp = await item(expectationOf(signatureInForce, "ramp", "measure").id);
    expect(within(ramp).getByTestId("expectation-phase")).toHaveTextContent("ramp");
    expect(within(ramp).getByTestId("expectation-fault")).toHaveTextContent("early yield");
    expect(within(ramp).getByTestId("expectation-sentence")).toHaveTextContent(
      "cup weight at the end of the ramp, as a share of the target yield, at most 0.15",
    );
    // In force is the ordinary state: no status badge, a tier and Reject on every row.
    expect(within(ramp).queryByTestId("expectation-status")).toBeNull();
    expect(within(ramp).getByTestId("reject-expectation")).toBeInTheDocument();
    expect(within(ramp).getByTestId("expectation-tier")).toBeInTheDocument();
    // A shot-wide expectation says so.
    const reading = await item(expectationOf(signatureInForce, null, "free_text").id);
    expect(within(reading).getByTestId("expectation-phase")).toHaveTextContent("Whole shot");
    expect(screen.getByTestId("signature-summary")).toHaveTextContent("6 in force");
    // Nothing offers to confirm: there is nothing to confirm.
    expect(screen.queryByTestId("confirm-expectation")).toBeNull();
    expect(screen.queryByTestId("confirm-all")).toBeNull();
    // The disclosure is always there, as the sketch has it, even with nothing rejected.
    expect(screen.getByTestId("signature-rejected-toggle")).toHaveTextContent("Rejected (0)");
  });

  it("keeps the rejected ones in a disclosure that says how many, each with its reason and Restore", async () => {
    const user = setupUser();
    renderCard(signatureMixed);
    await openCard(user);

    expect(screen.getByTestId("signature-summary")).toHaveTextContent("5 in force · 1 rejected");
    const toggle = screen.getByTestId("signature-rejected-toggle");
    expect(toggle).toHaveTextContent("Rejected (1)");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // Only what is in force is drawn in the tier groups.
    const tierRows = screen
      .getAllByTestId("signature-tier")
      .flatMap((t) => within(t).getAllByTestId("expectation"));
    expect(tierRows).toHaveLength(5);

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const ramp = await item(expectationOf(signatureMixed, "ramp", "measure").id);
    expect(ramp).toHaveAttribute("data-status", "rejected");
    expect(within(ramp).getByTestId("expectation-reject-reason")).toHaveTextContent(
      "too tight for this lever",
    );
    expect(within(ramp).getByTestId("restore-expectation")).toBeInTheDocument();
    expect(within(ramp).queryByTestId("reject-expectation")).toBeNull();
    expect(within(ramp).queryByTestId("expectation-tier")).toBeNull();
  });

  it("is closed while everything is in force, and toggles", async () => {
    const user = setupUser();
    renderCard(signatureInForce);

    const card = await screen.findByTestId("signature-card");
    expect(card).toHaveAttribute("data-open", "no");
    await user.click(screen.getByTestId("signature-toggle"));
    expect(card).toHaveAttribute("data-open", "yes");
    expect(screen.getAllByTestId("expectation")).toHaveLength(6);
  });

  it("is open while a row needs a new phase, which is shown with Reject only and never in force", async () => {
    renderCard(signatureCarried);

    const card = await screen.findByTestId("signature-card");
    expect(card).toHaveAttribute("data-open", "yes");
    expect(screen.getByTestId("signature-summary")).toHaveTextContent("5 in force");
    const decline = await item(expectationOf(signatureCarried, "decline", "reached").id);
    expect(within(decline).getByTestId("expectation-carried")).toHaveTextContent(
      /^carried from v1 \(added /,
    );
    expect(within(decline).getByTestId("reject-expectation")).toBeInTheDocument();

    const soak = await item(expectationOf(signatureCarried, "soak", "measure").id);
    expect(within(soak).getByTestId("expectation-needs-phase")).toHaveTextContent(
      "needs a new phase",
    );
    expect(soak).toHaveTextContent("checked on no shot");
    expect(within(soak).getByTestId("reject-expectation")).toBeInTheDocument();
    expect(within(soak).queryByTestId("expectation-tier")).toBeNull();
    expect(within(soak).queryByTestId("confirm-expectation")).toBeNull();
    expect(within(soak).queryByTestId("restore-expectation")).toBeNull();
  });

  it("draws a row proposed before signatures were in force at once as not in force, with Reject only", async () => {
    const legacy: SignatureData = {
      ...signatureInForce,
      confirmed: 5,
      not_in_force: 1,
      expectations: signatureInForce.expectations.map((e) =>
        e.phase === "decline" ? { ...e, status: "proposed" as const } : e,
      ),
    };
    renderCard(legacy);

    // Open by itself: something is shown that checks nothing.
    const card = await screen.findByTestId("signature-card");
    expect(card).toHaveAttribute("data-open", "yes");
    expect(screen.getByTestId("signature-summary")).toHaveTextContent(
      "5 in force · 1 not in force",
    );
    const row = await item(expectationOf(legacy, "decline", "reached").id);
    expect(within(row).getByTestId("expectation-not-in-force")).toHaveTextContent("not in force");
    expect(within(row).getByTestId("expectation-legacy")).toHaveTextContent(
      "From before signatures were in force at once",
    );
    expect(within(row).getByTestId("expectation-legacy")).toHaveTextContent(
      "Reject it, then Restore it to put it in force.",
    );
    expect(within(row).getByTestId("reject-expectation")).toBeInTheDocument();
    expect(within(row).queryByTestId("expectation-tier")).toBeNull();
    expect(within(row).queryByTestId("restore-expectation")).toBeNull();
  });

  it("never says none yet while rows that are not in force are listed", async () => {
    renderCard({
      ...signatureInForce,
      confirmed: 0,
      not_in_force: 6,
      expectations: signatureInForce.expectations.map((e) => ({
        ...e,
        status: "proposed" as const,
      })),
    });

    expect(await screen.findByTestId("signature-summary")).toHaveTextContent("6 not in force");
    expect(screen.getByTestId("signature-summary")).not.toHaveTextContent("none yet");
  });

  it("opens the reason form from a Reject that is aria-disabled while a call is out, never disabled", async () => {
    const user = setupUser();
    api.setExpectationTier.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureInForce);
    await openCard(user);
    const row = await item(expectationOf(signatureInForce, "soak", "measure").id);
    const reject = within(row).getByTestId("reject-expectation");
    expect(reject).not.toBeDisabled();
    expect(reject).toHaveAttribute("aria-disabled", "false");

    fireEvent.change(within(row).getByTestId("expectation-tier"), { target: { value: "context" } });
    await waitFor(() => expect(reject).toHaveAttribute("aria-disabled", "true"));
    expect(reject).not.toBeDisabled();
    await user.click(reject);
    expect(screen.queryByTestId("reject-form")).toBeNull();
  });

  it("offers no Restore for a rejected row that needs a new phase", async () => {
    const user = setupUser();
    const rejected: SignatureData = {
      ...signatureCarried,
      confirmed: 5,
      rejected: 1,
      expectations: signatureCarried.expectations.map((e) =>
        e.needs_a_new_phase ? { ...e, status: "rejected" as const } : e,
      ),
    };
    renderCard(rejected);
    await openCard(user);
    await user.click(screen.getByTestId("signature-rejected-toggle"));

    const soak = await item(expectationOf(rejected, "soak", "measure").id);
    expect(within(soak).queryByTestId("restore-expectation")).toBeNull();
  });

  it("links the conversation that proposed an expectation", async () => {
    const user = setupUser();
    renderCard(signatureInForce);
    await openCard(user);

    const ramp = await item(expectationOf(signatureInForce, "ramp", "measure").id);
    expect(within(ramp).getByTestId("expectation-thread-link")).toHaveAttribute(
      "href",
      `/chat?thread=${SIGNATURE_THREAD_ID}`,
    );
  });

  it("has no signature to show for a version nobody proposed anything for, and links the Set chats", async () => {
    const user = setupUser();
    renderCard(signatureNone);
    await openCard(user);

    expect(await screen.findByTestId("signature-none")).toHaveTextContent("No signature yet");
    const link = screen.getByTestId("signature-set-chat");
    expect(link).toHaveTextContent("Constructed lever");
    const href = link.getAttribute("href") ?? "";
    expect(href).toContain(`set=${signatureNone.sets[0].set_id}`);
    expect(href).toContain(`version=${signatureNone.sets[0].version_id}`);
  });

  it("offers the general chat when no Set brews the version", async () => {
    const user = setupUser();
    renderCard({ ...signatureNone, sets: [] });
    await openCard(user);

    expect(await screen.findByTestId("signature-general-chat")).toBeInTheDocument();
    expect(screen.queryByTestId("signature-set-chat")).toBeNull();
  });

  it("cuts a long phase name inside its row instead of widening the card", async () => {
    const user = setupUser();
    const long = "a phase named at some length by someone who likes long names";
    const data: SignatureData = {
      ...signatureInForce,
      expectations: signatureInForce.expectations.map((e) =>
        e.phase === "ramp" ? { ...e, phase: long } : e,
      ),
    };
    renderCard(data);
    await openCard(user);

    const phase = within(await item(data.expectations[1].id)).getByTestId("expectation-phase");
    expect(phase).toHaveClass("truncate", "max-w-full", "min-w-0");
    expect(phase).toHaveAttribute("title", long);
  });
});

describe("answering", () => {
  it("rejects with the reason typed, in one call under a double press", async () => {
    const user = setupUser();
    api.rejectExpectation.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureInForce);
    await openCard(user);
    const id = expectationOf(signatureInForce, "soak", "measure").id;

    await user.click(within(await item(id)).getByTestId("reject-expectation"));
    await user.type(screen.getByLabelText(/why reject the soak expectation/i), "too strict");
    const submit = screen.getByTestId("reason-submit");
    await user.dblClick(submit);

    expect(api.rejectExpectation).toHaveBeenCalledTimes(1);
    expect(api.rejectExpectation).toHaveBeenCalledWith(id, "too strict");
  });

  it("rejects with no reason when none is typed, and Cancel sends nothing", async () => {
    const user = setupUser();
    renderCard(signatureInForce);
    await openCard(user);
    const id = expectationOf(signatureInForce, "soak", "measure").id;

    await user.click(within(await item(id)).getByTestId("reject-expectation"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.rejectExpectation).not.toHaveBeenCalled();
    expect(screen.queryByTestId("reject-form")).toBeNull();

    await user.click(within(await item(id)).getByTestId("reject-expectation"));
    await user.click(screen.getByTestId("reason-submit"));
    expect(api.rejectExpectation).toHaveBeenCalledWith(id, "");
  });

  it("restores a rejected expectation with one call under a double press", async () => {
    const user = setupUser();
    api.restoreExpectation.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureMixed);
    await openCard(user);
    await user.click(screen.getByTestId("signature-rejected-toggle"));
    const id = expectationOf(signatureMixed, "ramp", "measure").id;

    await user.dblClick(within(await item(id)).getByTestId("restore-expectation"));

    expect(api.restoreExpectation).toHaveBeenCalledTimes(1);
    expect(api.restoreExpectation).toHaveBeenCalledWith(id);
  });

  it("shows the signature the answer returns without waiting for a refetch", async () => {
    const user = setupUser();
    // The server's answer from here on: the ramp rejected, the others in force.
    api.rejectExpectation.mockImplementation(async () => {
      api.getSignature.mockResolvedValue(signatureMixed);
      return answered(signatureMixed);
    });
    renderCard(signatureInForce);
    await openCard(user);

    await user.click(
      within(await item(expectationOf(signatureInForce, "ramp", "measure").id)).getByTestId(
        "reject-expectation",
      ),
    );
    await user.click(screen.getByTestId("reason-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("signature-summary")).toHaveTextContent("5 in force · 1 rejected"),
    );
  });

  it("moves an expectation to another tier with one call, and sends nothing for the same tier", async () => {
    const user = setupUser();
    api.setExpectationTier.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureInForce);
    await openCard(user);
    const id = expectationOf(signatureInForce, "soak", "measure").id;
    const select = within(await item(id)).getByTestId("expectation-tier");
    expect(select).toHaveValue("important");

    fireEvent.change(select, { target: { value: "important" } });
    expect(api.setExpectationTier).not.toHaveBeenCalled();

    fireEvent.change(select, { target: { value: "context" } });
    fireEvent.change(select, { target: { value: "critical" } });
    await waitFor(() => expect(api.setExpectationTier).toHaveBeenCalledTimes(1));
    expect(api.setExpectationTier).toHaveBeenCalledWith(id, "context");
  });
});

describe("focus", () => {
  it("moves to the row on a rejection's submit and on its Cancel", async () => {
    const user = setupUser();
    renderCard(signatureInForce);
    await openCard(user);
    const id = expectationOf(signatureInForce, "soak", "measure").id;
    const row = await item(id);

    await user.click(within(row).getByTestId("reject-expectation"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(row).toHaveFocus();

    await user.click(within(row).getByTestId("reject-expectation"));
    await user.click(screen.getByTestId("reason-submit"));
    expect(row).toHaveFocus();
  });

  it("moves to the row on a restore, and marks Restore aria-disabled while the call is out, never disabled", async () => {
    const user = setupUser();
    api.restoreExpectation.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureMixed);
    await openCard(user);
    await user.click(screen.getByTestId("signature-rejected-toggle"));
    const row = await item(expectationOf(signatureMixed, "ramp", "measure").id);
    const button = within(row).getByTestId("restore-expectation");
    expect(button).toHaveAttribute("aria-disabled", "false");

    await user.click(button);

    expect(row).toHaveFocus();
    // A disabled button would take focus away from a second press of a double click.
    await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));
    expect(button).not.toBeDisabled();
  });

  it("moves to the row when the tier select is disabled by the call", async () => {
    const user = setupUser();
    api.setExpectationTier.mockReturnValue(new Promise(() => undefined));
    renderCard(signatureInForce);
    await openCard(user);
    const row = await item(expectationOf(signatureInForce, "soak", "measure").id);
    const select = within(row).getByTestId("expectation-tier");
    select.focus();

    fireEvent.change(select, { target: { value: "context" } });

    expect(row).toHaveFocus();
  });

  it("follows an expectation to its new tier group, where its row is focused", async () => {
    const user = setupUser();
    const id = expectationOf(signatureInForce, "soak", "measure").id;
    api.setExpectationTier.mockImplementation(async () => {
      const moved = {
        ...signatureInForce,
        expectations: [
          ...signatureInForce.expectations.filter((e) => e.id !== id),
          ...signatureInForce.expectations
            .filter((e) => e.id === id)
            .map((e) => ({ ...e, tier: "context" as const })),
        ],
      };
      api.getSignature.mockResolvedValue(moved);
      return { changed: [], signature: moved };
    });
    renderCard(signatureInForce);
    await openCard(user);
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
});
