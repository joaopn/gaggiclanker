import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { usePushDraft, useRollbackDraft } from "@/hooks/useDrafts";
import { queryKeys } from "@/lib/queryKeys";
import { draft } from "@/test/draftFixtures";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";
import { version } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { pushProfileDraft, rollbackProfileDraft } = vi.hoisted(() => ({
  pushProfileDraft: vi.fn(),
  rollbackProfileDraft: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  pushProfileDraft,
  rollbackProfileDraft,
}));

const pushed = draft({
  status: "pushed",
  pushed_device_profile_id: "aB3xYz90Pq",
  set_id: 3,
  set_name: "Guji on the Niche",
});

beforeEach(() => {
  vi.clearAllMocks();
  pushProfileDraft.mockResolvedValue({ draft: pushed, set_version: null });
  rollbackProfileDraft.mockResolvedValue(draft({ status: "discarded" }));
});

function spyOn(queryClient: QueryClient): readonly unknown[][] {
  const keys: unknown[][] = [];
  const original = queryClient.invalidateQueries.bind(queryClient);
  vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
    keys.push([...((filters?.queryKey ?? []) as readonly unknown[])]);
    return original(filters);
  });
  return keys;
}

describe("a push and a rollback reach the Sets they wrote", () => {
  it("a push refreshes the Sets, since a push for a Set records its next version", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => usePushDraft());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ id: 1, setId: 3 });

    await waitFor(() => expect(keys).toContainEqual([...queryKeys.sets.all]));
  });

  it("a rollback refreshes the Sets, since it clears the device id their versions name", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useRollbackDraft());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(1);

    await waitFor(() => expect(keys).toContainEqual([...queryKeys.sets.all]));
  });
});

describe("the push toast says what the push recorded", () => {
  it("names the version when the push recorded one", async () => {
    pushProfileDraft.mockResolvedValue({
      draft: pushed,
      set_version: version({ set_id: 3, version_no: 4 }),
    });
    const { result } = renderHookWithQueryClient(() => usePushDraft());

    await result.current.mutateAsync({ id: 1, setId: 3 });

    expect(toast.success).toHaveBeenCalledWith(
      "On the machine as aB3xYz90Pq, recorded as v4 of Guji on the Niche",
    );
  });

  it("says only where it is on the machine when nothing was recorded", async () => {
    const { result } = renderHookWithQueryClient(() => usePushDraft());

    await result.current.mutateAsync({ id: 1 });

    expect(toast.success).toHaveBeenCalledWith("On the machine as aB3xYz90Pq");
  });
});

describe("the toasts carry what the machine did, since the card leaves the default view", () => {
  it("a push says what it replaced", async () => {
    pushProfileDraft.mockResolvedValue({
      draft: draft({
        status: "pushed",
        pushed_device_profile_id: "nEw1234567",
        outcome: { action: "push", lines: ["Replaced aB3xYz90Pq: the previous copy is off."] },
      }),
      set_version: null,
    });
    const { result } = renderHookWithQueryClient(() => usePushDraft());

    await result.current.mutateAsync({ id: 1 });

    expect(toast.success).toHaveBeenCalledWith("On the machine as nEw1234567", {
      description: "Replaced aB3xYz90Pq: the previous copy is off.",
    });
  });

  it("a rollback that removed the profile says so", async () => {
    rollbackProfileDraft.mockResolvedValue(
      draft({
        status: "discarded",
        outcome: {
          action: "rollback",
          removed_device_profile_id: "aB3xYz90Pq",
          lines: ["Removed aB3xYz90Pq from the machine."],
        },
      }),
    );
    const { result } = renderHookWithQueryClient(() => useRollbackDraft());

    await result.current.mutateAsync(1);

    expect(toast.success).toHaveBeenCalledWith("Rolled back on the machine", {
      description: "Removed aB3xYz90Pq from the machine.",
    });
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it("a rollback that left the profile alone does not claim a rollback", async () => {
    rollbackProfileDraft.mockResolvedValue(
      draft({
        status: "discarded",
        outcome: {
          action: "rollback",
          removed_device_profile_id: null,
          kept_reason: "not created by this app",
          lines: ["Left aB3xYz90Pq on the machine: not created by this app."],
        },
      }),
    );
    const { result } = renderHookWithQueryClient(() => useRollbackDraft());

    await result.current.mutateAsync(1);

    expect(toast.warning).toHaveBeenCalledWith("The profile was left on the machine", {
      description: "Left aB3xYz90Pq on the machine: not created by this app.",
    });
    expect(toast.success).not.toHaveBeenCalled();
  });
});
