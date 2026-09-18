import { screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CleanupPlan, CleanupRunsData } from "@/api/types";
import { CleanupSection } from "@/components/sync/CleanupSection";
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
const WHY = "Older than the newest 20 shots the policy keeps on the machine; archived here intact.";

function plan(overrides: Partial<CleanupPlan> = {}): CleanupPlan {
  return {
    policy: {
      mode: "keep_newest",
      keep_newest: 20,
      min_free_kb: 2048,
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
        reason: WHY,
      },
      {
        shot_id: 12,
        device_id: "000102",
        started_at: "2026-02-01T09:00:00.000Z",
        raw_bytes: 4096,
        profile_name: "9 Bar",
        reason: WHY,
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

function renderReady() {
  return renderWithQueryClient(<CleanupSection identity={IDENTITY} configured connected />);
}

beforeEach(() => {
  vi.clearAllMocks();
  getCleanupPlan.mockResolvedValue(plan());
  getCleanupRuns.mockResolvedValue(runs());
  runCleanup.mockResolvedValue({ planned: 2, task: "cleanup" });
});

describe("CleanupSection", () => {
  it("says how much room is left, how many shots are on the machine and what would go", async () => {
    renderReady();

    // The policy badge is the first thing the plan query renders, so awaiting
    // it is what separates "the section is on screen" from "the server answered".
    expect(await screen.findByText("keep newest 20")).toBeInTheDocument();
    expect(screen.getByTestId("device-storage")).toHaveTextContent("Internal (SPIFFS)");
    const summary = screen.getByTestId("cleanup-summary");
    expect(summary).toHaveTextContent("23");
    expect(summary).toHaveTextContent("1.0 MB");
  });

  it("shows the plan without being asked: which shots, why each, and what is kept", async () => {
    renderReady();
    await screen.findByText("keep newest 20");

    const planned = screen.getByTestId("cleanup-planned");
    expect(planned).toHaveTextContent("000101");
    expect(planned).toHaveTextContent(WHY);
    expect(screen.getByTestId("cleanup-skipped")).toHaveTextContent("quarantined");
  });

  it("asks before it deletes anything, says it cannot be undone, and sends the ids it showed", async () => {
    const user = setupUser();
    renderReady();
    await screen.findByText("keep newest 20");

    await user.click(screen.getByRole("button", { name: /Delete 2 shots…/ }));
    const confirm = screen.getByTestId("cleanup-confirm");
    expect(confirm).toHaveTextContent("Delete 2 shots from the machine?");
    expect(confirm).toHaveTextContent("cannot be undone on the machine");
    expect(confirm).toHaveTextContent("The archive here keeps every one of them");
    expect(runCleanup).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "Delete 2 shots" }));
    await waitFor(() => expect(runCleanup).toHaveBeenCalledWith([11, 12]));
    expect(toast.success).toHaveBeenCalledWith("Cleaning up 2 shots on the machine");
  });

  it("cancelling the confirmation deletes nothing", async () => {
    const user = setupUser();
    renderReady();
    await screen.findByText("keep newest 20");

    await user.click(screen.getByRole("button", { name: /Delete 2 shots…/ }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByTestId("cleanup-confirm")).not.toBeInTheDocument();
    expect(runCleanup).not.toHaveBeenCalled();
  });

  it("re-reads the plan and says so when the server refuses one that moved", async () => {
    runCleanup.mockRejectedValue(new Error("The cleanup plan has changed since it was previewed"));
    const user = setupUser();
    renderReady();
    await screen.findByText("keep newest 20");

    await user.click(screen.getByRole("button", { name: /Delete 2 shots…/ }));
    await user.click(screen.getByRole("button", { name: "Delete 2 shots" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "The cleanup plan has changed since it was previewed",
      ),
    );
    await waitFor(() => expect(getCleanupPlan).toHaveBeenCalledTimes(2));
  });

  it("cannot run while device writes are off, and says where the switch is", async () => {
    getCleanupPlan.mockResolvedValue(plan({ policy: { ...plan().policy, writes_enabled: false } }));
    renderReady();
    await screen.findByTestId("cleanup-blocked");

    expect(screen.getByRole("button", { name: /Delete 2 shots…/ })).toBeDisabled();
    expect(screen.getByTestId("cleanup-blocked")).toHaveTextContent("Settings → Machine access");
  });

  it("cannot run while the machine is not connected", async () => {
    renderWithQueryClient(<CleanupSection identity={IDENTITY} configured connected={false} />);

    expect(await screen.findByTestId("cleanup-blocked")).toHaveTextContent("not connected");
    expect(screen.getByRole("button", { name: /Delete 2 shots…/ })).toBeDisabled();
  });

  it("shows both figures for a run that stopped early", async () => {
    // "planned 40, deleted 7, and here is the error" is a complete account of a
    // run that hit a machine mid-OTA; "7" is not.
    renderReady();

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
    renderReady();

    expect(await screen.findByText(/has not reported its free space/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete 0 shots…/ })).toBeDisabled();
  });
});
