import { useRef, useState } from "react";
import { ApiClientError, resetApp } from "@/api/client";
import { Button } from "@/components/ui/button";
import { RestartOverlay } from "@/pages/settings/RestartOverlay";

/** What a refusal means to the person, by the server's stable code. Nothing has changed. */
export function resetRefusalText(error: unknown): string {
  const code = error instanceof ApiClientError ? error.code : undefined;
  switch (code) {
    case "RESET_BUSY":
    case "RESTORE_BUSY":
      return "A sync, a chat answer or a review is running. Try again when it finishes.";
    case "RESET_PENDING":
      return "A reset is already under way.";
    case "RESTORE_PENDING":
      return "A restore is already under way.";
    default:
      return error instanceof Error
        ? `The app can't be reset: ${error.message}`
        : "The app can't be reset.";
  }
}

type Phase = "idle" | "confirm" | "restarting";

/**
 * Put the app back to a fresh install: ask, confirm inline, restart.
 *
 * One meaning: this button empties the app. The confirm is a plain second button (no typing,
 * no dialog) that says what goes and what the app is like afterwards.
 */
export function ResetSection() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const sending = useRef(false);

  async function confirm() {
    // A ref, not the button's disabled state: a double click lands before React re-renders.
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setRefusal(null);
    try {
      await resetApp();
      setPhase("restarting");
    } catch (error) {
      sending.current = false;
      setRefusal(resetRefusalText(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {refusal && (
        <p className="text-destructive text-sm" role="alert">
          {refusal}
        </p>
      )}
      {phase === "idle" && (
        <>
          <p className="text-muted-foreground text-sm">
            Puts the app back to a fresh install: every shot, Set, bean, profile, chat, insight,
            edited prompt and setting is deleted. Nothing on the machine changes.
          </p>
          <p className="text-muted-foreground text-sm">
            Download a backup above first if you want to keep any of it.
          </p>
          <Button
            variant="outline"
            type="button"
            onClick={() => {
              setRefusal(null);
              setPhase("confirm");
            }}
          >
            Reset the app…
          </Button>
        </>
      )}
      {phase === "confirm" && (
        <div className="space-y-3 text-sm">
          <p className="font-medium">
            This deletes everything in the app and restarts it. It cannot be undone.
          </p>
          <ul className="list-disc pl-5">
            <li>Your API keys and tokens are deleted.</li>
            <li>
              Sign-in is switched off: anyone who can reach the app can use it until you set a
              password again.
            </li>
            <li>The app restarts, which takes a few seconds.</li>
          </ul>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => void confirm()}
            >
              Reset and restart
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => {
                setRefusal(null);
                setPhase("idle");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
      {phase === "restarting" && <RestartOverlay heading="Resetting… the app is restarting." />}
    </div>
  );
}
