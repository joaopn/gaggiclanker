import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { getKnowledgeRules, patchKnowledgeRule, reloadKnowledgeRules } from "@/api/client";
import type { KnowledgeRule, KnowledgeRuleListData, KnowledgeRulePatch } from "@/api/types";
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
