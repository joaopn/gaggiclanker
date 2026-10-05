import { useRef } from "react";
import { useShotSamples } from "@/hooks/useArchive";
import { useHasBeenVisible } from "@/hooks/useVirtualRows";
import { sparklineCurves } from "@/lib/shotChart";
import { type CurveChoice, curveColour } from "@/lib/shotCurves";

/** How many points a row's curve is thinned to. Enough for a shape, small
    enough that a hundred rows is a hundred small responses rather than a
    megabyte — and *evenly spaced*, never averaged, because averaging hides the
    spikes a shape is being scanned for (`gaggiclanker/api/shots.py`). */
export const SPARKLINE_POINTS = 40;

const WIDTH = 96;
const HEIGHT = 20;

/**
 * The chosen curves for one row, drawn small (pressure and puck flow unless
 * the reader chose otherwise).
 *
 * The fetch waits until the row has been scrolled to (`useHasBeenVisible`) and
 * the result is cached for ever (`useShotSamples` sets an infinite staleTime,
 * because samples never change once a shot is ingested). Scrolling a thousand
 * shots therefore costs one small request per row actually looked at, and
 * scrolling back up costs nothing.
 */
export function ShotSparkline({ shotId, curves }: { shotId: number; curves: CurveChoice }) {
  const ref = useRef<HTMLSpanElement>(null);
  const visible = useHasBeenVisible(ref);
  const samples = useShotSamples(shotId, { downsample: SPARKLINE_POINTS, enabled: visible });

  const rows = samples.data?.samples ?? [];
  const drawn = sparklineCurves(rows, curves.shown, WIDTH, HEIGHT);
  const names = drawn.map((curve) => curve.spec.label).join(", ");

  return (
    <span ref={ref} className="inline-block" data-testid="shot-sparkline" data-shot={shotId}>
      {drawn.length > 0 ? (
        <svg
          width={WIDTH}
          height={HEIGHT}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={names}
          className="overflow-visible"
        >
          <title>{names}</title>
          {drawn.map(({ spec, d }) => (
            <path
              key={spec.key}
              d={d}
              data-series={spec.key}
              fill="none"
              stroke={`var(${curveColour(curves, spec.key)})`}
              strokeWidth="1.25"
              // Targets are what was commanded, drawn dashed against what happened.
              strokeDasharray={spec.dashed ? "3 2" : undefined}
            />
          ))}
        </svg>
      ) : (
        // Reserves the row's height whether the fetch is in flight, refused, or
        // the shot is quarantined and has no samples at all.
        <span
          data-testid="sparkline-placeholder"
          className="block rounded bg-muted/50"
          style={{ width: WIDTH, height: HEIGHT }}
          aria-hidden="true"
        />
      )}
    </span>
  );
}
