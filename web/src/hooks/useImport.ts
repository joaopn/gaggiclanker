import { type UseMutationResult, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { importFiles } from "@/api/client";
import type { ImportOptions, ImportSummary } from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

export type ImportRequest = { files: File[] } & ImportOptions;

/**
 * Upload export files and refresh whatever they changed.
 *
 * No optimistic update, unlike the settings mutation: nothing here is known
 * until the server has read the files, and a per-file result list is precisely
 * the thing that cannot be guessed. The archive queries are invalidated
 * afterwards rather than patched, for the same reason the SSE events carry no
 * payload — the server is the authority on what landed.
 *
 * A file that failed is *not* a failed mutation: it comes back in `items` with
 * `status: "failed"`, and the page renders it beside the ones that worked.
 */
export function useImportFiles(): UseMutationResult<ImportSummary, Error, ImportRequest> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ files, ...options }: ImportRequest) => importFiles(files, options),
    onSuccess: (summary) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.shots.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.profiles.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.all });
      const landed = summary.created + summary.updated;
      if (summary.failed > 0) {
        toast.error(`${summary.failed} file(s) could not be imported`);
      } else if (landed > 0) {
        toast.success(`Imported ${landed} of ${summary.items.length} file(s)`);
      } else {
        toast.info("Everything in that batch is already in the archive");
      }
    },
    onError: (error) => {
      toast.error(error.message || "Import failed");
    },
  });
}
