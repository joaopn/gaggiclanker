import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  acceptSetProposal,
  addSetVersion,
  archiveSet,
  clearVersionOutcome,
  createSet,
  declineSetProposal,
  deleteJudgement,
  getSet,
  getSetProposals,
  getSets,
  getSetTrends,
  getShot,
  matchShotsByProfile,
  putJudgement,
  putShotSetVersion,
  rollbackSet,
  setAutomatch,
  setVersionOutcome,
  setVersionPrediction,
} from "@/api/client";
import type {
  JudgementWrite,
  ProfileMatchSummary,
  RollbackWrite,
  SetCreate,
  SetDetailData,
  SetListData,
  SetProposal,
  SetProposalDecision,
  SetProposalListData,
  SetRow,
  SetTrends,
  SetVersionPatch,
  SetVersionRow,
  ShotDetailRow,
  ShotJudgement,
  VersionOutcomeWrite,
  VersionPredictionWrite,
} from "@/api/types";
import {
  invalidateChatThread,
  invalidateDrafts,
  invalidateSetDetail,
  invalidateSetList,
  invalidateSetProposals,
  invalidateSets,
  invalidateShotDetails,
  invalidateShots,
} from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Sets, their versions, the trend chart, and the two things a person writes
 * about a shot: the verdict and which Set it belongs to.
 *
 * Every mutation here invalidates both `sets` and `shots`, and that is not
 * laziness. Saving a judgement changes the shot row (`has_judgement`), the Set
 * page's own copy of that row, and the trend chart's averages; assigning a shot
 * changes its badge in the list and moves a point between two versions on the
 * chart. Naming three keys at each call site is how one of them gets forgotten,
 * and the symptom — "the page did not update" — surfaces long after the commit.
 */

export function useSets(includeArchived = false): UseQueryResult<SetListData, Error> {
  return useQuery({
    queryKey: queryKeys.sets.list(includeArchived),
    queryFn: () => getSets(includeArchived),
  });
}

