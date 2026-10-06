import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useConfirmAllExpectations,
  useConfirmExpectation,
  useConfirmOverride,
  useRejectExpectation,
  useRejectOverride,
  useSetExpectationTier,
  useWithdrawOverride,
} from "@/hooks/useSignatures";
import { queryKeys } from "@/lib/queryKeys";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";
import { overrideConfirmed, signatureConfirmed } from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  confirmExpectation,
  rejectExpectation,
  setExpectationTier,
  confirmAllExpectations,
  confirmSignatureOverride,
  rejectSignatureOverride,
  withdrawSignatureOverride,
} = vi.hoisted(() => ({
  confirmExpectation: vi.fn(),
  rejectExpectation: vi.fn(),
  setExpectationTier: vi.fn(),
  confirmAllExpectations: vi.fn(),
  confirmSignatureOverride: vi.fn(),
  rejectSignatureOverride: vi.fn(),
  withdrawSignatureOverride: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  confirmExpectation,
  rejectExpectation,
  setExpectationTier,
  confirmAllExpectations,
  confirmSignatureOverride,
  rejectSignatureOverride,
  withdrawSignatureOverride,
}));

const answer = { changed: [], signature: signatureConfirmed };
const overrideAnswer = { override: overrideConfirmed };

beforeEach(() => {
  vi.clearAllMocks();
  confirmExpectation.mockResolvedValue(answer);
  rejectExpectation.mockResolvedValue(answer);
  setExpectationTier.mockResolvedValue(answer);
  confirmAllExpectations.mockResolvedValue(answer);
  confirmSignatureOverride.mockResolvedValue(overrideAnswer);
  rejectSignatureOverride.mockResolvedValue(overrideAnswer);
  withdrawSignatureOverride.mockResolvedValue(overrideAnswer);
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
  ["confirming one expectation", () => useConfirmExpectation(), { expectationId: 3 }],
  ["rejecting one with a reason", () => useRejectExpectation(), { expectationId: 3, reason: "no" }],
  [
    "moving one to another tier",
    () => useSetExpectationTier(),
    { expectationId: 3, tier: "context" },
  ],
  ["confirming every waiting one", () => useConfirmAllExpectations(), { versionId: 1 }],
  ["confirming a Set version's override", () => useConfirmOverride(), { setId: 1, overrideId: 2 }],
  ["rejecting an override", () => useRejectOverride(), { setId: 1, overrideId: 2, reason: "" }],
  ["withdrawing an override", () => useWithdrawOverride(), { setId: 1, overrideId: 2 }],
] as const;

describe("every answer about a signature invalidates what it touches", () => {
  it.each(ANSWERS)("%s", async (_name, hook, variables) => {
    // biome-ignore lint/suspicious/noExplicitAny: one table for seven hooks with different variables.
    const { result, queryClient } = renderHookWithQueryClient(hook as () => any);
    const keys = spyOn(queryClient);

    await result.current.mutateAsync(variables);

    await waitFor(() => expect(keys.length).toBe(3));
    expect(keys).toContainEqual(queryKeys.signatures.all);
    expect(keys).toContainEqual(queryKeys.shots.all);
    expect(keys).toContainEqual(queryKeys.sets.all);
  });

  it("also when the answer is refused, so the card shows the answer that won", async () => {
    confirmExpectation.mockRejectedValue(new Error("already answered"));
    const { result, queryClient } = renderHookWithQueryClient(() => useConfirmExpectation());
    const keys = spyOn(queryClient);

    await expect(result.current.mutateAsync({ expectationId: 3 })).rejects.toThrow();

    await waitFor(() => expect(keys.length).toBe(3));
    expect(keys).toContainEqual(queryKeys.signatures.all);
  });

  it("sends each answer to its own route, and Confirm all as one call for the version", async () => {
    const all = renderHookWithQueryClient(() => useConfirmAllExpectations());
    await all.result.current.mutateAsync({ versionId: 7 });
    expect(confirmAllExpectations).toHaveBeenCalledTimes(1);
    expect(confirmAllExpectations).toHaveBeenCalledWith(7);
    expect(confirmExpectation).not.toHaveBeenCalled();

    const reject = renderHookWithQueryClient(() => useRejectExpectation());
    await reject.result.current.mutateAsync({ expectationId: 4, reason: "too tight" });
    expect(rejectExpectation).toHaveBeenCalledWith(4, "too tight");

    const tier = renderHookWithQueryClient(() => useSetExpectationTier());
    await tier.result.current.mutateAsync({ expectationId: 4, tier: "important" });
    expect(setExpectationTier).toHaveBeenCalledWith(4, "important");
  });
});
