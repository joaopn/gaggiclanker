import { Activity } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ShotListRow } from "@/api/types";
import type { LivePoint } from "@/components/charts/LiveChart";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Skeleton } from "@/components/ui/skeleton";
import { useShots } from "@/hooks/useArchive";
import { useLiveStatus } from "@/hooks/useDeviceLive";
import { isBrewing } from "@/lib/liveStatus";
import { DEVICE_MODES, formatClock, formatNumber, profileName } from "@/lib/shots";

const LiveChart = lazy(() =>
  import("@/components/charts/LiveChart").then((module) => ({ default: module.LiveChart })),
);

/**
 * The shot in progress.
 *
 * Fed by `/api/device/live`, which is the merged `evt:status` — one socket to
 * the machine, merged server-side, so a tab that opens mid-shot is immediately
 * correct rather than waiting for the next full frame. The store behind
 * `useLiveStatus` rate-limits that to 2 Hz (`lib/liveStatus.ts`).
 *
 * The curve is accumulated here rather than fetched: while the shot is running
 * there is no file to fetch. When it ends, the sync engine pulls the `.slog`
 * and `shot.ingested` invalidates the shots list — which is how the "saved as"
 * link below finds the archived shot a second or two later.
 */
/** What the page remembers about the shot that just finished. */
type BrewEnd = {
  at: number;
  /** The newest archived shot at the moment the brew ended — so the one that
      appears *after* it can be told apart from the one already there. */
  previousShotId: number | null;
};

export function LivePage() {
  const live = useLiveStatus();
  const [points, setPoints] = useState<LivePoint[]>([]);
  const brewing = isBrewing(live);
  // Whether the previous frame was part of a running shot, and how far into it
  // that shot was. Both are needed to spot a *new* brew: the firmware restarts
  // `process.e` from zero, and between two shots there is at least one frame
  // with `a: 0`.
  const wasActive = useRef(false);
  const lastElapsed = useRef(0);
  const [endedAt, setEndedAt] = useState<BrewEnd | null>(null);

  // Mounted for the whole page, not only after a shot ends: the id of the
  // newest archived shot has to be known *before* the brew finishes for the
  // link below to tell the new one from the old one.
  const newest = useShots({ limit: 1 });
  const newestShot = newest.data?.items?.[0] ?? null;
  const newestId = useRef<number | null>(null);
  newestId.current = newestShot?.id ?? null;

  const status = live.status;

  useEffect(() => {
    if (!status) return;

    if (status.process?.a !== 1) {
      if (wasActive.current) {
        // The shot ended. Its curve belongs to it and to the `.slog` the sync
        // engine is about to pull — not to whatever runs next, which would
        // otherwise open with a line drawn back from its first sample to the
        // end of the previous shot.
        setPoints([]);
        setEndedAt({ at: Date.now(), previousShotId: newestId.current });
      }
      wasActive.current = false;
      lastElapsed.current = 0;
      return;
    }

    const elapsed = status.process?.e ?? 0;
    // A shot we were not already watching, or a clock that went backwards
    // because the machine started another one between two frames.
    const fresh = !wasActive.current || elapsed < lastElapsed.current;
    wasActive.current = true;
    lastElapsed.current = elapsed;
    if (fresh) setEndedAt(null);
    setPoints((current) => {
      const base = fresh ? [] : current;
      if (base.length > 0 && base[base.length - 1].t === elapsed) return current;
      return [
        ...base,
        {
          t: elapsed,
          pressure: status.pr ?? null,
          flow: status.fl ?? null,
          weight: status.cw ?? null,
          temperature: status.ct ?? null,
        },
      ];
    });
  }, [status]);

  const elapsed = status?.process?.e ?? 0;

  if (!live.configured) {
    return (
      <div className="space-y-4">
        <PageHeader title="Live" subtitle="The shot in progress." />
        <EmptyState
          icon={Activity}
          title="No machine configured"
          description="Set `gaggimateHost` in Settings and the live view follows the next shot."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Live"
        subtitle={
          brewing
            ? [status?.process?.l, status?.p].filter(Boolean).join(" · ") || "Brewing"
            : live.connected
              ? `Idle · ${DEVICE_MODES[status?.m ?? 0] ?? "unknown mode"}`
              : "Not connected to the machine."
        }
      />

      {brewing ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5" data-testid="live-readout">
            <Readout label="Elapsed" value={formatClock(elapsed)} />
            <Readout label="Pressure" value={formatNumber(status?.pr, 1, "bar")} />
            <Readout label="Flow" value={formatNumber(status?.fl, 1, "ml/s")} />
            <Readout label="Weight" value={formatNumber(status?.cw, 1, "g")} />
            <Readout label="Temperature" value={formatNumber(status?.ct, 1, "°C")} />
          </div>
          <TargetProgress
            kind={status?.process?.tt ?? null}
            target={status?.process?.pt ?? null}
            progress={status?.process?.pp ?? null}
          />
          <SectionCard title="Curves" description="Pressure, flow, weight and temperature at 2 Hz.">
            <Suspense fallback={<Skeleton className="h-64 w-full" />}>
              <LiveChart points={points} />
            </Suspense>
          </SectionCard>
        </>
      ) : (
        <SavedShotLink endedAt={endedAt} connected={live.connected} newestShot={newestShot} />
      )}
    </div>
  );
}

