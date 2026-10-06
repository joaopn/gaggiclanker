import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  confirmAllExpectations,
  confirmExpectation,
  confirmSignatureOverride,
  getSignature,
  getSignatureOverrides,
  rejectExpectation,
  rejectSignatureOverride,
  setExpectationTier,
  withdrawSignatureOverride,
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
 * What a profile version is for, and the person's answers to what an agent proposed.
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
): () => UseMutationResult<Out, Error, Vars> {
  return function useAnswer() {
    const queryClient = useQueryClient();
    return useMutation({
      mutationFn,
      onError: (error) => toast.error(error.message),
      onSettled: () => void invalidateSignatureAnswers(queryClient),
    });
  };
}

export const useConfirmExpectation = answerMutation<{ expectationId: number }, SignatureAnswer>(
  ({ expectationId }) => confirmExpectation(expectationId),
);

export const useRejectExpectation = answerMutation<
  { expectationId: number; reason: string },
  SignatureAnswer
>(({ expectationId, reason }) => rejectExpectation(expectationId, reason));

export const useSetExpectationTier = answerMutation<
  { expectationId: number; tier: SignatureTier },
  SignatureAnswer
>(({ expectationId, tier }) => setExpectationTier(expectationId, tier));

/** One call for the whole version: not one per expectation, so it is all or nothing. */
export const useConfirmAllExpectations = answerMutation<{ versionId: number }, SignatureAnswer>(
  ({ versionId }) => confirmAllExpectations(versionId),
);

export const useConfirmOverride = answerMutation<
  { setId: number; overrideId: number },
  SignatureOverrideAnswer
>(({ setId, overrideId }) => confirmSignatureOverride(setId, overrideId));

export const useRejectOverride = answerMutation<
  { setId: number; overrideId: number; reason: string },
  SignatureOverrideAnswer
>(({ setId, overrideId, reason }) => rejectSignatureOverride(setId, overrideId, reason));

export const useWithdrawOverride = answerMutation<
  { setId: number; overrideId: number },
  SignatureOverrideAnswer
>(({ setId, overrideId }) => withdrawSignatureOverride(setId, overrideId));
