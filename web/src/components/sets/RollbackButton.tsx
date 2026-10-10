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
/**
 * The button that opens the going-back confirmation. Split from the confirmation itself
 * (`RollbackPanel`) so a row of version buttons can hold this one beside others while the
 * confirmation opens below the whole row.
 */
export function RollbackTrigger({
  open,
  panelId,
  onToggle,
  label,
  icon,
}: {
  open: boolean;
  panelId: string;
  onToggle: () => void;
  label: string;
  icon?: ReactNode;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      data-testid="rollback"
      aria-expanded={open}
      aria-controls={panelId}
      onClick={onToggle}
    >
      {icon}
      {label}
    </Button>
  );
}

/** The confirmation: always rendered and toggled with `hidden`, so `aria-controls` resolves. */
export function RollbackPanel({
  setId,
  versionId,
  versionLabel,
  open,
  panelId,
  onClose,
}: {
  setId: number;
  versionId: number;
  /** The name of the version gone back to, "v1.2". */
  versionLabel: string;
  open: boolean;
  panelId: string;
  onClose: () => void;
}) {
  const rollback = useRollbackSet();
  const [note, setNote] = useState("");
  const noteId = useId();
  const close = () => {
    onClose();
    setNote("");
  };
  return (
    <div
      id={panelId}
      hidden={!open}
      className="mt-2 space-y-2 rounded-md border border-border bg-muted/30 p-2"
    >
      {open ? (
        <>
          <p className="text-sm" data-testid="rollback-confirm">
            This puts the Set back on {versionLabel}, as it was, with its prediction and shots. New
            shots for this Set count toward {versionLabel} and your next change continues from it.
            The log gets one line saying you went back. Nothing is sent to the machine.
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
                if (done) close();
              }}
            >
              Go back to {versionLabel}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={close}>
              Cancel
            </Button>
          </div>
        </>
      ) : null}
    </div>
  );
}

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
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <div>
      <RollbackTrigger
        open={open}
        panelId={panelId}
        onToggle={() => setOpen((shown) => !shown)}
        label={label}
        icon={icon}
      />
      <RollbackPanel
        setId={setId}
        versionId={versionId}
        versionLabel={versionLabel}
        open={open}
        panelId={panelId}
        onClose={() => setOpen(false)}
      />
    </div>
  );
}
