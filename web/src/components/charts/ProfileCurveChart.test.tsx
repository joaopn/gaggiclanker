import { readFileSync } from "node:fs";
import path from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProfileCurveChart } from "@/components/charts/ProfileCurveChart";
import { DARK_THEME_ID, LIGHT_THEME_ID, THEME_STORAGE_KEY } from "@/lib/theme";

type Segment = {
  borderColor: (ctx: unknown) => string | undefined;
  borderDash: (ctx: unknown) => number[] | undefined;
};
type Dataset = {
  label: string;
  data: { x: number; y: number; target: boolean }[];
  yAxisID: string;
  borderColor: string;
  segment: Segment;
};
type Annotation = {
  xMin: number;
  drawTime: string;
  label: { display: boolean; content: string; backgroundColor: string; drawTime?: string };
};
type ChartProps = {
  data: { datasets: Dataset[] };
  options: {
    scales: Record<
      string,
      { min: number; max: number; ticks: { callback?: (v: number) => string } }
    >;
    plugins: { annotation: { annotations: Record<string, Annotation> } };
  };
};
let renders: ChartProps[] = [];
vi.mock("react-chartjs-2", () => ({
  Line: (props: ChartProps) => {
    renders.push(props);
    return <canvas />;
  },
}));

const css = readFileSync(path.resolve(__dirname, "../../index.css"), "utf8");
function paletteBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`index.css has no ${selector} block`);
  return css.slice(start, css.indexOf("}", start) + 1);
}
const token = (block: string, name: string) =>
  new RegExp(`${name}:\\s*([^;]+);`).exec(block)?.[1].trim();

const profile = {
  type: "pro",
  phases: [
    { name: "Fill", duration: 4, pump: { target: "flow", flow: 4, pressure: 0 } },
    {
      name: "Ramp",
      duration: 6,
      transition: { type: "linear", duration: 3 },
      pump: { target: "pressure", pressure: 9, flow: 6 },
    },
    { duration: 5, pump: { target: "pressure", pressure: 9, flow: 6 } },
  ],
};

const last = () => renders[renders.length - 1];

function atWidth(width: number) {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(width);
}

beforeEach(() => {
  renders = [];
});
afterEach(() => {
  cleanup();
  document.getElementById("palettes")?.remove();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.classList.remove("dark");
  localStorage.removeItem(THEME_STORAGE_KEY);
});

describe("ProfileCurveChart", () => {
  it("says in text what it draws", () => {
    render(<ProfileCurveChart profile={profile} />);
    expect(screen.getByTestId("profile-curve-summary")).toHaveTextContent(
      "Pressure and flow the profile aims for over 15 s: Fill, Ramp, Phase 3.",
    );
    expect(
      screen.getByText(/Solid: what the pump aims for\. Dashed: only a limit\./),
    ).toBeInTheDocument();
  });

  it("draws pressure on the left 0-12 bar axis and flow on the right 0-10 ml/s axis", () => {
    render(<ProfileCurveChart profile={profile} />);
    const { data, options } = last();
    expect(data.datasets.map((d) => [d.label, d.yAxisID])).toEqual([
      ["Pressure", "y"],
      ["Flow", "y1"],
    ]);
    expect(options.scales.y).toMatchObject({ min: 0, max: 12 });
    expect(options.scales.y1).toMatchObject({ min: 0, max: 10 });
    expect(options.scales.x).toMatchObject({ min: 0, max: 15 });
    expect(options.scales.x.ticks.callback?.(10)).toBe("10s");
    expect(options.scales.x.ticks.callback?.(2.4)).toBe("2s");
  });

  it("dashes a segment that starts on a point the pump only limits", () => {
    render(<ProfileCurveChart profile={profile} />);
    const [pressure] = last().data.datasets;
    // The firmware looks at the point a segment starts from (p0), never the one it ends on (p1).
    const seg = (from: boolean, to: boolean) => ({
      p0: { raw: { x: 0, y: 0, target: from } },
      p1: { raw: { x: 0.1, y: 0, target: to } },
    });
    for (const to of [true, false]) {
      expect(pressure.segment.borderDash(seg(true, to))).toBeUndefined();
      expect(pressure.segment.borderColor(seg(true, to))).toBeUndefined();
      expect(pressure.segment.borderDash(seg(false, to))).toEqual([6, 6]);
      expect(pressure.segment.borderColor(seg(false, to))).toMatch(/^rgba\(\d+, \d+, \d+, 0\.6\)$/);
    }
    // Fill aims for flow, so its pressure is the limit and its flow the target.
    expect(pressure.data[0].target).toBe(false);
    expect(last().data.datasets[1].data[0].target).toBe(true);
  });

  it("marks each phase's start with its name", () => {
    atWidth(800);
    render(<ProfileCurveChart profile={profile} />);
    const lines = Object.values(last().options.plugins.annotation.annotations);
    expect(lines.map((a) => [a.xMin, a.label.content, a.label.display])).toEqual([
      [0, "Fill", true],
      [4, "Ramp", true],
      [10, "Phase 3", true],
    ]);
    // Over the curves, so a line crossing a name does not strike through it.
    expect(new Set(lines.map((a) => a.drawTime))).toEqual(new Set(["afterDatasetsDraw"]));
  });

  it("hides the names when the chart itself is narrower than 520 px", async () => {
    atWidth(519);
    render(<ProfileCurveChart profile={profile} />);
    expect(screen.getByTestId("profile-curve")).toHaveAttribute("data-names", "hidden");
    expect(
      Object.values(last().options.plugins.annotation.annotations).map((a) => a.label.display),
    ).toEqual([false, false, false]);
    cleanup();
    vi.restoreAllMocks();
    atWidth(520);
    render(<ProfileCurveChart profile={profile} />);
    expect(screen.getByTestId("profile-curve")).toHaveAttribute("data-names", "shown");
  });

  it("takes a shared end of the time axis and a title", () => {
    render(<ProfileCurveChart profile={profile} xMax={30} title="Proposed" />);
    expect(last().options.scales.x.max).toBe(30);
    expect(screen.getByTestId("profile-curve-title")).toHaveTextContent("Proposed");
    expect(screen.getByTestId("profile-curve-summary")).toHaveTextContent("over 30 s");
    // The curve itself still ends where its phases do.
    const points = last().data.datasets[0].data;
    expect(points[points.length - 1].x).toBeLessThan(15.5);
  });

  it("two curves with one shared end draw on the same axes", () => {
    render(
      <>
        <ProfileCurveChart profile={profile} xMax={20} title="Active" />
        <ProfileCurveChart
          profile={{ ...profile, phases: profile.phases.slice(0, 2) }}
          xMax={20}
          title="Proposed"
        />
      </>,
    );
    expect(new Set(renders.map((r) => r.options.scales.x.max))).toEqual(new Set([20]));
  });

  it("does not rebuild the chart for a render that changes nothing", () => {
    const { rerender } = render(<ProfileCurveChart profile={profile} title="Same" />);
    const first = last();
    rerender(<ProfileCurveChart profile={profile} title="Same" />);
    expect(renders.every((r) => r.data === first.data && r.options === first.options)).toBe(true);
  });

  it("draws nothing for a profile with no phases", () => {
    render(<ProfileCurveChart profile={{ type: "pro", phases: [] }} />);
    expect(screen.queryByTestId("profile-curve")).toBeNull();
  });
});

