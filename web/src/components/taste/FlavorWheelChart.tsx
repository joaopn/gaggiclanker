import { useMemo } from "react";
import type { FlavorNode } from "@/api/types";
import { pathLabel, type Segment, segmentColor, sunburst, textOn } from "@/lib/flavorWheel";
import { cn } from "@/lib/utils";

/**
 * The flavour wheel as a sunburst: categories at the centre, their groups, the
 * notes at the rim, each segment in its category's colour.
 *
 * A pointer control only. A hundred and ten segments are a hundred and ten tab
 * stops, which is not a keyboard interface, so the drawing is hidden from
 * assistive technology and the page's "All notes" list is the control a
 * keyboard and a screen reader use — the two change the same list.
 *
 * Drawn in a fixed 800-unit `viewBox`, so it scales to whatever width it is
 * given: readable labels at desktop width, still a recognisable wheel on a
 * phone where the list underneath does the work.
 */

/**
 * Ring boundaries in viewBox units, centre out: ring 0 is 40–160, and so on.
 * Each ring is as wide as its longest label ("Green/Vegetative",
 * "Alcohol/Fermented", "Isovaleric acid") needs at the size below, measured in
 * DejaVu Sans, which is wider than the faces browsers actually use.
 */
const RADII = [40, 160, 262, 392];
/** Label size per ring; the rim has the most, and the narrowest, segments. */
const FONT = [12, 10.5, 10];

function point(radius: number, degrees: number): [number, number] {
  const radians = (degrees * Math.PI) / 180;
  return [radius * Math.sin(radians), -radius * Math.cos(radians)];
}

/** An annular sector, as SVG path data. */
function sectorPath(inner: number, outer: number, start: number, end: number): string {
  // A full circle cannot be one arc; a sliver short of it draws the same.
  const stop = end - start >= 360 ? start + 359.999 : end;
  const large = stop - start > 180 ? 1 : 0;
  const [x0, y0] = point(outer, start);
  const [x1, y1] = point(outer, stop);
  const [x2, y2] = point(inner, stop);
  const [x3, y3] = point(inner, start);
  const f = (value: number) => value.toFixed(2);
  return [
    `M${f(x0)} ${f(y0)}`,
    `A${outer} ${outer} 0 ${large} 1 ${f(x1)} ${f(y1)}`,
    `L${f(x2)} ${f(y2)}`,
    `A${inner} ${inner} 0 ${large} 0 ${f(x3)} ${f(y3)}`,
    "Z",
  ].join(" ");
}

/**
 * Where a segment's label sits and how it is turned: along the radius, at the
 * middle of the segment, flipped on the left half so it never reads upside
 * down.
 */
function labelPlacement(segment: Segment): { x: number; y: number; rotate: number } {
  const middle = (segment.start + segment.end) / 2;
  const radius = (RADII[segment.innerRing] + RADII[segment.outerRing + 1]) / 2;
  const [x, y] = point(radius, middle);
  const rotate = middle <= 180 ? middle - 90 : middle + 90;
  return { x, y, rotate };
}

export function FlavorWheelChart({
  wheel,
  selected,
  onToggle,
  className,
}: {
  wheel: FlavorNode[];
  /** The notes on the list being edited. */
  selected: readonly string[];
  onToggle: (note: string) => void;
  className?: string;
}) {
  const segments = useMemo(() => sunburst(wheel), [wheel]);
  const picked = new Set(selected);

  return (
    <svg
      viewBox="-400 -400 800 800"
      className={cn("h-auto w-full select-none", className)}
      aria-hidden="true"
      data-testid="flavor-wheel"
    >
      {segments.map((segment) => {
        const { note } = segment;
        const on = picked.has(note.value);
        const fill = segmentColor(note.category, note.depth);
        const label = labelPlacement(segment);
        return (
          // biome-ignore lint/a11y/noStaticElementInteractions: a pointer target inside an aria-hidden drawing; the "All notes" list is the keyboard control for the same toggle.
          <g
            key={note.value}
            data-note={note.value}
            data-picked={on ? "" : undefined}
            onClick={() => onToggle(note.value)}
            className="cursor-pointer [&:hover>path]:opacity-100"
          >
            <title>{pathLabel(note)}</title>
            <path
              d={sectorPath(
                RADII[segment.innerRing],
                RADII[segment.outerRing + 1],
                segment.start,
                segment.end,
              )}
              fill={fill}
              // Picked: full colour and an outline. Not picked: dimmed, so the
              // picks stand out at a glance whichever theme is behind them.
              opacity={on ? 1 : 0.5}
              className="transition-opacity"
              style={{
                stroke: on ? "var(--foreground)" : "var(--background)",
                strokeWidth: on ? 2 : 1,
              }}
            />
            <text
              x={0}
              y={0}
              transform={`translate(${label.x.toFixed(2)} ${label.y.toFixed(2)}) rotate(${label.rotate.toFixed(2)})`}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={FONT[segment.innerRing]}
              fontWeight={on ? 600 : 400}
              // The fills are the wheel's own colours in both themes, so a
              // picked segment's text is chosen against its fill. A dimmed one
              // is mostly page background, so it takes the theme's text colour.
              style={{ fill: on ? textOn(fill) : "var(--foreground)" }}
              className="pointer-events-none"
            >
              {note.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
