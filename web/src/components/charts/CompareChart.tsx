import type { ChartOptions } from "chart.js";
import { useMemo } from "react";
import { Line } from "react-chartjs-2";
import type { ShotListRow, ShotSampleRow } from "@/api/types";
import { chartPalette, crosshairPlugin } from "@/components/charts/chartSetup";
import { profileName } from "@/lib/shots";
import { useTheme } from "@/lib/theme";

export type CompareSeries = { shot: ShotListRow; samples: ShotSampleRow[] };

/**
 * Pressure and puck flow for up to three shots on one time axis.
 *
 * One colour per *shot*, not per signal: the question is "how do these two
 * differ", so the eye has to group by shot first. Pressure is solid and flow
 * dashed within each colour.
 */
export function CompareChart({
  series,
  height = 180,
}: {
  series: CompareSeries[];
  height?: number;
}) {
  const { isDark } = useTheme();

  const { data, options } = useMemo(() => {
    const palette = chartPalette(isDark);
    const datasets = series.flatMap(({ shot, samples }, index) => {
      const color = palette.series[index % palette.series.length];
      const points = (field: "cp" | "pf") =>
        samples
          .filter((sample) => typeof sample[field] === "number")
          .map((sample) => ({ x: sample.t_ms / 1000, y: sample[field] as number }));
      return [
        {
          label: `${profileName(shot)} — pressure`,
          data: points("cp"),
          borderColor: color,
          borderWidth: 1.75,
          pointRadius: 0,
          tension: 0,
        },
        {
          label: `${profileName(shot)} — puck flow`,
          data: points("pf"),
          borderColor: color,
          borderWidth: 1,
          borderDash: [4, 3],
          pointRadius: 0,
          tension: 0,
        },
      ];
    });

    return {
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false as const,
        interaction: { mode: "index" as const, intersect: false },
        parsing: false as const,
        normalized: true,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            type: "linear" as const,
            title: { display: true, text: "seconds", color: palette.text },
            ticks: { color: palette.text },
            grid: { color: palette.grid },
          },
          y: {
            beginAtZero: true,
            title: { display: true, text: "bar · ml/s", color: palette.text },
            ticks: { color: palette.text },
            grid: { color: palette.grid },
          },
        },
      } satisfies ChartOptions<"line">,
    };
  }, [series, isDark]);

  return (
    <div style={{ height }} data-testid="compare-chart">
      <Line data={data} options={options} plugins={[crosshairPlugin]} aria-label="Shot overlay" />
      <ul className="sr-only" data-testid="compare-series">
        {data.datasets.map((dataset) => (
          <li key={dataset.label}>{`${dataset.label}: ${dataset.data.length} points`}</li>
        ))}
      </ul>
    </div>
  );
}
