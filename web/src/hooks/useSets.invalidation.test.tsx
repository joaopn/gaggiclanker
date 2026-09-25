import type { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useDecideProposal,
  useRollbackSet,
  useSetVersionOutcome,
  useSetVersionPrediction,
  useStartDesign,
} from "@/hooks/useSets";
import { queryKeys } from "@/lib/queryKeys";
import { renderHookWithQueryClient } from "@/test/renderWithQueryClient";
import { designProposal, proposal, setRow, version } from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const {
  setVersionPrediction,
  setVersionOutcome,
  clearVersionOutcome,
  rollbackSet,
  acceptSetProposal,
  declineSetProposal,
  designSet,
} = vi.hoisted(() => ({
  setVersionPrediction: vi.fn(),
  setVersionOutcome: vi.fn(),
  clearVersionOutcome: vi.fn(),
  rollbackSet: vi.fn(),
  acceptSetProposal: vi.fn(),
  declineSetProposal: vi.fn(),
  designSet: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  setVersionPrediction,
  setVersionOutcome,
  clearVersionOutcome,
  rollbackSet,
  acceptSetProposal,
  declineSetProposal,
  designSet,
}));

beforeEach(() => {
  vi.clearAllMocks();
  setVersionPrediction.mockResolvedValue(version());
  setVersionOutcome.mockResolvedValue(version());
  clearVersionOutcome.mockResolvedValue(version());
  rollbackSet.mockResolvedValue(version({ version_no: 3, restores_version_no: 1 }));
  acceptSetProposal.mockResolvedValue({ proposal: proposal(), version: version() });
  declineSetProposal.mockResolvedValue({
    proposal: proposal({ status: "declined" }),
    version: null,
  });
  designSet.mockResolvedValue({
    set: setRow({ id: 6, designing: true }),
    version: version({ id: 60, set_id: 6 }),
    thread_id: 12,
  });
});

/**
 * What each Set-side write invalidates, and — just as much — what it does not.
 *
 * `invalidateQueries` is spied rather than the cache inspected: what is under
 * test is the *breadth* of each call, and a key that is one level too wide
 * refetches every open shots list on a page that only needed a badge redrawn.
 * That is invisible in a passing render test and obvious here.
 */
function spyOn(queryClient: QueryClient): readonly unknown[][] {
  const keys: unknown[][] = [];
  const original = queryClient.invalidateQueries.bind(queryClient);
  vi.spyOn(queryClient, "invalidateQueries").mockImplementation((filters) => {
    keys.push([...((filters?.queryKey ?? []) as readonly unknown[])]);
    return original(filters);
  });
  return keys;
}

