import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CleanupPlan, CleanupRunsData } from "@/api/types";
import { StorageCard } from "@/components/device/StorageCard";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getCleanupPlan, getCleanupRuns, runCleanup } = vi.hoisted(() => ({
  getCleanupPlan: vi.fn(),
  getCleanupRuns: vi.fn(),
  runCleanup: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getCleanupPlan,
  getCleanupRuns,
  runCleanup,
}));

const IDENTITY = { spiffsTotal: 4_194_304, spiffsUsed: 3_145_728, spiffsFree: 1_048_576 };

function plan(overrides: Partial<CleanupPlan> = {}): CleanupPlan {
  return {
    policy: {
      mode: "keep_newest",
      keep_newest: 20,
      min_free_kb: 2048,
      auto: false,
      writes_enabled: true,
    },
    on_device_count: 23,
    free_bytes: 1_048_576,
    free_source: "spiffs",
    planned: [
      {
        shot_id: 11,
        device_id: "000101",
        started_at: "2026-02-01T08:00:00.000Z",
        raw_bytes: 4096,
        profile_name: "9 Bar",
      },
      {
        shot_id: 12,
        device_id: "000102",
        started_at: "2026-02-01T09:00:00.000Z",
        raw_bytes: 4096,
        profile_name: "9 Bar",
      },
    ],
    skipped: [
      {
        shot_id: 13,
        device_id: "000103",
        reason: "Shot 000103 is quarantined: its bytes are stored but did not parse.",
      },
    ],
    blocked: null,
    ...overrides,
  };
}

function runs(): CleanupRunsData {
  return {
    items: [
      {
        id: 4,
        mode: "keep_newest",
        target: 20,
        trigger: "manual",
        status: "error",
        planned: 40,
        deleted: 7,
        errors: 1,
        error: "The machine did not answer req:history:delete within 15s",
        free_before: 900_000,
        free_after: 930_000,
        started_at: "2026-02-02T08:00:00.000Z",
        finished_at: "2026-02-02T08:00:30.000Z",
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  getCleanupPlan.mockResolvedValue(plan());
  getCleanupRuns.mockResolvedValue(runs());
  runCleanup.mockResolvedValue({ planned: 2, task: "cleanup" });
});

describe("StorageCard", () => {
  it("says how much room is left, how many shots are on the machine and what would go", async () => {
    renderWithQueryClient(<StorageCard identity={IDENTITY} writesEnabled />);

    // The policy badge is the first thing the plan query renders, so awaiting
    // it is what separates "the card is on screen" from "the server answered".
    expect(await screen.findByText("keep newest 20")).toBeInTheDocument();
    expect(screen.getByTestId("device-storage")).toHaveTextContent("Internal (SPIFFS)");
    const summary = screen.getByTestId("cleanup-summary");
    expect(summary).toHaveTextContent("23");
    expect(summary).toHaveTextContent("1.0 MB");
  });

  it("names the shots it would keep, and why", async () => {
    // The half of the preview people actually need: "why is that shot still on
    // my machine" is unanswerable without it.
    const user = setupUser();
    renderWithQueryClient(<StorageCard identity={IDENTITY} writesEnabled />);
    await screen.findByText("keep newest 20");

    await user.click(screen.getByRole("button", { name: "Preview cleanup" }));

    expect(screen.getByTestId("cleanup-planned")).toHaveTextContent("000101");
    expect(screen.getByTestId("cleanup-skipped")).toHaveTextContent("quarantined");
  });

  it("asks before it deletes anything, and says what it queued", async () => {
    const user = setupUser();
    renderWithQueryClient(<StorageCard identity={IDENTITY} writesEnabled />);
    await screen.findByText("keep newest 20");

    await user.click(screen.getByRole("button", { name: /Run cleanup/ }));
    expect(await screen.findByText("Delete 2 shots from the machine?")).toBeInTheDocument();
    expect(runCleanup).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Delete them" }));
    await waitFor(() => expect(runCleanup).toHaveBeenCalledTimes(1));
    expect(toast.success).toHaveBeenCalledWith("Cleaning up 2 shots on the machine");
  });

  it("cannot run while device writes are off, and says where the switch is", async () => {
    renderWithQueryClient(<StorageCard identity={IDENTITY} writesEnabled={false} />);
    await screen.findByTestId("cleanup-writes-off");

    expect(screen.getByRole("button", { name: /Run cleanup/ })).toBeDisabled();
    expect(screen.getByTestId("cleanup-writes-off")).toHaveTextContent("Settings → Machine");
  });

  it("shows both figures for a run that stopped early", async () => {
    // "planned 40, deleted 7, and here is the error" is a complete account of a
    // run that hit a machine mid-OTA; "7" is not.
    renderWithQueryClient(<StorageCard identity={IDENTITY} writesEnabled />);

    const table = await screen.findByTestId("cleanup-runs");
    expect(table).toHaveTextContent("7 / 40");
    expect(table).toHaveTextContent("error");
  });

  it("says when a free-space policy has no figures to act on", async () => {
    getCleanupPlan.mockResolvedValue(
      plan({
        free_bytes: null,
        free_source: null,
        planned: [],
        blocked: "The machine has not reported its free space.",
      }),
    );
    renderWithQueryClient(<StorageCard identity={IDENTITY} writesEnabled />);

    expect(await screen.findByText(/has not reported its free space/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run cleanup/ })).toBeDisabled();
  });
});
