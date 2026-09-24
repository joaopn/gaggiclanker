import { AlertTriangle, ArrowRight } from "lucide-react";
import { useLayoutEffect, useRef } from "react";
import { Link } from "react-router-dom";
import type { ShotDiagnosticsBlob, ShotListRow, ShotPhase } from "@/api/types";
import { VersionPrediction } from "@/components/sets/VersionPrediction";
import { JudgementForm } from "@/components/shots/JudgementForm";
import { ShotCurvesCard } from "@/components/shots/ShotCurvesCard";
import { Skeleton } from "@/components/ui/skeleton";
import { useShot, useShotSamples } from "@/hooks/useArchive";
import { formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * An open row in the shots list: what somebody needs to judge a shot without
 * leaving the list.
 *
 * The shot page's own two boxes, side by side: the judgement on the left and
 * the curves on the right. The same components as the page (`JudgementForm`,
 * `ShotCurvesCard`), so a verdict is given the same way in both places and
 * saved with the same button. The version's prediction sits above the
 * judgement as it does on the page, hidden until the shot has a decision.
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
  const diagnostics = (row?.diagnostics ?? {}) as ShotDiagnosticsBlob;

  return (
    <section
      ref={ref}
      id={id}
      aria-label={`Shot ${shot.device_id}`}
      data-testid="shot-panel"
      data-shot={shot.id}
      // Width 0 stretched to the row: the table is as wide as its columns, and
      // the panel's text at its widest (a note on one line) must not widen it.
      className="w-0 min-w-full border-border border-b bg-muted/20 px-3 py-3"
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

      <div className="grid gap-3 md:grid-cols-2" data-testid="panel-columns">
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
            />
          )}
        </div>
      </div>
    </section>
  );
}
