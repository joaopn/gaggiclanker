import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
import { BoardSection } from "@/components/sync/BoardSection";
import { DeviceWritesSection } from "@/components/sync/DeviceWritesSection";
import { PullSection } from "@/components/sync/PullSection";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";

/**
 * Anchors of the two sections, so a link can land on one. The Device page
 * redirects the anchors of the cards that moved here to these.
 */
export const SYNC_ANCHORS = {
  pull: "sync",
  board: "board",
  writes: "writes",
} as const;

/**
 * The sync from the machine, and the audit of what this box has written to it.
 *
 * The rule the page makes visible: the only thing this box ever writes to the
 * machine is a profile, and only a sync does it, with the Writes switch on, by
 * making the machine hold the profiles that are on in the Profiles list through every
 * safety layer. The page shows what the last sync did about that (a sync that paused
 * because the machine looked reset is resumed on the Profiles page) and lists the audit.
 */
export function SyncPage() {
  const device = useDeviceStatus();
  const { hash } = useLocation();
  useQueryErrorToast(device.error, "Could not read the device status");

  const configured = device.data?.configured ?? false;
  const connected = device.data?.connected ?? false;

  useEffect(() => {
    // `#pull` is what the section was called; old bookmarks keep landing on it.
    const raw = hash.slice(1);
    const anchor = raw === "pull" ? SYNC_ANCHORS.pull : raw;
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
        Shots, profiles and notes are read from the machine when you sync. The only thing this box
        ever writes to it is a profile: with writes on, a sync also makes the machine hold exactly
        the profiles that are on in the Profiles page's list.
      </p>

      <div id={SYNC_ANCHORS.pull} className="scroll-mt-4">
        <PullSection />
      </div>
      <div id={SYNC_ANCHORS.board} className="scroll-mt-4">
        <BoardSection />
      </div>
      <div id={SYNC_ANCHORS.writes} className="scroll-mt-4">
        <DeviceWritesSection />
      </div>
    </div>
  );
}
