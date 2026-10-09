import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DraftStanding } from "@/api/types";
import { TellAgentContext } from "@/components/chat/tellAgent";
import { ProfileProposalCard } from "@/components/profiles/ProfileProposalCard";
import { NAME_CHECK_DELAY_MS } from "@/components/profiles/ProposalName";
import { SyncOwnerProvider } from "@/components/sync/SyncOwner";
import { baseProfile, draft, draftProfile, proProfile, standing } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

// Canvas is opaque to jsdom: the chart is a figure that says what it was asked to draw.
vi.mock("@/components/charts/ProfileCurveChart", () => ({
  ProfileCurveChart: ({ title, xMax }: { title?: string; xMax?: number }) => (
    <figure data-testid="profile-curve" data-title={title ?? ""} data-xmax={xMax ?? ""} />
  ),
}));

const {
  getDraftStanding,
  checkDraftName,
  putOnBoard,
  discardProfileDraft,
  runSync,
  getSyncStatus,
  getDeviceStatus,
} = vi.hoisted(() => ({
  getDraftStanding: vi.fn(),
  checkDraftName: vi.fn(),
  putOnBoard: vi.fn(),
  discardProfileDraft: vi.fn(),
  runSync: vi.fn(),
  getSyncStatus: vi.fn(),
  getDeviceStatus: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getDraftStanding,
  checkDraftName,
  putOnBoard,
  discardProfileDraft,
  runSync,
  getSyncStatus,
  getDeviceStatus,
}));

const ROW = { id: 4, label: "9 Bar Espresso", on_machine: true };

function renderCard(
  props: Partial<React.ComponentProps<typeof ProfileProposalCard>> = {},
  tell: ((message: string) => void) | null = null,
) {
  return renderWithQueryClient(
    <TellAgentContext.Provider value={tell}>
      <SyncOwnerProvider>
        <ProfileProposalCard draftId={1} place="chat" {...props} />
      </SyncOwnerProvider>
    </TellAgentContext.Provider>,
  );
}

/** A proposal that would be a profile of its own. */
function brandNew(overrides: Record<string, unknown> = {}): DraftStanding {
  return standing({
    draft: draft({ is_new: true, base_label: null, draft_label: "Soft Bloom" }),
    active_profile: null,
    profile: draftProfile({ label: "Soft Bloom" }),
    landing: {
      draft_id: 1,
      already_on_board_label: null,
      plain: { row_id: null, row_label: null },
      for_set: null,
    },
    row_id: null,
    row_label: null,
    ...overrides,
  });
}

/** A change proposed for a Set: the next minor is v1.2, the next major v2. */
function forSet(overrides: Record<string, unknown> = {}): DraftStanding {
  return standing({
    draft: draft({
      set_id: 3,
      set_name: "Guji natural",
      set_next_minor_label: "v1.2",
      set_next_major_label: "v2",
      prediction: "Sweeter and rounder.",
      compares_to_version_label: "v1.1",
    }),
    landing: {
      draft_id: 1,
      already_on_board_label: null,
      plain: { row_id: 4, row_label: "9 Bar Espresso" },
      for_set: { row_id: 4, row_label: "9 Bar Espresso" },
    },
    ...overrides,
  });
}