export function useSet(id: number | undefined): UseQueryResult<SetDetailData, Error> {
  return useQuery({
    queryKey: queryKeys.sets.detail(String(id)),
    queryFn: () => getSet(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

export function useSetTrends(id: number | undefined): UseQueryResult<SetTrends, Error> {
  return useQuery({
    queryKey: queryKeys.sets.trends(String(id)),
    queryFn: () => getSetTrends(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

/**
 * The changes an agent has proposed for a Set.
 *
 * Read on the Set page's behalf by the page itself (the detail carries the
 * waiting one) and, on its own, by a proposal card inside a conversation: that
 * card has to say whether the person has answered yet, and it is often open
 * when the Set page is not.
 */
export function useSetProposals(
  id: number | undefined,
): UseQueryResult<SetProposalListData, Error> {
  return useQuery({
    queryKey: queryKeys.sets.proposals(String(id)),
    queryFn: () => getSetProposals(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

/**
 * Accept or decline one. The two answers to the same question, so one hook.
 *
 * They invalidate different things, because they change different things.
 * **Accepting** appends a version: the Sets list's version count moves, the
 * trend chart gains a boundary and the Set page's whole log changes, so the
 * `sets` prefix goes. **Declining** changes one row — no version, no shot, no
 * chart — so it reaches the Set detail (which carries the waiting proposal)
 * and the proposals list, and nothing else. A decline that swept the prefix
 * would refetch every open Set page's five hundred shots to record a sentence.
 *
 * **A Set's first recipe** (`kind: "design"`) is answered the same two ways and
 * reaches further in one direction and less far in another. It adds no version
 * — accepting fills version 1 in place — and no shot moves, so the trend chart
 * is left alone; but it ends the design, which takes the badge off the Sets
 * list and changes the tools the conversation it came from is offered, so the
 * list and that conversation are read again. Declining it discards the profile
 * draft it carried, so the draft queue is read again too.
 *
 * The failure is the interesting half: the server answers a stale proposal and
 * an ungraded prediction with sentences the person can act on, so the toast
 * carries the server's words rather than "Could not accept".
 */
export function useDecideProposal(): UseMutationResult<
  SetProposalDecision,
  Error,
  {
    setId: number;
    proposalId: number;
    decision: "accept" | "decline";
    note?: string;
    /** Which kind of proposal is being answered. A change unless said otherwise. */
    kind?: SetProposal["kind"];
    /** The conversation it was argued in, when there is one. */
    threadId?: number | null;
  }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, proposalId, decision, note }) =>
      decision === "accept"
        ? acceptSetProposal(setId, proposalId)
        : declineSetProposal(setId, proposalId, { note: note ?? "" }),
    onSuccess: (result) =>
      toast.success(
        result.proposal.kind === "design"
          ? result.version
            ? "Version 1 is set. Its profile is a draft on the Profiles page, waiting for you to approve and push it."
            : "First recipe declined, and its draft discarded"
          : result.version
            ? `Version ${result.version.version_no} recorded. Nothing was sent to the machine.`
            : "Proposal declined",
      ),
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, variables) => {
      if (variables.kind === "design") {
        const setId = String(variables.setId);
        void invalidateSetDetail(queryClient, setId);
        void invalidateSetList(queryClient);
        void invalidateSetProposals(queryClient, setId);
        if (variables.threadId !== null && variables.threadId !== undefined) {
          void invalidateChatThread(queryClient, String(variables.threadId));
        }
        if (variables.decision === "decline") void invalidateDrafts(queryClient);
        return;
      }
      if (variables.decision === "accept") {
        void invalidateSets(queryClient);
        return;
      }
      void invalidateSetDetail(queryClient, String(variables.setId));
      void queryClient.invalidateQueries({
        queryKey: queryKeys.sets.proposals(String(variables.setId)),
      });
    },
  });
}

export function useCreateSet(): UseMutationResult<SetRow, Error, SetCreate> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createSet,
    onSuccess: (row) => toast.success(`Started "${row.name}"`),
    onError: (error) => toast.error(`Could not start the Set: ${error.message}`),
    onSettled: () => {
      void invalidateSets(queryClient);
      // A new Set is active by default, so the machine's other Set just lost
      // the flag its card renders.
      void invalidateShots(queryClient);
    },
  });
}

export function useAddSetVersion(): UseMutationResult<
  SetVersionRow,
  Error,
  { setId: number; patch: SetVersionPatch }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, patch }) => addSetVersion(setId, patch),
    onSuccess: (version) => toast.success(`Version ${version.version_no} recorded`),
    onError: (error) => toast.error(`Could not add the version: ${error.message}`),
    onSettled: () => invalidateSets(queryClient),
  });
}

/**
 * The three writes that are not a new version: the prediction, the outcome and
 * the roll back.
 *
 * Each invalidates the least that can have changed. A prediction is read on a
 * shot through that shot's own detail, so a prediction write reaches the shot
 * details and no other shots query — not the lists, not the sync counts. An
 * outcome is on the Set page alone: no list column and no chart series shows
 * one. A roll back appends a version, which does move the Sets list's current
 * version and the trend chart, so that one sweeps the whole `sets` prefix — and
 * nothing else, because the version it appends carries no shots.
 */
export function useSetVersionPrediction(): UseMutationResult<
  SetVersionRow,
  Error,
  { setId: number; versionId: number; body: VersionPredictionWrite }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, versionId, body }) => setVersionPrediction(setId, versionId, body),
    onSuccess: (version) =>
      toast.success(
        version.prediction ? `Prediction recorded on v${version.version_no}` : "Prediction removed",
      ),
    onError: (error) => toast.error(`Could not save the prediction: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateSetDetail(queryClient, String(variables.setId));
      void invalidateShotDetails(queryClient);
    },
  });
}

export function useSetVersionOutcome(): UseMutationResult<
  SetVersionRow,
  Error,
  { setId: number; versionId: number; body: VersionOutcomeWrite | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    // One hook for recording and for clearing: they are the same button's two
    // answers, and a second hook would need the same invalidations spelled out
    // again.
    mutationFn: ({ setId, versionId, body }) =>
      body === null
        ? clearVersionOutcome(setId, versionId)
        : setVersionOutcome(setId, versionId, body),
    onSuccess: (version) => toast.success(version.outcome ? "Outcome recorded" : "Outcome cleared"),
    onError: (error) => toast.error(`Could not save the outcome: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateSetDetail(queryClient, String(variables.setId));
    },
  });
}

