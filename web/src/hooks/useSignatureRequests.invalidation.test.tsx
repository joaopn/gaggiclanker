import type { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAnswerSignatureRequest } from "@/hooks/useSignatureRequests";
import { queryKeys } from "@/lib/queryKeys";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";
import { signatureRequest } from "@/test/signatureRequestFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { approveSignatureRequest, declineSignatureRequest } = vi.hoisted(() => ({
  approveSignatureRequest: vi.fn(),
  declineSignatureRequest: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  approveSignatureRequest,
  declineSignatureRequest,
}));

beforeEach(() => {
  vi.clearAllMocks();
  approveSignatureRequest.mockResolvedValue({ request: signatureRequest({ status: "approved" }) });
  declineSignatureRequest.mockResolvedValue({ request: signatureRequest({ status: "declined" }) });
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
 * An approval changes what every shot of the profile says, so it refreshes the request list, the
 * signature, every shot's fields and list rows, and the Recipe pages that brew the profile; it
 * does so when refused too (a 409 means the card must show what happened).
 */
describe("answering a signature request", () => {
  it("approve refreshes the requests, the signature, every shot and every Recipe", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useAnswerSignatureRequest());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ requestId: 4, answer: "approve", threadId: 14 });

    expect(approveSignatureRequest).toHaveBeenCalledWith(4);
    expect(keys).toContainEqual([...queryKeys.signatureRequests.all]);
    expect(keys).toContainEqual([...queryKeys.signatures.all]);
    expect(keys).toContainEqual([...queryKeys.shots.all]);
    expect(keys).toContainEqual([...queryKeys.recipes.all]);
    expect(keys).toContainEqual([...queryKeys.chat.thread("14")]);
  });

  it("decline sends the reason and refreshes the same", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useAnswerSignatureRequest());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({
      requestId: 4,
      answer: "decline",
      reason: "keep it",
      threadId: null,
    });

    expect(declineSignatureRequest).toHaveBeenCalledWith(4, "keep it");
    expect(keys).toContainEqual([...queryKeys.signatureRequests.all]);
    expect(keys).toContainEqual([...queryKeys.shots.all]);
  });

  it("refreshes after a refusal as well", async () => {
    approveSignatureRequest.mockRejectedValue(new Error("stale"));
    const { result, queryClient } = renderHookWithQueryClient(() => useAnswerSignatureRequest());
    const keys = spyOn(queryClient);

    await expect(
      result.current.mutateAsync({ requestId: 4, answer: "approve", threadId: 14 }),
    ).rejects.toThrow("stale");

    expect(keys).toContainEqual([...queryKeys.signatureRequests.all]);
    expect(keys).toContainEqual([...queryKeys.signatures.all]);
  });
});
