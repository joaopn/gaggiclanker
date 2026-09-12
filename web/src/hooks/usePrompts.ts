import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { getPrompt, getPrompts, putPrompt, resetPrompt } from "@/api/client";
import type { PromptData, PromptListData } from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

export function usePrompts(): UseQueryResult<PromptListData, Error> {
  return useQuery({ queryKey: queryKeys.prompts.list(), queryFn: getPrompts });
}

export function usePrompt(name: string | null): UseQueryResult<PromptData, Error> {
  return useQuery({
    queryKey: queryKeys.prompts.detail(name ?? ""),
    queryFn: () => getPrompt(name ?? ""),
    enabled: Boolean(name),
  });
}

/**
 * No optimistic update, on purpose. The server validates the YAML and refuses
 * a broken document, so showing the edit as saved before it answered would
 * show the one state that is definitely wrong.
 */
export function useSavePrompt(): UseMutationResult<
  PromptData,
  Error,
  { name: string; content: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, content }) => putPrompt(name, content),
    onSuccess: (prompt) => {
      queryClient.setQueryData(queryKeys.prompts.detail(prompt.name), prompt);
      void queryClient.invalidateQueries({ queryKey: queryKeys.prompts.list() });
      toast.success(`Saved ${prompt.name}`);
    },
    onError: (error) => toast.error(error.message),
  });
}

export function useResetPrompt(): UseMutationResult<PromptData, Error, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => resetPrompt(name),
    onSuccess: (prompt) => {
      queryClient.setQueryData(queryKeys.prompts.detail(prompt.name), prompt);
      void queryClient.invalidateQueries({ queryKey: queryKeys.prompts.list() });
      toast.success(`${prompt.name} is back to the version that shipped`);
    },
    onError: (error) => toast.error(error.message),
  });
}
