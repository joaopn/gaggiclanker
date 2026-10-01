import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DraftCard } from "@/components/drafts/DraftCard";
import { boardRow } from "@/test/boardFixtures";
import { draft, draftDetail, yieldChange } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { getProfileDraft, discardProfileDraft, refineProfileDraft, putOnBoard } = vi.hoisted(() => ({
  getProfileDraft: vi.fn(),
  discardProfileDraft: vi.fn(),
  refineProfileDraft: vi.fn(),
  putOnBoard: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getProfileDraft,
  discardProfileDraft,
  refineProfileDraft,
  putOnBoard,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getProfileDraft.mockResolvedValue(draftDetail());
  discardProfileDraft.mockResolvedValue(draft({ status: "discarded" }));
  refineProfileDraft.mockResolvedValue(draft({ id: 2, parent_draft_id: 1 }));
  putOnBoard.mockResolvedValue({ id: 5, label: "9 Bar Espresso [AI]" });
});

const NEW_PROFILE = {
  draft_id: 1,
  already_on_board_label: null,
  plain: { row_id: null, row_label: null, holds_newer_draft: false },
  for_set: null,
};

const FOR_GUJI = {
  set_id: 3,
  set_name: "Guji on the Niche",
  set_next_version_no: 4,
  set_next_minor_label: "v2.2",
  set_next_major_label: "v3",
  prediction: "Compared to v2.1: less of the dry finish, and no slower.",
  compares_to_version_no: 3,
  compares_to_version_label: "v2.1",
};

describe("putting a draft on the board is one action", () => {
  it("offers Put on a drafted draft, with no Approve step, and puts it in one click", async () => {
    const user = setupUser();
    renderWithQueryClient(<DraftCard draft={draft()} adopted landing={NEW_PROFILE} />);

    expect(screen.queryByTestId("approve-draft")).not.toBeInTheDocument();
    expect(screen.getByTestId("discard-draft")).toBeInTheDocument();
    await user.click(screen.getByTestId("put-on-board"));

    await waitFor(() => expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1 }));
  });

  it("puts a draft approved before this was one action as it is", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <DraftCard
        draft={draft({
          status: "approved",
          stop_condition_changes: [yieldChange()],
          acknowledged_stop_changes: true,
        })}
        adopted
        landing={NEW_PROFILE}
      />,
    );

    expect(screen.queryByTestId("acknowledge-stop-changes")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("put-on-board"));

    await waitFor(() => expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1 }));
  });

  it("asks for the stop-condition acknowledgement on the same click and sends it", async () => {
    // crema's rule. A stop condition decides how much coffee ends up in the cup, and the
    // button stays disabled until somebody says they meant it.
    const user = setupUser();
    renderWithQueryClient(
      <DraftCard
        draft={draft({ stop_condition_changes: [yieldChange()] })}
        adopted
        landing={NEW_PROFILE}
      />,
    );

    expect(screen.getByTestId("stop-condition-warning")).toHaveTextContent(
      "how much coffee is in the cup",
    );
    expect(screen.getByTestId("stop-condition-warning")).toHaveTextContent("volumetric");
    expect(screen.getByTestId("put-on-board")).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    expect(screen.getByTestId("put-on-board")).not.toBeDisabled();
    await user.click(screen.getByTestId("put-on-board"));

    await waitFor(() =>
      expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1, acknowledgeStopChanges: true }),
    );
  });

  it("needs no checkbox for a draft that moves nothing", () => {
    renderWithQueryClient(<DraftCard draft={draft()} adopted landing={NEW_PROFILE} />);

    expect(screen.queryByTestId("acknowledge-stop-changes")).not.toBeInTheDocument();
    expect(screen.getByTestId("put-on-board")).not.toBeDisabled();
  });

  it("carries the Set and the major choice, and holds the Set's button for the acknowledgement", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <DraftCard
        draft={draft({ stop_condition_changes: [yieldChange()], ...FOR_GUJI })}
        adopted
        landing={{ ...NEW_PROFILE, for_set: NEW_PROFILE.plain }}
      />,
    );

    expect(screen.getByTestId("put-on-board-for-set")).toBeDisabled();
    expect(screen.getByTestId("put-on-board")).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /I understand/ }));
    await user.click(screen.getByRole("checkbox", { name: "Major change" }));
    await user.click(screen.getByTestId("put-on-board-for-set"));

    await waitFor(() =>
      expect(putOnBoard).toHaveBeenCalledWith({
        draftId: 1,
        setId: 3,
        major: true,
        acknowledgeStopChanges: true,
      }),
    );
  });

  it("does not put a draft on the board that the server would refuse", () => {
    renderWithQueryClient(
      <DraftCard
        draft={draft()}
        adopted
        landing={{ ...NEW_PROFILE, plain: { ...NEW_PROFILE.plain, taken_label: "Londinium" } }}
      />,
    );

    expect(screen.queryByTestId("put-on-board")).not.toBeInTheDocument();
    expect(screen.getByTestId("draft-landing")).toHaveTextContent(
      "The board already has Londinium; make the change by editing that profile on the board instead, or discard this draft.",
    );
  });
});

