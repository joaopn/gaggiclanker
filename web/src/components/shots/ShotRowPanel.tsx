import { AlertTriangle, ArrowRight } from "lucide-react";
import { lazy, Suspense, useLayoutEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import type { ShotListRow, ShotPhase } from "@/api/types";
import { DeviceNotesCard } from "@/components/shots/DeviceNotesCard";
import { JudgementForm } from "@/components/shots/JudgementForm";
import { Skeleton } from "@/components/ui/skeleton";
import { useShot, useShotSamples } from "@/hooks/useArchive";
import { DEFAULT_SERIES } from "@/lib/shotChart";
import { formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * Lazy, like the shot page and the compare drawer: the list with no row open
 * must not download Chart.js, and opening a row is the first moment it needs
 * it. The chart module is shared with the shot page, so a visit that opens a
 * row and then the page downloads it once.
 */
const ShotChart = lazy(() =>
  import("@/components/charts/ShotChart").then((module) => ({ default: module.ShotChart })),
);

/** Tall enough to read a phase, short enough that the form beside it is the same size. */
const CHART_HEIGHT = 200;

/**
 * An open row in the shots list: what somebody needs to judge a shot without
 * leaving the list.
 *
 * Three things, because those are the three that were asked for: the curve,
 * the verdict and the machine's own notes. The judgement is the shot page's
 * own form rather than a smaller copy, so a verdict written here is the same
 * verdict with the same fields, and the row editor's three-field panel is not
 * turned into a fourth way to write one. The curve and the notes are links to
 * the shot page — they are what somebody clicks when they want more of the
 * same — while the form is not, because a click inside a form is a click on a
 * field. "Open shot page" is the explicit way there, for a keyboard and for a
 * new tab; the other two links are out of the tab order so the panel is not
 * three stops to the same place.
 *
 * The detail and the full curve are fetched when the panel mounts, with the
 * hooks and the cache keys the shot page uses: opening the page after the row
 * costs nothing, and a verdict saved on either invalidates both.
 *
 * `onMeasure` reports the panel's height on every layout change, because the
 * list is windowed and has to know how far down it pushes the rows under it
 * (`useVirtualRows`). `ready` says the content has arrived, so the table knows
 * when a height is final enough to stop scrolling it into view.
 */
export function ShotRowPanel({
  shot,
  id,
  onMeasure,
}: {
  shot: ShotListRow;
  id: string;
  onMeasure: (height: number, ready: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const detail = useShot(shot.id);
  // A quarantined shot has no samples at all; asking would be a 404 per open.
  const samples = useShotSamples(shot.id, { enabled: !shot.quarantined });
  const ready = !detail.isPending && (shot.quarantined || !samples.isPending);

  // Measured after every commit, and on every resize after that — the form's
  // taste chips wrap differently at every width, and the vocabulary arrives a
  // moment after the panel does. `getBoundingClientRect` rather than
  // `offsetHeight` so a fractional height does not accumulate into a row's
  // worth of drift over a long scroll.
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    onMeasure(element.getBoundingClientRect().height, ready);
  });
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      onMeasure(element.getBoundingClientRect().height, ready);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [onMeasure, ready]);

  const shotPage = `/shots/${shot.id}`;
  const rows = useMemo(() => samples.data?.samples ?? [], [samples.data]);
  const row = detail.data?.shot;

  return (
    <section
      ref={ref}
      id={id}
      aria-label={`Shot ${shot.device_id}`}
      data-testid="shot-panel"
      data-shot={shot.id}
      className="border-border border-b bg-muted/20 px-3 py-3"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 truncate text-muted-foreground text-xs">
          {profileName(shot)} · {formatTime(shot.started_at)} · shot {shot.device_id}
        </p>
        <Link
          to={shotPage}
          data-testid="open-shot-page"
          className={cn(
            "inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm hover:bg-muted",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          Open shot page
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </Link>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="min-w-0 space-y-3">
          {shot.quarantined ? (
            <div
              className="rounded-md border border-border bg-muted/50 p-3"
              data-testid="panel-quarantined"
            >
              <p className="flex items-center gap-1.5 font-medium text-sm">
                <AlertTriangle className="size-3.5 text-status-warn-text" aria-hidden="true" />
                Quarantined: there is no curve to draw
              </p>
              <p className="mt-1 font-mono text-muted-foreground text-xs">
                {shot.quarantine_reason ?? "No reason was recorded."}
              </p>
            </div>
          ) : samples.isPending || detail.isPending ? (
            <Skeleton className="w-full" style={{ height: CHART_HEIGHT }} />
          ) : samples.isError ? (
            <p className="text-muted-foreground text-sm">
              Could not load the curve: {samples.error.message}
            </p>
          ) : rows.length === 0 ? (
            <p className="text-muted-foreground text-sm">This shot has no stored samples.</p>
          ) : (
            <Link
              to={shotPage}
              tabIndex={-1}
              data-testid="panel-curve"
              className="block rounded-md border border-border bg-background p-2 hover:border-foreground/30"
            >
              <Suspense fallback={<Skeleton className="w-full" style={{ height: CHART_HEIGHT }} />}>
                <ShotChart
                  samples={rows}
                  phases={(row?.phases ?? []) as ShotPhase[]}
                  visible={DEFAULT_SERIES}
                  finalExitReason={row?.final_exit_reason}
                  durationMs={shot.duration_ms}
                  height={CHART_HEIGHT}
                />
              </Suspense>
            </Link>
          )}

          {detail.data?.notes ? (
            <Link
              to={shotPage}
              tabIndex={-1}
              data-testid="panel-notes"
              className="block rounded-xl hover:ring-1 hover:ring-foreground/20"
            >
              <DeviceNotesCard
                notes={detail.data.notes}
                description="What the machine's own notes card holds for this shot."
              />
            </Link>
          ) : null}
        </div>

        <div className="min-w-0">
          {detail.isPending ? (
            <Skeleton className="h-64 w-full" />
          ) : detail.isError ? (
            <p className="text-muted-foreground text-sm" data-testid="panel-error">
              Could not load this shot: {detail.error.message}
            </p>
          ) : (
            <JudgementForm shotId={shot.id} judgement={detail.data.judgement} compact />
          )}
        </div>
      </div>
    </section>
  );
}
