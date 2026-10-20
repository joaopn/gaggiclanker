import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiClientError,
  acceptOutcomeProposal,
  acceptRecipeProposal,
  addRecipeVersion,
  archiveRecipe,
  changeOutcomeProposal,
  clearVersionOutcome,
  createRecipe,
  declineRecipeProposal,
  deleteJudgement,
  designRecipe,
  discardDesign,
  dismissOutcomeProposal,
  getOutcomeProposals,
  getProfileChoices,
  getRecipe,
  getRecipeProposals,
  getRecipes,
  getRecipeTrends,
  getShot,
  matchShotsByProfile,
  putJudgement,
  putShotRecipeVersion,
  rollbackRecipe,
  setAutomatch,
  setVersionOutcome,
  setVersionPrediction,
} from "@/api/client";
import type {
  JudgementWrite,
  OutcomeProposalDecision,
  OutcomeProposalListData,
  ProfileChoiceListData,
  ProfileMatchSummary,
  RecipeCreate,
  RecipeDesignCreate,
  RecipeDesignCreated,
  RecipeDesignDiscarded,
  RecipeDetailData,
  RecipeListData,
  RecipeProposal,
  RecipeProposalDecision,
  RecipeProposalListData,
  RecipeRow,
  RecipeTrends,
  RecipeVersionAdd,
  RecipeVersionRow,
  RollbackWrite,
  ShotDetailRow,
  ShotJudgement,
  VersionOutcome,
  VersionOutcomeWrite,
  VersionPredictionWrite,
} from "@/api/types";
import {
  invalidateBoardWrites,
  invalidateChatThread,
  invalidateChatThreads,
  invalidateChatTranscripts,
  invalidateDrafts,
  invalidateOutcomeProposals,
  invalidateRecipeDetail,
  invalidateRecipeList,
  invalidateRecipeProposals,
  invalidateRecipes,
  invalidateShotDetails,
  invalidateShots,
} from "@/lib/invalidate";
import { noAnswerSentence } from "@/lib/mutations";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Recipes, their versions, the trend chart, and the two things a person writes
 * about a shot: the verdict and which Recipe it belongs to.
 *
 * Every mutation here invalidates both `recipes` and `shots`, and that is not
 * laziness. Saving a judgement changes the shot row (`has_judgement`), the Recipe
 * page's own copy of that row, and the trend chart's averages; assigning a shot
 * changes its badge in the list and moves a point between two versions on the
 * chart. Naming three keys at each call site is how one of them gets forgotten,
 * and the symptom — "the page did not update" — surfaces long after the commit.
 */

export function useRecipes(includeArchived = false): UseQueryResult<RecipeListData, Error> {
  return useQuery({
    queryKey: queryKeys.recipes.list(includeArchived),
    queryFn: () => getRecipes(includeArchived),
  });
}

export function useProfileChoices(
  options: { enabled?: boolean } = {},
): UseQueryResult<ProfileChoiceListData, Error> {
  return useQuery({
    queryKey: queryKeys.recipes.profileChoices(),
    queryFn: getProfileChoices,
    enabled: options.enabled ?? true,
  });
}

