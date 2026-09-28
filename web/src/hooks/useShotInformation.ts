import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { getShotInformation, putShotInformationTier, resetShotInformation } from "@/api/client";
import type { ShotInformation, ShotInfoTier } from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Every item of shot information, its tier, its example and the cost.
 *
 * One document the server answers whole to every route, so a write puts its
 * answer straight into the cache: the estimates are measured by the server
 * on the example shot and cannot be recomputed here.
 */
export function useShotInformation(): UseQueryResult<ShotInformation, Error> {
  return useQuery({
    queryKey: queryKeys.settings.shotInformation(),
    queryFn: getShotInformation,
  });
}

/**
 * The writes run one at a time, in the order they were clicked. Each answers
 * the whole document, so a later click's answer already carries every earlier
 * change; if two ran at once, the slower answer could land last and put an item
 * back on screen in the tier it had just left.
 */
const SCOPE = { id: "shot-information" };

/**
 * Move one item to a tier. Not optimistic: the row shows the tier it is
 * saving as pending, and the page takes the server's document when it answers.
 * A refusal leaves the cache as it was, so the control is back on the stored
 * tier, and the row shows the error itself.
 */
export function useSetShotInfoTier(): UseMutationResult<
  ShotInformation,
  Error,
  { key: string; tier: ShotInfoTier }
> {
  const queryClient = useQueryClient();
  return useMutation({
    scope: SCOPE,
    mutationFn: ({ key, tier }) => putShotInformationTier(key, tier),
    onSuccess: (document) => {
      queryClient.setQueryData(queryKeys.settings.shotInformation(), document);
    },
  });
}

/** Every item back in its default tier. */
export function useResetShotInformation(): UseMutationResult<ShotInformation, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    scope: SCOPE,
    mutationFn: () => resetShotInformation(),
    onSuccess: (document) => {
      queryClient.setQueryData(queryKeys.settings.shotInformation(), document);
    },
  });
}
