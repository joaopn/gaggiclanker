import type { ChartOptions } from "chart.js";
import { useMemo } from "react";
import { Line } from "react-chartjs-2";
import { chartPalette } from "@/components/charts/chartSetup";
import { useTheme } from "@/lib/theme";

export type LivePoint = {
  t: number;
  pressure: number | null;
  flow: number | null;
  weight: number | null;
  temperature: number | null;
};

/**
 * The shot happening now.
 *
 * Same three-axis layout as the shot page, minus the targets and the phase
 * bands: a live view has no transition table until the file is written, and a
 * dashed target line that appears and disappears as phases change reads as a
 * glitch. What it has instead is an axis that grows with the shot.
 */
export function LiveChart({ points, height = 260 }: { points: LivePoint[]; height?: number }) {
  const { isDark } = useTheme();

  const { data, options } = useMemo(() => {
    const palette = chartPalette(isDark);
    const series = (
      [
        ["Pressure", "pressure", 0, "y"],
        ["Flow", "flow", 1, "y"],
        ["Weight", "weight", 3, "yWeight"],
        ["Temperature", "temperature", 2, "yTemp"],
      ] as Array<[string, keyof LivePoint, number, string]>
    ).map(([label, key, color, axis]) => ({
      label,
      data: points
        .filter((point) => typeof point[key] === "number")
        .map((point) => ({ x: point.t / 1000, y: point[key] as number })),
      borderColor: palette.series[color],
      borderWidth: 1.75,
      pointRadius: 0,
      tension: 0,
      yAxisID: axis,
    }));

    return {
      data: { datasets: series },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        // Animating a chart that redraws twice a second means it is always
        // mid-transition and never shows the current value.
        animation: false as const,
        parsing: false as const,
        normalized: true,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: {
            type: "linear" as const,
            title: { display: true, text: "seconds", color: palette.text },
            ticks: { color: palette.text },
            grid: { color: palette.grid },
          },
          y: { beginAtZero: true, ticks: { color: palette.text }, grid: { color: palette.grid } },
          yTemp: {
            position: "right" as const,
            ticks: { color: palette.text },
            grid: { drawOnChartArea: false },
          },
          yWeight: {
            position: "right" as const,
            beginAtZero: true,
            ticks: { color: palette.text },
            grid: { drawOnChartArea: false },
          },
        },
      } satisfies ChartOptions<"line">,
    };
  }, [points, isDark]);

  return (
    <div style={{ height }} data-testid="live-chart">
      <Line data={data} options={options} aria-label="Live shot curves" />
      {/* The canvas says nothing to a screen reader and nothing to a test; this
          is the same information in the form that survives having no pixels. */}
      <ul className="sr-only" data-testid="live-series">
        {data.datasets.map((dataset) => {
          const first = dataset.data[0]?.x ?? 0;
          const last = dataset.data[dataset.data.length - 1]?.x ?? 0;
          return (
            <li key={dataset.label} data-series={dataset.label}>
              {`${dataset.label}: ${dataset.data.length} points, ${first.toFixed(1)}s to ${last.toFixed(1)}s`}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