export function useRecipe(id: number | undefined): UseQueryResult<RecipeDetailData, Error> {
  return useQuery({
    queryKey: queryKeys.recipes.detail(String(id)),
    queryFn: () => getRecipe(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

export function useRecipeTrends(id: number | undefined): UseQueryResult<RecipeTrends, Error> {
  return useQuery({
    queryKey: queryKeys.recipes.trends(String(id)),
    queryFn: () => getRecipeTrends(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

/**
 * The changes an agent has proposed for a Recipe.
 *
 * Read on the Recipe page's behalf by the page itself (the detail carries the
 * waiting one) and, on its own, by a proposal card inside a conversation: that
 * card has to say whether the person has answered yet, and it is often open
 * when the Recipe page is not.
 */
export function useRecipeProposals(
  id: number | undefined,
): UseQueryResult<RecipeProposalListData, Error> {
  return useQuery({
    queryKey: queryKeys.recipes.proposals(String(id)),
    queryFn: () => getRecipeProposals(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

/**
 * The grades an agent has proposed for a Recipe's versions.
 *
 * Read by the chat's outcome card, which has to say whether the person has
 * answered yet and is often open when the Recipe page is not; the Recipe page reads
 * the waiting one of each version off its own detail.
 */
export function useOutcomeProposals(
  id: number | undefined,
): UseQueryResult<OutcomeProposalListData, Error> {
  return useQuery({
    queryKey: queryKeys.recipes.outcomeProposals(String(id)),
    queryFn: () => getOutcomeProposals(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

/** How a person answers a proposed grade. */
export type OutcomeAnswer =
  | { kind: "accept" }
  | { kind: "change"; outcome: VersionOutcome; note?: string }
  | { kind: "dismiss"; note?: string };

/**
 * Accept a proposed grade, record another outcome, or dismiss it: the three
 * buttons of one card, so one hook.
 *
 * They invalidate the same things, because the card and the page read the same
 * rows. **Accepting or changing** writes the version's outcome, which the Recipe
 * detail carries (the version, the track record and the ledger it feeds) and
 * which a waiting version proposal's "also records" line reads; **dismissing**
 * writes no outcome but ends the waiting grade the detail, that line and the
 * card all show. So: the Recipe detail, the Recipe's proposals, its outcome
 * proposals, and the conversation the grade was argued in (its card is drawn
 * from the transcript). Not the Recipes list, the trend chart or any shot: no
 * outcome is shown there.
 */
export function useAnswerOutcomeProposal(): UseMutationResult<
  OutcomeProposalDecision,
  Error,
  {
    recipeId: number;
    proposalId: number;
    answer: OutcomeAnswer;
    /** The conversation it was argued in, when there is one. */
    threadId?: number | null;
  }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ recipeId, proposalId, answer }) =>
      answer.kind === "accept"
        ? acceptOutcomeProposal(recipeId, proposalId)
        : answer.kind === "change"
          ? changeOutcomeProposal(recipeId, proposalId, {
              outcome: answer.outcome,
              ...(answer.note ? { note: answer.note } : {}),
            })
          : dismissOutcomeProposal(recipeId, proposalId, { note: answer.note ?? "" }),
    onSuccess: (result, variables) =>
      toast.success(
        variables.answer.kind === "dismiss"
          ? "Grade dismissed. Nothing was recorded."
          : `${result.proposal.version_label ?? "The version"}'s outcome recorded`,
      ),
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, variables) => {
      const recipeId = String(variables.recipeId);
      void invalidateRecipeDetail(queryClient, recipeId);
      void invalidateRecipeProposals(queryClient, recipeId);
      void invalidateOutcomeProposals(queryClient, recipeId);
      if (variables.threadId !== null && variables.threadId !== undefined) {
        void invalidateChatThread(queryClient, String(variables.threadId));
      }
    },
  });
}

/**
 * Accept or decline one. The two answers to the same question, so one hook.
 *
 * They invalidate different things, because they change different things.
 * **Accepting** appends a version: the Recipes list's version count moves, the
 * trend chart gains a boundary and the Recipe page's whole log changes, so the
 * `recipes` prefix goes. **Declining** changes one row — no version, no shot, no
 * chart — so it reaches the Recipe detail (which carries the waiting proposal)
 * and the proposals list, and nothing else. A decline that swept the prefix
 * would refetch every open Recipe page's five hundred shots to record a sentence.
 *
 * **A Recipe's first recipe** (`kind: "design"`) is answered the same two ways and
 * reaches further in one direction and less far in another. It adds no version
 * — accepting fills version 1 in place — and no shot moves, so the trend chart
 * is left alone; but it ends the design, which takes the badge off the Recipes
 * list and changes the tools the conversation it came from is offered, so the
 * list and that conversation are read again. Declining it discards the profile
 * draft it carried, so the draft queue is read again too, and accepting approves
 * that draft with version 1 (and switches its profile on), so everything a put on
 * the board changes is read again: the board, the drafts and each proposal's
 * standing, the profile mirror, the sync status and the write audit.
 *
 * The failure is the interesting half: the server answers a stale proposal and
 * an ungraded prediction with sentences the person can act on, so the toast
 * carries the server's words rather than "Could not accept".
 */
export function useDecideProposal(): UseMutationResult<
  RecipeProposalDecision,
  Error,
  {
    recipeId: number;
    proposalId: number;
    decision: "accept" | "decline";
    note?: string;
    /** Which kind of proposal is being answered. A change unless said otherwise. */
    kind?: RecipeProposal["kind"];
    /** The conversation it was argued in, when there is one. */
    threadId?: number | null;
    /** The card's "Major change" box, on an accept of a change. */
    major?: boolean;
    /**
     * The accept also records the agent's grade of the version it was made
     * against, which the conversation's outcome card is drawn from.
     */
    recordsOutcome?: boolean;
    /** A first recipe only: the name for its new profile, as the person typed it. */
    profileLabel?: string;
    /** No toast: the caller starts a sync, whose own notification follows. */
    quiet?: boolean;
  }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ recipeId, proposalId, decision, note, major, profileLabel }) =>
      decision === "accept"
        ? major === undefined && profileLabel === undefined
          ? acceptRecipeProposal(recipeId, proposalId)
          : acceptRecipeProposal(recipeId, proposalId, {
              ...(major === undefined ? {} : { major }),
              ...(profileLabel === undefined ? {} : { profileLabel }),
            })
        : declineRecipeProposal(recipeId, proposalId, { note: note ?? "" }),
    onSuccess: (result, variables) => {
      if (variables.quiet) return;
      toast.success(
        result.proposal.kind === "design"
          ? result.version
            ? "Accepted: version 1 is set."
            : "Version 1 declined, and its proposal declined too"
          : result.version
            ? `${result.version.version_label} recorded. Nothing was sent to the machine.`
            : "Proposal declined",
      );
    },
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, variables) => {
      if (variables.kind === "design") {
        const recipeId = String(variables.recipeId);
        void invalidateRecipeDetail(queryClient, recipeId);
        void invalidateRecipeList(queryClient);
        void invalidateRecipeProposals(queryClient, recipeId);
        if (variables.threadId !== null && variables.threadId !== undefined) {
          void invalidateChatThread(queryClient, String(variables.threadId));
        }
        if (variables.decision === "decline") {
          void invalidateDrafts(queryClient);
        } else {
          // Accepting also put the card's profile on the list: the board, the drafts (and each
          // proposal's standing under them), the mirror and the sync status all changed.
          void invalidateBoardWrites(queryClient);
        }
        return;
      }
      if (variables.decision === "accept") {
        // The `recipes` prefix reaches the detail (the version, the track record,
        // the ledger), the proposals and the outcome proposals, and the versions
        // list the new version joins. An accept that also recorded a grade
        // changes what that grade's card says in its own conversation.
        void invalidateRecipes(queryClient);
        if (
          variables.recordsOutcome &&
          variables.threadId !== null &&
          variables.threadId !== undefined
        ) {
          void invalidateChatThread(queryClient, String(variables.threadId));
        }
        return;
      }
      void invalidateRecipeDetail(queryClient, String(variables.recipeId));
      void queryClient.invalidateQueries({
        queryKey: queryKeys.recipes.proposals(String(variables.recipeId)),
      });
    },
  });
}

export function useCreateRecipe(): UseMutationResult<RecipeRow, Error, RecipeCreate> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createRecipe,
    onSuccess: (row) => toast.success(`Started "${row.name}"`),
    onError: (error) => toast.error(`Could not start the Recipe: ${error.message}`),
    onSettled: () => {
      void invalidateRecipes(queryClient);
      // A new Recipe is active by default, so the machine's other Recipe just lost
      // the flag its card renders.
      void invalidateShots(queryClient);
    },
  });
}

/**
 * Start a Recipe to be designed in its own conversation.
 *
 * A new Recipe and a new conversation, and nothing else: the Recipes list gains a
 * card and the Chat page a badge with a thread under it, so those two lists are
 * read again. No version with a recipe exists yet, so no chart and no shot is
 * touched.
 */
export function useStartDesign(): UseMutationResult<
  RecipeDesignCreated,
  Error,
  RecipeDesignCreate
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: designRecipe,
    onSuccess: (created) => toast.success(`Started designing "${created.recipe.name}"`),
    onError: (error) => toast.error(`Could not start the design: ${error.message}`),
    onSettled: () => {
      void invalidateRecipeList(queryClient);
      void invalidateChatThreads(queryClient);
    },
  });
}

