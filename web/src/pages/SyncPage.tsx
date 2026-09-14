import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import type { DeviceIdentity } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { CleanupSection } from "@/components/sync/CleanupSection";
import { DeviceWritesSection } from "@/components/sync/DeviceWritesSection";
import { NotesSection } from "@/components/sync/NotesSection";
import { PullSection } from "@/components/sync/PullSection";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";

/**
 * Anchors of the four sections, so a link can land on one. The Device page
 * redirects the anchors of the cards that moved here to these.
 */
export const SYNC_ANCHORS = {
  pull: "pull",
  notes: "notes",
  storage: "storage",
  writes: "writes",
} as const;

/**
 * Every exchange with the machine that a person starts, in one place.
 *
 * The rule the page exists to make visible: profiles may be pushed by the app
 * (from the Profiles page, through every safety layer); everything else that is
 * written to or deleted from the machine happens here, by a person, after they
 * have seen exactly what will change. Nothing on this page runs by itself.
 *
 * The sections are always rendered, whatever the switches say, and each write
 * action says why it cannot start rather than only disabling a button: a
 * section that disappeared when writes were off would leave nowhere to find
 * out that they are.
 */
export function SyncPage() {
  const device = useDeviceStatus();
  const { hash } = useLocation();
  useQueryErrorToast(device.error, "Could not read the device status");

  const configured = device.data?.configured ?? false;
  const connected = device.data?.connected ?? false;
  const identity = (device.data?.identity ?? {}) as DeviceIdentity & Record<string, unknown>;

  useEffect(() => {
    const anchor = hash.slice(1);
    if (!anchor || device.isPending) return;
    document.getElementById(anchor)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [hash, device.isPending]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Sync"
        subtitle={
          !configured
            ? "No machine is configured."
            : `${device.data?.host} · ${connected ? "connected" : "not connected"}`
        }
      />
      <p className="text-muted-foreground text-sm">
        Profiles are pushed from the Profiles page. Everything else this box writes to or deletes
        from the machine starts here, when you confirm it.
      </p>

      <div id={SYNC_ANCHORS.pull} className="scroll-mt-4">
        <PullSection />
      </div>
      <div id={SYNC_ANCHORS.notes} className="scroll-mt-4">
        <NotesSection configured={configured} connected={connected} />
      </div>
      <div id={SYNC_ANCHORS.storage} className="scroll-mt-4">
        <CleanupSection identity={identity} configured={configured} connected={connected} />
      </div>
      <div id={SYNC_ANCHORS.writes} className="scroll-mt-4">
        <DeviceWritesSection />
      </div>
    </div>
  );
}