function answered(state: string, reason: string | null, extra: Record<string, unknown> = {}) {
  return standing({
    draft: draft({ status: "approved", draft_label: "Adaptive Bloom" }),
    active_profile: null,
    landing: null,
    state,
    reason,
    ...extra,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  getDraftStanding.mockResolvedValue(standing());
  checkDraftName.mockImplementation(async (_id: number, label: string) => ({
    label: label.trim(),
    refused: null,
  }));
  putOnBoard.mockResolvedValue(ROW);
  discardProfileDraft.mockResolvedValue({});
  runSync.mockResolvedValue({ queued: ["shots", "profiles"] });
  getSyncStatus.mockResolvedValue({
    configured: true,
    connected: true,
    running: false,
    last_runs: {},
    last_error: null,
    counts: {
      total: 0,
      quarantined: 0,
      deleted_on_device: 0,
      incomplete: 0,
      samples: 0,
      needs_set: 0,
    },
    recent_events: [],
  });
  getDeviceStatus.mockResolvedValue({
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: null,
    last_status: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("a proposal waiting for a person", () => {
  it("says what it changes and offers Approve and sync and Decline, with Writes on", async () => {
    renderCard();

    expect(await screen.findByText("Proposed change · 9 Bar Espresso [AI]")).toBeInTheDocument();
    expect(screen.getByText("Dropped the peak to 8 bar.")).toBeInTheDocument();
    expect(screen.getByText("What changes")).toBeInTheDocument();
    expect(screen.getByTestId("approve-proposal")).toHaveTextContent("Approve and sync");
    expect(screen.getByTestId("decline-proposal")).toBeInTheDocument();
    // A change to a profile that exists has no Name field and no Set box.
    expect(screen.queryByTestId("proposal-name")).not.toBeInTheDocument();
    expect(screen.queryByTestId("major-choice")).not.toBeInTheDocument();
    expect(screen.queryByTestId("proposal-writes-off")).not.toBeInTheDocument();
  });

  it("says Approve, and why nothing syncs, when Writes are off", async () => {
    getDraftStanding.mockResolvedValue(standing({ writes_enabled: false }));
    renderCard();

    expect(await screen.findByTestId("approve-proposal")).toHaveTextContent(/^Approve$/);
    expect(screen.getByTestId("proposal-writes-off")).toHaveTextContent(
      "Writes are off (top bar), so it goes to the machine at the first sync after you turn them on.",
    );
  });

  it("draws the active version and the proposed one on the same axes for a pro profile", async () => {
    getDraftStanding.mockResolvedValue(
      standing({
        active_profile: proProfile(),
        profile: proProfile({
          phases: [
            { ...(proProfile().phases as object[])[0], duration: 12 },
            (proProfile().phases as object[])[1],
          ],
        }),
      }),
    );
    renderCard();

    const curves = await screen.findAllByTestId("profile-curve");
    expect(curves.map((c) => c.getAttribute("data-title"))).toEqual(["Active version", "Proposed"]);
    // The longer of the two (6 + 24 against 12 + 24) is the axis both are drawn on.
    expect(curves[0]).toHaveAttribute("data-xmax", "36");
    expect(curves[1]).toHaveAttribute("data-xmax", "36");
    // Two columns when the card itself is 42rem wide (each chart then 300 px or more), stacked
    // below it, whatever the window.
    expect(screen.getByTestId("proposal-curves")).toHaveClass("@2xl:grid-cols-2");
  });

  it("draws one curve for a new profile and none for a profile that is not pro", async () => {
    getDraftStanding.mockResolvedValue(brandNew({ profile: proProfile({ label: "Soft Bloom" }) }));
    const first = renderCard();
    expect(await screen.findAllByTestId("profile-curve")).toHaveLength(1);
    expect(screen.getByTestId("proposal-curves")).not.toHaveClass("@2xl:grid-cols-2");
    first.unmount();

    getDraftStanding.mockResolvedValue(standing());
    renderCard();
    await screen.findByTestId("approve-proposal");
    expect(screen.queryByTestId("profile-curve")).not.toBeInTheDocument();
  });

  it("shows the prediction and the clamps and the stop-condition warning the draft carries", async () => {
    getDraftStanding.mockResolvedValue(
      standing({
        draft: draft({
          prediction: "Sweeter and rounder.",
          set_name: "Guji natural",
          compares_to_version_label: "v1.1",
          clamp_changes: [
            { path: "phases[0].pump.pressure", before: "13", after: "12", reason: "policy" },
          ],
          stop_condition_changes: [
            {
              phase_index: 0,
              phase_name: "Pump",
              kind: "changed",
              target_type: "volumetric",
              before: { type: "volumetric", operator: "gte", value: 36 },
              after: { type: "volumetric", operator: "gte", value: 44 },
            },
          ],
        }),
      }),
    );
    renderCard();

    expect(await screen.findByTestId("proposal-prediction")).toHaveTextContent(
      "Prediction for Guji natural · compared to v1.1Sweeter and rounder.",
    );
    expect(screen.getByTestId("proposal-clamps")).toHaveTextContent("13 → 12");
    expect(screen.getByTestId("stop-condition-warning")).toHaveTextContent("gte 36 → gte 44");
  });

  it("holds Approve, and says the server's sentence under the Name field, when the landing is refused", async () => {
    const taken = "There is already a profile called Soft Bloom. Choose another name.";
    getDraftStanding.mockResolvedValue(
      brandNew({
        landing: {
          draft_id: 1,
          already_on_board_label: null,
          // What the server really sends for a new or renamed draft whose name is taken.
          plain: { row_id: null, row_label: null, refused: taken },
          for_set: null,
        },
      }),
    );
    checkDraftName.mockResolvedValue({ label: "Soft Bloom", refused: taken });
    renderCard();

    expect(await screen.findByTestId("proposal-name")).toHaveValue("Soft Bloom");
    await waitFor(() =>
      expect(screen.getByTestId("proposal-name-problem")).toHaveTextContent(taken),
    );
    const approve = screen.getByTestId("approve-proposal");
    expect(approve).toHaveAttribute("aria-disabled", "true");
    // The held button does not offer to add a name that cannot be taken.
    expect(approve).toHaveTextContent(/^Approve and sync$/);
    expect(screen.getByTestId("decline-proposal")).toBeInTheDocument();
    expect(screen.queryByTestId("proposal-refused")).not.toBeInTheDocument();
  });
});

describe("a proposal for a Set", () => {
  it("names the Set version the click records, and the Major box changes it", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(forSet());
    renderCard();

    const button = await screen.findByTestId("approve-proposal");
    expect(button).toHaveTextContent("Approve as v1.2 of Guji natural and sync");
    await user.click(screen.getByRole("checkbox", { name: "Major change" }));
    expect(button).toHaveTextContent("Approve as v2 of Guji natural and sync");
  });

  it("has no secondary button in the chat, and keeps 'without recording it' on the Profiles page", async () => {
    getDraftStanding.mockResolvedValue(forSet());
    const chat = renderCard({ place: "chat" });
    await screen.findByTestId("approve-proposal");
    chat.unmount();

    renderCard({ place: "profiles" });
    expect(await screen.findByTestId("approve-proposal-without-set")).toHaveTextContent(
      "Approve without recording it on Guji natural",
    );
  });

  it("approves without the Set when asked on the Profiles page, and syncs when Writes are on", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(forSet());
    renderCard({ place: "profiles" });

    await user.click(await screen.findByTestId("approve-proposal-without-set"));

    await waitFor(() => expect(putOnBoard).toHaveBeenCalledTimes(1));
    expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1 });
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
  });

  it("names the sync on the secondary button, and starts none with Writes off", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(forSet({ writes_enabled: false }));
    renderCard({ place: "profiles" });

    const button = await screen.findByTestId("approve-proposal-without-set");
    expect(button).toHaveTextContent(/^Approve without recording it on Guji natural$/);
    await user.click(button);
    await waitFor(() => expect(putOnBoard).toHaveBeenCalledTimes(1));
    expect(runSync).not.toHaveBeenCalled();
  });

  it("records the version on the Set with the Major answer, then starts the sync, and tells the agent", async () => {
    const user = setupUser();
    const tell = vi.fn();
    getDraftStanding.mockResolvedValue(forSet());
    renderCard({}, tell);

    await user.click(await screen.findByRole("checkbox", { name: "Major change" }));
    await user.click(screen.getByTestId("approve-proposal"));

    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1, setId: 3, major: true });
    expect(tell).toHaveBeenCalledTimes(1);
    expect(tell).toHaveBeenCalledWith("Approved: 9 Bar Espresso [AI] as v2 of Guji natural.");
  });
});

