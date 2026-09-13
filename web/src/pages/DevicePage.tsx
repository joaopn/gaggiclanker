import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Cpu, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { runSync } from "@/api/client";
import type { DeviceIdentity, DeviceWrite } from "@/api/types";
import { NotesWritebackCard } from "@/components/device/NotesWritebackCard";
import { StorageCard } from "@/components/device/StorageCard";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSyncStatus } from "@/hooks/useArchive";
import { useDeviceStatus, useDeviceWrites } from "@/hooks/useDeviceStatus";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { queryKeys } from "@/lib/queryKeys";
import { formatTime } from "@/lib/shots";

/**
 * What the machine is, and what this box has done to it.
 *
 * Two sources. `/api/device/status` carries what the machine is — configured,
 * connected, identity — and the sync ledger carries what has been pulled off
 * it. There is no telemetry here: what the boiler is doing right now is on the
 * machine's own display and in its own web UI, and a second copy of it on a
 * page nobody has open during a shot was a subscription per tab for nothing.
 *
 * Two of the cards write to the machine rather than reading from it
 * (`StorageCard` deletes shots, `NotesWritebackCard` overwrites notes), and both
 * are rendered whatever the switches say: a card that disappeared when writes
 * were off would leave nowhere to find out that they are.
 */
export function DevicePage() {
  const device = useDeviceStatus();
  const sync = useSyncStatus();
  const writes = useDeviceWrites();
  const queryClient = useQueryClient();

  useQueryErrorToast(device.error, "Could not read the device status");

  const syncNow = useMutation({
    mutationFn: () => runSync("all"),
    onSuccess: (data) => {
      toast.success(`Queued: ${data.queued.join(", ")}`);
      // 202 means "the loops were woken", not "it is done", so the ledger is
      // re-read rather than assumed: the run shows up as it progresses.
      void queryClient.invalidateQueries({ queryKey: queryKeys.sync.all });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  if (device.isPending) {
    return (
      <div className="space-y-4">
        <PageHeader title="Device" subtitle="The machine this archive follows." />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!device.data?.configured) {
    return (
      <div className="space-y-4">
        <PageHeader title="Device" subtitle="The machine this archive follows." />
        <EmptyState
          icon={Cpu}
          title="No machine configured"
          description="Set `gaggimateHost` in Settings. The archive works without one — imported shots are shots like any other — but there is nothing to pull from."
        />
      </div>
    );
  }

  const identity = (device.data.identity ?? {}) as DeviceIdentity & Record<string, unknown>;

  return (
    <div className="space-y-4">
      <PageHeader
        title={identity.hardware ?? "GaggiMate"}
        subtitle={`${device.data.host} · ${device.data.connected ? "connected" : "not connected"}`}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => syncNow.mutate()}
            disabled={syncNow.isPending}
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Sync now
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-2">
        <SectionCard title="Versions" description="What the display and controller are running.">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2" data-testid="device-versions">
            <Fact label="Hardware" value={identity.hardware ?? "—"} />
            <Fact label="Display" value={identity.displayVersion ?? "—"} />
            <Fact label="Controller" value={identity.controllerVersion ?? "—"} />
            <Fact label="Latest available" value={identity.latestVersion ?? "—"} />
            <Fact label="Channel" value={identity.channel ?? "—"} />
            <Fact label="Updating" value={identity.updating ? "yes" : "no"} />
          </dl>
        </SectionCard>

        <NotesWritebackCard />
      </div>

      {/* Full width rather than half: it carries a preview list and a run
          history, and a two-column card would wrap every row. */}
      <StorageCard identity={identity} writesEnabled={writes.data?.enabled ?? false} />

      <SectionCard
        title="Sync"
        description="The last run of each pass, and what the archive holds."
        actions={sync.data?.running ? <Badge variant="secondary">running</Badge> : null}
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

      <DeviceWritesCard
        enabled={writes.data?.enabled ?? false}
        items={writes.data?.items ?? []}
        pending={writes.isPending}
      />
    </div>
  );
}

/**
 * Everything this box has ever asked the machine to change.
 *
 * The refusals are the rows worth having. "Nothing tried to write" and
 * "something tried and was stopped" look identical in an audit that only
 * records successes, and they are very different facts about a box sitting on
 * somebody's counter.
 */
function DeviceWritesCard({
  enabled,
  items,
  pending,
}: {
  enabled: boolean;
  items: DeviceWrite[];
  pending: boolean;
}) {
  return (
    <SectionCard
      title="Writes to the machine"
      description="Every write attempt, refused ones included. gaggiclanker writes profiles and nothing else: never device settings, which clear every boolean key they omit, and never shot history, which is unrecoverable."
      actions={
        enabled ? (
          <Badge variant="secondary">writes enabled</Badge>
        ) : (
          <Badge variant="outline">writes off</Badge>
        )
      }
      contentClassName="overflow-x-auto"
    >
      {pending ? (
        <Skeleton className="h-16 w-full" />
      ) : items.length === 0 ? (
        <p className="text-muted-foreground text-sm" data-testid="device-writes-empty">
          Nothing has been written to this machine.
        </p>
      ) : (
        <table className="w-full border-collapse text-left text-sm">
          <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
            <tr>
              <th className="py-2 pr-4 font-medium">When</th>
              <th className="py-2 pr-4 font-medium">What</th>
              <th className="py-2 pr-4 font-medium">Profile</th>
              <th className="py-2 font-medium">Result</th>
            </tr>
          </thead>
          <tbody data-testid="device-writes">
            {items.map((write) => (
              <tr key={write.id} className="border-border border-b last:border-0">
                <td className="py-2 pr-4">{formatTime(write.created_at)}</td>
                <td className="py-2 pr-4 font-mono text-xs">{write.kind}</td>
                <td className="py-2 pr-4 font-mono text-xs">{write.device_id ?? "—"}</td>
                <td className="py-2">
                  {write.result === "ok" ? (
                    <Badge variant="secondary">ok</Badge>
                  ) : (
                    <span title={write.error}>
                      <Badge variant="outline">{write.result}</Badge>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </SectionCard>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-sm tabular-nums">{value}</dd>
    </div>
  );
}
