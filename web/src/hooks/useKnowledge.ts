import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  acceptInsightDeletion,
  createKnowledgeInsight,
  deleteKnowledgeInsight,
  dismissKnowledgeInsight,
  getInsightDeletions,
  getKnowledgeDoc,
  getKnowledgeDocs,
  getKnowledgeInsight,
  getKnowledgeInsights,
  getKnowledgeRules,
  getScopeOptions,
  getScopeReach,
  keepInsightDeletion,
  patchKnowledgeInsight,
  patchKnowledgeRule,
  putKnowledgeDoc,
  reloadKnowledgeRules,
  resetKnowledgeDoc,
  searchKnowledge,
} from "@/api/client";
import type {
  InsightDeletionDecision,
  InsightDeletionListData,
  KnowledgeDocDetail,
  KnowledgeDocListData,
  KnowledgeInsight,
  KnowledgeInsightCreate,
  KnowledgeInsightListData,
  KnowledgeInsightPatch,
  KnowledgeInsightScope,
  KnowledgeRule,
  KnowledgeRuleListData,
  KnowledgeRulePatch,
  KnowledgeSearchData,
  ScopeOptionsData,
  ScopeReachData,
} from "@/api/types";
import {
  invalidateChatThread,
  invalidateChatTranscripts,
  invalidateKnowledge,
} from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The rule tier: reading it, turning one off, and editing what one says.
 *
 * A rule change reaches the next review and the next chat turn; the rules a
 * stored review cites are the ones it was given, and stay as they were.
 */

export function useKnowledgeRules(
  filters: { category?: string; enabled?: boolean } = {},
): UseQueryResult<KnowledgeRuleListData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.list(filters),
    queryFn: () => getKnowledgeRules(filters),
  });
}

export function usePatchKnowledgeRule(): UseMutationResult<
  KnowledgeRule,
  Error,
  { id: number; patch: KnowledgeRulePatch }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }) => patchKnowledgeRule(id, patch),
    onSuccess: (rule) => toast.success(rule.enabled ? `${rule.key} is on` : `${rule.key} is off`),
    onError: (error) => toast.error(`Could not save the rule: ${error.message}`),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
    },
  });
}

export function useReloadKnowledgeRules(): UseMutationResult<{ changed: number }, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => reloadKnowledgeRules(),
    onSuccess: (result) =>
      toast.success(
        result.changed === 0
          ? "Already up to date"
          : `${result.changed} rule${result.changed === 1 ? "" : "s"} reloaded`,
      ),
    onError: (error) => toast.error(error.message),
    onSettled: () => void invalidateKnowledge(queryClient),
  });
}

/**
 * Tier 2: the documents, their chunks and the search over them.
 */

export function useKnowledgeDocs(): UseQueryResult<KnowledgeDocListData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.docs(),
    queryFn: () => getKnowledgeDocs(),
  });
}

export function useKnowledgeDoc(slug: string | null): UseQueryResult<KnowledgeDocDetail, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.doc(slug ?? ""),
    queryFn: () => getKnowledgeDoc(slug as string),
    enabled: Boolean(slug),
  });
}

/**
 * The search box.
 *
 * Disabled below two characters: a one-letter query matches most of the corpus
 * and the result is a page of noise that arrives while you are still typing.
 */
export function useKnowledgeSearch(
  query: string,
  k = 8,
): UseQueryResult<KnowledgeSearchData, Error> {
  const trimmed = query.trim();
  return useQuery({
    queryKey: queryKeys.knowledge.search(trimmed, k),
    queryFn: () => searchKnowledge(trimmed, k),
    enabled: trimmed.length > 1,
  });
}

export function useSaveKnowledgeDoc(): UseMutationResult<
  KnowledgeDocDetail,
  Error,
  { slug: string; markdown: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, markdown }) => putKnowledgeDoc(slug, markdown),
    onSuccess: (detail) =>
      toast.success(
        `${detail.doc.slug} saved — ${detail.chunks.length} chunk${
          detail.chunks.length === 1 ? "" : "s"
        }`,
      ),
    onError: (error) => toast.error(`Could not save the document: ${error.message}`),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
    },
  });
}

