import { screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PendingNotesData } from "@/api/types";
import { NotesSection } from "@/components/sync/NotesSection";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getPendingNotes, pushPendingNotes } = vi.hoisted(() => ({
  getPendingNotes: vi.fn(),
  pushPendingNotes: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getPendingNotes,
  pushPendingNotes,
}));

function pending(overrides: Partial<PendingNotesData> = {}): PendingNotesData {
  return {
    writes_enabled: true,
    fields: ["rating", "balance", "doseIn", "doseOut", "grindSetting"],
    items: [3, 4, 5].map((shotId) => ({
      shot_id: shotId,
      device_id: `00010${shotId}`,
      started_at: "2026-02-01T08:00:00.000Z",
      profile_name: "9 Bar",
      rating: 4,
      balance: null,
      notes: shotId === 4 ? "long finish" : "",
      updated_at: "2026-02-02T08:00:00.000Z",
    })),
    ...overrides,
  };
}

function renderReady() {
  return renderWithQueryClient(<NotesSection configured connected />);
}

beforeEach(() => {
  vi.clearAllMocks();
  getPendingNotes.mockResolvedValue(pending());
  pushPendingNotes.mockResolvedValue({ pending: 2 });
});

describe("NotesSection", () => {
  it("lists what the machine does not have yet, with nothing selected", async () => {
    renderReady();

    const list = await screen.findByTestId("notes-pending-list");
    expect(within(list).getAllByRole("row")).toHaveLength(3);
    expect(list).toHaveTextContent("000104");
    expect(list).toHaveTextContent("long finish");
    expect(screen.getByTestId("notes-pending")).toHaveTextContent(
      "3 judgements the machine does not have yet",
    );
    // Nothing leaves unless somebody ticks it: a pre-selected list would be the
    // automatic send again with one more click in front of it.
    expect(screen.getByRole("button", { name: /Send 0 to the machine/ })).toBeDisabled();
  });

  it("sends exactly the selected shots, and only after the confirmation", async () => {
    const user = setupUser();
    renderReady();
    await screen.findByTestId("notes-pending-list");

    await user.click(screen.getByRole("checkbox", { name: "Select shot 000105" }));
    await user.click(screen.getByRole("checkbox", { name: "Select shot 000103" }));
    await user.click(screen.getByRole("button", { name: /Send 2 to the machine/ }));

    const confirm = screen.getByTestId("notes-confirm");
    expect(confirm).toHaveTextContent("Send 2 judgements to the machine?");
    expect(pushPendingNotes).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "Send 2" }));

    await waitFor(() => expect(pushPendingNotes).toHaveBeenCalledWith([3, 5]));
    expect(toast.success).toHaveBeenCalledWith("Sending 2 judgements to the machine");
  });

  it("cancelling the confirmation sends nothing", async () => {
    const user = setupUser();
    renderReady();
    await screen.findByTestId("notes-pending-list");

    await user.click(screen.getByRole("checkbox", { name: "Select every pending judgement" }));
    await user.click(screen.getByRole("button", { name: /Send 3 to the machine/ }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByTestId("notes-confirm")).not.toBeInTheDocument();
    expect(pushPendingNotes).not.toHaveBeenCalled();
  });

  it("says why the list moved when the server refuses a stale selection", async () => {
    pushPendingNotes.mockRejectedValue(
      new Error("Some of the selected judgements are no longer waiting"),
    );
    const user = setupUser();
    renderReady();
    await screen.findByTestId("notes-pending-list");

    await user.click(screen.getByRole("checkbox", { name: "Select shot 000104" }));
    await user.click(screen.getByRole("button", { name: /Send 1 to the machine/ }));
    await user.click(screen.getByRole("button", { name: "Send 1" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Some of the selected judgements are no longer waiting",
      ),
    );
    // The list is re-read so the person sees what is actually pending now.
    await waitFor(() => expect(getPendingNotes).toHaveBeenCalledTimes(2));
  });

  it("names the switch that is in the way rather than just disabling the button", async () => {
    getPendingNotes.mockResolvedValue(pending({ writes_enabled: false }));
    const user = setupUser();
    renderReady();
    await screen.findByTestId("notes-pending-list");

    await user.click(screen.getByRole("checkbox", { name: "Select shot 000104" }));

    expect(screen.getByTestId("notes-blocked")).toHaveTextContent("Device writes enabled");
    expect(screen.getByRole("button", { name: /Send 1 to the machine/ })).toBeDisabled();
  });

  it("says the machine is not connected when that is what is in the way", async () => {
    renderWithQueryClient(<NotesSection configured connected={false} />);

    expect(await screen.findByTestId("notes-blocked")).toHaveTextContent("not connected");
  });

  it("says so plainly when there is nothing to send", async () => {
    getPendingNotes.mockResolvedValue(pending({ items: [] }));
    renderReady();

    expect(await screen.findByTestId("notes-pending")).toHaveTextContent(
      "Every judgement here is already on the machine.",
    );
    expect(screen.queryByTestId("notes-pending-list")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send 0 to the machine/ })).toBeDisabled();
  });
});
