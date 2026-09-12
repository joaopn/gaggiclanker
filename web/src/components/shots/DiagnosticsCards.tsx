import type { ShotDiagnosticsBlob, ShotPhase } from "@/api/types";
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
 * maintainer, and from the analyser.
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

export function ResistanceCard({ diagnostics }: { diagnostics: ShotDiagnosticsBlob }) {
  const resistance = diagnostics.diagnostics?.resistance;
  if (!resistance) return null;
  const annotations = resistance.annotations ?? {};
  return (
    <SectionCard
      title="Puck resistance"
      description="R = pressure / flow². One number that folds grind, dose and puck prep together — its shape over the shot is the part worth reading."
    >
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

export function ComplianceCard({ diagnostics }: { diagnostics: ShotDiagnosticsBlob }) {
  const compliance = diagnostics.diagnostics?.profile_compliance;
  if (!compliance) return null;
  const annotations = compliance.annotations ?? {};
  return (
    <SectionCard
      title="Profile compliance"
      description="How closely the machine followed what the profile commanded. Flow deviation is the better grind signal: the PID actively drives pump power to hold pressure, so pressure error is masked by the controller."
    >
      <BandRow
        label="Pressure RMSE"
        value={formatNumber(compliance.pressure_rmse_bar, 2, "bar")}
        metric="pressure_adherence"
        band={annotations.pressure_adherence}
      />
      <BandRow
        label="Worst pressure overshoot"
        value={formatNumber(compliance.max_pressure_overshoot_bar, 2, "bar")}
        metric="pressure_overshoot"
        band={annotations.pressure_overshoot}
      />
      <BandRow
        label="Flow RMSE"
        value={formatNumber(compliance.flow_rmse_ml_s, 2, "ml/s")}
        metric="flow_adherence"
        band={annotations.flow_adherence}
      />
      <BandRow
        label="Worst flow undershoot"
        value={formatNumber(compliance.max_flow_undershoot_ml_s, 2, "ml/s")}
        metric="flow_undershoot"
        band={annotations.flow_undershoot}
      />
    </SectionCard>
  );
}

export function WeightCard({ diagnostics }: { diagnostics: ShotDiagnosticsBlob }) {
  const weight = diagnostics.diagnostics?.weight;
  const extraction = diagnostics.diagnostics?.extraction;
  if (!weight && !extraction) return null;
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
    </SectionCard>
  );
}

/** Which phase went wrong: the table the per-phase detail level exists for. */
export function PhaseTable({ phases }: { phases: ShotPhase[] }) {
  if (phases.length === 0) return null;
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
