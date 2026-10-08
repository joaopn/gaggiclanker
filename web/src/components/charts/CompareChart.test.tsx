import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ShotListRow, ShotSampleRow } from "@/api/types";
import { CompareChart } from "@/components/charts/CompareChart";
import { shot129Samples } from "@/test/shotFixture";

type Dataset = { label: string; data: { x: number; y: number }[] };
let datasets: Dataset[] = [];
vi.mock("react-chartjs-2", () => ({
  Line: (props: { data: { datasets: Dataset[] } }) => {
    datasets = props.data.datasets;
    return <canvas />;
  },
}));

const real = shot129Samples.samples;
const shotRow = (id: number, hasScale: boolean) =>
  ({ id, profile_label: `Profile ${id}`, has_scale: hasScale }) as unknown as ShotListRow;

/** The real shot, with a flow the puck estimate does not have: so the two cannot be mistaken. */
const cupOnly: ShotSampleRow[] = real.map((row, index) => ({
  ...row,
  pf: null,
  vf: index === 0 ? -20 : 1.5,
}));

describe("CompareChart", () => {
  it("draws the cup flow, read at zero, when every shot had a scale", () => {
    render(
      <CompareChart
        series={[
          { shot: shotRow(1, true), samples: cupOnly },
          { shot: shotRow(2, true), samples: cupOnly },
        ]}
      />,
    );

    const flows = datasets.filter((dataset) => dataset.label.endsWith("cup flow"));
    expect(flows).toHaveLength(2);
    expect(datasets.some((dataset) => dataset.label.endsWith("puck flow"))).toBe(false);
    for (const flow of flows) {
      expect(flow.data).toHaveLength(cupOnly.length);
      expect(flow.data[0].y).toBe(0);
      expect(flow.data[1].y).toBe(1.5);
    }
  });

  it("draws the puck flow of every shot as soon as one had no scale", () => {
    const puckOnly = real.map((row) => ({ ...row, vf: null, pf: 2.5 }));
    render(
      <CompareChart
        series={[
          { shot: shotRow(1, true), samples: puckOnly },
          { shot: shotRow(2, false), samples: puckOnly },
        ]}
      />,
    );

    const flows = datasets.filter((dataset) => dataset.label.endsWith("puck flow"));
    expect(flows).toHaveLength(2);
    expect(datasets.some((dataset) => dataset.label.endsWith("cup flow"))).toBe(false);
    for (const flow of flows) expect(flow.data.every((point) => point.y === 2.5)).toBe(true);
  });
});