export function useResetKnowledgeDoc(): UseMutationResult<KnowledgeDocDetail, Error, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug) => resetKnowledgeDoc(slug),
    onSuccess: (detail) => toast.success(`${detail.doc.slug} is back to the shipped text`),
    onError: (error) => toast.error(`Could not reset the document: ${error.message}`),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
    },
  });
}

/**
 * Tier 3: the learned insights.
 *
 * Confirming is what puts one in front of the matching Recipe's conversations.
 */

export function useKnowledgeInsights(
  filters: { confirmed?: boolean; recipe_id?: number } = {},
): UseQueryResult<KnowledgeInsightListData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.insights(filters),
    queryFn: () => getKnowledgeInsights(filters),
  });
}

/** One insight in whatever state it is: what a card in the chat reads. */
export function useKnowledgeInsight(
  id: number | undefined,
): UseQueryResult<KnowledgeInsight, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.insight(String(id)),
    queryFn: () => getKnowledgeInsight(id as number),
    enabled: id !== undefined && Number.isFinite(id),
  });
}

/**
 * Add, dismiss or take back a Recipe's insight: the card's buttons.
 *
 * One hook, one breadth, because the card, the Recipe page's list and the
 * Knowledge page read the same rows: the whole `knowledge` prefix (the insight,
 * the Recipe's list with its general companions, the Knowledge list), plus the
 * conversation the insight was proposed in, whose card is drawn from the
 * transcript. Nothing under `recipes`: an insight is on no Recipe payload.
 */
export function useAnswerInsight(): UseMutationResult<
  KnowledgeInsight,
  Error,
  {
    id: number;
    answer: "add" | "dismiss" | "take_back";
    threadId?: number | null;
    /**
     * A general request's Add: the words and the scope the person settled on, sent with the
     * confirmation in one PATCH, so the row is never added as the agent worded it by accident.
     */
    edit?: { text: string; scope: KnowledgeInsightScope };
  }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, answer, edit }) =>
      answer === "dismiss"
        ? dismissKnowledgeInsight(id)
        : patchKnowledgeInsight(id, { ...edit, confirmed: answer === "add" }),
    onSuccess: (_insight, variables) =>
      toast.success(
        variables.answer === "add"
          ? variables.edit
            ? "Added — conversations about matching Recipes will be told this"
            : "Added — this Recipe's later conversations will be told this"
          : variables.answer === "dismiss"
            ? "Dismissed — it will not be put in front of the model"
            : "Taken back — it will not be put in front of the model",
      ),
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, variables) => {
      void invalidateKnowledge(queryClient);
      if (variables.threadId !== null && variables.threadId !== undefined) {
        void invalidateChatThread(queryClient, String(variables.threadId));
      }
    },
  });
}

/** Every deletion an agent proposed for a Recipe's insights: what the chat's deletion card reads. */
export function useInsightDeletions(
  recipeId: number | undefined,
  threadId?: number | null,
): UseQueryResult<InsightDeletionListData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.insightDeletions(String(recipeId), threadId),
    queryFn: () => getInsightDeletions(recipeId as number, threadId),
    enabled: recipeId !== undefined && Number.isFinite(recipeId),
  });
}

/**
 * Delete or keep the insight an agent proposed deleting: the deletion card's buttons.
 *
 * The `knowledge` prefix covers the insight itself, the Recipe's list (which carries
 * the waiting deletions), the Knowledge list and these cards; the conversation is
 * invalidated too, because its card is drawn from the transcript. Nothing under
 * `recipes`: an insight is on no Recipe payload.
 */