/**
 * Discard a Recipe that is still being designed.
 *
 * The Recipe leaves the Recipes list and its conversations leave the Chat page, and
 * the profile draft a waiting card carried is discarded with it, so those three
 * lists are read again. The Recipe's own page is left behind by the navigation
 * that follows, not refetched into a 404.
 *
 * The two refusals are answered on the page in words, not only in the toast:
 * a shot filed on it by hand, or a recipe it already has.
 */
export function useDiscardDesign(): UseMutationResult<RecipeDesignDiscarded, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: discardDesign,
    onSuccess: () => toast.success("Design discarded"),
    onError: (error) => toast.error(`Could not discard the design: ${error.message}`),
    onSettled: () => {
      void invalidateRecipeList(queryClient);
      void invalidateChatThreads(queryClient);
      void invalidateDrafts(queryClient);
    },
  });
}

export function useAddRecipeVersion(): UseMutationResult<
  RecipeVersionRow,
  Error,
  { recipeId: number; patch: RecipeVersionAdd }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ recipeId, patch }) => addRecipeVersion(recipeId, patch),
    onSuccess: (version) => toast.success(`${version.version_label} recorded`),
    onError: (error) => toast.error(`Could not add the version: ${error.message}`),
    onSettled: () => invalidateRecipes(queryClient),
  });
}

