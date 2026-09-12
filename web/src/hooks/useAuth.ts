import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { clearAuthSession, getAuthStatus, login, logout, setPassword } from "@/api/client";
import type { AuthStatusData, LoginData, PasswordData } from "@/api/types";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Whether this server wants a token, and whether we have a good one.
 *
 * `/api/auth/status` is the one public route under `/api`, and this hook is why
 * it exists: without it the app's only way to learn about auth is to fire a
 * request and be bounced by the 401 — which works, but flashes a page of empty
 * tables first and cannot show a sign-out control at all.
 *
 * `retry: 0` because the two answers this can give are both final; there is no
 * transient "maybe you are signed in".
 */
export function useAuthStatus(): UseQueryResult<AuthStatusData, Error> {
  return useQuery({
    queryKey: queryKeys.auth.status(),
    queryFn: getAuthStatus,
    retry: 0,
    staleTime: 60_000,
  });
}

/**
 * Sign in, then send the user where they were going.
 *
 * The cache is cleared first, not after: with auth on, everything already in it
 * was fetched as somebody else (or as nobody), and leaving it there would show
 * the previous session's data for one render.
 */
export function useSignIn(
  nextPath: string | null,
): UseMutationResult<LoginData, Error, { username: string; password: string }> {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: ({ username, password }) => login(username, password),
    onSuccess: () => {
      queryClient.clear();
      navigate(nextPath && nextPath !== "/sign-in" ? nextPath : "/shots", { replace: true });
    },
  });
}

/**
 * Revoke the session and go back to the form.
 *
 * `logout()` clears the local token even when the server call fails, so this
 * always ends signed out — a sign-out button that leaves you signed in because
 * the token had already expired is the worst of both.
 */
export function useSignOut(): UseMutationResult<void, Error, void> {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: logout,
    onSettled: () => {
      queryClient.clear();
      navigate("/sign-in", { replace: true });
    },
  });
}

/**
 * Change the sign-in password.
 *
 * The server revokes every session on success, this one included, so the local
 * token is dropped and the user is sent to sign in with the new password. That
 * is not a rough edge to smooth over — a password change that left the old
 * tokens working would be theatre, and the honest thing is to say so by asking
 * for the new one immediately.
 */
export function useSetPassword(): UseMutationResult<
  PasswordData,
  Error,
  { currentPassword?: string; newPassword: string }
> {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: setPassword,
    onSuccess: (data) => {
      clearAuthSession();
      queryClient.clear();
      // Only when a token was actually required. Setting the first password on
      // an open server must not bounce the user to a sign-in page that would
      // send them straight back.
      if (data.auth_required) navigate("/sign-in", { replace: true });
    },
  });
}
