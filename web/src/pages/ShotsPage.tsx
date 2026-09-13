import { Coffee, GitCompare, Layers } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ShotListRow, ShotSort } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { ColumnChooser } from "@/components/shots/ColumnChooser";
import { CompareDrawer, MAX_COMPARE } from "@/components/shots/CompareDrawer";
import { ImportDropZone } from "@/components/shots/ImportDropZone";
import { PullButton } from "@/components/shots/PullButton";
import { ShotFilters } from "@/components/shots/ShotFilters";
import { ShotsTable } from "@/components/shots/ShotsTable";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfileVersions, useShotsInfinite, useSyncStatus } from "@/hooks/useArchive";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { useSets } from "@/hooks/useSets";
import {
  loadShotColumns,
  type ShotColumnId,
  saveShotColumns,
  visibleColumns,
} from "@/lib/shotColumns";
import {
  fromSearchParams,
  isDefaultFilters,
  type ShotFilterState,
  toParams,
  toSearchParams,
} from "@/lib/shotFilters";
import { lastFinishedShotRun, relativeTime } from "@/lib/sync";

/**
 * The archive, and the front page of the whole application.
 *
 * It is a dataset, and it is laid out like one. The filters are behind a
 * button with a count on it rather than spread across the top; the column
 * headers sort; the reader picks which columns to see and the choice sticks in
 * their browser. The list is windowed, because a year of shots is a thousand
 * rows. It refreshes from the server's own events rather than a timer, so a
 * pull started in another tab shows up here.
 *
 * Three pieces of state, in three different places, on purpose. The filters
 * and the sort are in the query string, so a link carries them and the back
 * button undoes them. The visible columns are in `localStorage`, because they
 * are about this reader and not about this list. The selection is component
 * state, because it is about this minute.
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
  // Read once, lazily: `loadShotColumns` touches storage, which can throw, and
  // doing it in the initialiser keeps that to one call per mount rather than
  // one per render.
  const [columnIds, setColumnIds] = useState<ShotColumnId[]>(() => loadShotColumns());
  const columns = useMemo(() => visibleColumns(columnIds), [columnIds]);

  function chooseColumns(next: ShotColumnId[]) {
    setColumnIds(next);
    saveShotColumns(next);
  }

  /**
   * Clicking a header: the active column flips its order, another column
   * takes over descending. Descending because for every one of these — newest,
   * best, longest, highest rated — the interesting end is the top.
   */
  function sortBy(key: ShotSort) {
    if (key === filters.sort) {
      setFilters({ ...filters, order: filters.order === "asc" ? "desc" : "asc" });
      return;
    }
    setFilters({ ...filters, sort: key, order: "desc" });
  }

  const params = useMemo(() => toParams(filters, PAGE_SIZE), [filters]);
  const shots = useShotsInfinite(params);
  const sync = useSyncStatus();
  // Both sources, so an imported profile can be filtered on even though no
  // device profile points at it.
  const versions = useProfileVersions({ limit: 200 });
  // The filter popover's Set picker, and the header's "needs a Set" count.
  const sets = useSets();
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
  // When the archive last gained anything, because nothing fills it on its
  // own: "never pulled" next to a configured machine is the single most useful
  // sentence this page can say to somebody wondering where their shots are.
  const lastPull = lastFinishedShotRun(sync.data);
  const pulled = lastPull ? `Last pull ${relativeTime(lastPull.finished_at)}` : "Never pulled";
  const subtitle = counts
    ? `${counts.total} archived · ${counts.samples.toLocaleString()} samples` +
      (counts.quarantined ? ` · ${counts.quarantined} quarantined` : "") +
      ` · ${pulled}`
    : pulled;
  // A shot with no Set is invisible to every trend and to the analyser's view
  // of what has been tried, so the count is a call to action in the header
  // rather than a number buried in the filter bar.
  const needsSet = counts?.needs_set ?? 0;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Shots"
        subtitle={subtitle}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <PullButton />
            <ShotFilters
              value={filters}
              onChange={setFilters}
              versions={versions.data?.items ?? []}
              sets={sets.data?.items ?? []}
            />
            <ColumnChooser visible={columnIds} onChange={chooseColumns} />
            {needsSet > 0 && filters.set !== "needs" ? (
              <Button
                variant="outline"
                size="sm"
                data-testid="needs-set-count"
                onClick={() => setFilters({ ...filters, set: "needs" })}
              >
                <Layers className="size-3.5" aria-hidden="true" />
                {needsSet} need a Set
              </Button>
            ) : null}
            {selected.length > 0 ? (
              <Button variant="outline" size="sm" onClick={() => setCompareOpen(true)}>
                <GitCompare className="size-3.5" aria-hidden="true" />
                Compare {selected.length}
              </Button>
            ) : null}
          </div>
        }
      />

      <ImportDropZone />

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
              columns={columns}
              selected={selected}
              onToggleSelected={toggleSelected}
              scrollRef={scrollRef}
              maxCompare={MAX_COMPARE}
              sort={filters.sort}
              order={filters.order}
              onSort={sortBy}
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
                ? "Pull from the machine, or drop exported files here."
                : "Set the machine's address in Settings, or drop exported files here."
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
