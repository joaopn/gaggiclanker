import { useEffect, useRef, useState } from "react";
import { getHealth } from "@/api/client";

/** How long the restart overlay waits for the app before it says to start it by hand. */
export const RESTART_GIVE_UP_MS = 60_000;
const POLL_MS = 1_000;

/**
 * Covers the page while the app restarts, and reloads it when the app is back.
 *
 * "Back" means it answered after it had stopped answering: the app still answers for the
 * moment between the reply and its shutdown, and a reload then would land on the dying
 * process. A plain fixed div: no dialog primitive, so nothing here needs a portal.
 */
export function RestartOverlay({
  reload = () => window.location.reload(),
  heading = "Restoring… the app is restarting.",
}: {
  reload?: () => void;
  /** What is happening, in the words of the action that asked for the restart. */
  heading?: string;
}) {
  const [gaveUp, setGaveUp] = useState(false);
  const sawDown = useRef(false);

  useEffect(() => {
    let stopped = false;
    async function poll() {
      try {
        await getHealth();
        if (!stopped && sawDown.current) {
          stopped = true;
          reload();
        }
      } catch {
        sawDown.current = true;
      }
    }
    const timer = setInterval(() => void poll(), POLL_MS);
    const giveUp = setTimeout(() => setGaveUp(true), RESTART_GIVE_UP_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
      clearTimeout(giveUp);
    };
  }, [reload]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 p-6"
      role="alert"
      data-testid="restart-overlay"
    >
      <div className="max-w-md space-y-2 text-center">
        {gaveUp ? (
          <p>
            The app has not come back. If it does not run under Docker (restart: unless-stopped),
            start it again by hand.
          </p>
        ) : (
          <>
            <p className="font-medium">{heading}</p>
            <p className="text-muted-foreground text-sm">This page reloads by itself.</p>
          </>
        )}
      </div>
    </div>
  );
}
