import { useMutation } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { runSync } from "@/api/client";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSyncStatus } from "@/hooks/useArchive";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { attempt } from "@/lib/mutations";
import { isPulling, latestProfileRun, latestShotRun, pullSummary, syncSucceeded } from "@/lib/sync";

/**
 * How long a finished shot pass waits for its profile pass before the toast goes without it.
 * The two passes are separate runs that take the engine's lock in turn, so the second can be
 * a while behind (a large archive, a slow machine); a profile pass that never appears must not
 * leave the person with no answer at all.
 */
export const PROFILE_PASS_GRACE_MS = 15_000;

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
 * "What did this click do" is tracked by run id rather than by waiting on the
 * mutation: `POST /api/sync/run` answers 202 the moment the loops are woken,
 * and the run that follows is watched through the ledger, which the
 * `sync.progress` events keep fresh. A sync started in another tab therefore
 * shows the spinner here too, and only the tab that asked gets the toast. Being in
 * the top bar, it stays mounted while the person moves between pages, so the toast
 * arrives wherever they are when the sync finishes.
 *
 * The id to wait past is captured when the button is *pressed*, not when the
 * 202 comes back. The first `sync.progress` event lands well inside that
 * window and refetches the ledger, so by `onSuccess` the newest run is already
 * the one this click started — and waiting for a run newer than itself is a
 * toast that never arrives.
 */
export function PullButton({
  profileGraceMs = PROFILE_PASS_GRACE_MS,
}: {
  profileGraceMs?: number;
}) {
  const device = useDeviceStatus();
  const sync = useSyncStatus();
  const run = latestShotRun(sync.data);
  const running = isPulling(sync.data);
  // Read inside `onMutate`, which runs synchronously on the click. A piece of
  // state would be a render behind by then.
  const runAtClick = useRef<number | undefined>(undefined);
  runAtClick.current = run?.id;
  const profileRun = latestProfileRun(sync.data);
  const profileRunAtClick = useRef<number | undefined>(undefined);
  profileRunAtClick.current = profileRun?.id;
  // Whether the request this click made queued a profile pass, so the toast knows to wait
  // for it. Read from the 202, which says what was queued.
  const expectProfiles = useRef(false);
  const [profilesAfter, setProfilesAfter] = useState(0);
  const [gaveUp, setGaveUp] = useState(false);

  // The newest run id this tab has already accounted for. `null` means "not
  // waiting for anything", which is the state every tab starts in — including
  // one opened while a sync it did not start is under way.
  const [waitingAfter, setWaitingAfter] = useState<number | null>(null);
  const waiting = useRef(false);

  const pull = useMutation({
    mutationFn: () => runSync("all"),
    onMutate: () => {
      waiting.current = true;
      expectProfiles.current = false;
      setGaveUp(false);
      setWaitingAfter(runAtClick.current ?? 0);
      setProfilesAfter(profileRunAtClick.current ?? 0);
    },
    onSuccess: (queued) => {
      expectProfiles.current = queued?.queued?.includes("profiles") ?? false;
      void sync.refetch();
    },
    onError: (error: Error) => {
      waiting.current = false;
      setWaitingAfter(null);
      toast.error(error.message);
    },
  });

  // The profile pass of *this* click: newer than the one on the ledger when it was pressed,
  // and finished. Its numbers go in the toast.
  const profilesDone =
    profileRun && profileRun.id > profilesAfter && profileRun.finished_at ? profileRun : undefined;
  // Whether this click's profile pass is on the ledger yet, finished or not. Once it is, it is
  // waited for however long it takes: a slow pass must not be given up on and reported as a
  // success without its numbers (it may yet fail). The grace below covers only a pass that
  // never appears.
  const profilesStarted = !!profileRun && profileRun.id > profilesAfter;
  const shotsDone = run && waitingAfter !== null && run.id > waitingAfter && run.finished_at;

  useEffect(() => {
    if (!waiting.current || waitingAfter === null || pull.isPending) return;
    if (!run || !shotsDone) return;
    const wanted = expectProfiles.current;
    if (wanted && !profilesDone) {
      if (profilesStarted) return;
      if (!gaveUp) {
        const timer = setTimeout(() => setGaveUp(true), profileGraceMs);
        return () => clearTimeout(timer);
      }
    }
    waiting.current = false;
    setWaitingAfter(null);
    const profiles = wanted ? profilesDone : undefined;
    const message = pullSummary(run, profiles);
    if (syncSucceeded(run, profiles)) {
      toast.success(message);
    } else {
      toast.error(message);
    }
  }, [
    run,
    shotsDone,
    profilesDone,
    profilesStarted,
    waitingAfter,
    gaveUp,
    pull.isPending,
    profileGraceMs,
  ]);

  const configured = device.data?.configured ?? false;
  const connected = device.data?.connected ?? false;
  const disabled = !configured || !connected || running || pull.isPending;

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
      onClick={() => void attempt(() => pull.mutateAsync())}
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
