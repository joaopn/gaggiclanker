import type { ReactNode } from "react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { useRollbackSet } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";

/**
 * "Go back to that recipe", with the sentence that says what it will do.
 *
 * The confirm step is not ceremony: going back writes no version, it puts the
 * Set on that one again, and the next change continues its line. It also says the thing nobody should have to guess — nothing is sent to the
 * machine — because the restored version may well name a different profile, and
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
              This puts the Set back on {versionLabel}, as it was. No version is written, new shots
              are filed under {versionLabel}, and the next change continues from it. Nothing is sent
              to the machine.
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
                      // No note and no prediction from here: a revert takes none.
                      body: { to_version_id: versionId, note: "" },
                    }),
                  );
                  if (done) setOpen(false);
                }}
              >
                Roll back to {versionLabel}
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
