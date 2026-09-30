import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
import { DeviceWritesSection } from "@/components/sync/DeviceWritesSection";
import { PullSection } from "@/components/sync/PullSection";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";

/**
 * Anchors of the two sections, so a link can land on one. The Device page
 * redirects the anchors of the cards that moved here to these.
 */
export const SYNC_ANCHORS = {
  pull: "pull",
  writes: "writes",
} as const;

/**
 * The pull from the machine, and the audit of what this box has written to it.
 *
 * The rule the page makes visible: the only thing this box ever writes to the
 * machine is a profile, pushed from the Profiles page through every safety
 * layer. Everything here is a read, except that the audit lists those pushes.
 * Nothing on this page runs by itself.
 */
export function SyncPage() {
  const device = useDeviceStatus();
  const { hash } = useLocation();
  useQueryErrorToast(device.error, "Could not read the device status");

  const configured = device.data?.configured ?? false;
  const connected = device.data?.connected ?? false;

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
        Shots, profiles and notes are read from the machine when you pull. The only thing this box
        ever writes to it is a profile, pushed from the Profiles page.
      </p>

      <div id={SYNC_ANCHORS.pull} className="scroll-mt-4">
        <PullSection />
      </div>
      <div id={SYNC_ANCHORS.writes} className="scroll-mt-4">
        <DeviceWritesSection />
      </div>
    </div>
  );
}
