import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuthStatus, useSignIn } from "@/hooks/useAuth";

/**
 * The one page outside `AppShell`.
 *
 * No sidebar and no header, because neither is usable without a token: every
 * query behind them would 401 and bounce straight back here. It is also the
 * only page the API client's 401 handler can navigate to, so it must render
 * with nothing loaded.
 *
 * `?next=` is where the user was going before the 401. It is used verbatim as
 * a client-side path and never as a URL — an attacker-supplied `next` that
 * could be absolute would make this an open redirect, and
 * `lib/auth-navigation.ts` only ever puts an in-app path there.
 */
export function SignInPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const nextPath = searchParams.get("next");
  const status = useAuthStatus();
  const signIn = useSignIn(nextPath);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // A server with auth switched off has no sign-in to offer, and landing here
  // (a stale bookmark, a link from before it was turned off) should not leave
  // someone staring at a form that cannot do anything.
  const openServer = status.data?.auth_required === false;
  useEffect(() => {
    if (openServer) navigate("/shots", { replace: true });
  }, [openServer, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6">
          <h1 className="font-semibold text-2xl tracking-tight">gaggiclanker</h1>
          <p className="mt-1 text-muted-foreground text-sm">
            This archive is protected. Sign in to continue.
          </p>
        </div>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            signIn.mutate({ username, password });
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="username">Username</Label>
            <Input
              id="username"
              name="username"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {signIn.isError ? (
            // The server's own message, which is deliberately the same for a
            // wrong username and a wrong password, plus the throttle's "try
            // again in N seconds" when it is that.
            <p role="alert" className="text-destructive text-sm">
              {signIn.error.message}
            </p>
          ) : null}

          <Button type="submit" className="w-full" disabled={signIn.isPending}>
            {signIn.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        <p className="mt-6 text-muted-foreground text-xs">
          Set <code>AUTH_USER</code> and <code>AUTH_PASSWORD</code> in <code>.env</code> to change
          these, or turn authentication off by clearing them.
        </p>
      </div>
    </div>
  );
}
