import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PendingNotesData } from "@/api/types";
import { NotesWritebackCard } from "@/components/device/NotesWritebackCard";
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
    enabled: true,
    writes_enabled: true,
    fields: ["rating", "balance", "doseIn", "doseOut", "grindSetting"],
    shot_ids: [3, 4, 5],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  getPendingNotes.mockResolvedValue(pending());
  pushPendingNotes.mockResolvedValue({ machine_id: 1, pending: 3 });
});

describe("NotesWritebackCard", () => {
  it("counts the backlog and pushes it", async () => {
    const user = setupUser();
    renderWithQueryClient(<NotesWritebackCard />);

    expect(await screen.findByTestId("notes-writeback")).toHaveTextContent(
      "3 judgements the machine does not have yet",
    );
    await user.click(screen.getByRole("button", { name: /Push pending notes/ }));

    await waitFor(() => expect(pushPendingNotes).toHaveBeenCalledTimes(1));
    expect(toast.success).toHaveBeenCalledWith("Pushing 3 judgements to the machine");
  });

  it("names the switch that is in the way rather than just disabling the button", async () => {
    // Two switches gate this, and "nothing happens when I press it" is the
    // failure it is most likely to produce.
    getPendingNotes.mockResolvedValue(pending({ enabled: false, writes_enabled: true }));
    renderWithQueryClient(<NotesWritebackCard />);

    expect(await screen.findByText(/Notes writeback enabled/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Push pending notes/ })).toBeDisabled();
    expect(screen.getByText("write-back off")).toBeInTheDocument();
  });

  it("points at the master switch first when that is the one that is off", async () => {
    getPendingNotes.mockResolvedValue(pending({ enabled: true, writes_enabled: false }));
    renderWithQueryClient(<NotesWritebackCard />);

    expect(await screen.findByText(/Device writes are off/)).toBeInTheDocument();
  });

  it("says so plainly when there is nothing to send", async () => {
    getPendingNotes.mockResolvedValue(pending({ shot_ids: [] }));
    renderWithQueryClient(<NotesWritebackCard />);

    expect(await screen.findByTestId("notes-writeback")).toHaveTextContent(
      "Every judgement here is already on the machine.",
    );
    expect(screen.getByRole("button", { name: /Push pending notes/ })).toBeDisabled();
  });
});
