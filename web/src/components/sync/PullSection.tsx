import { SectionCard } from "@/components/layout/SectionCard";
import { PullButton } from "@/components/shots/PullButton";
import { Badge } from "@/components/ui/badge";
import { useSyncStatus } from "@/hooks/useArchive";
import { formatTime } from "@/lib/shots";

/**
 * Pull from the machine: the one exchange on the Sync page that only reads.
 *
 * The same `PullButton` the shots page carries, so the two cannot disagree about
 * whether a pull can start or what the last one did, with the ledger under it:
 * the last run of each pass and what the archive now holds.
 */
export function PullSection() {
  const sync = useSyncStatus();

  return (
    <SectionCard
      title="Pull from the machine"
      description="Read the machine's index, profiles and notes, and archive anything new. Nothing comes off the machine unless somebody asks, here or on the Shots page."
      actions={
        <>
          {sync.data?.running ? <Badge variant="secondary">running</Badge> : null}
          <PullButton />
        </>
      }
    >
      {sync.data?.last_error ? (
        <p className="mb-3 rounded-md border border-status-bad/40 bg-status-bad/10 p-2 text-sm">
          Last failure ({sync.data.last_error.kind}): {sync.data.last_error.error ?? "unknown"}
        </p>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
            <tr>
              <th className="py-2 pr-4 font-medium">Pass</th>
              <th className="py-2 pr-4 font-medium">Status</th>
              <th className="py-2 pr-4 font-medium">Finished</th>
              <th className="py-2 text-right font-medium">Shots</th>
            </tr>
          </thead>
          <tbody data-testid="sync-runs">
            {Object.entries(sync.data?.last_runs ?? {}).map(([kind, run]) => (
              <tr key={kind} className="border-border border-b last:border-0">
                <td className="py-2 pr-4">{kind}</td>
                <td className="py-2 pr-4">{run.status}</td>
                <td className="py-2 pr-4">{formatTime(run.finished_at ?? run.started_at)}</td>
                <td className="py-2 text-right tabular-nums">{run.shots_inserted}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sync.data?.counts ? (
        <p className="mt-3 text-muted-foreground text-xs">
          {sync.data.counts.total} shots · {sync.data.counts.samples.toLocaleString()} samples ·{" "}
          {sync.data.counts.quarantined} quarantined · {sync.data.counts.deleted_on_device} gone
          from the machine
        </p>
      ) : null}
    </SectionCard>
  );
}