describe("Approve and sync", () => {
  it("puts first, then starts the sync, and tells the agent once", async () => {
    const user = setupUser();
    const tell = vi.fn();
    const order: string[] = [];
    putOnBoard.mockImplementation(async () => {
      order.push("put");
      return ROW;
    });
    runSync.mockImplementation(async () => {
      order.push("sync");
      return { queued: ["shots", "profiles"] };
    });
    renderCard({}, tell);

    await user.click(await screen.findByTestId("approve-proposal"));

    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    expect(order).toEqual(["put", "sync"]);
    // The card says it is syncing and the sync reports itself: no "next sync" toast beside them.
    expect(toast.success).not.toHaveBeenCalled();
    expect(tell).toHaveBeenCalledTimes(1);
    expect(tell).toHaveBeenCalledWith(
      "Approved: 9 Bar Espresso [AI], a new version of that profile.",
    );
  });

  it("sends one request for a double click", async () => {
    const user = setupUser();
    renderCard();

    const button = await screen.findByTestId("approve-proposal");
    await user.dblClick(button);

    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    expect(putOnBoard).toHaveBeenCalledTimes(1);
  });

  it("starts no sync, and tells nobody, when the put is refused", async () => {
    const user = setupUser();
    const tell = vi.fn();
    putOnBoard.mockRejectedValue(new Error("Bloom is already a profile."));
    renderCard({}, tell);

    await user.click(await screen.findByTestId("approve-proposal"));

    expect(await screen.findByTestId("proposal-error")).toHaveTextContent(
      "Bloom is already a profile.",
    );
    expect(runSync).not.toHaveBeenCalled();
    expect(tell).not.toHaveBeenCalled();
    // And the buttons are there for another try.
    expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true");
  });

  it("starts no sync with Writes off, but still approves and tells the agent", async () => {
    const user = setupUser();
    const tell = vi.fn();
    getDraftStanding.mockResolvedValue(standing({ writes_enabled: false }));
    renderCard({}, tell);

    await user.click(await screen.findByTestId("approve-proposal"));

    await waitFor(() => expect(tell).toHaveBeenCalledTimes(1));
    expect(putOnBoard).toHaveBeenCalledTimes(1);
    expect(runSync).not.toHaveBeenCalled();
    // Nothing else says it was approved and waits for a sync, so the toast does.
    // Just the fact: the card (or the profile's row) says where it stands.
    expect(toast.success).toHaveBeenCalledWith("Approved: 9 Bar Espresso.");
  });

  it("keeps the focus where it was when a press lands on a button that is busy", async () => {
    getDraftStanding.mockResolvedValue(standing());
    putOnBoard.mockReturnValue(new Promise(() => undefined));
    const user = setupUser();
    renderCard();

    const button = await screen.findByTestId("approve-proposal");
    await user.click(button);
    await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));
    // Not `disabled`: the second press of a double click must not drop focus to the page.
    expect(button).not.toBeDisabled();
    const press = fireEvent.mouseDown(button);
    expect(press).toBe(false);
  });

  it("shows a refusal only on the proposal it was for", async () => {
    const user = setupUser();
    putOnBoard.mockRejectedValue(new Error("refused"));
    const view = renderCard({ draftId: 1 });
    await user.click(await screen.findByTestId("approve-proposal"));
    await screen.findByTestId("proposal-error");

    getDraftStanding.mockResolvedValue(standing({ draft: draft({ id: 2 }) }));
    view.rerender(
      <TellAgentContext.Provider value={null}>
        <SyncOwnerProvider>
          <ProfileProposalCard draftId={2} place="chat" />
        </SyncOwnerProvider>
      </TellAgentContext.Provider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("profile-proposal")).toHaveAttribute("data-draft-id", "2"),
    );
    expect(screen.queryByTestId("proposal-error")).not.toBeInTheDocument();
  });
});

