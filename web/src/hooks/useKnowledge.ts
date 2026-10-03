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
  KnowledgeRule,
  KnowledgeRuleListData,
  KnowledgeRulePatch,
  KnowledgeSearchData,
} from "@/api/types";
import { invalidateChatThread, invalidateKnowledge } from "@/lib/invalidate";
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
 * Confirming is what puts one in front of the matching Set's conversations.
 */

export function useKnowledgeInsights(
  filters: { confirmed?: boolean; set_id?: number } = {},
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
 * Add, dismiss or take back a Set's insight: the card's buttons.
 *
 * One hook, one breadth, because the card, the Set page's list and the
 * Knowledge page read the same rows: the whole `knowledge` prefix (the insight,
 * the Set's list with its general companions, the Knowledge list), plus the
 * conversation the insight was proposed in, whose card is drawn from the
 * transcript. Nothing under `sets`: an insight is on no Set payload.
 */
export function useAnswerInsight(): UseMutationResult<
  KnowledgeInsight,
  Error,
  { id: number; answer: "add" | "dismiss" | "take_back"; threadId?: number | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, answer }) =>
      answer === "dismiss"
        ? dismissKnowledgeInsight(id)
        : patchKnowledgeInsight(id, { confirmed: answer === "add" }),
    onSuccess: (_insight, variables) =>
      toast.success(
        variables.answer === "add"
          ? "Added — this Set's later conversations will be told this"
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

/** Every deletion an agent proposed for a Set's insights: what the chat's deletion card reads. */
export function useInsightDeletions(
  setId: number | undefined,
  threadId?: number | null,
): UseQueryResult<InsightDeletionListData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.insightDeletions(String(setId), threadId),
    queryFn: () => getInsightDeletions(setId as number, threadId),
    enabled: setId !== undefined && Number.isFinite(setId),
  });
}

/**
 * Delete or keep the insight an agent proposed deleting: the deletion card's buttons.
 *
 * The `knowledge` prefix covers the insight itself, the Set's list (which carries
 * the waiting deletions), the Knowledge list and these cards; the conversation is
 * invalidated too, because its card is drawn from the transcript. Nothing under
 * `sets`: an insight is on no Set payload.
 */
export function useAnswerInsightDeletion(): UseMutationResult<
  InsightDeletionDecision,
  Error,
  {
    setId: number;
    proposalId: number;
    answer: "delete" | "keep";
    /** The conversation it was argued in, when there is one. */
    threadId?: number | null;
  }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, proposalId, answer }) =>
      answer === "delete"
        ? acceptInsightDeletion(setId, proposalId)
        : keepInsightDeletion(setId, proposalId),
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
          ? "Confirmed — conversations about matching Sets will be told this"
          : "Unconfirmed — it will not be put in front of the model",
      );
    },
    onError: (error) => toast.error(`Could not save the insight: ${error.message}`),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
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
    },
  });
}