/**
 * The three writes that are not a new version: the prediction, the outcome and
 * the revert.
 *
 * Each invalidates the least that can have changed. A prediction is read on a
 * shot through that shot's own detail, so a prediction write reaches the shot
 * details and no other shots query — not the lists, not the sync counts. An
 * outcome is on the Recipe page alone: no list column and no chart series shows
 * one. A revert writes no version but moves the Recipe's current one, which the
 * Recipes list, the Recipe page, the proposals and the conversations all read, so
 * that one sweeps the whole `recipes` prefix and the chat — and no shot query,
 * because no shot moved.
 */
export function useSetVersionPrediction(): UseMutationResult<
  RecipeVersionRow,
  Error,
  { recipeId: number; versionId: number; body: VersionPredictionWrite }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ recipeId, versionId, body }) => setVersionPrediction(recipeId, versionId, body),
    onSuccess: (version) =>
      toast.success(
        version.prediction
          ? `Prediction recorded on ${version.version_label}`
          : "Prediction removed",
      ),
    onError: (error) => toast.error(`Could not save the prediction: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateRecipeDetail(queryClient, String(variables.recipeId));
      void invalidateShotDetails(queryClient);
    },
  });
}

export function useSetVersionOutcome(): UseMutationResult<
  RecipeVersionRow,
  Error,
  { recipeId: number; versionId: number; body: VersionOutcomeWrite | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    // One hook for recording and for clearing: they are the same button's two
    // answers, and a second hook would need the same invalidations spelled out
    // again.
    mutationFn: ({ recipeId, versionId, body }) =>
      body === null
        ? clearVersionOutcome(recipeId, versionId)
        : setVersionOutcome(recipeId, versionId, body),
    onSuccess: (version) => toast.success(version.outcome ? "Outcome recorded" : "Outcome cleared"),
    onError: (error) => toast.error(`Could not save the outcome: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateRecipeDetail(queryClient, String(variables.recipeId));
      // A waiting grade is shown beside the outcome somebody just wrote here,
      // by its card in the chat, which says both.
      void invalidateOutcomeProposals(queryClient, String(variables.recipeId));
    },
  });
}