describe("a new profile's name", () => {
  it("is prefilled with the proposed name, and the button follows it as it is typed", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    renderCard();

    const field = await screen.findByTestId("proposal-name");
    expect(field).toHaveValue("Soft Bloom");
    expect(screen.getByText("Proposed new profile")).toBeInTheDocument();
    expect(screen.getByTestId("approve-proposal")).toHaveTextContent(
      "Approve and sync — add “Soft Bloom”",
    );

    await user.clear(field);
    await user.type(field, "Gentle Bloom");
    expect(screen.getByTestId("approve-proposal")).toHaveTextContent(
      "Approve and sync — add “Gentle Bloom”",
    );
  });

  it("says a profile needs a name, and sends nothing, when the field is empty", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    renderCard();

    await user.clear(await screen.findByTestId("proposal-name"));

    expect(screen.getByTestId("proposal-name-problem")).toHaveTextContent("A profile needs a name");
    expect(screen.getByTestId("approve-proposal")).toHaveAttribute("aria-disabled", "true");
    await user.click(screen.getByTestId("approve-proposal"));
    expect(putOnBoard).not.toHaveBeenCalled();
    // No request is made for an empty name.
    expect(checkDraftName).not.toHaveBeenCalledWith(1, "");
  });

  it("asks the server's rule as it is typed, once the typing rests, and shows its sentence", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    checkDraftName.mockImplementation(async (_id: number, label: string) => ({
      label: label.trim(),
      refused:
        label.trim() === "Adaptive Bloom"
          ? "Adaptive Bloom is already a profile. Approving under that name would make this a new version of it, so choose another name."
          : null,
    }));
    renderCard();
    const field = await screen.findByTestId("proposal-name");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    checkDraftName.mockClear();

    await user.clear(field);
    await user.type(field, "Adaptive Bloom");
    // Still resting: nothing asked yet, and the button waits rather than offering a stale answer.
    expect(checkDraftName).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));

    await waitFor(() =>
      expect(screen.getByTestId("proposal-name-problem")).toHaveTextContent(
        "Adaptive Bloom is already a profile. Approving under that name would make this a new version of it, so choose another name.",
      ),
    );
    expect(checkDraftName).toHaveBeenCalledTimes(1);
    expect(checkDraftName).toHaveBeenCalledWith(1, "Adaptive Bloom");
    expect(screen.getByTestId("approve-proposal")).toHaveAttribute("aria-disabled", "true");
    await user.click(screen.getByTestId("approve-proposal"));
    expect(putOnBoard).not.toHaveBeenCalled();

    // Another name clears it.
    await user.clear(field);
    await user.type(field, "Gentle Bloom");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    expect(screen.getByTestId("proposal-name-problem")).toBeEmptyDOMElement();
  });

  it("approves under the typed name, and tells the agent what it was proposed as", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = setupUser();
    const tell = vi.fn();
    getDraftStanding.mockResolvedValue(brandNew());
    renderCard({}, tell);
    const field = await screen.findByTestId("proposal-name");

    await user.clear(field);
    await user.type(field, "  Gentle Bloom ");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(screen.getByTestId("approve-proposal"));

    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));
    expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1, label: "Gentle Bloom" });
    expect(tell).toHaveBeenCalledWith(
      "Approved: Gentle Bloom, a new profile (you proposed it as Soft Bloom).",
    );
  });

  it("draws the answered card as 'Approved as <new> (proposed as <old>)' after the rename", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValueOnce(brandNew()).mockResolvedValue(
      answered("approved", "It goes to the machine at the next sync.", {
        draft: draft({ status: "approved", draft_label: "Gentle Bloom" }),
      }),
    );
    renderCard();
    const field = await screen.findByTestId("proposal-name");
    await user.clear(field);
    await user.type(field, "Gentle Bloom");
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(screen.getByTestId("approve-proposal"));

    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      /^✓ Approved as Gentle Bloom \(proposed as Soft Bloom\)$/,
    );
  });

  it("sends the proposed name, and no rename ending, when the field is left alone", async () => {
    const user = setupUser();
    const tell = vi.fn();
    getDraftStanding.mockResolvedValue(brandNew());
    renderCard({}, tell);

    await screen.findByTestId("proposal-name");
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
    await user.click(screen.getByTestId("approve-proposal"));

    await waitFor(() => expect(tell).toHaveBeenCalledTimes(1));
    expect(putOnBoard).toHaveBeenCalledWith({ draftId: 1, label: "Soft Bloom" });
    expect(tell).toHaveBeenCalledWith("Approved: Soft Bloom, a new profile.");
  });

  it("starts from its own proposal when it is handed another, under StrictMode", async () => {
    const user = setupUser();
    getDraftStanding.mockImplementation(async (id: number) =>
      brandNew({
        draft: draft({
          id,
          is_new: true,
          base_label: null,
          draft_label: id === 1 ? "Soft Bloom" : "Other Bloom",
        }),
      }),
    );
    const view = renderWithQueryClient(
      <StrictMode>
        <SyncOwnerProvider>
          <ProfileProposalCard draftId={1} place="chat" />
        </SyncOwnerProvider>
      </StrictMode>,
    );
    const field = await screen.findByTestId("proposal-name");
    await user.type(field, " half typed");

    view.rerender(
      <StrictMode>
        <SyncOwnerProvider>
          <ProfileProposalCard draftId={2} place="chat" />
        </SyncOwnerProvider>
      </StrictMode>,
    );

    await waitFor(() => expect(screen.getByTestId("proposal-name")).toHaveValue("Other Bloom"));
  });
});