describe.each([
  { theme: LIGHT_THEME_ID, dark: false },
  { theme: DARK_THEME_ID, dark: true },
])("ProfileCurveChart colours in the $theme palette", ({ theme, dark }) => {
  it("uses Pressure's and Pump flow's colours from the shot chart", () => {
    const style = document.createElement("style");
    style.id = "palettes";
    style.textContent = `${paletteBlock(":root")}\n${paletteBlock(`:root[data-theme="${DARK_THEME_ID}"]`)}`;
    document.head.append(style);
    document.documentElement.setAttribute("data-theme", theme);
    if (dark) document.documentElement.classList.add("dark");
    localStorage.setItem(THEME_STORAGE_KEY, dark ? "dark" : "light");

    render(<ProfileCurveChart profile={profile} />);
    const block = paletteBlock(dark ? `:root[data-theme="${DARK_THEME_ID}"]` : ":root");
    const [pressure, flow] = last().data.datasets;
    expect(pressure.borderColor).toBe(token(block, "--chart-1"));
    expect(flow.borderColor).toBe(token(block, "--chart-2"));
    expect(pressure.borderColor).not.toBe(flow.borderColor);
  });

  it("draws each phase name on the card colour at 75 %, over the curves", () => {
    const style = document.createElement("style");
    style.id = "palettes";
    style.textContent = `${paletteBlock(":root")}\n${paletteBlock(`:root[data-theme="${DARK_THEME_ID}"]`)}`;
    document.head.append(style);
    document.documentElement.setAttribute("data-theme", theme);
    if (dark) document.documentElement.classList.add("dark");
    localStorage.setItem(THEME_STORAGE_KEY, dark ? "dark" : "light");

    atWidth(800);
    render(<ProfileCurveChart profile={profile} />);
    const block = paletteBlock(dark ? `:root[data-theme="${DARK_THEME_ID}"]` : ":root");
    const surface = token(block, "--card");
    const lines = Object.values(last().options.plugins.annotation.annotations);
    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) {
      const background = line.label.backgroundColor;
      expect(background).toBe(token(block, "--chart-label"));
      // The card's own colour with an alpha of 0xbf (75 %): see-through, but mostly opaque.
      expect(background).toBe(`${surface}bf`);
      expect(line.drawTime).toBe("afterDatasetsDraw");
      // The name inherits the line's draw time: one of its own could put it under the curves.
      expect(line.label.drawTime).toBeUndefined();
    }
  });
});
