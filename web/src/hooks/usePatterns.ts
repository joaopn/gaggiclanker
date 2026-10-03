import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  approvePatternProposal,
  dismissPatternProposal,
  getPatterns,
  startPatternRun,
} from "@/api/client";
import type { PatternProposalDecision, PatternRun, PatternsData } from "@/api/types";
import { invalidateKnowledge } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/** How often to re-read while a run is going, in case the event stream dropped a frame. */
const RUNNING_POLL_MS = 3000;

/**
 * The Find patterns section's one read: the newest run, its proposals and the counts.
 *
 * `patterns.started` / `finished` / `failed` on the event stream re-read it (see
 * `EVENT_INVALIDATIONS`), and while a run is `running` it also polls: the bus is lossy, and a
 * button that stays "finding patterns" after the run ended is worse than three seconds of
 * traffic.
 */
export function usePatterns(): UseQueryResult<PatternsData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.patterns(),
    queryFn: () => getPatterns(),
    refetchInterval: (query) =>
      query.state.data?.run?.status === "running" ? RUNNING_POLL_MS : false,
  });
}

/**
 * Press Find patterns across Sets: the one way a run starts.
 *
 * **The mutation resolves when the work is queued**, with the `running` row; the outcome
 * arrives on the event stream and the section follows the row. **A failed run resolves, it
 * does not reject**: the server answers 2xx with a `failed` row carrying the error, which the
 * section renders in words. Only the pattern queries are refreshed: a run writes no insight.
 */
export function useStartPatternRun(): UseMutationResult<PatternRun, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => startPatternRun(),
    onSuccess: (run) => {
      if (run.status === "running") toast.info("Finding patterns — this takes a minute or so");
      else if (run.status !== "done") toast.error(run.error ?? "The run did not complete");
    },
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.knowledge.patterns() });
    },
  });
}

/**
 * Approve or dismiss a proposed general insight: the card's two buttons.
 *
 * Approving writes a general insight and deletes the Set insights it came from, so it touches
 * the whole `knowledge` prefix: the Knowledge list, every source Set's insights (which the
 * Set pages read), the insight cards in the chat and the run. Dismissing changes only the
 * proposal, but the one breadth keeps the two from drifting apart.
 */
export function useAnswerPatternProposal(): UseMutationResult<
  PatternProposalDecision,
  Error,
  { id: number; answer: "approve" | "dismiss" }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, answer }) =>
      answer === "approve" ? approvePatternProposal(id) : dismissPatternProposal(id),
    onSuccess: (decision, variables) =>
      toast.success(
        variables.answer === "approve"
          ? (decision.skipped ?? []).length > 0
            ? "Approved — some Set insights were left in place"
            : "Approved — the general insight replaces the Set insights it came from"
          : "Dismissed — the next run is told you declined it",
      ),
    onError: (error) => toast.error(error.message),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
    },
  });
}
