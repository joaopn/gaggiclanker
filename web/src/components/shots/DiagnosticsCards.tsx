import type { FirmwareStats, ResistanceSource, ShotDiagnosticsBlob, ShotPhase } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { formatNumber } from "@/lib/shots";

/**
 * The deterministic diagnostics, as cards.
 *
 * Every number here comes from `gaggiclanker/domain/diagnostics.py` and is only
 * a number: nothing is computed in the browser and nothing is graded. What a
 * number means for a given profile is for the reader, and the warnings that need
 * no knowledge of the profile come from the server.
 */

/** One metric: the label and the number. */
export function MetricRow({
  label,
  value,
  metric,
}: {
  label: string;
  value: string;
  metric: string;
}) {
  return (
    <div
      className="border-border/60 border-b py-1.5 last:border-0"
      data-testid={`metric-${metric}`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm">{label}</span>
        <span className="text-sm tabular-nums">{value}</span>
      </div>
    </div>
  );
}

/** How the card says where R came from: the same two wordings as the shot information. */
export function resistanceSourceText(source: ResistanceSource | undefined): string | null {
  if (source === "machine") return "from the machine";
  if (source === "computed") return "computed from pressure and flow";
  return null;
}

/** The analyzer's five numbers, compact: the tooltip and the small line under an average. */
function statsLine(stats: FirmwareStats): string {
  return `start ${stats.start.toFixed(2)} · end ${stats.end.toFixed(2)} · min ${stats.min.toFixed(2)} · max ${stats.max.toFixed(2)}`;
}

/** One firmware-analyzer number: its average and unit, with the rest of its stats beneath. */
function FirmwareRow({
  label,
  unit,
  stats,
  testId,
}: {
  label: string;
  unit: string;
  stats: FirmwareStats | null | undefined;
  testId: string;
}) {
  if (!stats) return null;
  return (
    <div className="border-border/60 border-b py-1.5 last:border-0" data-testid={testId}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm">{label}</span>
        <span className="text-sm tabular-nums" title={statsLine(stats)}>
          {stats.avg.toFixed(2)} {unit}
        </span>
      </div>
      <p className="mt-0.5 text-muted-foreground text-xs tabular-nums">{statsLine(stats)}</p>
    </div>
  );
}

export function ResistanceCard({ diagnostics }: { diagnostics: ShotDiagnosticsBlob }) {
  const resistance = diagnostics.diagnostics?.resistance;
  if (!resistance) return null;
  const source = resistanceSourceText(resistance.source);
  const firmware = diagnostics.firmware;
  return (
    <SectionCard
      title="Puck resistance"
      description="One number that folds grind, dose and puck prep together — its shape over the shot is the part worth reading."
    >
      {source ? (
        <p className="mb-1 text-muted-foreground text-xs" data-testid="resistance-source">
          Resistance source: {source}.
        </p>
      ) : null}
      <MetricRow label="Average" value={formatNumber(resistance.avg)} metric="level" />
      <MetricRow label="Slope" value={formatNumber(resistance.slope)} metric="slope" />
      {firmware?.pr || firmware?.lr ? (
        <div className="mt-3" data-testid="firmware-resistance">
          <h3 className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
            Firmware analyzer
          </h3>
          <p className="mb-1 text-muted-foreground text-xs">
            The machine's own numbers in its units, as its shot analyzer shows them. Not the level
            above.
          </p>
          <FirmwareRow
            label="Machine puck resistance"
            unit="s·√bar/mL"
            stats={firmware.pr}
            testId="firmware-pr"
          />
          <FirmwareRow
            label="Liquid resistance"
            unit="bar·s/mL"
            stats={firmware.lr}
            testId="firmware-lr"
          />
        </div>
      ) : null}
    </SectionCard>
  );
}

/** What a compliance row says instead of a number: the profile has nothing of this kind to
    follow ("not applicable"), or the number could not be worked out ("not graded"). A block
    stored before the grading was recorded has neither field and shows a dash, as it always did. */
function gradingText(
  grading: "graded" | "not_applicable" | "not_graded" | undefined,
): string | null {
  if (grading === "not_applicable") return "not applicable";
  if (grading === "not_graded") return "not graded";
  return null;
}

export function ComplianceCard({ diagnostics }: { diagnostics: ShotDiagnosticsBlob }) {
  const compliance = diagnostics.diagnostics?.profile_compliance;
  if (!compliance) return null;
  const pressureText = gradingText(compliance.pressure_grading);
  const flowText = gradingText(compliance.flow_grading);
  return (
    <SectionCard
      title="Profile compliance"
      description="How closely the machine followed what the profile commanded, phase by phase: pressure over the phases that steer by pressure, and pump flow over the phases that steer by flow. A profile with no phase of one kind has nothing to grade for it. Both say how well the machine held the profile, not what the puck did: the pump flow is the machine's own estimate, and the controller drives the pump to hold pressure."
    >
      <MetricRow
        label="Pressure RMSE"
        value={pressureText ?? formatNumber(compliance.pressure_rmse_bar, 2, "bar")}
        metric="pressure_adherence"
      />
      {pressureText ? null : (
        <MetricRow
          label="Worst pressure overshoot"
          value={formatNumber(compliance.max_pressure_overshoot_bar, 2, "bar")}
          metric="pressure_overshoot"
        />
      )}
      <MetricRow
        label="Flow RMSE"
        value={flowText ?? formatNumber(compliance.flow_rmse_ml_s, 2, "ml/s")}
        metric="flow_adherence"
      />
      {flowText ? null : (
        <MetricRow
          label="Worst flow undershoot"
          value={formatNumber(compliance.max_flow_undershoot_ml_s, 2, "ml/s")}
          metric="flow_undershoot"
        />
      )}
    </SectionCard>
  );
}

export function WeightCard({ diagnostics }: { diagnostics: ShotDiagnosticsBlob }) {
  const weight = diagnostics.diagnostics?.weight;
  const extraction = diagnostics.diagnostics?.extraction;
  const water = diagnostics.firmware?.water_pumped_ml;
  const waterMinusWeight = diagnostics.firmware?.water_minus_weight_g;
  if (!weight && !extraction && water == null) return null;
  return (
    <SectionCard
      title="Extraction and weight"
      description="The average brew flow, how fast the scale climbed, and the water the pump moved."
    >
      {extraction ? (
        <MetricRow
          label="Average brew flow"
          value={formatNumber(extraction.flow_avg_brew_ml_s, 2, "ml/s")}
          metric="flow_avg_brew"
        />
      ) : null}
      {weight ? (
        weight.scale_connected ? (
          <MetricRow
            label="Weight rate"
            value={formatNumber(weight.rate_avg_g_s, 2, "g/s")}
            metric="weight_rate"
          />
        ) : (
          <p className="pt-2 text-muted-foreground text-sm">
            No BLE scale was connected, so weight was estimated from pump flow rather than measured.
            A diagnostic on a modelled signal means less than one on a measured signal.
          </p>
        )
      ) : null}
      {water != null ? (
        <div className="mt-2" data-testid="firmware-water">
          <h3 className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
            Firmware analyzer
          </h3>
          <div className="flex items-baseline justify-between gap-3 py-1.5">
            <span className="text-sm">Water pumped</span>
            <span className="text-sm tabular-nums" data-testid="firmware-water-pumped">
              {formatNumber(water, 1, "ml")}
            </span>
          </div>
          {waterMinusWeight != null ? (
            <div className="flex items-baseline justify-between gap-3 py-1.5">
              <span className="text-sm">Water pumped minus beverage weight</span>
              <span className="text-sm tabular-nums" data-testid="firmware-water-minus-weight">
                {formatNumber(waterMinusWeight, 1, "g")}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
    </SectionCard>
  );
}

/** A phase's machine puck resistance and liquid resistance: the average, and beneath it the rest. */
function FirmwarePhaseCell({
  entry,
}: {
  entry: NonNullable<ShotDiagnosticsBlob["firmware"]>["phases"][number] | undefined;
}) {
  if (!entry || (!entry.pr && !entry.lr)) return <span className="text-muted-foreground">—</span>;
  return (
    <>
      {entry.pr ? (
        <span className="mb-1 block">
          <span className="block">pr {entry.pr.avg.toFixed(2)} s·√bar/mL</span>
          <span className="block text-muted-foreground">{statsLine(entry.pr)}</span>
        </span>
      ) : null}
      {entry.lr ? (
        <span className="block">
          <span className="block">lr {entry.lr.avg.toFixed(2)} bar·s/mL</span>
          <span className="block text-muted-foreground">{statsLine(entry.lr)}</span>
        </span>
      ) : null}
    </>
  );
}

/** Which phase went wrong: the table the per-phase detail level exists for. */
export function PhaseTable({
  phases,
  firmware,
}: {
  phases: ShotPhase[];
  firmware?: ShotDiagnosticsBlob["firmware"];
}) {
  if (phases.length === 0) return null;
  const byNumber = new Map((firmware?.phases ?? []).map((entry) => [entry.phase_number, entry]));
  const showFirmware = (firmware?.phases ?? []).some((entry) => entry.pr || entry.lr);
  return (
    <SectionCard
      title="Phases"
      description="From the header's own transition table, not re-derived from the curve."
      contentClassName="overflow-x-auto"
    >
      <table className="w-full border-collapse text-left text-sm">
        <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
          <tr>
            <th className="py-2 pr-4 font-medium">Phase</th>
            <th className="py-2 pr-4 text-right font-medium">Start</th>
            <th className="py-2 pr-4 text-right font-medium">Duration</th>
            <th className="py-2 pr-4 text-right font-medium">Avg pressure</th>
            <th className="py-2 pr-4 text-right font-medium">Flow</th>
            {showFirmware ? (
              <th className="py-2 pr-4 text-right font-medium">Firmware analyzer</th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {phases.map((phase) => (
            <tr
              key={`${phase.phase_number}-${phase.start_time_seconds}`}
              className="border-border border-b last:border-0"
              data-testid="phase-row"
            >
              <td className="py-2 pr-4">
                <span className="block font-medium">{phase.name}</span>
                <span className="text-muted-foreground text-xs">
                  {phase.diagnostics?.phase_type ?? "—"}
                </span>
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {phase.start_time_seconds.toFixed(1)} s
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {phase.duration_seconds.toFixed(1)} s
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {phase.avg_pressure_bar.toFixed(1)} bar
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {phase.total_flow_ml.toFixed(1)} ml
              </td>
              {showFirmware ? (
                <td
                  className="py-2 pr-4 text-right text-xs tabular-nums"
                  data-testid="phase-firmware"
                >
                  <FirmwarePhaseCell entry={byNumber.get(phase.phase_number)} />
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  );
}
