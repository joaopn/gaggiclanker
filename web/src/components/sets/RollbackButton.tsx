import type { ReactNode } from "react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { useRollbackSet } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";

/**
 * "Go back to that recipe", with the sentence that says what it will do.
 *
 * The confirm step is not ceremony: a roll back appends a version, and somebody
 * who expected it to *edit* the Set back would be surprised by a v6 appearing.
 * It also says the thing nobody should have to guess — nothing is sent to the
 * machine — because the restored version may well name a different profile, and
 * the one rule this app never bends is that the machine is written only from
 * the Sync page, by a person, on purpose.
 *
 * An inline strip rather than a dialog: it is two sentences and two buttons,
 * and it is rendered always and toggled with `hidden` so `aria-controls`
 * resolves.
 */
export function RollbackButton({
  setId,
  versionId,
  versionNo,
  label,
  icon,
}: {
  setId: number;
  versionId: number;
  versionNo: number;
  label: string;
  icon?: ReactNode;
}) {
  const rollback = useRollbackSet();
  const [open, setOpen] = useState(false);
  const panelId = useId();

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
              This records a new version with v{versionNo}'s recipe — the grind, the dose, the
              target and the profile it named. Nothing is sent to the machine.
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                disabled={rollback.isPending}
                onClick={async () => {
                  const done = await attempt(() =>
                    rollback.mutateAsync({
                      setId,
                      // No intent and no prediction from here: the intent is
                      // "go back", which the restored version already says, and
                      // a prediction is added afterwards like any other.
                      body: { to_version_id: versionId, intent: "", prediction: "" },
                    }),
                  );
                  if (done) setOpen(false);
                }}
              >
                Roll back to v{versionNo}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
