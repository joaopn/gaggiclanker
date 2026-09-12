import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { createBackup, getSettings, patchSettings } from "@/api/client";
import type { BackupData, SettingsMap, SettingsPatch } from "@/api/types";
import { invalidateSettings } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

export function useSettings(): UseQueryResult<SettingsMap, Error> {
  return useQuery({ queryKey: queryKeys.settings.current(), queryFn: getSettings });
}

/**
 * The mutation template for the whole app: optimistic write, rollback on
 * failure, invalidate on settle.
 *
 * `onMutate` cancels in-flight reads first — otherwise a response that started
 * before the write lands after it and silently restores the old value. The
 * snapshot it returns is the rollback; `onSettled` re-reads regardless of
 * outcome, because the server is the authority on what a secret's hint became.
 */
export function useUpdateSettings(): UseMutationResult<
  SettingsMap,
  Error,
  SettingsPatch,
  { previous: SettingsMap | undefined }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: patchSettings,
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.settings.current() });
      const previous = queryClient.getQueryData<SettingsMap>(queryKeys.settings.current());
      if (previous) {
        const next: SettingsMap = { ...previous };
        for (const [key, value] of Object.entries(patch)) {
          const current = next[key];
          // A secret never shows its value, so there is nothing to preview
          // optimistically — leave it for the server's answer.
          if (!current || current.secret) continue;
          next[key] = { ...current, value, override: value, source: "database" };
        }
        queryClient.setQueryData(queryKeys.settings.current(), next);
      }
      return { previous };
    },
    onError: (_error, _patch, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKeys.settings.current(), context.previous);
      }
    },
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.settings.current(), data);
      toast.success("Settings saved");
    },
    onSettled: () => {
      void invalidateSettings(queryClient);
    },
  });
}

export function useCreateBackup(): UseMutationResult<BackupData, Error, void> {
  return useMutation({
    mutationFn: () => createBackup(),
    onSuccess: (data) => {
      toast.success(`Backup written: ${data.filename}`);
    },
    onError: (error) => {
      toast.error(`Backup failed: ${error.message}`);
    },
  });
}
