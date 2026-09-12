import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  createKnowledgeInsight,
  deleteKnowledgeInsight,
  getKnowledgeDoc,
  getKnowledgeDocs,
  getKnowledgeInsights,
  getKnowledgeRules,
  patchKnowledgeInsight,
  patchKnowledgeRule,
  putKnowledgeDoc,
  reloadKnowledgeRules,
  resetKnowledgeDoc,
  searchKnowledge,
} from "@/api/client";
import type {
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
import { invalidateAnalyses, invalidateKnowledge } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The rule tier: reading it, turning one off, and editing what one says.
 *
 * Every mutation invalidates `analyses` as well as `knowledge`, because a rule
 * change is only interesting in terms of what the analyzer does next: the rules
 * a stored analysis lists are the ones it was given, and the page linking a
 * rule key back to this one has to agree about whether that rule is still on.
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
      void invalidateAnalyses(queryClient);
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
 *
 * Editing a document invalidates `knowledge` **and** `analyses`, for the same
 * reason a rule edit does: an analysis's "excerpts it leaned on" links into a
 * document, and the two pages have to agree about what that document now says.
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
      void invalidateAnalyses(queryClient);
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
      void invalidateAnalyses(queryClient);
    },
  });
}

/**
 * Tier 3: the learned insights.
 *
 * Confirming is what puts one in front of the next analysis of a matching Set,
 * so every mutation here invalidates `analyses` too — the shot panel renders
 * the proposals of the analysis it is showing, and a confirm pressed there has
 * to move the badge on this page and the other way round.
 */

export function useKnowledgeInsights(
  filters: { confirmed?: boolean; analysis_id?: number; set_id?: number } = {},
): UseQueryResult<KnowledgeInsightListData, Error> {
  return useQuery({
    queryKey: queryKeys.knowledge.insights(filters),
    queryFn: () => getKnowledgeInsights(filters),
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
      void invalidateAnalyses(queryClient);
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
          ? "Confirmed — later analyses of matching Sets will be told this"
          : "Unconfirmed — it will not be put in front of the model",
      );
    },
    onError: (error) => toast.error(`Could not save the insight: ${error.message}`),
    onSettled: () => {
      void invalidateKnowledge(queryClient);
      void invalidateAnalyses(queryClient);
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
      void invalidateAnalyses(queryClient);
    },
  });
}
