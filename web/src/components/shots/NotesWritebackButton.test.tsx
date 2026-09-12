import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShotJudgement } from "@/api/types";
import { NotesWritebackButton } from "@/components/shots/NotesWritebackButton";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { writeBackNotes } = vi.hoisted(() => ({ writeBackNotes: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  writeBackNotes,
}));

function judgement(overrides: Partial<ShotJudgement> = {}): ShotJudgement {
  return {
    shot_id: 7,
    rating: 4,
    balance: "balanced",
    taste_tags: [],
    dose_in_g: 18,
    dose_out_g: 36,
    grind_setting: "2.4",
    notes: "",
    decision: null,
    seeded_from_device_note: false,
    device_synced_at: null,
    updated_at: "2026-03-01T09:00:00.000Z",
    ratio: 2,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  writeBackNotes.mockResolvedValue({ shot_id: 7, device_id: "000129", written: true });
});

describe("NotesWritebackButton", () => {
  it("sends the verdict and names the shot the machine now holds", async () => {
    const user = setupUser();
    renderWithQueryClient(<NotesWritebackButton shotId={7} judgement={judgement()} />);

    await user.click(screen.getByRole("button", { name: /Sync notes to machine/ }));

    await waitFor(() => expect(writeBackNotes).toHaveBeenCalledWith("7"));
    expect(toast.success).toHaveBeenCalledWith("Sent to the machine's notes for shot 000129");
  });

  it("renders a refusal as information, not as an error", async () => {
    // The server answers 200 with a sentence for every rule that says no, and
    // an error toast would claim the request was wrong when it was not.
    writeBackNotes.mockResolvedValue({
      shot_id: 7,
      device_id: "000129",
      written: false,
      reason: "This verdict came from the machine's own notes card.",
      device_error: false,
    });
    const user = setupUser();
    renderWithQueryClient(<NotesWritebackButton shotId={7} judgement={judgement()} />);

    await user.click(screen.getByRole("button", { name: /Sync notes to machine/ }));

    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(
        "This verdict came from the machine's own notes card.",
      ),
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("is absent when there is no verdict to send", () => {
    renderWithQueryClient(<NotesWritebackButton shotId={7} judgement={null} />);
    expect(screen.queryByRole("button", { name: /Sync notes/ })).not.toBeInTheDocument();
  });
});
