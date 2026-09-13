import { readFileSync } from "node:fs";
import path from "node:path";
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ShotPhase } from "@/api/types";
import { ShotChart } from "@/components/charts/ShotChart";
import { DEFAULT_SERIES } from "@/lib/shotChart";
import { DARK_THEME_ID, LIGHT_THEME_ID, THEME_STORAGE_KEY } from "@/lib/theme";
import { shot129, shot129Samples } from "@/test/shotFixture";

/**
 * The options the chart was handed.
 *
 * `<Line>` is replaced rather than inspected: the real component draws to a
 * canvas, which is opaque to jsdom, and what is under test — the colour and the
 * draw order of the phase bands — is decided before anything is drawn.
 */
type Annotation = {
  type: string;
  drawTime?: string;
  backgroundColor?: string;
  label?: { drawTime?: string; display?: boolean };
};
type ChartProps = {
  options?: { plugins?: { annotation?: { annotations?: Record<string, Annotation> } } };
};
let lastChartProps: ChartProps | null = null;
vi.mock("react-chartjs-2", () => ({
  Line: (props: ChartProps) => {
    lastChartProps = props;
    return <canvas />;
  },
}));

/**
 * The two palette blocks of the real stylesheet, as text.
 *
 * The whole of `index.css` is Tailwind input jsdom cannot parse, but the
 * palettes are plain custom-property blocks. Injecting them is what puts the
 * app's own `--muted` (an opaque surface colour) in reach of
 * `getComputedStyle`; without a stylesheet the chart reads its translucent
 * fallbacks, which is how opaque bands shipped with a green suite.
 */
const css = readFileSync(path.resolve(__dirname, "../../index.css"), "utf8");
function paletteBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`index.css has no ${selector} block`);
  return css.slice(start, css.indexOf("}", start) + 1);
}
const PALETTES = `${paletteBlock(":root")}\n${paletteBlock(`:root[data-theme="${DARK_THEME_ID}"]`)}`;

/** The alpha of a CSS colour a canvas accepts: hex, rgb()/rgba(), or `transparent`. */
function alphaOf(colour: string): number {
  const value = colour.trim().toLowerCase();
  if (value === "transparent") return 0;
  const hex = /^#([0-9a-f]{3,8})$/.exec(value)?.[1];
  if (hex) {
    if (hex.length === 4) return Number.parseInt(hex[3].repeat(2), 16) / 255;
    if (hex.length === 8) return Number.parseInt(hex.slice(6), 16) / 255;
    if (hex.length === 3 || hex.length === 6) return 1;
  }
  const fn = /^rgba?\(([^)]*)\)$/.exec(value)?.[1];
  if (fn) {
    const parts = fn.split(/[\s,/]+/).filter(Boolean);
    if (parts.length === 3) return 1;
    const alpha = parts[3];
    return alpha.endsWith("%") ? Number.parseFloat(alpha) / 100 : Number.parseFloat(alpha);
  }
  throw new Error(`cannot read the alpha of ${JSON.stringify(colour)}`);
}

const phases = (shot129.shot.phases ?? []) as ShotPhase[];

function renderShot129() {
  render(
    <ShotChart
      samples={shot129Samples.samples}
      phases={phases}
      visible={DEFAULT_SERIES}
      finalExitReason={shot129.shot.final_exit_reason}
      durationMs={shot129.shot.duration_ms}
    />,
  );
  const annotations = lastChartProps?.options?.plugins?.annotation?.annotations ?? {};
  return Object.values(annotations);
}

describe.each([
  { theme: LIGHT_THEME_ID, dark: false, muted: "#ece2d2", band: "#33291f14" },
  { theme: DARK_THEME_ID, dark: true, muted: "#2b241d", band: "#ece5db12" },
])("ShotChart phase bands in the $theme palette", ({ theme, dark, muted, band }) => {
  let style: HTMLStyleElement;

  beforeEach(() => {
    lastChartProps = null;
    window.localStorage.setItem(THEME_STORAGE_KEY, dark ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.classList.toggle("dark", dark);
    style = document.createElement("style");
    style.textContent = PALETTES;
    document.head.appendChild(style);
  });

  afterEach(() => {
    style.remove();
    window.localStorage.removeItem(THEME_STORAGE_KEY);
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.classList.remove("dark");
  });

  it("has the stylesheet's opaque --muted in effect", () => {
    // Guards the premise: if jsdom stopped resolving the injected palette, the
    // band assertions below would pass on the fallbacks and prove nothing.
    const value = getComputedStyle(document.documentElement).getPropertyValue("--muted").trim();
    expect(value).toBe(muted);
    expect(alphaOf(value)).toBe(1);
  });

  it("draws every phase band behind the curves", () => {
    const boxes = renderShot129().filter((annotation) => annotation.type === "box");
    expect(phases.length).toBeGreaterThan(1);
    expect(boxes).toHaveLength(phases.length);
    // chartjs-plugin-annotation draws a box after the datasets unless told
    // otherwise, so even a faint band would sit on top of the lines.
    expect(boxes.map((box) => box.drawTime)).toEqual(boxes.map(() => "beforeDatasetsDraw"));
  });

  it("shades the phase bands in a translucent colour", () => {
    const boxes = renderShot129().filter((annotation) => annotation.type === "box");
    expect(boxes).toHaveLength(phases.length);
    const fills = boxes.map((box) => box.backgroundColor ?? "");
    for (const fill of fills) {
      expect(alphaOf(fill), `band fill ${fill}`).toBeLessThanOrEqual(0.2);
    }
    // At least one band is actually shaded: a fix that made every band
    // transparent would lose the phase boundaries altogether.
    expect(fills.some((fill) => alphaOf(fill) > 0)).toBe(true);
    // And the shade is the stylesheet's own token, not the fallback that
    // happens to match it: a palette that lost `--chart-band` would otherwise
    // pass here on the fallback and drift from the stylesheet unnoticed.
    const token = getComputedStyle(document.documentElement).getPropertyValue("--chart-band");
    expect(token.trim()).toBe(band);
    expect(fills.filter((fill) => fill !== "transparent")).toEqual(
      fills.filter((fill) => fill !== "transparent").map(() => band),
    );
  });

  it("shades the bands translucent on the fallback palette too", () => {
    // No stylesheet at all, as in every other test of a page with this chart.
    style.remove();
    const boxes = renderShot129().filter((annotation) => annotation.type === "box");
    const fills = boxes.map((box) => box.backgroundColor ?? "");
    expect(fills.some((fill) => alphaOf(fill) > 0)).toBe(true);
    for (const fill of fills) expect(alphaOf(fill), `band fill ${fill}`).toBeLessThanOrEqual(0.2);
  });

  it("keeps the phase names and the exit line above the curves", () => {
    const annotations = renderShot129();
    for (const box of annotations.filter((annotation) => annotation.type === "box")) {
      expect(box.label?.drawTime).toBe("afterDatasetsDraw");
    }
    const exit = annotations.find((annotation) => annotation.type === "line");
    expect(exit?.drawTime).toBe("afterDatasetsDraw");
  });
});
