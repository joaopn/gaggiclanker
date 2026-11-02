import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  approveSignatureRequest,
  declineSignatureRequest,
  getSignatureRequests,
} from "@/api/client";
import type { SignatureRequestAnswer, SignatureRequestList } from "@/api/types";
import { invalidateChatThread, invalidateSignatureAnswers } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * The signature changes one conversation's agent asked for: what the chat's request card reads.
 * By conversation and uncapped, so a card finds its request however old it is; a card drawn with
 * no conversation around it reads the requests made for its profile version instead.
 */
export function useSignatureRequests(
  threadId: number | null,
  profileVersionId: number,
): UseQueryResult<SignatureRequestList, Error> {
  return useQuery({
    queryKey:
      threadId !== null
        ? queryKeys.signatureRequests.thread(threadId)
        : queryKeys.signatureRequests.version(profileVersionId),
    queryFn: () => getSignatureRequests(threadId !== null ? { threadId } : { profileVersionId }),
  });
}

/**
 * Approve or decline a request: the card's buttons.
 *
 * An approval changes the signature, so it settles with `invalidateSignatureAnswers` (the
 * signature, the request lists, every shot's fields and list rows, the Recipe pages that brew
 * the profile) beside the conversation. It does so on failure as well: a 409 means the request
 * went stale or was answered elsewhere, and the card must show what happened. Nothing is sent
 * to the agent as a message; it is told at the start of its next turn, from the record.
 */
export function useAnswerSignatureRequest(): UseMutationResult<
  SignatureRequestAnswer,
  Error,
  { requestId: number; answer: "approve" | "decline"; reason?: string; threadId: number | null }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ requestId, answer, reason }) =>
      answer === "approve"
        ? approveSignatureRequest(requestId)
        : declineSignatureRequest(requestId, reason ?? ""),
    onSuccess: (result, variables) =>
      toast.success(
        variables.answer === "approve"
          ? // A change of the Recipe version's own limit is not a change of the signature.
            result.request.changes.every((c) => c.kind.endsWith("_override"))
            ? "Approved — this Recipe version's limit has changed"
            : "Approved — the signature has changed"
          : "Declined — nothing changed, and the agent is told",
      ),
    onSettled: (_data, _error, variables) => {
      void invalidateSignatureAnswers(queryClient);
      if (variables.threadId !== null) {
        void invalidateChatThread(queryClient, String(variables.threadId));
      }
    },
  });
}
