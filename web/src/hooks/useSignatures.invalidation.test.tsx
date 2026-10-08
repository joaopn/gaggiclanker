import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useRejectExpectation,
  useRejectOverride,
  useRestoreExpectation,
  useRestoreOverride,
  useSetExpectationTier,
} from "@/hooks/useSignatures";
import { queryKeys } from "@/lib/queryKeys";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";
import { overrideInForce, signatureInForce } from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  rejectExpectation,
  restoreExpectation,
  setExpectationTier,
  rejectSignatureOverride,
  restoreSignatureOverride,
} = vi.hoisted(() => ({
  rejectExpectation: vi.fn(),
  restoreExpectation: vi.fn(),
  setExpectationTier: vi.fn(),
  rejectSignatureOverride: vi.fn(),
  restoreSignatureOverride: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  rejectExpectation,
  restoreExpectation,
  setExpectationTier,
  rejectSignatureOverride,
  restoreSignatureOverride,
}));

const answer = { changed: [], signature: signatureInForce };
const overrideAnswer = { override: overrideInForce };

beforeEach(() => {
  vi.clearAllMocks();
  rejectExpectation.mockResolvedValue(answer);
  restoreExpectation.mockResolvedValue(answer);
  setExpectationTier.mockResolvedValue(answer);
  rejectSignatureOverride.mockResolvedValue(overrideAnswer);
  restoreSignatureOverride.mockResolvedValue(overrideAnswer);
});

function spyOn(queryClient: QueryClient): unknown[][] {
  const keys: unknown[][] = [];
  const original = queryClient.invalidateQueries.bind(queryClient);
  vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
    keys.push([...((filters?.queryKey ?? []) as readonly unknown[])]);
    return original(filters);
  });
  return keys;
}

/**
 * A signature is read when a shot is, so one answer moves every shot's checks (the shot page's
 * fields and every list row's badge), every Set that brews the profile, and the signature
 * itself. Each answer is pinned, because a missing key is a stale red badge on a shot whose
 * signature was just confirmed, and nothing else in a render test shows it.
 */
const ANSWERS = [
  ["rejecting one with a reason", () => useRejectExpectation(), { expectationId: 3, reason: "no" }],
  ["restoring a rejected one", () => useRestoreExpectation(), { expectationId: 3 }],
  [
    "moving one to another tier",
    () => useSetExpectationTier(),
    { expectationId: 3, tier: "context" },
  ],
  ["rejecting an override", () => useRejectOverride(), { setId: 1, overrideId: 2, reason: "" }],
  ["restoring an override", () => useRestoreOverride(), { setId: 1, overrideId: 2 }],
] as const;

describe("every answer about a signature invalidates what it touches", () => {
  it.each(ANSWERS)("%s", async (_name, hook, variables) => {
    // biome-ignore lint/suspicious/noExplicitAny: one table for five hooks with different variables.
    const { result, queryClient } = renderHookWithQueryClient(hook as () => any);
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(variables);

    await waitFor(() => expect(keys.length).toBe(3));
    expect(keys).toContainEqual(queryKeys.signatures.all);
    expect(keys).toContainEqual(queryKeys.shots.all);
    expect(keys).toContainEqual(queryKeys.sets.all);
  });

  it("also when the answer is refused, so the card shows the answer that won", async () => {
    rejectExpectation.mockRejectedValue(new Error("already rejected"));
    const { result, queryClient } = renderHookWithQueryClient(() => useRejectExpectation());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync({ expectationId: 3, reason: "" })).rejects.toThrow();

    await waitFor(() => expect(keys.length).toBe(3));
    expect(keys).toContainEqual(queryKeys.signatures.all);
  });

  it("sends each answer to its own route", async () => {
    const reject = renderHookWithQueryClient(() => useRejectExpectation());
    await reject.result.current.mutateAsync({ expectationId: 4, reason: "too tight" });
    expect(rejectExpectation).toHaveBeenCalledWith(4, "too tight");

    const restore = renderHookWithQueryClient(() => useRestoreExpectation());
    await restore.result.current.mutateAsync({ expectationId: 4 });
    expect(restoreExpectation).toHaveBeenCalledWith(4);

    const tier = renderHookWithQueryClient(() => useSetExpectationTier());
    await tier.result.current.mutateAsync({ expectationId: 4, tier: "important" });
    expect(setExpectationTier).toHaveBeenCalledWith(4, "important");

    const restoreOverride = renderHookWithQueryClient(() => useRestoreOverride());
    await restoreOverride.result.current.mutateAsync({ setId: 1, overrideId: 2 });
    expect(restoreSignatureOverride).toHaveBeenCalledWith(1, 2);
  });
});
