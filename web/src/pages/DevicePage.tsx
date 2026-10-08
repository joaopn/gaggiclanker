import { Cpu } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import type { DeviceIdentity } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { BoardSection } from "@/components/sync/BoardSection";
import { DeviceWritesSection } from "@/components/sync/DeviceWritesSection";
import { PullSection } from "@/components/sync/PullSection";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";

/** Anchors of the sync cards, so a link can land on one. */
export const DEVICE_ANCHORS = {
  sync: "sync",
  board: "board",
  writes: "writes",
} as const;

/**
 * Anchors that name a section this page no longer has, and the card that took
 * its place: `#pull` is what the sync card was called, and the notes, storage
 * and clean-up cards are gone.
 */
const RENAMED_ANCHORS: Record<string, string> = {
  pull: DEVICE_ANCHORS.sync,
  notes: DEVICE_ANCHORS.sync,
  storage: DEVICE_ANCHORS.sync,
  cleanup: DEVICE_ANCHORS.sync,
};

/**
 * The Sync page's address, kept so its bookmarks and anchors still land: the
 * page's cards are this page's now.
 */
export function SyncRedirect() {
  const { hash } = useLocation();
  return <Navigate to={`/device${hash}`} replace />;
}

/**
 * The machine, and everything this box has done with it.
 *
 * `/api/device/status` carries the facts — configured, connected, identity.
 * There is no telemetry here: what the boiler is doing right now is on the
 * machine's own display and in its own web UI. Below the facts is the record
 * of the exchanges: what the last sync read, what it did to the machine's
 * profiles, and every write this box has attempted. The sync itself starts from
 * the Sync button in the top bar, beside the status pill that opens this page.
 *
 * The rule the record makes visible: the only thing this box ever writes to the
 * machine is a profile, and only a sync does it, with the Writes switch on, by
 * making the machine hold the profiles that are on in the Profiles list through
 * every safety layer. A sync that paused because the machine looked reset is
 * resumed on the Profiles page.
 *
 * The sync cards are drawn with or without a machine: a box whose address was
 * just cleared still has a last sync and an audit worth reading.
 */
export function DevicePage() {
  const device = useDeviceStatus();
  const { hash } = useLocation();

  useQueryErrorToast(device.error, "Could not read the device status");

  useEffect(() => {
    const raw = hash.slice(1);
    const anchor = RENAMED_ANCHORS[raw] ?? raw;
    // Wait for the facts above the cards, or the scroll lands before they push it down.
    if (!anchor || device.isPending) return;
    document.getElementById(anchor)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [hash, device.isPending]);

  const identity = (device.data?.identity ?? {}) as DeviceIdentity & Record<string, unknown>;
  const configured = device.data?.configured ?? false;

  let facts: ReactNode;
  if (device.isPending) {
    facts = (
      <>
        <PageHeader title="Device" subtitle="The machine this archive follows." />
        <Skeleton className="h-40 w-full" />
      </>
    );
  } else if (!configured) {
    facts = (
      <>
        <PageHeader title="Device" subtitle="The machine this archive follows." />
        <EmptyState
          icon={Cpu}
          title="No machine configured"
          description="Set `gaggimateHost` in Settings. The archive works without one — imported shots are shots like any other — but there is nothing to sync with."
        />
      </>
    );
  } else {
    facts = (
      <>
        <PageHeader
          title={identity.hardware ?? "GaggiMate"}
          subtitle={`${device.data?.host} · ${device.data?.connected ? "connected" : "not connected"}`}
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
              <Fact label="Address" value={device.data?.host ?? "—"} />
              <Fact label="State" value={device.data?.connected ? "connected" : "not connected"} />
            </dl>
          </SectionCard>
        </div>
      </>
    );
  }

  return (
    <div className="space-y-4">
      {facts}

      <p className="text-muted-foreground text-sm">
        Shots, profiles and notes are read from the machine when you press Sync in the top bar. The
        only thing this box ever writes to it is a profile: with writes on, a sync also makes the
        machine hold exactly the profiles that are on in the Profiles page's list.
      </p>

      <div id={DEVICE_ANCHORS.sync} className="scroll-mt-4">
        <PullSection />
      </div>
      <div id={DEVICE_ANCHORS.board} className="scroll-mt-4">
        <BoardSection />
      </div>
      <div id={DEVICE_ANCHORS.writes} className="scroll-mt-4">
        <DeviceWritesSection />
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