export function useRollbackSet(): UseMutationResult<
  SetVersionRow,
  Error,
  { setId: number; body: RollbackWrite }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, body }) => rollbackSet(setId, body),
    onSuccess: (version) =>
      toast.success(
        `Version ${version.version_no} brings v${version.restores_version_no} back. Nothing was sent to the machine.`,
      ),
    onError: (error) => toast.error(`Could not roll back: ${error.message}`),
    onSettled: () => {
      // The whole prefix, and only it: a new version moves the Sets list's
      // current-version row and adds a boundary to the trend chart, but it
      // moves no shot — the version it appends has none — so nothing a shot
      // detail renders has changed.
      void invalidateSets(queryClient);
    },
  });
}

export function useSetAutomatch(): UseMutationResult<
  SetRow,
  Error,
  { id: number; automatch: boolean }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, automatch }) => setAutomatch(id, automatch),
    onSuccess: (row) =>
      toast.success(
        row.automatch
          ? `New shots on "${row.name}"'s profile are filed under it`
          : `"${row.name}" no longer collects new shots on its own`,
      ),
    onError: (error) => toast.error(error.message),
    onSettled: () => invalidateSets(queryClient),
  });
}

export function useArchiveSet(): UseMutationResult<SetRow, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: archiveSet,
    onSuccess: (row) => toast.success(`"${row.name}" archived`),
    onError: (error) => toast.error(error.message),
    onSettled: () => invalidateSets(queryClient),
  });
}

export function useSaveJudgement(): UseMutationResult<
  ShotJudgement,
  Error,
  { shotId: number; body: JudgementWrite }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ shotId, body }) => putJudgement(shotId, body),
    onSuccess: () => toast.success("Judgement saved"),
    onError: (error) => toast.error(`Could not save: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateShots(queryClient, String(variables.shotId));
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
    },
  });
}

/**
 * The fields a verdict is made of, as the API takes them back.
 *
 * `PUT /api/shots/{id}/judgement` **replaces** the row — the detail form sends
 * everything it renders, so a field left out is a field the user cleared. That
 * is right for a form and wrong for anything that changes one thing, which is
 * what a list row does: a rating clicked in the list must not take the taste
 * and aroma notes, the doses, the grind and the decision with it.
 */
function toWrite(judgement: ShotJudgement | null | undefined): JudgementWrite {
  return {
    rating: judgement?.rating ?? null,
    balance: judgement?.balance ?? null,
    taste_notes: judgement?.taste_notes ?? [],
    aroma_notes: judgement?.aroma_notes ?? [],
    dose_in_g: judgement?.dose_in_g ?? null,
    dose_out_g: judgement?.dose_out_g ?? null,
    grind_setting: judgement?.grind_setting ?? null,
    notes: judgement?.notes ?? "",
    decision: judgement?.decision ?? null,
  };
}

/**
 * Change part of a verdict, leaving the rest of it alone.
 *
 * The current judgement is read before every write rather than merged from
 * whatever the list happens to hold: the list row carries a rating and a notes
 * string and nothing else, so merging from it would quietly clear the four
 * fields it does not know about. One extra GET per edit, against a row that is
 * usually already in the cache, buys the property that a click in a list
 * cannot destroy something typed on the detail page.
 */
export function usePatchJudgement(
  shotId?: number,
): UseMutationResult<ShotJudgement, Error, { shotId: number; patch: Partial<JudgementWrite> }> {
  const queryClient = useQueryClient();
  return useMutation({
    // One queue per shot, shared by every control that writes its verdict (the
    // row's stars and decision, the panel under it). Each write reads the
    // verdict and puts it back with one field changed, so two in flight at
    // once — a star, then a chip, a moment apart — could each read the verdict
    // from before the other and the second would undo the first. Queued, each
    // reads what the one before it wrote.
    scope: shotId === undefined ? undefined : { id: `judgement-${shotId}` },
    mutationFn: async ({ shotId, patch }) => {
      const detail = await queryClient.fetchQuery({
        queryKey: queryKeys.shots.detail(String(shotId)),
        queryFn: () => getShot(shotId),
        // The app's default `staleTime` is thirty seconds, and `fetchQuery`
        // honours it: without this, "read the current verdict" could read one
        // from half a minute ago and write it back over a newer edit made on
        // the detail page in another tab.
        staleTime: 0,
      });
      return putJudgement(shotId, { ...toWrite(detail.judgement), ...patch });
    },
    onError: (error) => toast.error(`Could not save: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateShots(queryClient, String(variables.shotId));
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
    },
  });
}

