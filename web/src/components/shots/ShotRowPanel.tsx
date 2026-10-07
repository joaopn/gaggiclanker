import { AlertTriangle, ArrowRight } from "lucide-react";
import { type RefObject, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ShotListRow } from "@/api/types";
import { ShotBoxes, useShotBoxState } from "@/components/shots/ShotBoxes";
import { Skeleton } from "@/components/ui/skeleton";
import { useShot, useShotFields, useShotSamples } from "@/hooks/useArchive";
import { formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * An open row in the shots list: what somebody needs to judge a shot without leaving the list.
 *
 * The shot page's own boxes, in the same fixed order, drawn by the same component
 * (`ShotBoxes`): Your judgement, the version's prediction, Curves, Review and Curve check. A
 * verdict is given the same way in both places and saved with the same button, a box is open or
 * folded as the person last left it, and "Open shot page" is the way to everything else the page
 * has. The judgement is as wide as the row's visible box and the curves sit on a row of their own
 * below it, never beside it.
 *
 * The detail and the fields are fetched when the panel mounts, with the hooks and the cache keys
 * the shot page uses: opening the page after the row costs nothing, and a verdict saved on either
 * invalidates both. The full curve is fetched only while the Curves box is open.
 *
 * `onMeasure` reports the panel's height on every layout change, because the list is windowed and
 * has to know how far down it pushes the rows under it (`useVirtualRows`). `ready` says the
 * content has arrived, so the table knows when a height is final enough to stop scrolling it into
 * view.
 */
export function ShotRowPanel({
  shot,
  id,
  onMeasure,
  scrollRef,
}: {
  shot: ShotListRow;
  id: string;
  onMeasure: (height: number, ready: boolean) => void;
  /** The table's scroll box: the panel's content is as wide as what it shows, and stays in view. */
  scrollRef?: RefObject<HTMLElement | null>;
}) {
  const visibleWidth = useVisibleWidth(scrollRef);
  const ref = useRef<HTMLDivElement>(null);
  const detail = useShot(shot.id);
  // The same query as the shot page's (one key, so invalidation covers both): the Review box
  // takes each free-text expectation's tier and sentence from the checks, not from a guess.
  const fields = useShotFields(shot.id, { enabled: !shot.quarantined });
  const chartId = `row-curves-${shot.id}`;
  // What the Review box marks on this row's own chart, keyed to the row by the panel's own
  // lifetime: it mounts when the row opens and goes when it closes.
  const { curvesOpen, claimSpan } = useShotBoxState(
    detail.data?.review?.in_force_id,
    chartId,
    shot.id,
  );
  // A quarantined shot has no samples at all, and a folded Curves box draws nothing: asking would
  // be a 404 per open, or a request for a chart nobody is looking at.
  const samples = useShotSamples(shot.id, { enabled: !shot.quarantined && curvesOpen });
  const ready = !detail.isPending && (shot.quarantined || !curvesOpen || !samples.isPending);

  // Measured after every commit, and on every resize after that — the flavour
  // chips wrap differently at every width, and the vocabulary and the picks
  // arrive a moment after the panel does. `getBoundingClientRect` rather than
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

  return (
    <section
      ref={ref}
      id={id}
      aria-label={`Shot ${shot.device_id}`}
      data-testid="shot-panel"
      data-shot={shot.id}
      // Width 0 stretched to the row: the table is as wide as its columns, and
      // the panel's text at its widest (a note on one line) must not widen it. The content
      // is a narrower box inside it (below), so the border and the tint still run the row's
      // full width while the table scrolls sideways under them.
      className="w-0 min-w-full border-border border-b bg-muted/20"
    >
      {/* As wide as the scroll box's visible width, and `sticky left-0`: the row's columns keep
          scrolling sideways, but what the panel says (a card, a chart, a form) stays in view and
          is never wider than the screen it is read on. Until the width is known (no layout yet)
          it falls back to the whole row. */}
      <div
        data-testid="panel-inner"
        className="sticky left-0 box-border min-w-0 px-3 py-3"
        style={visibleWidth === null ? undefined : { width: visibleWidth }}
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

        <div className="space-y-3" data-testid="panel-rows">
          {detail.isPending ? (
            <Skeleton className="h-64 w-full" />
          ) : detail.isError ? (
            <p className="text-muted-foreground text-sm" data-testid="panel-error">
              Could not load this shot: {detail.error.message}
            </p>
          ) : (
            <ShotBoxes
              detail={detail.data}
              fields={fields.data}
              samples={samples}
              chartId={chartId}
              claimSpan={claimSpan}
              whenQuarantined={
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
              }
            />
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * The visible width of the table's scroll box (`clientWidth`, which leaves the scrollbar out),
 * kept current by a `ResizeObserver`; `null` while it is not known, as in a test with no layout.
 */
export function useVisibleWidth(ref: RefObject<HTMLElement | null> | undefined): number | null {
  const [width, setWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = ref?.current;
    if (!element) return;
    const read = () => setWidth(element.clientWidth > 0 ? element.clientWidth : null);
    read();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(read);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
