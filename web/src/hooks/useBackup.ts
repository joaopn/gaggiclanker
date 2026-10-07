import { type UseMutationResult, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { downloadBackup } from "@/api/client";

/** Download the whole app as one file; the argument says whether API keys and tokens go in it. */
export function useDownloadBackup(): UseMutationResult<string, Error, boolean> {
  return useMutation({
    mutationFn: (includeKeys: boolean) => downloadBackup(includeKeys),
    onSuccess: (name) => {
      toast.success(`Backup downloaded: ${name}`);
    },
    onError: (error) => {
      toast.error(`Backup failed: ${error.message}`);
    },
  });
}