function Readout({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-card/40 p-3">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="font-medium text-lg tabular-nums">{value}</p>
    </div>
  );
}

/**
 * `pp` against `pt`, in whatever unit `tt` names.
 *
 * "volumetric" means grams on the scale and "time" means milliseconds of the
 * phase, and the difference matters: a progress bar labelled "28 / 36" is
 * meaningless without knowing which.
 */
function TargetProgress({
  kind,
  target,
  progress,
}: {
  kind: string | null;
  target: number | null;
  progress: number | null;
}) {
  if (!target || target <= 0) return null;
  const volumetric = kind === "volumetric";
  const fraction = Math.min(1, Math.max(0, (progress ?? 0) / target));
  const unit = volumetric ? "g" : "s";
  const scale = volumetric ? 1 : 1000;
  return (
    <div data-testid="target-progress">
      <div className="flex items-baseline justify-between text-sm">
        <span>{volumetric ? "Volumetric target" : "Phase duration"}</span>
        <span className="tabular-nums">
          {((progress ?? 0) / scale).toFixed(1)} / {(target / scale).toFixed(1)} {unit}
        </span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-status-good transition-[width] duration-300"
          style={{ width: `${(fraction * 100).toFixed(1)}%` }}
        />
      </div>
    </div>
  );
}

/**
 * Once a shot ends, the interesting thing is the shot that was saved.
 *
 * "Saved" means a row the archive did not have when the brew finished. The
 * obvious test — "the list has a newest row" — links to the *previous* shot
 * for the second or two the sync engine spends fetching the file, which is
 * exactly the moment somebody is looking at it. Comparing ids rather than
 * `synced_at` against the browser's clock keeps it right on a tab whose clock
 * disagrees with the appliance's, and ids only ever go up.
 *
 * The list query is invalidated by `shot.ingested` on `/api/sync/events`, so
 * the new row arrives without a poll.
 */
function SavedShotLink({
  endedAt,
  connected,
  newestShot,
}: {
  endedAt: BrewEnd | null;
  connected: boolean;
  newestShot: ShotListRow | null;
}) {
  if (!connected) {
    return (
      <EmptyState
        icon={Activity}
        title="Not connected"
        description="The archive still works; nothing is streaming. The header pill says what the machine is doing."
      />
    );
  }

  const saved =
    endedAt && newestShot && newestShot.id !== endedAt.previousShotId ? newestShot : null;

  if (saved) {
    return (
      <SectionCard title="Shot saved" description="The machine finished and the archive has it.">
        <Link
          to={`/shots/${saved.id}`}
          className="underline underline-offset-2"
          data-testid="saved-shot-link"
        >
          {profileName(saved)} — shot {saved.device_id}
        </Link>
      </SectionCard>
    );
  }
  return (
    <EmptyState
      icon={Activity}
      title={endedAt ? "Waiting for the file" : "No shot running"}
      description={
        endedAt
          ? "The shot finished; the sync engine is pulling the .slog. It appears here as soon as it lands."
          : "Start a shot on the machine and it appears here within half a second."
      }
    />
  );
}
