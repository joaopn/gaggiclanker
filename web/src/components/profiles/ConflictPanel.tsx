import { AlertTriangle } from "lucide-react";
import { useRef } from "react";
import type { ConflictView } from "@/api/types";
import { diffProfiles } from "@/components/drafts/ProfileDiff";
import { ProfileSummary } from "@/components/drafts/ProfileSummary";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { isStaleConflict, useBoardConflict, useResolveConflict } from "@/hooks/useBoard";
import { cn } from "@/lib/utils";

type Json = Record<string, unknown>;

/**
 * A profile whose file on the machine was edited outside the app: the app's active version and
 * the machine's version side by side, and a choice. Two columns from `md` up, stacked below;
 * the fields that differ are marked on both sides.
 *
 * The request carries the machine file's hash as shown here, so a file that changed again
 * since is refused ("the machine changed again") and the panel re-reads and shows the new one.
 * Either choice keeps the machine's content as a version, so nothing is lost.
 */
export function ConflictPanel({ rowId }: { rowId: number }) {
  const conflict = useBoardConflict(rowId, true);
  const resolve = useResolveConflict();
  // Gated on the profile the mutation was for: the page keeps its state across rows.
  const mine = resolve.variables?.rowId === rowId;
  const stale = mine && resolve.isError && isStaleConflict(resolve.error);
  const clicking = useRef(false);

  if (conflict.isPending) return <Skeleton className="h-24 w-full" />;
  const view = conflict.data;
  if (!view) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="conflict-none">
        {conflict.isError
          ? "The conflict could not be read right now."
          : "There is no conflict to resolve any more."}
      </p>
    );
  }

  const choose = (keep: "app" | "machine") => {
    if (clicking.current) return;
    clicking.current = true;
    resolve.mutate(
      { rowId, keep, contentHash: view.machine.content_hash },
      {
        onSettled: () => {
          clicking.current = false;
        },
      },
    );
  };

  return (
    <section
      aria-label={`Conflict on ${view.label}`}
      className="min-w-0 space-y-3 rounded-md border border-destructive/40 bg-destructive/5 p-3"
      data-testid="conflict-panel"
    >
      <div>
        <p className="flex items-center gap-1.5 font-medium text-sm">
          <AlertTriangle className="size-3.5 text-destructive" aria-hidden="true" />
          The machine's copy differs from the app's
        </p>
        <p className="mt-1 text-muted-foreground text-xs">
          Someone edited {view.label} on the machine, or made another profile with its name. The app
          holds the ground truth, so a sync leaves this profile alone until you choose. Either way
          the machine's version is kept as a version of the profile.
        </p>
      </div>

      {stale ? (
        <p className="text-sm text-status-warn-text" role="alert" data-testid="conflict-stale">
          The machine changed again since you looked, so nothing was done. This is its version now;
          choose again.
        </p>
      ) : null}

      <Sides
        app={{
          title: "The app's version",
          hash: view.app_short_hash,
          profile: view.app_profile ?? {},
        }}
        machine={{
          title: "The machine's version",
          hash: view.machine.short_hash,
          profile: view.machine_profile ?? {},
        }}
      />

      <div className="grid gap-2 md:grid-cols-2">
        <div className="space-y-1">
          <Button
            size="sm"
            className="w-full"
            disabled={resolve.isPending}
            data-testid="keep-app"
            onClick={() => choose("app")}
          >
            Keep the app's
          </Button>
          <p className="text-muted-foreground text-xs">
            The next sync replaces the machine's file with the app's active version.
          </p>
        </div>
        <div className="space-y-1">
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            disabled={resolve.isPending}
            data-testid="keep-machine"
            onClick={() => choose("machine")}
          >
            Keep the machine's
          </Button>
          <p className="text-muted-foreground text-xs">
            The machine's version becomes the profile's active version. Nothing is sent to the
            machine.
          </p>
        </div>
      </div>
    </section>
  );
}

type Side = { title: string; hash: string; profile: Json };

function Sides({ app, machine }: { app: Side; machine: Side }) {
  const changes = diffProfiles(app.profile, machine.profile);
  return (
    <div className="grid gap-3 md:grid-cols-2" data-testid="conflict-sides">
      {[
        { side: app, key: "app" as const },
        { side: machine, key: "machine" as const },
      ].map(({ side, key }) => (
        <div
          key={key}
          className="min-w-0 space-y-2 rounded-md border border-border bg-background p-3"
          data-testid={`conflict-${key}`}
        >
          <p className="font-medium text-sm">
            {side.title}{" "}
            <span className="font-mono text-muted-foreground text-xs">{side.hash}</span>
          </p>
          <ProfileSummary profile={side.profile} />
          <div>
            <h4 className="mb-1 font-medium text-xs uppercase tracking-wide">Differences</h4>
            {changes.length === 0 ? (
              <p className="text-muted-foreground text-xs">
                The documents differ in details this list does not show.
              </p>
            ) : (
              <ul className="space-y-1">
                {changes.map((change) => (
                  <li
                    key={change.path}
                    data-testid="conflict-difference"
                    className={cn(
                      "break-words rounded px-1.5 py-0.5 text-sm",
                      key === "app" ? "bg-status-ok/10" : "bg-status-warn/15",
                    )}
                  >
                    <span className="font-mono text-muted-foreground text-xs">{change.label}</span>{" "}
                    <span className="font-medium">
                      {(key === "app" ? change.before : change.after) ?? "—"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export type { ConflictView };
