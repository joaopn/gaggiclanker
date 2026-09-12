import { Activity, Coffee, GitCompare } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { ShotListRow } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { CompareDrawer, MAX_COMPARE } from "@/components/shots/CompareDrawer";
import { ShotFilters } from "@/components/shots/ShotFilters";
import { ShotsTable } from "@/components/shots/ShotsTable";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfileVersions, useShotsInfinite, useSyncStatus } from "@/hooks/useArchive";
import { useIsBrewing, useLiveStatus } from "@/hooks/useDeviceLive";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import {
  fromSearchParams,
  isDefaultFilters,
  type ShotFilterState,
  toParams,
  toSearchParams,
} from "@/lib/shotFilters";
import { formatClock } from "@/lib/shots";

/**
 * The archive, and the front page of the whole application.
 *
 * Three things happen here that do not happen on a plain table. The list is
 * windowed, because a year of shots is a thousand rows and each one carries a
 * curve. It refreshes from the server's own events rather than a timer — a
 * shot pulled on the machine appears at the top without a reload, which is the
 * chunk's acceptance criterion. And a live shot puts a banner at the top,
 * because the most interesting shot in the archive is the one happening now.
 */

const PAGE_SIZE = 50;

export function ShotsPage() {
  // The filters live in the query string, so a link can carry one and the back
  // button undoes one (`lib/shotFilters.ts`).
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => fromSearchParams(searchParams), [searchParams]);
  const setFilters = (next: ShotFilterState) =>
    setSearchParams(toSearchParams(next), { replace: true });
  const [selected, setSelected] = useState<number[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const params = useMemo(() => toParams(filters, PAGE_SIZE), [filters]);
  const shots = useShotsInfinite(params);
  const sync = useSyncStatus();
  // Both sources, so an imported profile can be filtered on even though no
  // device profile points at it.
  const versions = useProfileVersions({ limit: 200 });
  // A boolean, not the frame: reading the whole snapshot here re-rendered the
  // table twice a second for the length of every shot (`hooks/useDeviceLive`).
  // The banner itself reads the frame, and it is four spans.
  const brewing = useIsBrewing();

  useQueryErrorToast(shots.error, "Could not load shots");

  const rows: ShotListRow[] = useMemo(
    () => (shots.data?.pages ?? []).flatMap((page) => page.items),
    [shots.data],
  );
  const total = shots.data?.pages?.[0]?.total ?? 0;
  const selectedShots = rows.filter((shot) => selected.includes(shot.id));

  function toggleSelected(id: number) {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((value) => value !== id);
      if (current.length >= MAX_COMPARE) return current;
      const next = [...current, id];
      if (next.length >= 2) setCompareOpen(true);
      return next;
    });
  }

  const counts = sync.data?.counts;
  const subtitle = counts
    ? `${counts.total} archived · ${counts.samples.toLocaleString()} samples` +
      (counts.quarantined ? ` · ${counts.quarantined} quarantined` : "")
    : "Every shot the machine has pulled.";

  return (
    <div className="space-y-4">
      <PageHeader
        title="Shots"
        subtitle={subtitle}
        actions={
          selected.length > 0 ? (
            <Button variant="outline" size="sm" onClick={() => setCompareOpen(true)}>
              <GitCompare className="size-3.5" aria-hidden="true" />
              Compare {selected.length}
            </Button>
          ) : null
        }
      />

      {brewing ? <LiveBanner /> : null}

      <ShotFilters value={filters} onChange={setFilters} versions={versions.data?.items ?? []} />

      {shots.isPending ? (
        <div className="space-y-2" data-testid="shots-loading">
          {[0, 1, 2, 3, 4].map((row) => (
            <Skeleton key={row} className="h-10 w-full" />
          ))}
        </div>
      ) : rows.length > 0 ? (
        <div className="space-y-3">
          {/* The scroll container is the window `useVirtualRows` measures, so
              it owns a height rather than growing with its content. */}
          <div
            ref={scrollRef}
            data-testid="shots-scroll"
            className="max-h-[70vh] overflow-y-auto rounded-lg border border-border"
          >
            <ShotsTable
              shots={rows}
              selected={selected}
              onToggleSelected={toggleSelected}
              scrollRef={scrollRef}
              maxCompare={MAX_COMPARE}
            />
          </div>
          <div className="flex items-center justify-between gap-3">
            <p className="text-muted-foreground text-xs">
              Showing {rows.length} of {total}
            </p>
            {shots.hasNextPage ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => shots.fetchNextPage()}
                disabled={shots.isFetchingNextPage}
              >
                {shots.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <EmptyState
          icon={Coffee}
          title={isDefaultFilters(filters) ? "No shots archived yet" : "No shots match"}
          description={
            !isDefaultFilters(filters)
              ? "Nothing in the archive matches these filters. Clear them to see everything."
              : sync.data?.configured
                ? "The sync engine is running. A shot appears here within seconds of the machine finishing it."
                : "No machine is configured. Set `gaggimateHost` in Settings, or import shots exported from the machine's web UI."
          }
        />
      )}

      {compareOpen && selectedShots.length > 0 ? (
        <CompareDrawer
          shots={selectedShots}
          onRemove={(id) => setSelected((current) => current.filter((value) => value !== id))}
          onClose={() => setCompareOpen(false)}
        />
      ) : null}
    </div>
  );
}

/** "A shot is running." The one thing more interesting than the archive. */
function LiveBanner() {
  const live = useLiveStatus();
  const process = live.status?.process;
  return (
    <Link
      to="/live"
      data-testid="live-banner"
      className="flex items-center gap-3 rounded-lg border border-status-good/40 bg-status-good/10 px-3 py-2 text-sm"
    >
      <Activity className="size-4 animate-pulse text-status-good-text" aria-hidden="true" />
      <span className="font-medium">Shot in progress</span>
      <span className="text-muted-foreground">
        {process?.l ?? "brewing"} · {formatClock(process?.e ?? 0)}
      </span>
      <span className="ml-auto text-muted-foreground text-xs underline underline-offset-2">
        Watch it
      </span>
    </Link>
  );
}
