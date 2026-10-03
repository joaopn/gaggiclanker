import type { ReactNode } from "react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { useRollbackSet } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";

/**
 * "Go back to this version", with the sentence that says what it will do.
 *
 * The confirm step is not ceremony: going back writes no version, it puts the
 * Set on that one again, and the next change continues its line. It takes an
 * optional note for the log's "Went back" line and nothing else: no prediction,
 * since the version already has one or has shots. It also says the thing nobody
 * should have to guess — nothing is sent to the machine — because the restored version may well name a different profile, and
 * the one rule this app never bends is that the only thing it writes to the
 * machine is a profile, pushed by a person, on purpose.
 *
 * An inline strip rather than a dialog: it is two sentences and two buttons,
 * and it is rendered always and toggled with `hidden` so `aria-controls`
 * resolves.
 */
export function RollbackButton({
  setId,
  versionId,
  versionLabel,
  label,
  icon,
}: {
  setId: number;
  versionId: number;
  /** The name of the version gone back to, "v1.2". */
  versionLabel: string;
  label: string;
  icon?: ReactNode;
}) {
  const rollback = useRollbackSet();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const panelId = useId();
  const noteId = useId();

  return (
    <div data-testid="rollback">
      <Button
        type="button"
        size="sm"
        variant="outline"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((shown) => !shown)}
      >
        {icon}
        {label}
      </Button>
      <div
        id={panelId}
        hidden={!open}
        className="mt-2 space-y-2 rounded-md border border-border bg-muted/30 p-2"
      >
        {open ? (
          <>
            <p className="text-sm" data-testid="rollback-confirm">
              This puts the Set back on {versionLabel}, as it was, with its prediction and shots.
              New shots for this Set count toward {versionLabel} and your next change continues from
              it. The log gets one line saying you went back. Nothing is sent to the machine.
            </p>
            <div>
              <label htmlFor={noteId} className="mb-1 block text-muted-foreground text-xs">
                Why? (optional)
              </label>
              <input
                id={noteId}
                maxLength={500}
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="the finer grind made it bitter"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                disabled={rollback.isPending}
                onClick={async () => {
                  const done = await attempt(() =>
                    rollback.mutateAsync({
                      setId,
                      // A note and nothing else: a revert takes no prediction.
                      body: { to_version_id: versionId, note: note.trim() },
                    }),
                  );
                  if (done) {
                    setOpen(false);
                    setNote("");
                  }
                }}
              >
                Go back to {versionLabel}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setOpen(false);
                  setNote("");
                }}
              >
                Cancel
              </Button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