describe("Decline", () => {
  it("reveals a note, declines with it, and tells the agent", async () => {
    const user = setupUser();
    const tell = vi.fn();
    renderCard({}, tell);

    await user.click(await screen.findByTestId("decline-proposal"));
    expect(screen.getByTestId("decline-note")).toBeVisible();
    await user.type(screen.getByLabelText(/Why not/), "  Too aggressive for a light roast. ");
    await user.click(screen.getByTestId("decline-confirm"));

    await waitFor(() => expect(discardProfileDraft).toHaveBeenCalledWith(1));
    expect(tell).toHaveBeenCalledWith("Declined: Too aggressive for a light roast.");
    expect(runSync).not.toHaveBeenCalled();
  });

  it("says no reason was given when the note is empty, and Escape declines nothing", async () => {
    const user = setupUser();
    const tell = vi.fn();
    renderCard({}, tell);

    await user.click(await screen.findByTestId("decline-proposal"));
    await user.type(screen.getByLabelText(/Why not/), "half{Escape}");
    expect(screen.getByTestId("decline-note")).not.toBeVisible();
    expect(discardProfileDraft).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("decline-proposal"));
    await user.click(screen.getByTestId("decline-confirm"));
    await waitFor(() => expect(tell).toHaveBeenCalledWith("Declined: no reason given."));
  });

  it("declines at once on the Profiles page, where no agent is listening: no note field", async () => {
    const user = setupUser();
    renderCard({ place: "profiles" }, null);

    await user.click(await screen.findByTestId("decline-proposal"));

    await waitFor(() => expect(discardProfileDraft).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("decline-note")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("tells the agent nothing when the decline is refused", async () => {
    const user = setupUser();
    const tell = vi.fn();
    discardProfileDraft.mockRejectedValue(new Error("not waiting"));
    renderCard({}, tell);

    await user.click(await screen.findByTestId("decline-proposal"));
    await user.type(screen.getByLabelText(/Why not/), "too sweet");
    await user.click(screen.getByTestId("decline-confirm"));

    expect(await screen.findByTestId("proposal-error")).toHaveTextContent("not waiting");
    expect(tell).not.toHaveBeenCalled();
  });
});

describe("a proposal that has been answered", () => {
  it("is approved, with the Set version it is recorded as and the server's sentence", async () => {
    getDraftStanding.mockResolvedValue(
      answered("approved", "Writes are off, so a sync will not send it.", {
        draft: draft({
          status: "approved",
          draft_label: "Adaptive Bloom",
          set_name: "Guji natural",
        }),
        set_version_label: "v1.2",
      }),
    );
    renderCard();

    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      "✓ Approved · Adaptive Bloom → v1.2 of Guji natural",
    );
    expect(screen.getByText("Writes are off, so a sync will not send it.")).toBeInTheDocument();
    expect(screen.queryByTestId("approve-proposal")).not.toBeInTheDocument();
  });

  it("says Syncing while the sync this card started runs, and the server's sentence otherwise", async () => {
    const user = setupUser();
    // Read waiting first; the put settles the card's reads and the next read says approved.
    getDraftStanding
      .mockResolvedValueOnce(standing())
      .mockResolvedValue(answered("approved", "It goes to the machine at the next sync."));
    renderCard();
    await user.click(await screen.findByTestId("approve-proposal"));
    await waitFor(() => expect(runSync).toHaveBeenCalledTimes(1));

    expect(await screen.findByTestId("proposal-syncing")).toHaveTextContent(
      "Syncing with the machine…",
    );
    expect(screen.queryByText("It goes to the machine at the next sync.")).not.toBeInTheDocument();
  });

  it("does not say Syncing for a card that started no sync", async () => {
    getDraftStanding.mockResolvedValue(
      answered("approved", "It goes to the machine at the next sync."),
    );
    renderCard();

    expect(await screen.findByText("It goes to the machine at the next sync.")).toBeInTheDocument();
    expect(screen.queryByTestId("proposal-syncing")).not.toBeInTheDocument();
  });

  it("is on the machine, and says to select it, or that it is selected", async () => {
    getDraftStanding.mockResolvedValue(answered("on_machine", null, { selected: false }));
    const first = renderCard();
    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      "✓ On the machine · Adaptive Bloom",
    );
    expect(
      screen.getByText("Select “Adaptive Bloom” on the machine for your next shot."),
    ).toBeInTheDocument();
    first.unmount();

    getDraftStanding.mockResolvedValue(answered("on_machine", null, { selected: true }));
    renderCard();
    expect(
      await screen.findByText("It is the selected profile: your next shot uses it."),
    ).toBeInTheDocument();
  });

  it("is not on the machine, with the server's words and a link to the profile", async () => {
    getDraftStanding.mockResolvedValue(
      answered("not_on_machine", "The machine read it back differently, so it was not kept."),
    );
    renderCard();

    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      "✗ Not on the machine · Adaptive Bloom",
    );
    expect(
      screen.getByText("The machine read it back differently, so it was not kept."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("open-in-profiles")).toHaveAttribute("href", "/profiles#version-8");
  });

  it("is declined, or replaced with the server's sentence and no version name", async () => {
    getDraftStanding.mockResolvedValue(
      answered("declined", null, {
        draft: draft({ status: "discarded", draft_label: "Adaptive Bloom" }),
      }),
    );
    const first = renderCard();
    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      "Declined · Adaptive Bloom",
    );
    first.unmount();

    getDraftStanding.mockResolvedValue(
      answered("replaced", "Another version of this profile is active now."),
    );
    renderCard();
    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      "Replaced · Adaptive Bloom",
    );
    expect(screen.getByText("Another version of this profile is active now.")).toBeInTheDocument();
    expect(screen.queryByText(/\(v\d/)).not.toBeInTheDocument();
  });

  it("says what a rename did, from the name the call gave it", async () => {
    getDraftStanding.mockResolvedValue(
      answered("approved", "It goes to the machine at the next sync.", {
        draft: draft({ status: "approved", draft_label: "Gentle Bloom" }),
      }),
    );
    renderCard({ proposedAs: "Soft Bloom" });

    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      "✓ Approved as Gentle Bloom (proposed as Soft Bloom)",
    );
  });

  it("says it was renamed only when the proposed name differs after trimming, exactly", async () => {
    getDraftStanding.mockResolvedValue(answered("approved", "It goes at the next sync."));
    const same = renderCard({ proposedAs: "  Adaptive Bloom " });
    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      /^✓ Approved · Adaptive Bloom$/,
    );
    same.unmount();
    renderCard({ proposedAs: "adaptive bloom" });
    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      /^✓ Approved as Adaptive Bloom \(proposed as adaptive bloom\)$/,
    );
  });

  it("does not say it was renamed when the name is the one that was proposed", async () => {
    getDraftStanding.mockResolvedValue(answered("approved", "It goes at the next sync."));
    renderCard({ proposedAs: "Adaptive Bloom" });

    expect(await screen.findByTestId("proposal-title")).toHaveTextContent(
      /^✓ Approved · Adaptive Bloom$/,
    );
  });

  it("keeps the proposed curve behind a button, and loads nothing for a profile without one", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(
      answered("on_machine", null, { profile: proProfile(), selected: false }),
    );
    renderCard();

    const toggle = await screen.findByTestId("show-proposal-curve");
    expect(toggle).toHaveTextContent("Show the profile curve");
    expect(screen.queryByTestId("profile-curve")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(await screen.findByTestId("profile-curve")).toBeInTheDocument();
    expect(toggle).toHaveTextContent("Hide the profile curve");
    expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).not.toBeNull();
  });

  it("offers no curve for a profile that has none", async () => {
    getDraftStanding.mockResolvedValue(answered("on_machine", null, { profile: baseProfile() }));
    renderCard();

    await screen.findByTestId("proposal-title");
    expect(screen.queryByTestId("show-proposal-curve")).not.toBeInTheDocument();
  });
});

