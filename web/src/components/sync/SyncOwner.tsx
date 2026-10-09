import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";
import { getSyncRunsAfter, runSync } from "@/api/client";
import { useSyncStatus } from "@/hooks/useArchive";
import { invalidateBoard, invalidateDrafts } from "@/lib/invalidate";
import {
  isShotRunKind,
  latestProfileRun,
  latestShotRun,
  mergeProfileRuns,
  mergeShotRuns,
  pullSummary,
  syncSucceeded,
} from "@/lib/sync";

/**
 * How long a finished shot pass waits for its profile pass before the toast goes without it.
 * The two passes are separate runs that take the engine's lock in turn, so the second can be
 * a while behind (a large archive, a slow machine); a profile pass that never appears must not
 * leave the person with no answer at all.
 */
export const PROFILE_PASS_GRACE_MS = 15_000;

/**
 * What the app offers to whatever starts a sync: the top bar's button and every card whose
 * Approve and sync begins one.
 */
export type SyncOwner = {
  /**
   * Ask for the same sync the top bar's button asks for, and watch it until it ends. `key` names
   * who asked (a card passes its draft), so that card can say "Syncing…" while this sync runs.
   * A request made while one is watched asks the server again and waits past the later run, so
   * a second approval is sent and the one notification covers both. Resolves `true` when the
   * request was accepted, `false` when it was refused; the refusal is already a toast.
   */
  startSync: (key?: string) => Promise<boolean>;
  /** The request for a sync is on its way to the server. */
  starting: boolean;
  /** A sync this app started is still being watched: queued, running, or its profile pass due. */
  waiting: boolean;
  /** Whether the sync being watched was asked for, or joined, under `key`. */
  waitingFor: (key: string) => boolean;
};

const NOT_MOUNTED: SyncOwner = {
  // A tree with no AppShell (a component under test, a story) starts nothing rather than
  // posting a sync nobody watches.
  startSync: async () => false,
  starting: false,
  waiting: false,
  waitingFor: () => false,
};

const SyncOwnerContext = createContext<SyncOwner>(NOT_MOUNTED);

export function useSyncOwner(): SyncOwner {
  return useContext(SyncOwnerContext);
}

/**
 * The one owner of "a sync this app started is under way", mounted with `AppShell`.
 *
 * A sync is a request that answers 202 the moment the loops are woken; the runs that follow are
 * watched through the ledger, which the `sync.progress` events keep fresh. What a click started
 * is tracked by run id, and the id to wait past is captured when the sync is *asked for*, not
 * when the 202 comes back: the first `sync.progress` event lands well inside that window and
 * refetches the ledger, so by `onSuccess` the newest run is already the one this click started,
 * and waiting for a run newer than itself is a toast that never arrives.
 *
 * This state lived in the top bar's button, so only the button that was clicked could report the
 * result. A card that starts a sync unmounts when the person leaves the chat, which would have
 * taken the notification with it. Here it outlives every page: one wait, one toast, wherever the
 * person is when the sync ends, whoever asked.
 */
