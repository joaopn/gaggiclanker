import type { ChartOptions } from "chart.js";
import { useMemo } from "react";
import { Line } from "react-chartjs-2";
import type { SetTrends } from "@/api/types";
import { chartPalette, crosshairPlugin } from "@/components/charts/chartSetup";
import { useTheme } from "@/lib/theme";

/**
 * The Set's trajectory: one point per shot, in the order they were pulled, with
 * a marked boundary wherever a new version began.
 *
 * Shot index rather than time on the x axis. A Set is pulled in bursts — six
 * shots on Saturday morning, nothing until Thursday — and a time axis spends
 * most of its width on the gaps, which is exactly the part with no information
 * in it. The version boundaries are annotations rather than a second series
 * because "what changed here" is the question the chart is answering.
 *
 * Four series on three axes, which is one more axis than it looks like it
 * needs. The execution score and the rating share the left, because both are
 * small integers and reading them against each other is the point — a clean
 * shot that tasted bad is the interesting case. Duration is tens of seconds and
 * gets the right. The **ratio gets its own**: it lives around 2 while duration
 * lives around 28, so sharing an axis with duration squashes it onto the
 * baseline and the one series that answers "did the recipe change" is drawn as
 * a flat line at zero. A hidden third axis costs nothing and is the difference
 * between a series and a decoration.
 */
export function SetTrendChart({ trends, height = 220 }: { trends: SetTrends; height?: number }) {
  const { isDark } = useTheme();

  const { data, options, boundaries } = useMemo(() => {
    const palette = chartPalette(isDark);
    const labels = trends.shots.map((_, index) => String(index + 1));

    // The x position where each version after the first begins. Computed from
    // the ordered points rather than from timestamps, because the points are
    // what the axis is indexed by.
    const marks: Array<{ at: number; version: number; intent: string }> = [];
    trends.shots.forEach((point, index) => {
      const previous = trends.shots[index - 1];
      if (previous && previous.set_version_id !== point.set_version_id) {
        const summary = trends.versions.find(
          (version) => version.set_version_id === point.set_version_id,
        );
        marks.push({
          at: index,
          version: point.version_no,
          intent: summary?.intent ?? "",
        });
      }
    });

    const series = [
      {
        label: "Execution score",
        data: trends.shots.map((point) => point.execution_score),
        borderColor: palette.series[0],
        yAxisID: "y",
      },
      {
        label: "Your rating",
        data: trends.shots.map((point) => point.rating),
        borderColor: palette.series[2],
        yAxisID: "y",
        borderDash: [4, 3],
      },
      {
        label: "Duration (s)",
        data: trends.shots.map((point) => point.duration_s),
        borderColor: palette.series[1],
        yAxisID: "y2",
      },
      {
        label: "Ratio",
        data: trends.shots.map((point) => point.ratio),
        borderColor: palette.series[3],
        yAxisID: "y3",
        borderDash: [2, 2],
      },
    ].map((dataset) => ({
      ...dataset,
      borderWidth: 1.75,
      pointRadius: 2,
      tension: 0,
      // A shot with no rating is a gap in the line, not a zero: most shots are
      // never rated and a zero would draw the taste of every one of them at the
      // bottom of the chart.
      spanGaps: true,
    }));

    return {
      boundaries: marks,
      data: { labels, datasets: series },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false as const,
        interaction: { mode: "index" as const, intersect: false },
        plugins: {
          legend: { display: true, labels: { color: palette.text, boxWidth: 10 } },
          annotation: {
            annotations: Object.fromEntries(
              marks.map((mark) => [
                `v${mark.version}`,
                {
                  type: "line" as const,
                  xMin: mark.at - 0.5,
                  xMax: mark.at - 0.5,
                  borderColor: palette.text,
                  borderWidth: 1,
                  borderDash: [3, 3],
                  label: {
                    display: true,
                    content: `v${mark.version}`,
                    position: "start" as const,
                    color: palette.text,
                    backgroundColor: "transparent",
                    font: { size: 10 },
                  },
                },
              ]),
            ),
          },
        },
        scales: {
          x: {
            title: { display: true, text: "shots, oldest first", color: palette.text },
            ticks: { color: palette.text },
            grid: { color: palette.grid },
          },
          y: {
            beginAtZero: true,
            suggestedMax: 10,
            title: { display: true, text: "score · rating", color: palette.text },
            ticks: { color: palette.text },
            grid: { color: palette.grid },
          },
          y2: {
            position: "right" as const,
            beginAtZero: true,
            title: { display: true, text: "seconds", color: palette.text },
            ticks: { color: palette.text },
            grid: { display: false },
          },
          // Not drawn: a third set of tick labels on a chart this size is more
          // clutter than information, and the ratio is read off the tooltip and
          // the shape of the line. `beginAtZero` is deliberately off — a ratio
          // never approaches zero, and anchoring there would flatten it again.
          y3: {
            display: false,
            position: "right" as const,
            grid: { display: false },
          },
        },
      } satisfies ChartOptions<"line">,
    };
  }, [trends, isDark]);

  return (
    <div style={{ height }} data-testid="set-trend-chart">
      <Line
        data={data}
        options={options}
        plugins={[crosshairPlugin]}
        aria-label="How this Set's shots have gone, version by version"
      />
      {/* A canvas is invisible to a screen reader and to a test, so each chart
          renders what it drew in text. This is what the page tests assert on. */}
      <ul className="sr-only" data-testid="set-trend-summary">
        {data.datasets.map((dataset) => (
          <li key={dataset.label}>
            {`${dataset.label}: ${dataset.data.filter((value) => value != null).length} points`}
          </li>
        ))}
        {boundaries.map((mark) => (
          <li key={mark.version}>{`Version ${mark.version} begins at shot ${mark.at + 1}`}</li>
        ))}
      </ul>
    </div>
  );
}