describe("while the standing is read", () => {
  it("shows the tool's summary until it lands, and says so when it cannot be read", async () => {
    getDraftStanding.mockReturnValue(new Promise(() => undefined));
    const first = renderCard({ summary: "Longer bloom, a gentler peak" });
    expect(screen.getByText("Longer bloom, a gentler peak")).toBeInTheDocument();
    first.unmount();

    getDraftStanding.mockRejectedValue(new Error("down"));
    renderCard();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This proposal could not be read right now.",
    );
    expect(
      within(screen.getByTestId("profile-proposal-unsettled")).queryByRole("button"),
    ).toBeNull();
  });
});

describe("a put that is refused", () => {
  it("shows the server's sentence for the person under the Name field, never the put's, with no toast", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    // The name was free when it was checked and taken before the put.
    checkDraftName.mockResolvedValueOnce({ label: "Soft Bloom", refused: null });
    let answerAgain: () => void = () => undefined;
    checkDraftName.mockImplementation(
      () =>
        new Promise((resolve) => {
          answerAgain = () =>
            resolve({
              label: "Soft Bloom",
              refused: "There is already a profile called Soft Bloom. Choose another name.",
            });
        }),
    );
    putOnBoard.mockRejectedValue(
      new Error("Soft Bloom is already a profile: draft a change from it. (request abc123)"),
    );
    renderCard();
    await screen.findByTestId("proposal-name");
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );

    await user.click(screen.getByTestId("approve-proposal"));

    // While the name is asked again, the put's own words are nowhere on screen.
    await waitFor(() => expect(checkDraftName).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/is already a profile: draft a change/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("proposal-error")).not.toBeInTheDocument();
    answerAgain();
    await waitFor(() =>
      expect(screen.getByTestId("proposal-name-problem")).toHaveTextContent(
        "There is already a profile called Soft Bloom. Choose another name.",
      ),
    );
    expect(screen.queryByText(/is already a profile: draft a change/)).not.toBeInTheDocument();
    expect(screen.queryByText(/request abc123/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("proposal-error")).not.toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
    expect(checkDraftName).toHaveBeenCalledTimes(2);

    const field = screen.getByTestId("proposal-name");
    await user.type(field, "x");
    await waitFor(() => expect(screen.getByTestId("proposal-name-problem")).toBeEmptyDOMElement());
  });

  it("puts a refusal that is not about the name under the buttons, once the name checks out", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    putOnBoard.mockRejectedValue(new Error("That profile version is already in the list."));
    renderCard();
    await screen.findByTestId("proposal-name");
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );

    await user.click(screen.getByTestId("approve-proposal"));

    expect(await screen.findByTestId("proposal-error")).toHaveTextContent(
      "That profile version is already in the list.",
    );
    expect(screen.getByTestId("proposal-name-problem")).toBeEmptyDOMElement();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("shows a refusal that carried no name under the buttons, once", async () => {
    const user = setupUser();
    putOnBoard.mockRejectedValue(new Error("That profile version is already in the list."));
    renderCard();

    await user.click(await screen.findByTestId("approve-proposal"));

    expect(await screen.findAllByText("That profile version is already in the list.")).toHaveLength(
      1,
    );
    expect(toast.error).not.toHaveBeenCalled();
  });
});

