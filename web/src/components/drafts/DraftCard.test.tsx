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
  it("shows the prediction a Set's draft carries, and where it lands", () => {
    renderWithQueryClient(
      <DraftCard
        draft={draft({
          set_id: 3,
          set_name: "Guji on the Niche",
          prediction: "Compared to v2: less of the dry finish, and no slower.",
          compares_to_version_no: 2,
        })}
      />,
    );

    const block = screen.getByTestId("draft-prediction");
    expect(block).toHaveTextContent("Guji on the Niche");
    expect(block).toHaveTextContent("compared to v2");
    expect(block).toHaveTextContent("less of the dry finish");
    // Where it lands matters as much as what it says: pushing this draft for a
    // different Set records no prediction at all.
    expect(block).toHaveTextContent("when you push this draft for that Set");
  });

  it("shows no prediction block on a draft nobody predicted anything about", () => {
    renderWithQueryClient(<DraftCard draft={draft()} />);
    expect(screen.queryByTestId("draft-prediction")).not.toBeInTheDocument();
  });

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

  describe("pushing a Set's draft", () => {
    const forGuji = {
      set_id: 3,
      set_name: "Guji on the Niche",
      set_next_version_no: 4,
      prediction: "Compared to v3: less of the dry finish, and no slower.",
      compares_to_version_no: 3,
    };

    it("pushes it for its Set by default, and says which version it records", async () => {
      const user = setupUser();
      renderWithQueryClient(<DraftCard draft={draft({ ...forGuji, status: "approved" })} />);

      const button = screen.getByTestId("push-draft-for-set");
      expect(button).toHaveTextContent(
        "Push to the machine and record it as v4 of Guji on the Niche",
      );
      await user.click(button);

      // The Set in the body is what makes the server record the version and
      // the prediction; without it the push records nothing on the Set.
      await waitFor(() =>
        expect(pushProfileDraft).toHaveBeenCalledWith(1, { setId: 3, allowStaleBase: false }),
      );
    });

    it("can still be pushed without recording it on the Set", async () => {
      const user = setupUser();
      renderWithQueryClient(<DraftCard draft={draft({ ...forGuji, status: "approved" })} />);

      const plain = screen.getByTestId("push-draft");
      expect(plain).toHaveTextContent("Push without recording it on the Set");
      await user.click(plain);

      await waitFor(() =>
        expect(pushProfileDraft).toHaveBeenCalledWith(1, {
          setId: undefined,
          allowStaleBase: false,
        }),
      );
    });

    it("carries the stale-base override on the push for the Set too", async () => {
      const user = setupUser();
      renderWithQueryClient(
        <DraftCard draft={draft({ ...forGuji, status: "approved", base_is_current: false })} />,
      );
      expect(screen.getByTestId("push-draft-for-set")).toBeDisabled();
      expect(screen.getByTestId("push-draft")).toBeDisabled();

      await user.click(screen.getByRole("checkbox"));
      await user.click(screen.getByTestId("push-draft-for-set"));

      await waitFor(() =>
        expect(pushProfileDraft).toHaveBeenCalledWith(1, { setId: 3, allowStaleBase: true }),
      );
    });

    it("offers only the plain push once the Set is gone", () => {
      renderWithQueryClient(
        <DraftCard draft={draft({ ...forGuji, set_name: null, status: "approved" })} />,
      );
      expect(screen.queryByTestId("push-draft-for-set")).not.toBeInTheDocument();
      expect(screen.getByTestId("push-draft")).toHaveTextContent("Push to the machine");
    });

    it("says the prediction was recorded only when the push recorded it", () => {
      const pushed = { ...forGuji, status: "pushed", pushed_device_profile_id: "aB3xYz90Pq" };
      const { rerender } = renderWithQueryClient(
        <DraftCard draft={draft({ ...pushed, recorded_version_no: 4 })} />,
      );
      expect(screen.getByTestId("draft-prediction-landing")).toHaveTextContent(
        "Recorded as v4 of Guji on the Niche when this was pushed for it.",
      );

      rerender(<DraftCard draft={draft({ ...pushed, recorded_version_no: null })} />);
      const landing = screen.getByTestId("draft-prediction-landing");
      expect(landing).toHaveTextContent("Pushed without recording it on Guji on the Niche");
      expect(landing).not.toHaveTextContent("Recorded as");
    });

    it("says nothing about where it lands once the draft can no longer be pushed", () => {
      // A failed push recorded nothing, and a discarded draft may have been
      // recorded before its rollback: neither sentence would be true of both.
      for (const status of ["failed", "discarded", "superseded"]) {
        const { unmount } = renderWithQueryClient(
          <DraftCard draft={draft({ ...forGuji, status, pushed_device_profile_id: null })} />,
        );
        expect(screen.getByTestId("draft-prediction")).toBeInTheDocument();
        expect(screen.queryByTestId("draft-prediction-landing")).not.toBeInTheDocument();
        unmount();
      }
    });
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