export function SyncOwnerProvider({
  children,
  profileGraceMs = PROFILE_PASS_GRACE_MS,
}: {
  children: ReactNode;
  profileGraceMs?: number;
}) {
  const queryClient = useQueryClient();
  const sync = useSyncStatus();
  const run = latestShotRun(sync.data);
  const profileRun = latestProfileRun(sync.data);
  // Read synchronously when a sync is asked for. A piece of state would be a render behind.
  const runAtClick = useRef<number | undefined>(undefined);
  runAtClick.current = run?.id;
  const profileRunAtClick = useRef<number | undefined>(undefined);
  profileRunAtClick.current = profileRun?.id;
  // Whether the request queued a profile pass, so the toast knows to wait for it. Read from the
  // 202, which says what was queued.
  const expectProfiles = useRef(false);
  const [profilesAfter, setProfilesAfter] = useState(0);
  const [gaveUp, setGaveUp] = useState(false);
  // The newest shot run this app had already accounted for when it asked. `null` means "not
  // waiting for anything", which is the state every tab starts in, including one opened while a
  // sync it did not start is under way.
  const [waitingAfter, setWaitingAfter] = useState<number | null>(null);
  // Who asked, for "Syncing…" on a card. Kept until the cards' own reads have landed after the
  // sync's end, so a card never shows its old sentence between the toast and the new state.
  const [keys, setKeys] = useState<ReadonlySet<string>>(() => new Set());
  const keysRef = useRef<ReadonlySet<string>>(new Set());
  const updateKeys = useCallback((change: (current: Set<string>) => void) => {
    const next = new Set(keysRef.current);
    change(next);
    keysRef.current = next;
    setKeys(next);
  }, []);
  const waiting = useRef(false);
  // How many requests of the wait being watched the server has accepted, and how many are on
  // their way. A refused request only ends the wait when it is the last hope of one.
  const accepted = useRef(0);
  const pending = useRef(0);
  // The newest run of any kind on the ledger when the first request of the wait was asked: every
  // run after it is this wait's, and the one notification is read from them, whichever request
  // queued them (the ledger itself keeps only the newest run of each kind).
  const newestRunAtClick = useRef(0);
  newestRunAtClick.current = Math.max(
    0,
    ...Object.values(sync.data?.last_runs ?? {}).map((entry) => entry.id),
  );
  const firstRunAfter = useRef(0);

  const pull = useMutation({
    mutationFn: () => runSync("all"),
    onSuccess: (queued) => {
      accepted.current += 1;
      expectProfiles.current = queued?.queued?.includes("profiles") ?? false;
      void sync.refetch();
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const { mutateAsync } = pull;

  const startSync = useCallback(
    async (key?: string): Promise<boolean> => {
      const wasWaiting = waiting.current;
      // Read synchronously now: by the time the 202 comes back the ledger has moved on.
      const shotAfter = runAtClick.current ?? 0;
      const profileAfter = profileRunAtClick.current ?? 0;
      const newestRun = newestRunAtClick.current;
      const begin = () => {
        waiting.current = true;
        accepted.current = 0;
        expectProfiles.current = false;
        firstRunAfter.current = newestRun;
        setGaveUp(false);
        setWaitingAfter(shotAfter);
        setProfilesAfter(profileAfter);
      };
      if (key !== undefined) updateKeys((current) => current.add(key));
      if (!wasWaiting) begin();
      pending.current += 1;
      try {
        await mutateAsync();
        if (wasWaiting) {
          // The server queued another pass, which can carry what the first one did not have yet
          // (a second approval). Only now, with the request accepted, does the wait move past
          // the newest runs on the ledger, so the one notification comes after the later pass.
          if (waiting.current) {
            setGaveUp(false);
            setWaitingAfter(shotAfter);
            setProfilesAfter(profileAfter);
          } else {
            begin();
            accepted.current = 1;
          }
        }
        return true;
      } catch {
        // `onError` has shown the server's words. A refusal leaves an earlier request's wait
        // exactly as it was, and takes back only the card that asked.
        if (accepted.current === 0 && pending.current === 1) {
          waiting.current = false;
          setWaitingAfter(null);
          updateKeys((current) => current.clear());
        } else if (key !== undefined && wasWaiting) {
          updateKeys((current) => current.delete(key));
        }
        return false;
      } finally {
        pending.current -= 1;
      }
    },
    [mutateAsync, updateKeys],
  );

  // The profile pass of the sync being watched: newer than the one on the ledger when it was
  // asked for, and finished. Its numbers go in the toast.
  const profilesDone =
    profileRun && profileRun.id > profilesAfter && profileRun.finished_at ? profileRun : undefined;
  // Whether that profile pass is on the ledger yet, finished or not. Once it is, it is waited for
  // however long it takes: a slow pass must not be given up on and reported as a success without
  // its numbers (it may yet fail). The grace below covers only a pass that never appears.
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
    accepted.current = 0;
    setWaitingAfter(null);
    // Every pass the wait covered, read from the server and added up: the ledger shows only the
    // newest of each kind. If that read fails the sentence says no counts rather than wrong ones.
    const after = firstRunAfter.current;
    const ledgerProfiles = wanted ? profilesDone : undefined;
    void (async () => {
      try {
        const { runs, truncated } = await getSyncRunsAfter(after);
        // More runs than one read returns: the sum would be short, so no counts at all.
        if (truncated) throw new Error("truncated");
        const done = (runs ?? []).filter((entry) => entry.finished_at);
        // Only the shot passes the top bar reads (a notes run's failed card is not the sync's
        // failure), and the profile pass. A wait that saw its shot pass finish and is told of
        // none has been told wrong.
        const shotPasses = done.filter((entry) => isShotRunKind(entry.kind));
        if (shotPasses.length === 0) throw new Error("no shot pass");
        const shots = mergeShotRuns(shotPasses);
        const profiles = wanted
          ? mergeProfileRuns(done.filter((entry) => entry.kind === "profiles"))
          : undefined;
        if (syncSucceeded(shots, profiles)) toast.success(pullSummary(shots, profiles));
        else toast.error(pullSummary(shots, profiles));
      } catch {
        if (syncSucceeded(run, ledgerProfiles)) toast.success("Synced.");
        else toast.error(pullSummary(run, ledgerProfiles));
      }
    })();
    // The cards that asked read where their proposal stands from the drafts and the board; the
    // `profile.updated` event does the same, but the bus is lossy. They keep saying "Syncing…"
    // until those reads have landed.
    const asked = new Set(keysRef.current);
    void Promise.all([invalidateDrafts(queryClient), invalidateBoard(queryClient)]).then(() => {
      updateKeys((current) => {
        for (const key of asked) current.delete(key);
      });
    });
  }, [
    run,
    shotsDone,
    profilesDone,
    profilesStarted,
    waitingAfter,
    gaveUp,
    pull.isPending,
    profileGraceMs,
    queryClient,
    updateKeys,
  ]);

  const value = useMemo<SyncOwner>(
    () => ({
      startSync,
      starting: pull.isPending,
      waiting: waitingAfter !== null,
      waitingFor: (key) => keys.has(key),
    }),
    [startSync, pull.isPending, waitingAfter, keys],
  );

  return <SyncOwnerContext.Provider value={value}>{children}</SyncOwnerContext.Provider>;
}
