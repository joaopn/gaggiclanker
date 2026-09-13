import { useMutation } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { runSync } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSyncStatus } from "@/hooks/useArchive";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { attempt } from "@/lib/mutations";
import { isPulling, latestShotRun, pullSummary } from "@/lib/sync";

/**
 * The button that fills the archive.
 *
 * Nothing comes off the machine on its own, so this is the front page's most
 * important control and it has to answer three questions without being
 * clicked: can it work (is a machine configured and connected), is it working
 * right now, and what did it do last time. The first two come from
 * `/api/device/status` and the sync ledger; the third is a toast, because a
 * pull is something you ask for and then look away from.
 *
 * "What did this click do" is tracked by run id rather than by waiting on the
 * mutation: `POST /api/sync/run` answers 202 the moment the loops are woken,
 * and the run that follows is watched through the ledger, which the
 * `sync.progress` events keep fresh. A pull started in another tab therefore
 * shows the spinner here too, and only the tab that asked gets the toast.
 *
 * The id to wait past is captured when the button is *pressed*, not when the
 * 202 comes back. The first `sync.progress` event lands well inside that
 * window and refetches the ledger, so by `onSuccess` the newest run is already
 * the one this click started — and waiting for a run newer than itself is a
 * toast that never arrives.
 */
export function PullButton() {
  const device = useDeviceStatus();
  const sync = useSyncStatus();
  const run = latestShotRun(sync.data);
  const running = isPulling(sync.data);
  // Read inside `onMutate`, which runs synchronously on the click. A piece of
  // state would be a render behind by then.
  const runAtClick = useRef<number | undefined>(undefined);
  runAtClick.current = run?.id;

  // The newest run id this tab has already accounted for. `null` means "not
  // waiting for anything", which is the state every tab starts in — including
  // one opened while a pull it did not start is under way.
  const [waitingAfter, setWaitingAfter] = useState<number | null>(null);
  const waiting = useRef(false);

  const pull = useMutation({
    mutationFn: () => runSync("all"),
    onMutate: () => {
      waiting.current = true;
      setWaitingAfter(runAtClick.current ?? 0);
    },
    onSuccess: () => void sync.refetch(),
    onError: (error: Error) => {
      waiting.current = false;
      setWaitingAfter(null);
      toast.error(error.message);
    },
  });

  useEffect(() => {
    if (!waiting.current || waitingAfter === null) return;
    if (!run || run.id <= waitingAfter || !run.finished_at) return;
    waiting.current = false;
    setWaitingAfter(null);
    if (run.status === "ok") {
      toast.success(pullSummary(run));
    } else {
      toast.error(pullSummary(run));
    }
  }, [run, waitingAfter]);

  const configured = device.data?.configured ?? false;
  const connected = device.data?.connected ?? false;
  const disabled = !configured || !connected || running || pull.isPending;

  const why = !configured
    ? "No machine is configured. Set its address in Settings."
    : !connected
      ? "The machine is not reachable. The archive still works; a pull cannot."
      : running
        ? "A pull is already running."
        : "Read the machine's index and archive anything new.";

  const button = (
    <Button
      size="sm"
      data-testid="pull-button"
      disabled={disabled}
      onClick={() => void attempt(() => pull.mutateAsync())}
    >
      {running ? (
        <RefreshCw className="size-3.5 animate-spin" aria-hidden="true" />
      ) : (
        <Download className="size-3.5" aria-hidden="true" />
      )}
      {running ? "Pulling…" : "Pull from machine"}
    </Button>
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