describe("a machine that cannot be synced with", () => {
  it.each([
    [
      { configured: false, connected: false },
      "No machine is configured. Set its address in Settings.",
    ],
    [
      { configured: true, connected: false },
      "The machine is not reachable. The archive still works; a sync cannot.",
    ],
  ])("says Approve, gives the top bar's words, and starts no sync (%o)", async (status, words) => {
    const user = setupUser();
    getDeviceStatus.mockResolvedValue({ host: "", identity: null, last_status: null, ...status });
    renderCard();

    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).toHaveTextContent(/^Approve$/),
    );
    expect(screen.getByTestId("proposal-no-sync")).toHaveTextContent(words);
    await user.click(screen.getByTestId("approve-proposal"));

    await waitFor(() => expect(putOnBoard).toHaveBeenCalledTimes(1));
    expect(runSync).not.toHaveBeenCalled();
  });
});

describe("after an approval", () => {
  it("puts focus on the answered card's heading, not on the page body", async () => {
    const user = setupUser();
    getDraftStanding
      .mockResolvedValueOnce(standing())
      .mockResolvedValue(answered("approved", "It goes to the machine at the next sync."));
    renderCard();

    await user.click(await screen.findByTestId("approve-proposal"));

    const title = await screen.findByTestId("proposal-title");
    await waitFor(() => expect(title).toHaveFocus());
  });

  it("says what a rename was in every answered state, not only in the approved one", async () => {
    const cases: [string, string | null, RegExp][] = [
      ["on_machine", null, /^✓ On the machine · Gentle Bloom \(proposed as Soft Bloom\)$/],
      [
        "not_on_machine",
        "The machine read it back differently.",
        /^✗ Not on the machine · Gentle Bloom \(proposed as Soft Bloom\)$/,
      ],
      [
        "replaced",
        "Another version is active.",
        /^Replaced · Gentle Bloom \(proposed as Soft Bloom\)$/,
      ],
    ];
    for (const [state, reason, title] of cases) {
      getDraftStanding.mockResolvedValue(
        answered(state, reason, {
          draft: draft({ status: "approved", draft_label: "Gentle Bloom" }),
          selected: false,
        }),
      );
      const view = renderCard({ proposedAs: "Soft Bloom" });
      expect(await screen.findByTestId("proposal-title")).toHaveTextContent(title);
      view.unmount();
    }
  });
});

