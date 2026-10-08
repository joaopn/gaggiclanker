import type { ChartOptions, ScriptableLineSegmentContext } from "chart.js";
import type { AnnotationOptions } from "chartjs-plugin-annotation";
import { memo, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Line } from "react-chartjs-2";
import { chartPalette } from "@/components/charts/chartSetup";
import { type CurvePoint, profileCurve } from "@/lib/profileCurve";
import { SHOT_SERIES } from "@/lib/shotChart";
import { useTheme } from "@/lib/theme";

/** The firmware's web page hides the phase names under this width. */
export const LABELS_MIN_WIDTH = 520;

/** A segment is dashed when the pump only limits the quantity there (the firmware's 6 on, 6 off). */
const LIMIT_DASH = [6, 6];

const seriesOf = (key: string) => {
  const spec = SHOT_SERIES.find((s) => s.key === key);
  if (!spec) throw new Error(`no shot series ${key}`);
  return spec;
};
const PRESSURE = seriesOf("pressure");
const FLOW = seriesOf("flow");

/** The same colour, translucent, for a limit: the firmware draws these at 0.6. */
function withAlpha(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim());
  if (!hex) return color;
  const digits =
    hex[1].length === 3
      ? hex[1]
          .split("")
          .map((c) => c + c)
          .join("")
      : hex[1];
  const [r, g, b] = [0, 2, 4].map((i) => Number.parseInt(digits.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** The firmware's `segment` callbacks look at the point a segment starts from. */
const startsOnTarget = (ctx: ScriptableLineSegmentContext): boolean =>
  (ctx.p0 as unknown as { raw: CurvePoint }).raw.target;

function seconds(value: number): string {
  return `${Number(value.toFixed(1))}`;
}

/**
 * A profile's curve, drawn as the machine's own web page draws it: the pressure (left axis,
 * 0-12 bar) and flow (right axis, 0-10 ml/s) the profile aims for over time, a line at the start
 * of each phase with its name. Solid is what the pump aims for in a phase; dashed is a quantity
 * the phase only limits.
 *
 * `xMax` fixes the end of the time axis, so two curves can be drawn on the same axes; `title`
 * is plain text the caller chooses, shown above the curve and used as the chart's name. Nothing is drawn for a profile the machine draws no curve for.
 *
 * The figure's text is the same information for a screen reader and for the tests, since a
 * canvas is neither.
 */
function ProfileCurveChartView({
  profile,
  xMax,
  title,
  height = 200,
}: {
  profile: Record<string, unknown>;
  xMax?: number;
  title?: string;
  height?: number;
}) {
  const { isDark } = useTheme();
  const curve = useMemo(() => profileCurve(profile), [profile]);

  // The chart's own width, not the window's: it sits in a column that is narrower than the
  // screen, and the names are drawn along it.
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    setWidth(element.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  // Until it is measured (and where nothing can measure it) the names are drawn.
  const showNames = width === null || width === 0 || width >= LABELS_MIN_WIDTH;

  const config = useMemo(() => {
    if (!curve) return null;
    const palette = chartPalette(isDark);
    const pressure = palette.series[PRESSURE.color];
    const flow = palette.series[FLOW.color];
    const dataset = (label: string, points: CurvePoint[], color: string, axis: string) => ({
      label,
      data: points,
      yAxisID: axis,
      borderColor: color,
      backgroundColor: color,
      borderWidth: 1.75,
      pointRadius: 0,
      tension: 0.4,
      cubicInterpolationMode: "monotone" as const,
      spanGaps: true,
      segment: {
        borderColor: (ctx: ScriptableLineSegmentContext) =>
          startsOnTarget(ctx) ? undefined : withAlpha(color, 0.6),
        borderDash: (ctx: ScriptableLineSegmentContext) =>
          startsOnTarget(ctx) ? undefined : LIMIT_DASH,
      },
    });

    const annotations: Record<string, AnnotationOptions> = {};
    curve.ranges.forEach((range, index) => {
      annotations[`phase-${index}`] = {
        type: "line" as const,
        xMin: range.start,
        xMax: range.start,
        drawTime: "afterDatasetsDraw" as const,
        borderColor: palette.text,
        borderWidth: 1,
        label: {
          display: showNames,
          content: range.name,
          rotation: -90,
          position: "end" as const,
          // Over the curves (the line's own draw time), on a translucent card colour.
          // The first name leans in, away from the axis it would otherwise sit on.
          xAdjust: index === 0 ? -7 : 8,
          padding: { x: 4, y: 0 },
          color: palette.text,
          backgroundColor: palette.label,
          font: { size: 10, weight: 500 },
        },
      };
    });

    const axisText = { color: palette.text };
    return {
      data: {
        datasets: [
          dataset("Pressure", curve.pressure, pressure, "y"),
          dataset("Flow", curve.flow, flow, "y1"),
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false as const,
        parsing: false as const,
        interaction: { intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
          annotation: { annotations },
        },
        scales: {
          x: {
            type: "linear" as const,
            min: 0,
            max: xMax ?? curve.xMax,
            ticks: {
              ...axisText,
              precision: 0,
              maxTicksLimit: 10,
              callback: (value: string | number) => `${Math.round(Number(value))}s`,
            },
            grid: { color: palette.grid },
          },
          y: {
            type: "linear" as const,
            position: "left" as const,
            min: 0,
            max: 12,
            title: { display: true, text: "Pressure (bar)", color: pressure },
            ticks: axisText,
            grid: { color: palette.grid },
          },
          y1: {
            type: "linear" as const,
            position: "right" as const,
            min: 0,
            max: 10,
            title: { display: true, text: "Flow (ml/s)", color: flow },
            ticks: axisText,
            grid: { drawOnChartArea: false },
          },
        },
      } satisfies ChartOptions<"line">,
      colors: { pressure, flow },
    };
  }, [curve, xMax, isDark, showNames]);

  if (!curve || !config || curve.ranges.length === 0) return null;
  const names = curve.ranges.map((range) => range.name).join(", ");

  return (
    <figure
      className="m-0 min-w-0 space-y-1"
      data-testid="profile-curve"
      data-names={showNames ? "shown" : "hidden"}
    >
      {title ? (
        <p className="font-medium text-sm" data-testid="profile-curve-title">
          {title}
        </p>
      ) : null}
      <div ref={box} className="min-w-0" style={{ height }}>
        <Line
          data={config.data}
          options={config.options}
          aria-label={title ? `${title}: the profile's curve` : "The profile's curve"}
        />
      </div>
      <p
        className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground text-xs"
        aria-hidden="true"
      >
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-0.5 w-5" style={{ background: config.colors.pressure }} />
          Pressure
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-0.5 w-5" style={{ background: config.colors.flow }} />
          Flow
        </span>
        <span>Solid: what the pump aims for. Dashed: only a limit.</span>
      </p>
      <figcaption className="sr-only" data-testid="profile-curve-summary">
        {`Pressure and flow the profile aims for over ${seconds(xMax ?? curve.xMax)} s: ${names}. Solid lines are what the pump aims for, dashed lines are only a limit.`}
      </figcaption>
    </figure>
  );
}

/** Memoised on the document: many versions are open at once, and none redraws for another. */
export const ProfileCurveChart = memo(ProfileCurveChartView);