describe("before the board has taken the machine's profiles", () => {
  it("says what makes it happen and offers only refine and discard", () => {
    renderWithQueryClient(<DraftCard draft={draft()} />);

    expect(screen.getByTestId("draft-board-not-adopted")).toHaveTextContent(
      "Profiles reach the machine once the Writes switch is on and a sync has taken the machine's profiles onto the board.",
    );
    expect(screen.queryByTestId("put-on-board")).not.toBeInTheDocument();
    expect(screen.queryByTestId("put-on-board-for-set")).not.toBeInTheDocument();
    expect(screen.queryByTestId("approve-draft")).not.toBeInTheDocument();
    expect(screen.queryByTestId("push-draft")).not.toBeInTheDocument();
    expect(screen.getByTestId("discard-draft")).toBeInTheDocument();
    expect(screen.getByTestId("refine-draft")).toBeInTheDocument();
  });

  it("says the same of a draft approved earlier", () => {
    renderWithQueryClient(<DraftCard draft={draft({ status: "approved" })} />);

    expect(screen.getByTestId("draft-board-not-adopted")).toBeInTheDocument();
    expect(screen.queryByTestId("put-on-board")).not.toBeInTheDocument();
  });

  it("says it once the board is adopted no more", () => {
    renderWithQueryClient(<DraftCard draft={draft()} adopted landing={NEW_PROFILE} />);

    expect(screen.queryByTestId("draft-board-not-adopted")).not.toBeInTheDocument();
  });
});

