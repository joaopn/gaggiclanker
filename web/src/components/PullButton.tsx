import { Download, RefreshCw } from "lucide-react";
import { useSyncOwner } from "@/components/sync/SyncOwner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSyncStatus } from "@/hooks/useArchive";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { attempt } from "@/lib/mutations";
import { isPulling } from "@/lib/sync";

/**
 * The top-bar Sync button: the one control that reads the machine.
 *
 * A sync reads the machine's shots, profiles and notes, and with the Writes switch
 * on makes its profile list match the Profiles page, so it belongs to no one page:
 * it sits first in the top bar, on every page. Nothing comes off the machine on its
 * own, so it is the app's most important control: filled where the rest of the bar
 * is outlined, and it has to answer three questions without being clicked: can it work (is a machine configured and connected), is it working
 * right now, and what did it do last time. The first two come from
 * `/api/device/status` and the sync ledger; the third is a toast, because a
 * sync is something you ask for and then look away from.
 *
 * Asking for the sync, watching it and the toast are the app's `SyncOwner`, shared with the
 * proposal cards whose Approve and sync begins the same sync. A sync started in another tab
 * shows the spinner here too, from the ledger.
 */
export function PullButton() {
  const device = useDeviceStatus();
  const sync = useSyncStatus();
  const owner = useSyncOwner();
  const running = isPulling(sync.data);

  const configured = device.data?.configured ?? false;
  const connected = device.data?.connected ?? false;
  const disabled = !configured || !connected || running || owner.starting;

  const why = !configured
    ? "No machine is configured. Set its address in Settings."
    : !connected
      ? "The machine is not reachable. The archive still works; a sync cannot."
      : running
        ? "A sync is already running."
        : "Read the machine's shots, profiles and notes, and archive anything new. With Writes on, also make its profile list match the Profiles page.";

  // "Sync" on screen and "Sync with machine" to a screen reader, which is the action's
  // name everywhere else; icon-only below `sm`, like the switch and Flush beside it, so
  // the bar still fits at phone width.
  const button = (
    <button
      type="button"
      data-testid="pull-button"
      disabled={disabled}
      onClick={() => void attempt(() => owner.startSync())}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-primary bg-primary px-2.5 py-1 font-medium text-primary-foreground text-xs hover:bg-primary/90 disabled:opacity-60"
    >
      {running ? (
        <RefreshCw className="size-3.5 animate-spin" aria-hidden="true" />
      ) : (
        <Download className="size-3.5" aria-hidden="true" />
      )}
      {running ? (
        <span className="max-sm:sr-only">Syncing…</span>
      ) : (
        <>
          <span className="max-sm:sr-only">Sync</span>
          <span className="sr-only"> with machine</span>
        </>
      )}
    </button>
  );

  return (
    <Tooltip>
      {/* A disabled button fires no pointer events, so the tooltip needs a
          wrapper to hang off — otherwise the one state that most needs
          explaining is the one with no explanation. */}
      <TooltipTrigger asChild>
        <span className="inline-flex">{button}</span>
      </TooltipTrigger>
      <TooltipContent>{why}</TooltipContent>
    </Tooltip>
  );
}
