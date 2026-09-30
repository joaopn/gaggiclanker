import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { FirmwareValues, ResistanceSource, ShotDiagnosticsBlob, ShotPhase } from "@/api/types";
import {
  ComplianceCard,
  PhaseTable,
  ResistanceCard,
  WeightCard,
} from "@/components/shots/DiagnosticsCards";

function blob(source: ResistanceSource | undefined): ShotDiagnosticsBlob {
  return {
    diagnostics: {
      has_pressure: true,
      resistance: {
        source,
        avg: 2.1,
        std: 0.3,
        slope: -0.03,
        peak: 2.9,
        peak_timing_pct: 0.1,
        annotations: { level: "MODERATE" },
      },
    },
  };
}

describe("ResistanceCard source", () => {
  it("says the machine's own measurement was used", () => {
    render(<ResistanceCard diagnostics={blob("machine")} />);

    expect(screen.getByTestId("resistance-source")).toHaveTextContent("from the machine");
    expect(screen.getByTestId("band-level")).toHaveTextContent("2.1");
  });

  it("says ours was computed when the shot has no machine value", () => {
    render(<ResistanceCard diagnostics={blob("computed")} />);

    expect(screen.getByTestId("resistance-source")).toHaveTextContent(
      "computed from pressure and flow",
    );
  });

  it("says nothing about a source a stored shot never recorded", () => {
    render(<ResistanceCard diagnostics={blob(undefined)} />);

    expect(screen.queryByTestId("resistance-source")).toBeNull();
    expect(screen.getByTestId("band-level")).toBeInTheDocument();
  });
});

const stats = (avg: number) => ({
  start: avg - 1,
  end: avg + 1,
  min: avg - 1.5,
  max: avg + 2,
  avg,
});

function withFirmware(firmware: FirmwareValues | undefined): ShotDiagnosticsBlob {
  const base = blob("machine");
  return {
    ...base,
    diagnostics: {
      ...base.diagnostics,
      weight: { rate_avg_g_s: 1, rate_std_g_s: 0.1, scale_connected: true, annotations: {} },
    },
    firmware,
  };
}

const FIRMWARE: FirmwareValues = {
  pr: stats(1.94),
  lr: stats(3.67),
  phases: [
    { phase_number: 0, pr: null, lr: null },
    { phase_number: 1, pr: stats(1.07), lr: stats(2.99) },
  ],
  water_pumped_ml: 40.24,
  water_minus_weight_g: 3.5,
};

const PHASES: ShotPhase[] = [0, 1].map((n) => ({
  name: `Phase ${n}`,
  phase_number: n,
  start_time_seconds: n * 5,
  duration_seconds: 5,
  sample_count: 20,
  avg_temperature_c: 93,
  avg_pressure_bar: 6,
  total_flow_ml: 10,
}));

describe("the firmware analyzer's values on the shot page", () => {
  it("shows the machine's puck resistance and the liquid resistance under the level, unbanded", () => {
    render(<ResistanceCard diagnostics={withFirmware(FIRMWARE)} />);

    const pr = screen.getByTestId("firmware-pr");
    expect(pr).toHaveTextContent("Machine puck resistance");
    expect(pr).toHaveTextContent("1.94 s·√bar/mL");
    expect(pr).toHaveTextContent("start 0.94 · end 2.94 · min 0.44 · max 3.94");
    expect(screen.getByTestId("firmware-lr")).toHaveTextContent("3.67 bar·s/mL");
    expect(screen.getByTestId("firmware-resistance")).toHaveTextContent("Firmware analyzer");
    expect(screen.getByTestId("firmware-resistance")).toHaveTextContent("Not banded");
    // The banded level is still the level.
    expect(screen.getByTestId("band-level")).toHaveTextContent("2.1");
  });

  it("shows the water pumped and its difference to the beverage weight", () => {
    render(<WeightCard diagnostics={withFirmware(FIRMWARE)} />);

    expect(screen.getByTestId("firmware-water-pumped")).toHaveTextContent("40.2 ml");
    expect(screen.getByTestId("firmware-water-minus-weight")).toHaveTextContent("3.5 g");
  });

  it("shows the water alone when the shot has no yield", () => {
    render(<WeightCard diagnostics={withFirmware({ ...FIRMWARE, water_minus_weight_g: null })} />);

    expect(screen.getByTestId("firmware-water-pumped")).toBeInTheDocument();
    expect(screen.queryByTestId("firmware-water-minus-weight")).toBeNull();
  });

  it("adds each phase's averages beside its own row", () => {
    render(<PhaseTable phases={PHASES} firmware={FIRMWARE} />);

    const cells = screen.getAllByTestId("phase-firmware");
    expect(cells).toHaveLength(2);
    expect(cells[0]).toHaveTextContent("—");
    expect(cells[1]).toHaveTextContent("pr 1.07 s·√bar/mL");
    expect(cells[1]).toHaveTextContent("lr 2.99 bar·s/mL");
    // The rest of each stream is on the page, not in a tooltip.
    expect(cells[1]).toHaveTextContent("start 0.07 · end 2.07 · min -0.43 · max 3.07");
  });

  it("shows no water for a shot without the pump's count (an older file format)", () => {
    const v5 = { ...FIRMWARE, water_pumped_ml: null, water_minus_weight_g: null };
    render(<WeightCard diagnostics={withFirmware(v5)} />);

    expect(screen.queryByTestId("firmware-water")).toBeNull();
    expect(screen.getByTestId("band-rate_stability")).toBeInTheDocument();
  });

  it("shows no water for a machine whose pump does not count it, though it shows resistance", () => {
    // The stored answer for an all-zero counter is null, never 0 ml or a negative difference.
    const uncounted = { ...FIRMWARE, water_pumped_ml: null, water_minus_weight_g: null };
    render(<WeightCard diagnostics={withFirmware(uncounted)} />);
    render(<ResistanceCard diagnostics={withFirmware(uncounted)} />);

    expect(screen.queryByTestId("firmware-water")).toBeNull();
    expect(screen.queryByText(/0\.0 ml/)).toBeNull();
    expect(screen.getByTestId("firmware-pr")).toBeInTheDocument();
  });

  it("shows nothing for a machine without a pressure sensor", () => {
    // The stored shape: one entry per phase, every stream null.
    const none: FirmwareValues = {
      ...FIRMWARE,
      pr: null,
      lr: null,
      phases: [
        { phase_number: 0, pr: null, lr: null },
        { phase_number: 1, pr: null, lr: null },
      ],
    };
    render(<ResistanceCard diagnostics={withFirmware(none)} />);
    render(<PhaseTable phases={PHASES} firmware={none} />);

    expect(screen.queryByTestId("firmware-resistance")).toBeNull();
    expect(screen.queryByTestId("phase-firmware")).toBeNull();
  });

  it("renders a shot derived before the block existed without a trace of it", () => {
    render(<ResistanceCard diagnostics={withFirmware(undefined)} />);
    render(<WeightCard diagnostics={withFirmware(undefined)} />);
    render(<PhaseTable phases={PHASES} firmware={undefined} />);

    expect(screen.queryByTestId("firmware-resistance")).toBeNull();
    expect(screen.queryByTestId("firmware-water")).toBeNull();
    expect(screen.queryByTestId("phase-firmware")).toBeNull();
    expect(screen.queryByText("Firmware analyzer")).toBeNull();
  });
});

