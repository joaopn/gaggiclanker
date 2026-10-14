import { toast } from "sonner";
import type { BoardRowView } from "@/api/types";
import { Button } from "@/components/ui/button";
import { isStaleConflict, useResolveConflict } from "@/hooks/useBoard";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import type { NameConflict } from "@/lib/board";

/** Buttons wrap their words: a label like "Keep <name> (rename it back on the machine)" is long. */
const WRAPPING = "h-auto min-h-8 max-w-full shrink whitespace-normal py-1 text-left";

/**
 * The choices under a conflict about a profile's name alone (the line above them says which).
 *
 * `taken`, the machine's name is another profile's: **Keep <name>** keeps the app's side (the next
 * sync renames the file back), and **Rename to …** opens the Name field, whose Rename also keeps
 * the app's side. `both`, renamed here and at the machine: **Keep <app name>** and **Keep <machine
 * name>**. Each is the conflict route, answered with the machine's file as the list saw it, so a
 * file that changed since is refused and the list re-reads. Nothing is sent to the machine here.
 */
export function NameConflictActions({
  entry,
  conflict,
  onRenameTo,
}: {
  entry: BoardRowView;
  conflict: NameConflict;
  onRenameTo: () => void;
}) {
  const resolve = useResolveConflict();
  const once = useSingleFlight();
  const choose = (keep: "app" | "machine") =>
    once((release) =>
      resolve.mutate(
        { rowId: entry.row.id, keep, contentHash: conflict.contentHash, nameOnly: true },
        {
          onError: (error) => {
            if (isStaleConflict(error)) {
              toast.error("The machine changed again since you looked, so nothing was done.", {
                description: "The line now says what its file is called.",
              });
            }
          },
          onSettled: release,
        },
      ),
    );
  return (
    <div className="flex flex-wrap gap-2" data-testid="name-conflict-actions">
      {conflict.kind === "taken" ? (
        <>
          <Button
            type="button"
            size="sm"
            className={WRAPPING}
            disabled={resolve.isPending}
            data-testid="keep-app-name"
            onClick={() => choose("app")}
          >
            Keep {conflict.appName} (rename it back on the machine)
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className={WRAPPING}
            disabled={resolve.isPending}
            data-testid="rename-to"
            onClick={onRenameTo}
          >
            Rename to …
          </Button>
        </>
      ) : (
        <>
          <Button
            type="button"
            size="sm"
            className={WRAPPING}
            disabled={resolve.isPending}
            data-testid="keep-app-name"
            onClick={() => choose("app")}
          >
            Keep {conflict.appName}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className={WRAPPING}
            disabled={resolve.isPending}
            data-testid="keep-machine-name"
            onClick={() => choose("machine")}
          >
            Keep {conflict.machineName}
          </Button>
        </>
      )}
    </div>
  );
}
