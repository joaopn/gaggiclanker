import type { ChartOptions, TooltipItem } from "chart.js";
import type { AnnotationOptions } from "chartjs-plugin-annotation";
import { useMemo } from "react";
import { Line } from "react-chartjs-2";
import type { ShotPhase, ShotSampleRow } from "@/api/types";
import { chartPalette, crosshairPlugin } from "@/components/charts/chartSetup";
import { type BuiltSeries, buildShotSeries, phaseBands } from "@/lib/shotChart";
import { exitReasonLabel } from "@/lib/shots";
import { useTheme } from "@/lib/theme";

/**
 * The shot, drawn: four signals and their targets on one time axis, with the
 * machine's own phase boundaries behind them.
 *
 * Three y-axes rather than four. Pressure (bar) and flow (ml/s) share one,
 * because they live in the same 0-12 range and reading them against each other
 * is most of the point; temperature and weight get their own, because a 93 °C
 * line on a 0-12 axis is a line off the top of the chart.
 *
 * A `<canvas>` says nothing to a screen reader and nothing to a test, so the
 * figure carries a text summary of what was drawn — which series, over what
 * range, and where the phases fell. It is the same information, in the form
 * that survives having no pixels.
 */
export function ShotChart({
  samples,
  phases,
  visible,
  finalExitReason,
  durationMs,
  height = 340,
}: {
  samples: ShotSampleRow[];
  phases?: ShotPhase[] | null;
  visible: string[];
  finalExitReason?: number | null;
  durationMs?: number;
  height?: number;
}) {
  // The palette lives in CSS custom properties, which a canvas cannot read, so
  // it is resolved here — and re-resolved when the theme flips, which is what
  // this subscription is for. `isDark` also picks the fallback palette for an
  // environment with no stylesheet attached.
  const { isDark } = useTheme();

  const built = useMemo(() => buildShotSeries(samples, visible), [samples, visible]);
  const bands = useMemo(() => phaseBands(phases), [phases]);

  const { data, options } = useMemo(() => {
    const palette = chartPalette(isDark);
    const usesTemp = built.some((series) => series.spec.axis === "yTemp");
    const usesWeight = built.some((series) => series.spec.axis === "yWeight");

    const annotations: Record<string, AnnotationOptions> = {};
    bands.forEach((band, index) => {
      annotations[`phase-${band.phaseNumber}-${index}`] = {
        type: "box" as const,
        xMin: band.start,
        xMax: band.end,
        // Behind the curves. chartjs-plugin-annotation draws every annotation
        // after the datasets unless told otherwise, which put the band on top
        // of the lines it is meant to frame.
        drawTime: "beforeDatasetsDraw" as const,
        // Alternating rather than one colour per phase: the boundary is the
        // information, and five named colours behind five series is noise.
        backgroundColor: index % 2 === 0 ? "transparent" : palette.band,
        borderWidth: 0,
        label: {
          display: true,
          content: band.name,
          position: { x: "center" as const, y: "start" as const },
          color: palette.text,
          font: { size: 10 },
          // The name stays above the curves: a label inherits its box's draw
          // time, and a pressure line crossing the top of the chart would
          // otherwise strike through it. Text over a line reads; a line over
          // text does not.
          drawTime: "afterDatasetsDraw" as const,
        },
      };
    });
    if (durationMs) {
      annotations.exit = {
        type: "line" as const,
        xMin: durationMs / 1000,
        xMax: durationMs / 1000,
        // Stated rather than left to the plugin's default: the end of the shot
        // is a mark to read against the curves, a 1px dash hides nothing, and
        // it should not move if the bands' draw time ever changes again.
        drawTime: "afterDatasetsDraw" as const,
        borderColor: palette.text,
        borderWidth: 1,
        borderDash: [2, 2],
        label: {
          display: true,
          content: exitReasonLabel(finalExitReason),
          position: "start",
          color: palette.text,
          font: { size: 10 },
          backgroundColor: "transparent",
        },
      };
    }

    return {
      data: {
        datasets: built.map((series) => ({
          label: series.spec.label,
          data: series.points,
          borderColor: palette.series[series.spec.color % palette.series.length],
          backgroundColor: palette.series[series.spec.color % palette.series.length],
          borderWidth: series.spec.dashed ? 1 : 1.75,
          borderDash: series.spec.dashed ? [4, 3] : undefined,
          yAxisID: series.spec.axis,
          pointRadius: 0,
          // The curve is 250 ms per point; smoothing it would invent shapes
          // that are not in the data, and shape is what a channel looks like.
          tension: 0,
          spanGaps: true,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false as const,
        // One tooltip listing every series at that instant, whether or not the
        // cursor is on a line: this is the crosshair readout, so requiring a
        // hit on a 1px path would make it unusable.
        interaction: { mode: "index" as const, intersect: false },
        parsing: false as const,
        normalized: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items: TooltipItem<"line">[]) =>
                items.length ? `${(items[0].parsed.x ?? 0).toFixed(1)} s` : "",
            },
          },
          annotation: { annotations },
        },
        scales: {
          x: {
            type: "linear" as const,
            title: { display: true, text: "seconds", color: palette.text },
            ticks: { color: palette.text },
            grid: { color: palette.grid },
          },
          y: {
            position: "left" as const,
            title: { display: true, text: "bar · ml/s", color: palette.text },
            ticks: { color: palette.text },
            grid: { color: palette.grid },
            beginAtZero: true,
          },
          yTemp: {
            display: usesTemp,
            position: "right" as const,
            title: { display: true, text: "°C", color: palette.text },
            ticks: { color: palette.text },
            grid: { drawOnChartArea: false },
          },
          yWeight: {
            display: usesWeight,
            position: "right" as const,
            title: { display: true, text: "g", color: palette.text },
            ticks: { color: palette.text },
            grid: { drawOnChartArea: false },
            beginAtZero: true,
          },
        },
      } satisfies ChartOptions<"line">,
    };
  }, [built, bands, durationMs, finalExitReason, isDark]);

  return (
    <figure className="m-0">
      <div style={{ height }} data-testid="shot-chart">
        <Line data={data} options={options} plugins={[crosshairPlugin]} aria-label="Shot curves" />
      </div>
      <figcaption className="sr-only">
        <ChartSummary built={built} bands={bands} />
      </figcaption>
    </figure>
  );
}

/** The chart as text. Read by screen readers, and by the test suite. */
function ChartSummary({
  built,
  bands,
}: {
  built: BuiltSeries[];
  bands: ReturnType<typeof phaseBands>;
}) {
  return (
    <>
      <ul data-testid="chart-series">
        {built.map((series) => {
          const values = series.points.map((point) => point.y);
          const max = values.length ? Math.max(...values) : 0;
          const last = values.length ? values[values.length - 1] : 0;
          return (
            <li key={series.spec.key} data-series={series.spec.key}>
              {`${series.spec.label}: ${series.points.length} points, peak ${max.toFixed(2)} ${
                series.spec.unit
              }, ending ${last.toFixed(2)} ${series.spec.unit}`}
            </li>
          );
        })}
      </ul>
      <ul data-testid="chart-phases">
        {bands.map((band) => (
          <li key={`${band.phaseNumber}-${band.start}`} data-phase={band.name}>
            {`${band.name}: ${band.start.toFixed(1)}s to ${band.end.toFixed(1)}s`}
          </li>
        ))}
      </ul>
    </>
  );
}