type Compliance = NonNullable<
  NonNullable<ShotDiagnosticsBlob["diagnostics"]>["profile_compliance"]
>;

function complianceBlob(compliance: Compliance | null): ShotDiagnosticsBlob {
  return { diagnostics: { has_pressure: true, profile_compliance: compliance } };
}

const pressureProfile: Compliance = {
  pressure_rmse_bar: 2.25,
  flow_rmse_ml_s: null,
  max_pressure_overshoot_bar: 0.2,
  max_pressure_undershoot_bar: 8,
  max_flow_overshoot_ml_s: null,
  max_flow_undershoot_ml_s: null,
  pressure_grading: "graded",
  flow_grading: "not_applicable",
  annotations: { pressure_adherence: "POOR", pressure_overshoot: "WITHIN_TOLERANCE" },
};

describe("ComplianceCard", () => {
  it("says a pressure profile has no flow to grade, and shows no flow number", () => {
    render(<ComplianceCard diagnostics={complianceBlob(pressureProfile)} />);

    expect(screen.getByTestId("band-pressure_adherence")).toHaveTextContent("2.25 bar");
    expect(screen.getByTestId("band-flow_adherence")).toHaveTextContent("not applicable");
    expect(screen.getByTestId("band-flow_adherence")).not.toHaveTextContent("ml/s");
    expect(screen.getByTestId("band-flow_adherence")).not.toHaveTextContent(/poor/i);
    // The flow deviation row has nothing to say either.
    expect(screen.queryByTestId("band-flow_undershoot")).toBeNull();
    expect(screen.getByTestId("band-pressure_overshoot")).toHaveTextContent("0.20 bar");
  });

  it("says a pressure that could not be worked out is not graded, never 0.00", () => {
    render(
      <ComplianceCard
        diagnostics={complianceBlob({
          ...pressureProfile,
          pressure_rmse_bar: null,
          max_pressure_overshoot_bar: null,
          max_pressure_undershoot_bar: null,
          pressure_grading: "not_graded",
          annotations: {},
        })}
      />,
    );

    expect(screen.getByTestId("band-pressure_adherence")).toHaveTextContent("not graded");
    expect(screen.queryByTestId("band-pressure_overshoot")).toBeNull();
    expect(screen.queryByText(/0\.00/)).toBeNull();
  });

  it("shows a flow-steered profile's flow adherence with its band", () => {
    render(
      <ComplianceCard
        diagnostics={complianceBlob({
          ...pressureProfile,
          flow_rmse_ml_s: 0.08,
          max_flow_undershoot_ml_s: 0.32,
          flow_grading: "graded",
          annotations: { ...pressureProfile.annotations, flow_adherence: "EXCELLENT" },
        })}
      />,
    );

    expect(screen.getByTestId("band-flow_adherence")).toHaveTextContent("0.08 ml/s");
    expect(screen.getByTestId("band-flow_adherence")).toHaveTextContent(/excellent/);
    expect(screen.getByTestId("band-flow_undershoot")).toHaveTextContent("0.32 ml/s");
  });

  it("keeps a dash for a block stored before the grading was recorded", () => {
    render(
      <ComplianceCard
        diagnostics={complianceBlob({
          ...pressureProfile,
          pressure_grading: undefined,
          flow_grading: undefined,
        })}
      />,
    );

    expect(screen.getByTestId("band-flow_adherence")).toHaveTextContent("—");
  });

  it("draws nothing for a shot with no profile to grade against", () => {
    const { container } = render(<ComplianceCard diagnostics={complianceBlob(null)} />);

    expect(container).toBeEmptyDOMElement();
  });
});