export function useAnswerInsightDeletion(): UseMutationResult<
  InsightDeletionDecision,
  Error,
  {
    recipeId: number;
    proposalId: number;
    answer: "delete" | "keep";
    /** The conversation it was argued in, when there is one. */
    threadId?: number | null;
  }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ recipeId, proposalId, answer }) =>
      answer === "delete"
        ? acceptInsightDeletion(recipeId, proposalId)
        : keepInsightDeletion(recipeId, proposalId),
    onSuccess: (_result, variables) =>
      toast.success(
        variables.answer === "delete"
          ? "Insight deleted"
          : "Kept — the insight stays, and the agent is told",
      ),
    onError: (error) => toast.error(error.message),
    onSettled: (_data, _error, variables) => {
      void invalidateKnowledge(queryClient);
      if (variables.threadId !== null && variables.threadId !== undefined) {
        void invalidateChatThread(queryClient, String(variables.threadId));
      }
    },
  });
}

/** What the scope picker offers: a Recipe's own attributes with a Recipe, the archive's choices without. */
export function useScopeOptions(recipeId: number | null): UseQueryResult<ScopeOptionsData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.scopeOptions(recipeId),
    queryFn: () => getScopeOptions(recipeId),
  });
}

/** How many Recipes a scope applies to now: refreshed with every change to the scope. */
export function useScopeReach(scope: KnowledgeInsightScope): UseQueryResult<ScopeReachData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.scopeReach({ ...scope }),
    queryFn: () => getScopeReach(scope),
    // The previous number stays on screen while the next one is read, so a click does not flicker.
    placeholderData: (previous) => previous,
  });
}

/**
 * "Save as general insight": the person's own message kept, with the scope they chose.
 *
 * Invalidates the knowledge prefix (the Knowledge list, the Recipes' insights) and the
 * conversation, whose message then says it was saved.
 */
export function useSaveMessageAsInsight(): UseMutationResult<
  KnowledgeInsight,
  Error,
  { text: string; scope: KnowledgeInsightScope; messageId: number; threadId: number | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ text, scope, messageId }) =>
      createKnowledgeInsight({ text, scope, from_message_id: messageId, confirmed: true }),
    onSuccess: () => toast.success("Saved as a general insight"),
    onError: (error) => toast.error(`Could not save it: ${error.message}`),
    onSettled: (_data, _error, variables) => {
      void invalidateKnowledge(queryClient);
      if (variables.threadId !== null) {
        void invalidateChatThread(queryClient, String(variables.threadId));
      }
    },
  });
}

export function useCreateKnowledgeInsight(): UseMutationResult<
  KnowledgeInsight,
  Error,
  KnowledgeInsightCreate
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) => createKnowledgeInsight(body),
    onSuccess: () => toast.success("Insight saved"),
    onError: (error) => toast.error(`Could not save the insight: ${error.message}`),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
    },
  });
}

export function usePatchKnowledgeInsight(): UseMutationResult<
  KnowledgeInsight,
  Error,
  { id: number; patch: KnowledgeInsightPatch }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }) => patchKnowledgeInsight(id, patch),
    onSuccess: (insight, variables) => {
      if (variables.patch.confirmed === undefined) {
        toast.success("Insight saved");
        return;
      }
      toast.success(
        insight.confirmed
          ? "Confirmed — conversations about matching Recipes will be told this"
          : "Unconfirmed — it will not be put in front of the model",
      );
    },
    onError: (error) => toast.error(`Could not save the insight: ${error.message}`),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
      // A message says "Saved as general insight #N" from the insight; an edit or a delete here
      // moves that line too.
      void invalidateChatTranscripts(queryClient);
    },
  });
}

export function useDeleteKnowledgeInsight(): UseMutationResult<
  { deleted: boolean },
  Error,
  number
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => deleteKnowledgeInsight(id),
    onSuccess: () => toast.success("Insight deleted"),
    onError: (error) => toast.error(`Could not delete the insight: ${error.message}`),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
      // A message says "Saved as general insight #N" from the insight; an edit or a delete here
      // moves that line too.
      void invalidateChatTranscripts(queryClient);
    },
  });
}
