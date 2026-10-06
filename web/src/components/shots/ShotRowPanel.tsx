import { AlertTriangle, ArrowRight } from "lucide-react";
import { type RefObject, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ShotDiagnosticsBlob, ShotListRow, ShotPhase } from "@/api/types";
import { VersionPrediction } from "@/components/sets/VersionPrediction";
import { JudgementForm } from "@/components/shots/JudgementForm";
import { ReviewCard } from "@/components/shots/ReviewCard";
import { ShotRowChecksCard } from "@/components/shots/ShotChecksCard";
import { ShotCurvesCard } from "@/components/shots/ShotCurvesCard";
import { Skeleton } from "@/components/ui/skeleton";
import { useShot, useShotFields, useShotSamples } from "@/hooks/useArchive";
import { useClaimSpan } from "@/lib/claimSpan";
import { formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * An open row in the shots list: what somebody needs to judge a shot without
 * leaving the list.
 *
 * The shot's checks first (the failed ones and the warnings its row carries), when it has any, then the shot page's own two boxes, stacked as they are on the page: the
 * judgement across the full width (its own two columns, the notes on the
 * right), and the curves on a row of their own below it, never beside it. The
 * same components as the page (`JudgementForm`, `ShotCurvesCard`), so a
 * verdict is given the same way in both places and saved with the same button.
 * The version's prediction sits above the judgement as it does on the page,
 * hidden until the shot has a decision.
 * "Open shot page" is the way to everything else the page has.
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
  // The same query as the shot page's (one key, so invalidation covers both): the Reading card
  // takes each free-text expectation's tier and sentence from the checks, not from a guess.
  const fields = useShotFields(shot.id, { enabled: !shot.quarantined });
  const chartId = `row-curves-${shot.id}`;
  // What the reading card marks on this row's own chart, keyed to the row by the panel's own
  // lifetime: it mounts when the row opens and goes when it closes.
  const claimSpan = useClaimSpan(detail.data?.reading?.in_force_id, chartId);
  // A quarantined shot has no samples at all; asking would be a 404 per open.
  const samples = useShotSamples(shot.id, { enabled: !shot.quarantined });
  const ready = !detail.isPending && (shot.quarantined || !samples.isPending);

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
  const row = detail.data?.shot;
  const reading = detail.data?.reading ?? shot.reading;
  const diagnostics = (row?.diagnostics ?? {}) as ShotDiagnosticsBlob;

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

        {/* What is plainly wrong comes first here as on the page. The list row
          carries the warnings, so there is nothing more to fetch. */}
        <div className="mb-3 empty:hidden">
          <ShotRowChecksCard warnings={shot.warnings} />
        </div>

        <div className="space-y-3" data-testid="panel-rows">
          <div className="min-w-0 space-y-3">
            {detail.isPending ? (
              <Skeleton className="h-64 w-full" />
            ) : detail.isError ? (
              <p className="text-muted-foreground text-sm" data-testid="panel-error">
                Could not load this shot: {detail.error.message}
              </p>
            ) : (
              <>
                <VersionPrediction
                  key={shot.id}
                  shotId={shot.id}
                  version={detail.data.set_version}
                  decision={detail.data.judgement?.decision ?? null}
                />
                <JudgementForm shotId={shot.id} judgement={detail.data.judgement} />
              </>
            )}
          </div>

          <div className="min-w-0">
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
            ) : samples.isError ? (
              <p className="text-muted-foreground text-sm">
                Could not load the curve: {samples.error.message}
              </p>
            ) : (
              <ShotCurvesCard
                shotId={shot.id}
                deviceId={shot.device_id}
                samples={samples.data}
                pending={samples.isPending || detail.isPending}
                phases={(row?.phases ?? []) as ShotPhase[]}
                hasPressure={diagnostics.has_pressure !== false}
                finalExitReason={row?.final_exit_reason}
                durationMs={shot.duration_ms}
                highlight={claimSpan.shown}
                chartId={chartId}
              />
            )}
          </div>

          {/* The reading itself, the same card as on the shot page and directly under the curve its
            claims point into: it is the information a shot is judged by, not a link away. A
            discarded shot shows its last reading and no Read button. No `key` here, on purpose:
            the table keys each row by its shot and mounts this panel only for the open one, so
            the panel (and the card's state) is one shot's for its whole life. */}
          {shot.quarantined || detail.isPending || detail.isError ? null : (
            <div className="min-w-0" data-testid="panel-reading">
              <ReviewCard
                shotId={shot.id}
                reviews={detail.data.reviews ?? []}
                reading={reading}
                checks={fields.data?.checks}
                span={claimSpan.controls}
                decision={detail.data.judgement?.decision ?? null}
                hasPrediction={Boolean(detail.data.set_version?.prediction)}
                badge={shot.badge}
                warnings={shot.warnings}
              />
            </div>
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
