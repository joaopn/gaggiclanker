import { useRef } from "react";
import { useShotSamples } from "@/hooks/useArchive";
import { useHasBeenVisible } from "@/hooks/useVirtualRows";
import { sparklinePath } from "@/lib/shotChart";

/** How many points a row's curve is thinned to. Enough for a shape, small
    enough that a hundred rows is a hundred small responses rather than a
    megabyte — and *evenly spaced*, never averaged, because averaging hides the
    spikes a shape is being scanned for (`gaggiclanker/api/shots.py`). */
export const SPARKLINE_POINTS = 40;

const WIDTH = 96;
const HEIGHT = 20;

/**
 * Pressure and puck flow for one row, drawn small.
 *
 * The fetch waits until the row has been scrolled to (`useHasBeenVisible`) and
 * the result is cached for ever (`useShotSamples` sets an infinite staleTime,
 * because samples never change once a shot is ingested). Scrolling a thousand
 * shots therefore costs one small request per row actually looked at, and
 * scrolling back up costs nothing.
 */
export function ShotSparkline({ shotId }: { shotId: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const visible = useHasBeenVisible(ref);
  const samples = useShotSamples(shotId, { downsample: SPARKLINE_POINTS, enabled: visible });

  const rows = samples.data?.samples ?? [];
  const pressure = sparklinePath(rows, "cp", WIDTH, HEIGHT);
  const flow = sparklinePath(rows, "pf", WIDTH, HEIGHT);

  return (
    <span ref={ref} className="inline-block" data-testid="shot-sparkline" data-shot={shotId}>
      {pressure || flow ? (
        <svg
          width={WIDTH}
          height={HEIGHT}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label="Pressure and flow"
          className="overflow-visible"
        >
          <title>Pressure and flow</title>
          {pressure ? (
            <path d={pressure} fill="none" stroke="var(--chart-1)" strokeWidth="1.25" />
          ) : null}
          {flow ? <path d={flow} fill="none" stroke="var(--chart-2)" strokeWidth="1.25" /> : null}
        </svg>
      ) : (
        // Reserves the row's height whether the fetch is in flight, refused, or
        // the shot is quarantined and has no samples at all.
        <span
          className="block rounded bg-muted/50"
          style={{ width: WIDTH, height: HEIGHT }}
          aria-hidden="true"
        />
      )}
    </span>
  );
}