describe("what the card says while the board is unknown", () => {
  it("offers no put for a draft the board has not placed yet, and says it is checking", () => {
    renderWithQueryClient(<DraftCard draft={draft()} adopted />);

    expect(screen.getByTestId("draft-landing-pending")).toBeInTheDocument();
    expect(screen.queryByTestId("put-on-board")).not.toBeInTheDocument();
    expect(screen.queryByTestId("put-on-board-for-set")).not.toBeInTheDocument();
    expect(screen.getByTestId("discard-draft")).toBeInTheDocument();
  });

  it("says only that the board can't be read when it can't, for a drafted draft too", () => {
    renderWithQueryClient(<DraftCard draft={draft()} boardUnknown />);

    expect(screen.getByTestId("draft-board-unknown")).toBeInTheDocument();
    expect(screen.queryByTestId("draft-board-not-adopted")).not.toBeInTheDocument();
    expect(screen.queryByTestId("put-on-board")).not.toBeInTheDocument();
  });

  it("offers no put when the board was adopted but can't be read now", () => {
    renderWithQueryClient(<DraftCard draft={draft()} adopted boardUnknown landing={NEW_PROFILE} />);

    expect(screen.getByTestId("draft-board-unknown")).toBeInTheDocument();
    expect(screen.queryByTestId("put-on-board")).not.toBeInTheDocument();
  });

  it("shows no acknowledgement checkbox before the board is adopted: nothing to apply it to", () => {
    renderWithQueryClient(<DraftCard draft={draft({ stop_condition_changes: [yieldChange()] })} />);

    expect(screen.getByTestId("stop-condition-warning")).toBeInTheDocument();
    expect(screen.queryByTestId("acknowledge-stop-changes")).not.toBeInTheDocument();
  });
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
        adopted
      />,
    );

    const block = screen.getByTestId("draft-prediction");
    expect(block).toHaveTextContent("Guji on the Niche");
    expect(block).toHaveTextContent("compared to v2");
    expect(block).toHaveTextContent("less of the dry finish");
    // Where it lands matters as much as what it says: putting this draft on the board for a
    // different Set records no prediction at all.
    expect(block).toHaveTextContent("if you put it on the board for that Set");
  });

  it("says a draft made from an analysis came from one, as history and not a link", () => {
    renderWithQueryClient(<DraftCard draft={draft({ source_analysis_id: 3 })} />);
    const card = screen.getByTestId("draft-card");
    expect(card).toHaveTextContent("· from an analysis (now a review)");
    expect(card).not.toHaveTextContent("analysis #3");

    renderWithQueryClient(<DraftCard draft={draft({ id: 9, source_analysis_id: null })} />);
    expect(screen.getAllByTestId("draft-card")[1]).not.toHaveTextContent("from an analysis");
  });

  it("names the versions of its Set, never by their ordinals", () => {
    renderWithQueryClient(
      <DraftCard
        draft={draft({
          set_id: 3,
          set_name: "Guji on the Niche",
          status: "approved",
          prediction: "Compared to v1.2: less of the dry finish, and no slower.",
          compares_to_version_no: 3,
          compares_to_version_label: "v1.2",
          set_next_version_no: 4,
          set_next_minor_label: "v1.3",
          set_next_major_label: "v2",
        })}
        adopted
        landing={{ ...NEW_PROFILE, for_set: NEW_PROFILE.plain }}
      />,
    );

    const card = screen.getByTestId("draft-card");
    expect(screen.getByTestId("draft-prediction")).toHaveTextContent("compared to v1.2");
    expect(screen.getByTestId("put-on-board-for-set")).toHaveTextContent("record it as v1.3");
    expect(card).not.toHaveTextContent(/\bv3\b/);
    expect(card).not.toHaveTextContent(/\bv4\b/);
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
    // to avoid, so the list is on the card next to the put button.
    expect(screen.getByTestId("draft-clamps")).toHaveTextContent("140 → 100");
  });

  describe("putting a Set's draft on the board", () => {
    const setCard = (extra: Record<string, unknown> = {}) => (
      <DraftCard
        draft={draft({ ...FOR_GUJI, status: "approved", ...extra })}
        adopted
        landing={{ ...NEW_PROFILE, for_set: NEW_PROFILE.plain }}
      />
    );

    it("puts it for its Set by default, and says which version it records", async () => {
      const user = setupUser();
      renderWithQueryClient(setCard());

      const button = screen.getByTestId("put-on-board-for-set");
      // A put draft is a minor version by default, and the name is the server's, never the
      // ordinal: this is the Set's fourth version.
      expect(button).toHaveTextContent(
        "Put on the board and record it as v2.2 of Guji on the Niche",
      );
      expect(button).not.toHaveTextContent("v4");
      await user.click(button);

      // The Set in the body is what makes the sync record the version and the prediction;
      // without it nothing is recorded on the Set.
      await waitFor(() =>
        expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1, setId: 3, major: false }),
      );
    });

    it("is a minor version unless the person marks it major", async () => {
      const user = setupUser();
      renderWithQueryClient(setCard());

      const box = screen.getByRole("checkbox", { name: "Major change" });
      expect(box).not.toBeChecked();
      await user.click(box);
      const button = screen.getByTestId("put-on-board-for-set");
      expect(button).toHaveTextContent("record it as v3 of Guji on the Niche");
      await user.click(button);

      await waitFor(() =>
        expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1, setId: 3, major: true }),
      );
    });

    it("preselects the agent's suggestion of major and shows its reason", () => {
      renderWithQueryClient(
        setCard({
          suggest_major: true,
          major_reason: "Eight bar is a different kind of shot from nine.",
        }),
      );

      expect(screen.getByRole("checkbox", { name: "Major change" })).toBeChecked();
      expect(screen.getByTestId("major-reason")).toHaveTextContent(
        "Eight bar is a different kind of shot from nine.",
      );
      expect(screen.getByTestId("put-on-board-for-set")).toHaveTextContent("record it as v3");
    });

    it("can still be put without recording it on the Set", async () => {
      const user = setupUser();
      renderWithQueryClient(setCard());

      const plain = screen.getByTestId("put-on-board");
      expect(plain).toHaveTextContent("Put on the board without recording it on the Set");
      await user.click(plain);

      // No version is named by a put that records nothing on the Set.
      await waitFor(() => expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1 }));
    });

    it("offers only the plain put once the Set is gone", () => {
      renderWithQueryClient(
        <DraftCard
          draft={draft({ ...FOR_GUJI, set_name: null, status: "approved" })}
          adopted
          landing={NEW_PROFILE}
        />,
      );
      expect(screen.queryByTestId("put-on-board-for-set")).not.toBeInTheDocument();
      expect(screen.getByTestId("put-on-board")).toHaveTextContent("Put on the board");
    });

    it("says the prediction was recorded only when the sync recorded it", () => {
      const pushed = { ...FOR_GUJI, status: "pushed", pushed_device_profile_id: "aB3xYz90Pq" };
      const { rerender } = renderWithQueryClient(
        <DraftCard
          draft={draft({ ...pushed, recorded_version_no: 4, recorded_version_label: "v2.2" })}
        />,
      );
      expect(screen.getByTestId("draft-prediction-landing")).toHaveTextContent(
        "Recorded as v2.2 of Guji on the Niche when it reached the machine for it.",
      );

      rerender(<DraftCard draft={draft({ ...pushed, recorded_version_no: null })} />);
      const landing = screen.getByTestId("draft-prediction-landing");
      expect(landing).toHaveTextContent(
        "It reached the machine without being recorded on Guji on the Niche",
      );
      expect(landing).not.toHaveTextContent("Recorded as");
    });

    it("says nothing about where it lands once the draft can no longer be put", () => {
      // A failed push recorded nothing, and a discarded draft may have been recorded before
      // it was taken off the machine: neither sentence would be true of both.
      for (const status of ["failed", "discarded", "superseded"]) {
        const { unmount } = renderWithQueryClient(
          <DraftCard draft={draft({ ...FOR_GUJI, status, pushed_device_profile_id: null })} />,
        );
        expect(screen.getByTestId("draft-prediction")).toBeInTheDocument();
        expect(screen.queryByTestId("draft-prediction-landing")).not.toBeInTheDocument();
        unmount();
      }
    });
  });

  it("says what the sync that put it on the machine replaced and kept", () => {
    const lines = [
      "Replaced aB3xYz90Pq: the previous copy is off the machine.",
      "Left cD4wXy12Rs on the machine: not created by this app.",
    ];
    renderWithQueryClient(
      <DraftCard
        draft={draft({
          status: "pushed",
          pushed_device_profile_id: "nEw1234567",
          replaced_device_profile_id: "aB3xYz90Pq",
          outcome: { action: "push", lines },
        })}
      />,
    );
    expect(screen.getByTestId("draft-pushed")).toHaveTextContent("nEw1234567");
    expect(screen.getByTestId("draft-outcome")).toHaveTextContent("Replaced aB3xYz90Pq");
    expect(screen.getByTestId("draft-outcome")).toHaveTextContent("not created by this app");
  });

  it("takes a profile off the machine from the board, not from the card", () => {
    renderWithQueryClient(
      <DraftCard draft={draft({ status: "pushed", pushed_device_profile_id: "aB3xYz90Pq" })} />,
    );

    expect(screen.getByTestId("draft-pushed")).toHaveTextContent(
      "delete the profile from the board, or go back to its previous version there",
    );
    expect(screen.queryByTestId("rollback-draft")).not.toBeInTheDocument();
    expect(screen.queryByTestId("discard-draft")).not.toBeInTheDocument();
  });

  it("says a later version replaced a pushed draft's profile", () => {
    renderWithQueryClient(
      <DraftCard
        draft={draft({
          status: "pushed",
          pushed_device_profile_id: "aB3xYz90Pq",
          replaced_by_draft_id: 2,
        })}
      />,
    );
    expect(screen.getByTestId("draft-replaced")).toHaveTextContent("replaced this profile");
    expect(screen.queryByTestId("draft-pushed")).not.toBeInTheDocument();
  });

  it("shows no outcome for a draft that has not touched the machine", () => {
    renderWithQueryClient(<DraftCard draft={draft()} />);
    expect(screen.queryByTestId("draft-outcome")).not.toBeInTheDocument();
  });

  it("explains an old push that did not verify, leaves its copy to the display, and lets it be cleared", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <DraftCard
        draft={draft({
          status: "failed",
          pushed_device_profile_id: "aB3xYz90Pq",
          error: "the machine stored something other than what was sent",
        })}
        adopted
      />,
    );

    expect(screen.getByTestId("draft-failed")).toHaveTextContent("stored something other than");
    expect(screen.getByTestId("draft-failed")).toHaveTextContent("remove it on the display");
    expect(screen.queryByTestId("rollback-draft")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("discard-draft"));

    await waitFor(() => expect(discardProfileDraft).toHaveBeenCalledWith(1));
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

  it("warns about a base that changed on the machine, and still offers the put", () => {
    // The diff is against a version the display no longer holds. The board does not refuse it:
    // the next sync puts this version beside whatever was changed there.
    renderWithQueryClient(
      <DraftCard draft={draft({ base_is_current: false })} adopted landing={NEW_PROFILE} />,
    );

    expect(screen.getByTestId("stale-base-warning")).toHaveTextContent("changed on the machine");
    expect(screen.getByTestId("stale-base-warning")).toHaveTextContent(
      "the next sync puts this version beside whatever was changed there",
    );
    expect(screen.queryByTestId("allow-stale-base")).not.toBeInTheDocument();
    expect(screen.getByTestId("put-on-board")).not.toBeDisabled();
  });

  it("does not offer to discard a draft waiting on the board under it", () => {
    renderWithQueryClient(
      <DraftCard
        draft={draft({ status: "approved" })}
        adopted
        boardRow={boardRow({ id: 2, origin: "draft", pending_draft_id: 1 })}
      />,
    );

    expect(screen.getByTestId("draft-on-board")).toHaveTextContent("On the board.");
    expect(screen.queryByTestId("discard-draft")).not.toBeInTheDocument();
    expect(screen.queryByTestId("put-on-board")).not.toBeInTheDocument();
  });
});

describe("long button labels stay inside the card at phone width", () => {
  // jsdom has no layout: what can be pinned is the classes that let a button shrink and wrap
  // (the base button is `shrink-0 whitespace-nowrap`, which made the page scroll sideways).
  const WRAPS = ["min-w-0", "max-w-full", "shrink", "whitespace-normal", "h-auto"];

  it("a put for the Set and a put without it", () => {
    renderWithQueryClient(
      <DraftCard
        draft={draft({ ...FOR_GUJI, status: "approved" })}
        adopted
        landing={{ ...NEW_PROFILE, for_set: NEW_PROFILE.plain }}
      />,
    );
    for (const id of ["put-on-board-for-set", "put-on-board"]) {
      for (const cls of WRAPS) expect(screen.getByTestId(id)).toHaveClass(cls);
    }
  });

  it("the plain put", () => {
    renderWithQueryClient(
      <DraftCard draft={draft({ status: "approved" })} adopted landing={NEW_PROFILE} />,
    );
    for (const cls of WRAPS) expect(screen.getByTestId("put-on-board")).toHaveClass(cls);
  });
});
