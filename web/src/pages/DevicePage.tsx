import { ArrowLeftRight, Cpu } from "lucide-react";
import { Link, Navigate, useLocation } from "react-router-dom";
import type { DeviceIdentity } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { SYNC_ANCHORS } from "@/pages/SyncPage";

/**
 * Where an anchor on this page used to point, now that its cards live on the
 * Sync page. A bookmark to `/device#storage` lands on the section it meant.
 */
const MOVED_ANCHORS: Record<string, string> = {
  "#sync": SYNC_ANCHORS.pull,
  "#pull": SYNC_ANCHORS.pull,
  "#notes": SYNC_ANCHORS.notes,
  "#storage": SYNC_ANCHORS.storage,
  "#cleanup": SYNC_ANCHORS.storage,
  "#writes": SYNC_ANCHORS.writes,
};

/**
 * What the machine is, and how this box reaches it.
 *
 * `/api/device/status` carries the facts — configured, connected, identity.
 * There is no telemetry here: what the boiler is doing right now is on the
 * machine's own display and in its own web UI. Everything this box exchanges
 * with the machine — pulling, sending notes, cleaning up storage, and the audit
 * of what it wrote — is on the Sync page, where a person starts it; this page
 * links there rather than carrying a second copy of any of it.
 */
export function DevicePage() {
  const device = useDeviceStatus();
  const { hash } = useLocation();

  useQueryErrorToast(device.error, "Could not read the device status");

  const moved = MOVED_ANCHORS[hash];
  if (moved) return <Navigate to={`/sync#${moved}`} replace />;

  if (device.isPending) {
    return (
      <div className="space-y-4">
        <PageHeader title="Device" subtitle="The machine this archive follows." />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const syncLink = (
    <Button asChild variant="outline" size="sm">
      <Link to="/sync">
        <ArrowLeftRight className="size-3.5" aria-hidden="true" />
        Sync with the machine
      </Link>
    </Button>
  );

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
        actions={syncLink}
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

        <SectionCard title="Connection" description="How this box reaches the machine.">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2" data-testid="device-connection">
            <Fact label="Address" value={device.data.host ?? "—"} />
            <Fact label="State" value={device.data.connected ? "connected" : "not connected"} />
          </dl>
          <p className="mt-3 text-muted-foreground text-sm">
            Pulling, sending notes, cleaning up storage and the record of every write are on the{" "}
            <Link className="underline underline-offset-2" to="/sync">
              Sync page
            </Link>
            .
          </p>
        </SectionCard>
      </div>
    </div>
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