export function useRollbackRecipe(): UseMutationResult<
  RecipeVersionRow,
  Error,
  { recipeId: number; body: RollbackWrite }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ recipeId, body }) => rollbackRecipe(recipeId, body),
    onSuccess: (version) =>
      toast.success(
        `The Recipe is back on ${version.version_label}. Its profile is back on that brew, and the next sync puts it on the machine.`,
      ),
    onError: (error) => toast.error(`Could not go back: ${error.message}`),
    onSettled: () => {
      // The whole `recipes` prefix: the Recipe's detail (which version is current,
      // the new log line), the Recipes list's current-version row, the chart, and
      // the proposal queries that live under it (a waiting card's base is no
      // longer the current version). Then the conversation list, because the
      // Recipe's live conversation is now the version's it went back to. No shot
      // moved, so no shot query is touched.
      void invalidateRecipes(queryClient);
      // The Recipe's profile went back with it: the Profiles page, its versions and what the next
      // sync will do read that.
      void invalidateBoardWrites(queryClient);
      void invalidateChatThreads(queryClient);
      // The open conversation's heading says whether its version is a dead end.
      void invalidateChatTranscripts(queryClient);
    },
  });
}

export function useSetAutomatch(): UseMutationResult<
  RecipeRow,
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
    onSettled: () => invalidateRecipes(queryClient),
  });
}

export function useArchiveRecipe(): UseMutationResult<RecipeRow, Error, number> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => archiveRecipe(id),
    onSuccess: (row) => toast.success(`"${row.name}" archived`),
    onError: (error) =>
      toast.error(error instanceof ApiClientError ? error.message : noAnswerSentence("the page")),
    // Archiving unpins the Recipe's pins in the same write: their profile-list rows are
    // tombstoned, so the board, the profile mirror and what the sync does about them moved too.
    onSettled: () =>
      Promise.all([invalidateRecipes(queryClient), invalidateBoardWrites(queryClient)]).then(
        () => undefined,
      ),
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
      void invalidateRecipes(queryClient);
      // A shot labelled Discard leaves the "needs a Recipe" inbox, and the shots
      // page's count of it comes from the sync status (see useAssignShot),
      // which no event refreshes after a judgement.
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.status() });
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
      void invalidateRecipes(queryClient);
      // The decision column is where Discard is most often set (useSaveJudgement).
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.status() });
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
      void invalidateRecipes(queryClient);
      // Withdrawing a Discard puts an unfiled shot back in the inbox (useSaveJudgement).
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.status() });
    },
  });
}

/**
 * "Match by profile": file waiting shots under the Recipe that brews their
 * profile. The toast says what happened to each kind, because a press that
 * filed nothing should say why (no Recipe brews the profile).
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
      void invalidateRecipes(queryClient);
      // The "N need a Recipe" count comes from the sync status (see useAssignShot).
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.status() });
    },
  });
}

/** One sentence for what a match did, leaving out the outcomes that did not happen. */
export function profileMatchMessage(summary: ProfileMatchSummary): string {
  const { matched, unmatched } = summary;
  if (matched + unmatched === 0) return "No shots were waiting for a Recipe";
  const parts = [
    matched > 0 ? `Filed ${matched} ${matched === 1 ? "shot" : "shots"}` : "Nothing filed",
  ];
  if (unmatched > 0) parts.push(`${unmatched} left: no Recipe brews the profile`);
  return parts.join(" · ");
}

export function useAssignShot(): UseMutationResult<
  ShotDetailRow,
  Error,
  { shotId: number; recipeVersionId: number | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ shotId, recipeVersionId }) => putShotRecipeVersion(shotId, recipeVersionId),
    onSuccess: (row) =>
      toast.success(
        row.recipe_badge
          ? `Filed under ${row.recipe_badge.recipe_name} ${row.recipe_badge.version_label}`
          : "Detached from its Recipe",
      ),
    onError: (error) => toast.error(`Could not assign: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateShots(queryClient, String(variables.shotId));
      void invalidateShots(queryClient);
      void invalidateRecipes(queryClient);
      // The shots page's "N need a Recipe" count comes from the sync status, not
      // from the list, and filing a shot publishes no event that would refresh
      // it — so the header kept counting a shot that was just filed. The status
      // key by name rather than the `sync` prefix, so nothing added under that
      // prefix later is refetched by every assignment.
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.status() });
    },
  });
}
