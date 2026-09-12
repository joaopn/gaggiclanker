import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuthStatus, useSignOut } from "@/hooks/useAuth";

/**
 * The header's sign-out control. Renders nothing at all when auth is off.
 *
 * cvclanker shipped `api.logout()` with no caller and no button, which meant
 * the only way to end a
 * session was to clear site data. This is the missing half: one click, and the
 * session row is revoked server-side, so the token is dead everywhere rather
 * than merely forgotten in this browser.
 */
export function SignOutButton() {
  const status = useAuthStatus();
  const signOut = useSignOut();

  if (!status.data?.auth_required || !status.data.authenticated) return null;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Sign out"
          disabled={signOut.isPending}
          onClick={() => signOut.mutate()}
        >
          <LogOut className="size-4" aria-hidden="true" />
          <span className="hidden sm:inline">Sign out</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent>Signed in as {status.data.user}</TooltipContent>
    </Tooltip>
  );
}