describe("the name as it is checked", () => {
  /** A check the test settles itself, one deferred answer per request. */
  function deferredChecks() {
    const asked: { label: string; settle: (refused: string | null) => void }[] = [];
    checkDraftName.mockImplementation(
      (_id: number, label: string) =>
        new Promise((resolve) => {
          asked.push({ label, settle: (refused) => resolve({ label: label.trim(), refused }) });
        }),
    );
    return asked;
  }

  it("never offers Approve while the check of what is in the field is still out", async () => {
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    const asked = deferredChecks();
    renderCard();

    await screen.findByTestId("proposal-name");
    await waitFor(() => expect(asked).toHaveLength(1));
    await user.click(screen.getByTestId("approve-proposal"));

    expect(screen.getByTestId("approve-proposal")).toHaveAttribute("aria-disabled", "true");
    expect(putOnBoard).not.toHaveBeenCalled();
    asked[0]?.settle(null);
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
  });

  it("does not offer Approve for a name that is still resting, whatever was checked before", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    renderCard();
    const field = await screen.findByTestId("proposal-name");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );

    await user.type(field, "x");

    // Typed, not yet asked: the earlier "fine" belongs to another name.
    expect(screen.getByTestId("approve-proposal")).toHaveAttribute("aria-disabled", "true");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );
  });

  it("ignores a late answer for a name that is no longer in the field", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    const asked = deferredChecks();
    renderCard();
    const field = await screen.findByTestId("proposal-name");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() => expect(asked).toHaveLength(1));
    asked[0]?.settle(null);

    await user.clear(field);
    await user.type(field, "Taken");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() => expect(asked).toHaveLength(2));
    await user.clear(field);
    await user.type(field, "Fine");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() => expect(asked).toHaveLength(3));
    asked[2]?.settle(null);
    await waitFor(() =>
      expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true"),
    );

    // The answer for "Taken" arrives after the person has moved on to "Fine".
    asked[1]?.settle("There is already a profile called Taken. Choose another name.");
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByTestId("proposal-name-problem")).toBeEmptyDOMElement();
    expect(screen.getByTestId("approve-proposal")).not.toHaveAttribute("aria-disabled", "true");
    expect(screen.getByTestId("approve-proposal")).toHaveTextContent("add “Fine”");
  });

  it("does not ask again when the window regains focus", async () => {
    getDraftStanding.mockResolvedValue(brandNew());
    renderCard();
    await screen.findByTestId("proposal-name");
    await waitFor(() => expect(checkDraftName).toHaveBeenCalledTimes(1));

    await act(async () => {
      window.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    // An answer is kept for the name it was asked about (infinite stale time): nothing else
    // that happens to the page is a reason to ask the server again.
    expect(checkDraftName).toHaveBeenCalledTimes(1);
  });

  it("asks once when the card first shows, once per changed name, and never for a refresh of the drafts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = setupUser();
    getDraftStanding.mockResolvedValue(brandNew());
    const view = renderCard();
    const field = await screen.findByTestId("proposal-name");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() => expect(checkDraftName).toHaveBeenCalledTimes(1));

    // The drafts and the board are refreshed again and again (a sync, an answer elsewhere).
    for (let round = 0; round < 3; round += 1) {
      await act(async () => {
        await view.queryClient.invalidateQueries({ queryKey: ["drafts"] });
        await view.queryClient.invalidateQueries({ queryKey: ["board"] });
      });
    }
    expect(checkDraftName).toHaveBeenCalledTimes(1);

    await user.type(field, " Two");
    await act(() => vi.advanceTimersByTimeAsync(NAME_CHECK_DELAY_MS));
    await waitFor(() => expect(checkDraftName).toHaveBeenCalledTimes(2));
    expect(checkDraftName).toHaveBeenLastCalledWith(1, "Soft Bloom Two");
  });
});
