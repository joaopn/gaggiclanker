import { AlertTriangle, Coffee } from "lucide-react";
import type { ShotListRow } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useShots, useSyncStatus } from "@/hooks/useArchive";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";

/**
 * The archive, as a table.
 *
 * Deliberately plain: the real shot page comes later, with curves, diagnostics
 * and filters. What this proves now is the whole path — the sync engine writes
 * SQLite, `/api/shots` reads it, `/api/sync/events` invalidates this query, and
 * a shot pulled on the machine appears here without a refresh.
 */

const PAGE_SIZE = 50;

function formatTime(value: string | null | undefined): string {
  // A shot from a machine whose clock never synced has no timestamp at all
  // (`startEpoch < 10000`); saying so is better than rendering 1970.
  if (!value) return "no clock";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatGrams(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(1)} g`;
}

function formatScore(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(1);
}

function ShotRow({ shot }: { shot: ShotListRow }) {
  return (
    <tr className="border-border border-b last:border-0 hover:bg-muted/40">
      <td className="py-2 pr-4 whitespace-nowrap tabular-nums">{formatTime(shot.started_at)}</td>
      <td className="py-2 pr-4">
        <span className="block max-w-[16rem] truncate">
          {shot.profile_label ?? shot.profile_name_on_device ?? "—"}
        </span>
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">{formatSeconds(shot.duration_ms)}</td>
      <td className="py-2 pr-4 text-right tabular-nums">{formatGrams(shot.volume_g)}</td>
      <td className="py-2 pr-4 text-right tabular-nums">{formatScore(shot.execution_score)}</td>
      <td className="py-2 pr-4 text-right tabular-nums">{shot.rating ?? "—"}</td>
      <td className="py-2">
        <div className="flex flex-wrap gap-1">
          {shot.quarantined ? (
            <Badge variant="destructive" className="gap-1">
              <AlertTriangle className="size-3" aria-hidden="true" />
              quarantined
            </Badge>
          ) : null}
          {shot.deleted_on_device ? <Badge variant="outline">gone from machine</Badge> : null}
          {shot.incomplete ? <Badge variant="outline">incomplete</Badge> : null}
          {/* Only worth saying when it is not the usual answer. */}
          {shot.source === "import" ? <Badge variant="secondary">imported</Badge> : null}
        </div>
      </td>
    </tr>
  );
}

export function ShotsPage() {
  const shots = useShots({ limit: PAGE_SIZE });
  const sync = useSyncStatus();

  useQueryErrorToast(shots.error, "Could not load shots");

  const counts = sync.data?.counts;
  const subtitle = counts
    ? `${counts.total} archived · ${counts.samples.toLocaleString()} samples` +
      (counts.quarantined ? ` · ${counts.quarantined} quarantined` : "")
    : "Every shot the machine has pulled.";

  return (
    <div className="space-y-6">
      <PageHeader title="Shots" subtitle={subtitle} />

      {shots.isPending ? (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((row) => (
            <Skeleton key={row} className="h-8 w-full" />
          ))}
        </div>
      ) : shots.data && shots.data.items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
              <tr>
                <th className="py-2 pr-4 font-medium">Time</th>
                <th className="py-2 pr-4 font-medium">Profile</th>
                <th className="py-2 pr-4 text-right font-medium">Duration</th>
                <th className="py-2 pr-4 text-right font-medium">Volume</th>
                <th className="py-2 pr-4 text-right font-medium">Score</th>
                <th className="py-2 pr-4 text-right font-medium">Rating</th>
                <th className="py-2 font-medium">Flags</th>
              </tr>
            </thead>
            <tbody>
              {shots.data.items.map((shot) => (
                <ShotRow key={shot.id} shot={shot} />
              ))}
            </tbody>
          </table>
          {shots.data.total > shots.data.items.length ? (
            <p className="mt-3 text-muted-foreground text-xs">
              Showing {shots.data.items.length} of {shots.data.total}. Paging and the curve view
              arrive later.
            </p>
          ) : null}
        </div>
      ) : (
        <EmptyState
          icon={Coffee}
          title="No shots archived yet"
          description={
            sync.data?.configured
              ? "The sync engine is running. A shot appears here within seconds of the machine finishing it."
              : "No machine is configured. Set `gaggimateHost` in Settings, or import shots exported from the machine's web UI."
          }
        />
      )}
    </div>
  );
}
