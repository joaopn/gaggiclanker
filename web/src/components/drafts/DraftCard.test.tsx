import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DraftCard } from "@/components/drafts/DraftCard";
import { draft, draftDetail, yieldChange } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const {
  getProfileDraft,
  approveProfileDraft,
  pushProfileDraft,
  rollbackProfileDraft,
  discardProfileDraft,
  refineProfileDraft,
} = vi.hoisted(() => ({
  getProfileDraft: vi.fn(),
  approveProfileDraft: vi.fn(),
  pushProfileDraft: vi.fn(),
  rollbackProfileDraft: vi.fn(),
  discardProfileDraft: vi.fn(),
  refineProfileDraft: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getProfileDraft,
  approveProfileDraft,
  pushProfileDraft,
  rollbackProfileDraft,
  discardProfileDraft,
  refineProfileDraft,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getProfileDraft.mockResolvedValue(draftDetail());
  approveProfileDraft.mockResolvedValue(draft({ status: "approved" }));
  pushProfileDraft.mockResolvedValue({
    draft: draft({ status: "pushed", pushed_device_profile_id: "aB3xYz90Pq" }),
    set_version: null,
  });
  rollbackProfileDraft.mockResolvedValue(draft({ status: "failed" }));
  discardProfileDraft.mockResolvedValue(draft({ status: "discarded" }));
  refineProfileDraft.mockResolvedValue(draft({ id: 2, parent_draft_id: 1 }));
});

describe("DraftCard", () => {
  it("shows what the draft actually changes, per phase and per field", async () => {
    renderWithQueryClient(<DraftCard draft={draft()} />);

    const diff = await screen.findByTestId("profile-diff");
    expect(diff).toHaveTextContent("label");
    expect(diff).toHaveTextContent("9 Bar Espresso [AI]");
    // The point of a per-field diff rather than a JSON one: this reads as a
    // pump setpoint, not as an object path.
    expect(diff).toHaveTextContent("phase 1 · Pump · pump");
    expect(diff).toHaveTextContent("pressure 8 bar");
  });

  it("approves a draft that moves nothing, with no checkbox in the way", async () => {
    const user = setupUser();
    renderWithQueryClient(<DraftCard draft={draft()} />);

    expect(screen.queryByTestId("acknowledge-stop-changes")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("approve-draft"));

    await waitFor(() => expect(approveProfileDraft).toHaveBeenCalledWith(1, false));
  });

  it("will not let a yield change be approved until it is acknowledged", async () => {
    // crema's rule. A stop condition decides how much coffee ends up in the
    // cup, and the button stays disabled until somebody says they meant it.
    const user = setupUser();
    renderWithQueryClient(<DraftCard draft={draft({ stop_condition_changes: [yieldChange()] })} />);

    expect(screen.getByTestId("stop-condition-warning")).toHaveTextContent(
      "how much coffee is in the cup",
    );
    expect(screen.getByTestId("stop-condition-warning")).toHaveTextContent("volumetric");
    expect(screen.getByTestId("approve-draft")).toBeDisabled();

    await user.click(screen.getByRole("checkbox"));

    expect(screen.getByTestId("approve-draft")).not.toBeDisabled();
    await user.click(screen.getByTestId("approve-draft"));
    await waitFor(() => expect(approveProfileDraft).toHaveBeenCalledWith(1, true));
  });

  it("lists what the safety policy moved on the way in", async () => {
    renderWithQueryClient(
      <DraftCard
        draft={draft({
          clamp_changes: [
            {
              path: "temperature",
              field: "temperature",
              before: 140,
              after: 100,
              reason: "profile temperature must be 60-100 °C",
            },
          ],
        })}
      />,
    );
    // Clamping without the list is exactly the silent rewrite the policy exists
    // to avoid, so the list is on the card next to the approve button.
    expect(screen.getByTestId("draft-clamps")).toHaveTextContent("140 → 100");
  });

  it("offers push only once a draft is approved", async () => {
    const user = setupUser();
    const { rerender } = renderWithQueryClient(<DraftCard draft={draft()} />);
    expect(screen.queryByTestId("push-draft")).not.toBeInTheDocument();

    rerender(<DraftCard draft={draft({ status: "approved" })} />);
    await user.click(screen.getByTestId("push-draft"));

    await waitFor(() =>
      expect(pushProfileDraft).toHaveBeenCalledWith(1, {
        setId: undefined,
        allowStaleBase: false,
      }),
    );
  });

  it("says a pushed draft was not selected", () => {
    renderWithQueryClient(
      <DraftCard draft={draft({ status: "pushed", pushed_device_profile_id: "aB3xYz90Pq" })} />,
    );
    expect(screen.getByTestId("draft-pushed")).toHaveTextContent("aB3xYz90Pq");
    expect(screen.getByTestId("draft-pushed")).toHaveTextContent("was not selected");
  });

  it("offers one click to remove a push that did not verify", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <DraftCard
        draft={draft({
          status: "failed",
          pushed_device_profile_id: "aB3xYz90Pq",
          error: "the machine stored something other than what was sent",
        })}
      />,
    );

    expect(screen.getByTestId("draft-failed")).toHaveTextContent("stored something other than");
    await user.click(screen.getByTestId("rollback-draft"));

    await waitFor(() => expect(rollbackProfileDraft).toHaveBeenCalledWith(1));
  });

  it("does not offer a rollback once the machine's copy is gone", () => {
    // The draft stays `failed` — what happened, happened — but the button must
    // not come back, or it would delete whatever inherits that id next.
    renderWithQueryClient(
      <DraftCard draft={draft({ status: "failed", pushed_device_profile_id: null })} />,
    );
    expect(screen.queryByTestId("rollback-draft")).not.toBeInTheDocument();
  });

  it("refines with notes rather than editing the draft in place", async () => {
    const user = setupUser();
    renderWithQueryClient(<DraftCard draft={draft()} />);

    await user.click(screen.getByTestId("refine-draft"));
    await user.type(screen.getByRole("textbox"), "Still too harsh.");
    await user.click(screen.getByTestId("submit-refine"));

    await waitFor(() =>
      expect(refineProfileDraft).toHaveBeenCalledWith(1, {
        notes: "Still too harsh.",
        model: undefined,
      }),
    );
  });

  it("discards a draft nobody wants", async () => {
    const user = setupUser();
    renderWithQueryClient(<DraftCard draft={draft()} />);
    await user.click(screen.getByTestId("discard-draft"));
    await waitFor(() => expect(discardProfileDraft).toHaveBeenCalledWith(1));
  });

  it("will not push a draft whose base has changed on the machine", async () => {
    // The diff that was approved is a diff against a version the display no
    // longer holds; pushing anyway proposes undoing whatever was changed there.
    const user = setupUser();
    renderWithQueryClient(
      <DraftCard draft={draft({ status: "approved", base_is_current: false })} />,
    );

    expect(screen.getByTestId("stale-base-warning")).toHaveTextContent("changed on the machine");
    expect(screen.getByTestId("push-draft")).toBeDisabled();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByTestId("push-draft"));

    await waitFor(() =>
      expect(pushProfileDraft).toHaveBeenCalledWith(1, {
        setId: undefined,
        allowStaleBase: true,
      }),
    );
  });

  it("warns about a stale base before the draft is even approved", () => {
    // Worth knowing while deciding whether to approve: the answer is usually
    // "draft again from the current profile", not "approve and override".
    renderWithQueryClient(<DraftCard draft={draft({ base_is_current: false })} />);
    expect(screen.getByTestId("stale-base-warning")).toBeInTheDocument();
    expect(screen.queryByTestId("allow-stale-base")).not.toBeInTheDocument();
  });

  it("offers to take a pushed profile back off the machine", async () => {
    // Not only a failed one: a profile you pushed and then thought better of is
    // the same deletion, and it leaves the draft `discarded` rather than lying.
    const user = setupUser();
    renderWithQueryClient(
      <DraftCard draft={draft({ status: "pushed", pushed_device_profile_id: "aB3xYz90Pq" })} />,
    );

    await user.click(screen.getByTestId("rollback-draft"));

    await waitFor(() => expect(rollbackProfileDraft).toHaveBeenCalledWith(1));
  });
});