describe("the Set-side writes invalidate no more than they changed", () => {
  it("an outcome touches the Set detail alone", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useSetVersionOutcome());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({
      setId: 3,
      versionId: 22,
      body: { outcome: "held", note: "" },
    });

    await waitFor(() => expect(keys.length).toBeGreaterThan(0));
    expect(keys).toEqual([queryKeys.sets.detail("3")]);
    // Nothing about a shot changes when a version is graded.
    expect(keys.flat()).not.toContain("shots");
  });

  it("clearing an outcome is the same write and the same breadth", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useSetVersionOutcome());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ setId: 3, versionId: 22, body: null });

    await waitFor(() => expect(keys.length).toBeGreaterThan(0));
    expect(keys).toEqual([queryKeys.sets.detail("3")]);
  });

  it("a prediction touches the Set detail and the shot details, not the lists", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useSetVersionPrediction());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({
      setId: 3,
      versionId: 22,
      body: { prediction: "less bitter" },
    });

    await waitFor(() => expect(keys.length).toBe(2));
    expect(keys).toContainEqual(queryKeys.sets.detail("3"));
    expect(keys).toContainEqual(["shots", "detail"]);
    // Not `["shots"]`: that would refetch every open list and its filters.
    expect(keys).not.toContainEqual(queryKeys.shots.all);
  });

  it("accepting a proposal sweeps the Sets, because a version appeared", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useDecideProposal());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ setId: 3, proposalId: 5, decision: "accept" });

    await waitFor(() => expect(keys.length).toBe(1));
    expect(keys).toEqual([queryKeys.sets.all]);
  });

  it("declining touches the Set detail and the proposals, and no shot", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useDecideProposal());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({ setId: 3, proposalId: 5, decision: "decline", note: "" });

    await waitFor(() => expect(keys.length).toBe(2));
    // No version, no shot, no chart: one row changed. Sweeping `sets` would
    // refetch every open Set page's five hundred shots to record a sentence.
    expect(keys).toContainEqual(queryKeys.sets.detail("3"));
    expect(keys).toContainEqual(queryKeys.sets.proposals("3"));
    expect(keys).not.toContainEqual(queryKeys.sets.all);
  });

  it("accepting a first recipe reaches the Set, the list, its proposals and its conversation", async () => {
    acceptSetProposal.mockResolvedValue({
      proposal: designProposal({ status: "accepted" }),
      version: version({ id: 60, set_id: 6 }),
    });
    const { result, queryClient } = renderHookWithQueryClient(() => useDecideProposal());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({
      setId: 6,
      proposalId: 8,
      decision: "accept",
      kind: "design",
      threadId: 12,
    });

    await waitFor(() => expect(keys.length).toBe(4));
    expect(keys).toContainEqual(queryKeys.sets.detail("6"));
    // The list carries the Designing badge, and the Chat page reads it to know
    // which tools the conversation has: both change when the design ends.
    expect(keys).toContainEqual(["sets", "list"]);
    expect(keys).toContainEqual(queryKeys.sets.proposals("6"));
    expect(keys).toContainEqual(queryKeys.chat.thread("12"));
    // Version 1 is filled in place and no shot moved: no chart, no shots.
    expect(keys).not.toContainEqual(queryKeys.sets.all);
    expect(keys.flat()).not.toContain("shots");
  });

  it("declining a first recipe reaches the same, and the draft queue it discarded from", async () => {
    declineSetProposal.mockResolvedValue({
      proposal: designProposal({ status: "declined" }),
      version: null,
    });
    const { result, queryClient } = renderHookWithQueryClient(() => useDecideProposal());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({
      setId: 6,
      proposalId: 8,
      decision: "decline",
      note: "",
      kind: "design",
      threadId: 12,
    });

    await waitFor(() => expect(keys.length).toBe(5));
    expect(keys).toContainEqual(queryKeys.sets.detail("6"));
    expect(keys).toContainEqual(["sets", "list"]);
    expect(keys).toContainEqual(queryKeys.sets.proposals("6"));
    expect(keys).toContainEqual(queryKeys.chat.thread("12"));
    expect(keys).toContainEqual(queryKeys.drafts.all);
    expect(keys).not.toContainEqual(queryKeys.sets.all);
  });

  it("starting a design reaches the Sets list and the conversations, and nothing else", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useStartDesign());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({
      bean_id: 1,
      grinder_id: 1,
      name: null,
      fork_profile_version_id: null,
      usual_grind: "",
      goal: "",
    });

    await waitFor(() => expect(keys.length).toBe(2));
    // A card on the Sets page and a folder with a thread on the Chat page. No
    // recipe exists yet, so no Set page, chart or shot has anything new.
    expect(keys).toContainEqual(["sets", "list"]);
    expect(keys).toContainEqual(queryKeys.chat.threads());
  });

  it("a roll back sweeps the Sets, and nothing else", async () => {
    const { result, queryClient } = renderHookWithQueryClient(() => useRollbackSet());
    const keys = spyOn(queryClient);

    await result.current.mutateAsync({
      setId: 3,
      body: { to_version_id: 21, intent: "", prediction: "" },
    });

    await waitFor(() => expect(keys.length).toBe(1));
    // The whole `sets` prefix: the list's current-version row and the trend
    // chart's version boundaries both moved. No shot did — the version a roll
    // back appends has none — so no shot query is touched at all.
    expect(keys).toEqual([queryKeys.sets.all]);
  });
});
