import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Title,
  Tooltip,
} from "chart.js";
import annotationPlugin from "chartjs-plugin-annotation";

/**
 * Chart.js, registered once for the whole bundle.
 *
 * Tree-shaking is the reason this file exists: Chart.js 4 ships nothing
 * registered, so every controller, scale and plugin a chart uses has to be
 * named. Doing it here rather than in each component means a second chart type
 * cannot half-register and fail at runtime with "line is not a registered
 * controller" — and it means the whole of Chart.js lands in one lazily-loaded
 * chunk (see `App.tsx`: the shot pages are `React.lazy`, so a list-only visit
 * never downloads it).
 */
let registered = false;

export function registerChartJs(): void {
  if (registered) return;
  ChartJS.register(
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    Title,
    Tooltip,
    Legend,
    Filler,
    annotationPlugin,
  );
  registered = true;
}

registerChartJs();

/**
 * The palette, read out of the stylesheet.
 *
 * A canvas cannot resolve `var(--chart-1)`, so the values are read from the
 * document at render time and the component re-reads them when the theme flips.
 * The fallbacks are the light palette's own values (`index.css`), for a test
 * environment where no stylesheet is attached.
 */
const FALLBACK = {
  light: {
    series: ["#8a4b1f", "#3a6786", "#a33526", "#4a7239", "#7a5ea8"],
    grid: "#00000014",
    text: "#5c5147",
    band: "#33291f14",
  },
  dark: {
    series: ["#d59a63", "#8fb4d0", "#e08a7c", "#92c081", "#b7a3dd"],
    grid: "#ffffff1f",
    text: "#a79c91",
    band: "#ece5db12",
  },
};

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined" || !document.documentElement) return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export type ChartPalette = {
  series: string[];
  grid: string;
  text: string;
  band: string;
};

export function chartPalette(isDark = false): ChartPalette {
  const fallback = isDark ? FALLBACK.dark : FALLBACK.light;
  return {
    series: fallback.series.map((value, index) => cssVar(`--chart-${index + 1}`, value)),
    grid: cssVar("--border", fallback.grid),
    text: cssVar("--muted-foreground", fallback.text),
    // Its own token rather than --muted: --muted is an opaque surface colour,
    // and the band is painted over the plot area, so it has to be see-through.
    band: cssVar("--chart-band", fallback.band),
  };
}

/**
 * A vertical line under the cursor.
 *
 * Chart.js draws the tooltip but not the rule, and without it a tooltip
 * carrying nine values gives no clue *where* on the shot those nine values
 * were read. Registered per-chart rather than globally: the live view does not
 * want one.
 */
export const crosshairPlugin = {
  id: "gaggiclanker-crosshair",
  afterDatasetsDraw(chart: ChartJS): void {
    const active = chart.tooltip?.getActiveElements?.() ?? [];
    if (active.length === 0) return;
    const { ctx, chartArea } = chart;
    const x = active[0].element.x;
    ctx.save();
    ctx.beginPath();
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = chartPalette(
      document.documentElement.dataset.theme === "dark" ||
        document.documentElement.classList.contains("dark"),
    ).text;
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.restore();
  },
};
