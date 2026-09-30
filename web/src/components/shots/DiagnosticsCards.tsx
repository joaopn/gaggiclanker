import type { FirmwareStats, ResistanceSource, ShotDiagnosticsBlob, ShotPhase } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import {
  bandMeaning,
  bandTone,
  formatNumber,
  humanizeBand,
  humanizeKey,
  TONE_TEXT,
} from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The deterministic diagnostics, as cards.
 *
 * Every number here comes from `gaggiclanker/domain/diagnostics.py`, which is
 * crema's calibration vendored and tested — nothing is computed in the browser.
 * Each band label carries a one-line meaning (`lib/shots.ts`) because "erosion:
 * MODERATE_DECLINE" is a fact and not yet information, and because the reader
 * should not have to hold five threshold tables in their head.
 *
 * What these cards deliberately do *not* do is advise. A deterministic band can
 * say the puck lost structure; it cannot say to grind coarser, and pretending
 * otherwise is how a diagnostic stops being trusted. The advice comes from the
 * maintainer, and from the chat.
 */

/** One metric: the number, its band, and what the band means. */
export function BandRow({
  label,
  value,
  metric,
  band,
}: {
  label: string;
  value: string;
  metric: string;
  band?: string;
}) {
  const tone = bandTone(band);
  const meaning = bandMeaning(metric, band);
  return (
    <div className="border-border/60 border-b py-1.5 last:border-0" data-testid={`band-${metric}`}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm">{label}</span>
        <span className="flex items-baseline gap-2">
          <span className="text-sm tabular-nums">{value}</span>
          {band ? (
            <span className={cn("font-medium text-xs", TONE_TEXT[tone])} data-band={band}>
              {humanizeBand(band)}
            </span>
          ) : null}
        </span>
      </div>
      {meaning ? <p className="mt-0.5 text-muted-foreground text-xs">{meaning}</p> : null}
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
  const annotations = resistance.annotations ?? {};
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
      <BandRow
        label="Average"
        value={formatNumber(resistance.avg)}
        metric="level"
        band={annotations.level}
      />
      <BandRow
        label="Stability (std)"
        value={formatNumber(resistance.std)}
        metric="stability"
        band={annotations.stability}
      />
      <BandRow
        label="Erosion (slope)"
        value={formatNumber(resistance.slope)}
        metric="erosion"
        band={annotations.erosion}
      />
      <BandRow
        label="Peak timing"
        value={`${(resistance.peak_timing_pct * 100).toFixed(0)} %`}
        metric="saturation"
        band={annotations.saturation}
      />
      {firmware?.pr || firmware?.lr ? (
        <div className="mt-3" data-testid="firmware-resistance">
          <h3 className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
            Firmware analyzer
          </h3>
          <p className="mb-1 text-muted-foreground text-xs">
            The machine's own numbers in its units, as its shot analyzer shows them. Not banded, and
            not the level above.
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

export function ChannelingCard({ diagnostics }: { diagnostics: ShotDiagnosticsBlob }) {
  const channeling = diagnostics.diagnostics?.channeling;
  if (!channeling) return null;
  const annotations = channeling.annotations ?? {};
  const tone = bandTone(channeling.channeling_risk === "LOW" ? "LOW" : channeling.channeling_risk);
  return (
    <SectionCard
      title="Channeling"
      description="Four independent puck-stability signals, scored together. One flag is usually noise; two that agree are a real signal."
      actions={
        <span className={cn("font-medium text-sm", TONE_TEXT[tone])} data-testid="channeling-risk">
          {humanizeBand(channeling.channeling_risk)}
        </span>
      }
    >
      {annotations.guidance ? (
        <p className="mb-2 text-sm" data-testid="channeling-guidance">
          {annotations.guidance}
        </p>
      ) : null}
      <p className="mb-2 text-muted-foreground text-xs">
        Primary signal: <span data-testid="channeling-primary">{annotations.primary_signal}</span> ·
        window confidence {humanizeBand(annotations.window_confidence)} · flow shape{" "}
        {humanizeBand(annotations.flow_shape)}
      </p>
      <BandRow
        label="Flow jitter"
        value={formatNumber(channeling.flow_jitter_ml_s, 3, "ml/s")}
        metric="flow_jitter"
        band={annotations.flow_jitter}
      />
      <BandRow
        label="Flow vs target"
        value={formatNumber(channeling.flow_vs_target_residual_ml_s, 3, "ml/s")}
        metric="flow_vs_target"
        band={annotations.flow_vs_target}
      />
      <BandRow
        label="Worst pressure drop"
        value={formatNumber(channeling.pressure_max_drop_rate_bar_s, 2, "bar/s")}
        metric="pressure_drop"
        band={annotations.pressure_drop}
      />
      <BandRow
        label="Late flow trend"
        value={formatNumber(channeling.flow_acceleration_late_ml_s2, 3, "ml/s²")}
        metric="late_flow_trend"
        band={annotations.late_flow_trend}
      />
      {annotations.note ? (
        <p className="mt-2 text-muted-foreground text-xs">{annotations.note}</p>
      ) : null}
    </SectionCard>
  );
}

export function TemperatureCard({ diagnostics }: { diagnostics: ShotDiagnosticsBlob }) {
  const temperature = diagnostics.diagnostics?.temperature;
  if (!temperature) return null;
  const annotations = temperature.annotations ?? {};
  return (
    <SectionCard
      title="Temperature"
      description="Measured against the profile's target over the brew window only — the warm-up before it is not a fault."
    >
      <BandRow
        label="Overshoot"
        value={formatNumber(temperature.overshoot_c, 2, "°C")}
        metric="overshoot"
        band={annotations.overshoot}
      />
      <BandRow
        label="Undershoot"
        value={formatNumber(temperature.undershoot_c, 2, "°C")}
        metric="undershoot"
        band={annotations.undershoot}
      />
      <BandRow
        label="Stability (std)"
        value={formatNumber(temperature.stability_std_c, 2, "°C")}
        metric="stability"
        band={annotations.stability}
      />
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
  const annotations = compliance.annotations ?? {};
  const pressureText = gradingText(compliance.pressure_grading);
  const flowText = gradingText(compliance.flow_grading);
  return (
    <SectionCard
      title="Profile compliance"
      description="How closely the machine followed what the profile commanded, phase by phase: pressure over the phases that steer by pressure, and pump flow over the phases that steer by flow. A profile with no phase of one kind has nothing to grade for it. Both say how well the machine held the profile, not what the puck did: the pump flow is the machine's own estimate, and the controller drives the pump to hold pressure."
    >
      <BandRow
        label="Pressure RMSE"
        value={pressureText ?? formatNumber(compliance.pressure_rmse_bar, 2, "bar")}
        metric="pressure_adherence"
        band={annotations.pressure_adherence}
      />
      {pressureText ? null : (
        <BandRow
          label="Worst pressure overshoot"
          value={formatNumber(compliance.max_pressure_overshoot_bar, 2, "bar")}
          metric="pressure_overshoot"
          band={annotations.pressure_overshoot}
        />
      )}
      <BandRow
        label="Flow RMSE"
        value={flowText ?? formatNumber(compliance.flow_rmse_ml_s, 2, "ml/s")}
        metric="flow_adherence"
        band={annotations.flow_adherence}
      />
      {flowText ? null : (
        <BandRow
          label="Worst flow undershoot"
          value={formatNumber(compliance.max_flow_undershoot_ml_s, 2, "ml/s")}
          metric="flow_undershoot"
          band={annotations.flow_undershoot}
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
      description="Pressure under the curve, the brew-window trends, and how evenly the scale climbed."
    >
      {extraction ? (
        <>
          <BandRow
            label="Pressure area"
            value={formatNumber(extraction.pressure_auc_bar_s, 1, "bar·s")}
            metric="pressure_auc"
          />
          <BandRow
            label="Brew pressure trend"
            value={formatNumber(extraction.pressure_slope_brew_bar_s, 3, "bar/s")}
            metric="pressure_trend"
            band={extraction.annotations?.pressure_trend}
          />
          <BandRow
            label="Brew flow trend"
            value={formatNumber(extraction.flow_slope_brew_ml_s2, 3, "ml/s²")}
            metric="flow_trend"
            band={extraction.annotations?.flow_trend}
          />
        </>
      ) : null}
      {weight ? (
        weight.scale_connected ? (
          <BandRow
            label="Weight rate"
            value={formatNumber(weight.rate_avg_g_s, 2, "g/s")}
            metric="rate_stability"
            band={weight.annotations?.rate_stability}
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
            <th className="py-2 font-medium">Notes</th>
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
              <td className="py-2">
                <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                  {Object.entries(phase.diagnostics?.annotations ?? {}).map(([key, band]) => (
                    <span key={key} className="text-xs">
                      <span className="text-muted-foreground">{humanizeKey(key)} </span>
                      <span className={TONE_TEXT[bandTone(band)]}>{humanizeBand(band)}</span>
                    </span>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  );
}
