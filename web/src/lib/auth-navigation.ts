/**
 * A seam between the API client and the router.
 *
 * `fetchApi` has to send the user to the sign-in page on a 401, but it is a
 * plain module — it has no hooks and no router context. `App.tsx` registers
 * `useNavigate` here once; everything else calls `redirectToSignIn()` and gets
 * a client-side navigation instead of a full page load (which would throw away
 * the query cache and flash white).
 *
 * The path it builds is always in-app and never absolute: `next` is fed
 * straight back into the router by `SignInPage`, so an absolute URL here would
 * be an open redirect.
 */

type AuthNavigator = (nextPath: string | null) => void;

let authNavigator: AuthNavigator | null = null;

export function setAuthNavigator(navigator: AuthNavigator | null): void {
  authNavigator = navigator;
}

export function getCurrentAppPath(): string {
  if (typeof window === "undefined") return "/";
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

export function buildSignInPath(nextPath: string | null): string {
  const url = new URL("/sign-in", "http://localhost");
  if (nextPath && nextPath !== "/sign-in" && !nextPath.startsWith("/sign-in?")) {
    url.searchParams.set("next", nextPath);
  }
  return `${url.pathname}${url.search}`;
}

export function redirectToSignIn(nextPath: string | null = getCurrentAppPath()): void {
  if (authNavigator) {
    authNavigator(nextPath);
    return;
  }
  if (typeof window !== "undefined") {
    window.location.assign(buildSignInPath(nextPath));
  }
}