export function useDeleteJudgement(): UseMutationResult<{ deleted: boolean }, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteJudgement,
    onSuccess: () => toast.success("Judgement withdrawn"),
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, shotId) => {
      void invalidateShots(queryClient, String(shotId));
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
    },
  });
}

/**
 * "Match by profile": file waiting shots under the one Set that brews their
 * profile. The toast says what happened to each kind, because a press that
 * filed nothing should say why (two Sets on the profile, or none).
 */
export function useMatchShotsByProfile(): UseMutationResult<
  ProfileMatchSummary,
  Error,
  number[] | undefined
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (shotIds) => matchShotsByProfile(shotIds),
    onSuccess: (summary) => {
      const message = profileMatchMessage(summary);
      if (summary.matched > 0) toast.success(message);
      else toast.info(message);
    },
    onError: (error) => toast.error(`Could not match: ${error.message}`),
    onSettled: (_data, _error, shotIds) => {
      for (const shotId of shotIds ?? []) void invalidateShots(queryClient, String(shotId));
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
      // The "N need a Set" count comes from the sync status (see useAssignShot).
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.status() });
    },
  });
}

/** One sentence for what a match did, leaving out the outcomes that did not happen. */
export function profileMatchMessage(summary: ProfileMatchSummary): string {
  const { matched, ambiguous, unmatched } = summary;
  if (matched + ambiguous + unmatched === 0) return "No shots were waiting for a Set";
  const parts = [
    matched > 0 ? `Filed ${matched} ${matched === 1 ? "shot" : "shots"}` : "Nothing filed",
  ];
  if (ambiguous > 0) parts.push(`${ambiguous} left: more than one Set brews the profile`);
  if (unmatched > 0) parts.push(`${unmatched} left: no Set brews the profile`);
  return parts.join(" · ");
}

export function useAssignShot(): UseMutationResult<
  ShotDetailRow,
  Error,
  { shotId: number; setVersionId: number | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ shotId, setVersionId }) => putShotSetVersion(shotId, setVersionId),
    onSuccess: (row) =>
      toast.success(
        row.set_badge
          ? `Filed under ${row.set_badge.set_name} v${row.set_badge.version_no}`
          : "Detached from its Set",
      ),
    onError: (error) => toast.error(`Could not assign: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateShots(queryClient, String(variables.shotId));
      void invalidateShots(queryClient);
      void invalidateSets(queryClient);
      // The shots page's "N need a Set" count comes from the sync status, not
      // from the list, and filing a shot publishes no event that would refresh
      // it — so the header kept counting a shot that was just filed. The status
      // key by name rather than the `sync` prefix, so nothing added under that
      // prefix later is refetched by every assignment.
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.status() });
    },
  });
}
