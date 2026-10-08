import {
  type QueryClient,
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  getSignature,
  getSignatureOverrides,
  rejectExpectation,
  rejectSignatureOverride,
  restoreExpectation,
  restoreSignatureOverride,
  setExpectationTier,
} from "@/api/client";
import type {
  SignatureAnswer,
  SignatureData,
  SignatureOverrideAnswer,
  SignatureOverrideList,
  SignatureTier,
} from "@/api/types";
import { invalidateSignatureAnswers } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";

/**
 * What a profile version is for (in force until a person rejects it), and the person's answers.
 *
 * Every mutation here settles with `invalidateSignatureAnswers`, on failure as well as on
 * success: an answer moves every shot's checks and every Set that brews the profile, and a
 * refused one (the expectation was answered in another tab) has to show the answer that won.
 */

export function useSignature(
  versionId: number | undefined,
  options: { enabled?: boolean } = {},
): UseQueryResult<SignatureData, Error> {
  const { enabled = true } = options;
  return useQuery({
    queryKey: queryKeys.signatures.version(versionId ?? 0),
    queryFn: () => getSignature(versionId as number),
    enabled: enabled && versionId !== undefined,
  });
}

export function useSignatureOverrides(
  setId: number,
  versionId: number,
  options: { enabled?: boolean } = {},
): UseQueryResult<SignatureOverrideList, Error> {
  return useQuery({
    queryKey: queryKeys.setOverrides(setId, versionId),
    queryFn: () => getSignatureOverrides(setId, versionId),
    enabled: options.enabled ?? true,
  });
}

function answerMutation<Vars, Out>(
  mutationFn: (vars: Vars) => Promise<Out>,
  /** What the answer already says, put in the cache before the refetch lands. */
  remember?: (out: Out, queryClient: QueryClient) => void,
): () => UseMutationResult<Out, Error, Vars> {
  return function useAnswer() {
    const queryClient = useQueryClient();
    return useMutation({
      mutationFn,
      onSuccess: (out) => remember?.(out, queryClient),
      onError: (error) => toast.error(error.message),
      onSettled: () => void invalidateSignatureAnswers(queryClient),
    });
  };
}

/**
 * The answer carries the signature as it now stands, so the card shows it at once instead of
 * waiting for the refetch the invalidation starts.
 */
function rememberSignature(answer: SignatureAnswer, queryClient: QueryClient): void {
  queryClient.setQueryData(
    queryKeys.signatures.version(answer.signature.profile_version_id),
    answer.signature,
  );
}

export const useRejectExpectation = answerMutation<
  { expectationId: number; reason: string },
  SignatureAnswer
>(({ expectationId, reason }) => rejectExpectation(expectationId, reason), rememberSignature);

export const useSetExpectationTier = answerMutation<
  { expectationId: number; tier: SignatureTier },
  SignatureAnswer
>(({ expectationId, tier }) => setExpectationTier(expectationId, tier), rememberSignature);

export const useRestoreExpectation = answerMutation<{ expectationId: number }, SignatureAnswer>(
  ({ expectationId }) => restoreExpectation(expectationId),
  rememberSignature,
);

export const useRejectOverride = answerMutation<
  { setId: number; overrideId: number; reason: string },
  SignatureOverrideAnswer
>(({ setId, overrideId, reason }) => rejectSignatureOverride(setId, overrideId, reason));

export const useRestoreOverride = answerMutation<
  { setId: number; overrideId: number },
  SignatureOverrideAnswer
>(({ setId, overrideId }) => restoreSignatureOverride(setId, overrideId));
