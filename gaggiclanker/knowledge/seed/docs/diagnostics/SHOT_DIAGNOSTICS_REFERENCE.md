# Shot Diagnostics Reference

Comprehensive reference for all diagnostic metrics computed by `analyze_shot`.
Use this to understand what each metric means, how to interpret annotations,
and when to request which detail level.

---

## Detail Levels

The `analyze_shot` tool accepts a `detail` parameter:

| Level | When to use | Token cost | Includes |
|-------|-------------|------------|----------|
| **summary** (default) | Quick triage, first assessment | Lowest | Key indicators, phase list (no samples) |
| **per_phase** | Identify which phase has the issue | Medium | Full diagnostics + per-phase breakdowns (no samples) |
| **per_phase_detailed** | See curve shape in problem phases | Higher | Everything in per_phase + ~5 evenly-spaced averaged samples per phase |

**Start with `summary`.** Escalate to `per_phase` to identify *which phase* has
the problem. Use `per_phase_detailed` when you need to see the pressure/flow
curve shape.

---

## Summary Level Diagnostics

Returned as a flat object with key indicators:

| Field | Type | What it tells you |
|-------|------|-------------------|
| `resistance_avg` | float | Average puck resistance (P/F²). Higher = finer grind or tighter puck. |
| `resistance_slope` | float | How resistance changes over the shot. Negative = erosion (normal). Steep negative = possible channeling. |
| `channeling_risk` | string | Overall channeling risk: LOW / MODERATE / HIGH / VERY_HIGH / INSUFFICIENT_DATA |
| `temperature_stability_c` | float | Std deviation of brew temp. Lower = more stable. |
| `pressure_rmse_bar` | float | RMSE between actual and target pressure (profile compliance). 0 = perfect adherence. |
| `max_overshoot_bar` | float | Largest pressure overshoot above target. >1.0 bar is highly unusual and almost certainly = grind too fine. |
| `flow_rmse_ml_s` | float? | RMSE between actual and target flow (when target flow data available). 0 = perfect adherence. |
| `max_flow_overshoot_ml_s` | float? | Largest flow above target. More reliable grind indicator than pressure overshoot (see note below). |
| `scale_connected` | bool | Whether BT scale data was present. |
| `annotations` | dict | Human-readable labels for all of the above. |

> **Flow vs pressure for grind diagnosis:** Flow deviation is a more reliable grind
> indicator than pressure overshoot. The Gaggimate/gaggiuino PID actively controls
> pump power to maintain target pressure, so pressure overshoot is artificially limited
> by the controller. Flow rate is a *consequence* of grind + dose + puck prep and cannot
> be masked by the pump.

### Summary Annotations

| Key | Bands |
|-----|-------|
| `resistance_level` | VERY_LOW (<0.5) · LOW (<1.5) · MODERATE (<3.0) · HIGH (<5.0) · VERY_HIGH |
| `resistance_erosion` | INCREASING (≥0.05) · FLAT (≥−0.02) · GRADUAL_DECLINE (≥−0.08) · MODERATE_DECLINE (≥−0.15) · STEEP_DECLINE |
| `channeling_risk` | LOW · MODERATE · HIGH · VERY_HIGH · INSUFFICIENT_DATA |
| `temperature_stability` | VERY_STABLE (<0.3) · STABLE (<0.8) · MODERATE (<1.5) · UNSTABLE |
| `pressure_adherence` | EXCELLENT (<0.3) · GOOD (<0.8) · FAIR (<1.5) · POOR |
| `pressure_overshoot` | WITHIN_TOLERANCE (<0.25) · MINOR_OVERSHOOT (<0.5) · NOTABLE_OVERSHOOT (<1.0) · SEVERE_OVERSHOOT |
| `flow_adherence` | EXCELLENT (<0.3) · GOOD (<0.8) · FAIR (<1.5) · POOR *(when target flow available)* |
| `flow_overshoot` | WITHIN_TOLERANCE (<0.3) · MINOR_DEVIATION (<0.7) · NOTABLE_DEVIATION (<1.5) · SEVERE_DEVIATION *(when target flow available)* |

---

## Full Diagnostics (per_phase / per_phase_detailed)

Returned as an object with these sub-sections:

### Resistance (Puck Resistance)

The **master diagnostic metric**. Computed as R = P / F² (quadratic Darcy model).
Captures grind fineness, puck prep quality, channeling, and erosion.

| Field | Unit | Meaning |
|-------|------|---------|
| `avg` | dimensionless | Average resistance across brew phase |
| `std` | dimensionless | Variability of resistance |
| `slope` | /s | Change rate — negative = erosion, steep negative = channeling |
| `peak` | dimensionless | Maximum resistance recorded |
| `peak_timing_pct` | 0–1 | When peak occurred (0 = start, 1 = end of brew) |

**Annotations:**

| Key | What it assesses | Bands |
|-----|------------------|-------|
| `level` | Grind fineness | VERY_LOW · LOW · MODERATE · HIGH · VERY_HIGH |
| `stability` | Puck consistency | VERY_STABLE (<0.2) · STABLE (<0.5) · MODERATE (<1.0) · VOLATILE |
| `erosion` | Puck degradation | INCREASING · FLAT · GRADUAL_DECLINE · MODERATE_DECLINE · STEEP_DECLINE |
| `saturation` | Peak timing | EARLY (<0.15) · GOOD_TIMING (<0.35) · MID_SHOT (<0.60) · LATE |

**Diagnostic patterns:**
- High avg + STEEP_DECLINE slope → grind too fine, channeling developing
- Low avg + INCREASING slope → grind too coarse, under-extraction
- VOLATILE stability → inconsistent puck prep or channeling
- EARLY peak → puck saturated before brew got going

### Channeling Indicators

Four independent indicators, each catching a different channeling signature. Reason about
them together — a single flag is usually noise; two or more aligned flags indicate a real
signal. Descriptor fields (`flow_spread_ml_s`, `pressure_jitter_bar`, `flow_shape`)
provide context for interpreting the indicators but do not contribute to `channeling_risk`.

**Steady-state window.** All channeling metrics are computed over a trimmed window that
excludes (a) the pressure-ramp portion at the start of the brew phase and (b) leading or
trailing samples where flow is below 0.1 ml/s (valve-closed at entry, volumetric-cutoff at
exit). The `annotations.note` field reports how many samples were excluded at each step
so the agent can judge window quality.

**INSUFFICIENT_DATA.** If fewer than 5 samples remain after trimming — a short brew phase,
flow that never opens, or a valve-closed shot — `channeling_risk` is set to
`INSUFFICIENT_DATA` rather than computing a misleading score. This is NOT a problem
indication; it simply means the phase was too short to judge. Check taste feedback and
adjacent phases instead.

#### Indicators (contribute to channeling_risk)

| Field | Unit | What it catches | Notes |
|-------|------|-----------------|-------|
| `flow_jitter_ml_s` | ml/s | Micro-spikes, oscillation, puck collapsing/re-forming | Std of first-differences of flow — insensitive to designed ramps. Primary signal. |
| `flow_vs_target_residual_ml_s` | ml/s | Systematic inability to hold the commanded flow curve | Std of (actual − target). `null` when no target flow is commanded (pressure-led profiles). Often the clearest fingerprint on flow-led profiles. |
| `pressure_max_drop_rate_bar_s` | bar/s | Abrupt channel opening (pressure cliff) | Most-negative *single-sample* dP/dt. Complementary to jitter — a single cliff can register as low jitter but high max_drop. |
| `flow_acceleration_late_ml_s2` | ml/s² | Late-shot runaway channeling | `late_slope − overall_slope`. A linear flow ramp scores 0; only *excess* acceleration beyond the designed trajectory is a runaway signal. |

#### Descriptors (context, not scored)

| Field | Unit | Meaning |
|-------|------|---------|
| `flow_spread_ml_s` | ml/s | Raw std of flow values — includes intended ramps. Pair with `flow_shape` annotation to distinguish intentional trajectory from unintended spread. |
| `pressure_jitter_bar` | bar | Same formula as flow_jitter on pressure. Sanity check — genuine channeling usually shows on both variables. Used as the secondary-indicator fallback in the risk score when no target_flow is commanded. |

#### Risk computation

`channeling_risk` is LOW / MODERATE / HIGH / VERY_HIGH derived from a 0–8 score:

- **flow_jitter:** +1 at ≥0.05, +1 more at ≥0.10
- **flow_vs_target (or pressure_jitter as fallback):** +1 at ≥0.35 (0.10 for jitter), +1 more at ≥0.70 (0.20)
- **pressure_max_drop_rate:** +1 at ≤−1.5, +1 more at ≤−3.0
- **flow_acceleration_late:** +1 at ≥0.05, +1 more at ≥0.10

Mapping: 0-1 → LOW · 2-3 → MODERATE · 4-5 → HIGH · 6-8 → VERY_HIGH.

#### Annotations

| Key | Values |
|-----|--------|
| `flow_jitter` | VERY_STABLE (<0.025) · STABLE (<0.05) · MODERATE_JITTER (<0.10) · JITTERY (<0.20) · VOLATILE |
| `flow_vs_target` | WITHIN_TOLERANCE (<0.15) · MINOR_DEVIATION (<0.35) · NOTABLE_DEVIATION (<0.70) · SEVERE_DEVIATION · N/A (when no target) |
| `pressure_jitter` | VERY_STABLE (<0.05) · STABLE (<0.10) · MODERATE_JITTER (<0.20) · JITTERY (<0.40) · VOLATILE |
| `pressure_drop` | NORMAL (≥−1.0) · MODERATE_DROP (≥−2.5) · STEEP_DROP (≥−5.0) · CLIFF |
| `late_flow_trend` | STABLE (<0.02) · SLIGHT_ACCELERATION (<0.05) · MODERATE_ACCELERATION (<0.10) · RAPID_ACCELERATION |
| `flow_shape` | FLAT · RAMPING_UP · RAMPING_DOWN — classifies the overall flow trajectory. Read alongside `flow_spread_ml_s` to distinguish designed ramps from jitter. |
| `window_confidence` | HIGH (≥15 samples) · MEDIUM (8-14) · LOW (5-7) · INSUFFICIENT. Discount HIGH/VERY_HIGH ratings when confidence is LOW. |
| `primary_signal` | Comma-separated list of indicators that fired at their lowest threshold, or `none`. Tells you *why* the rating came out the way it did without re-deriving from raw numbers. |
| `guidance` | One-sentence textual interpretation — a framing layer to cross-check your own reading of the data. |
| `note` | Present when trimming occurred; lists how many samples were excluded as ramp-up, leading zero-flow, or trailing zero-flow. |

### Temperature Diagnostics

Temperature stability vs target during brew.

| Field | Unit | Meaning |
|-------|------|---------|
| `overshoot_c` | °C | Maximum temperature above target |
| `undershoot_c` | °C | Maximum temperature below target |
| `stability_std_c` | °C | Std dev of brew temperature |

**Annotations:**

| Key | Bands |
|-----|-------|
| `overshoot` / `undershoot` | MINIMAL (<0.5) · SLIGHT (<1.0) · MODERATE (<2.0) · SIGNIFICANT |
| `stability` | VERY_STABLE (<0.3) · STABLE (<0.8) · MODERATE (<1.5) · UNSTABLE |

### Extraction Metrics

Brew quality indicators.

| Field | Unit | Meaning |
|-------|------|---------|
| `pressure_auc_bar_s` | bar·s | Area under pressure curve (total energy delivered) |
| `pressure_slope_brew_bar_s` | bar/s | Pressure trend during brew |
| `flow_slope_brew_ml_s2` | ml/s² | Flow trend during brew |
| `flow_avg_brew_ml_s` | ml/s | Average flow during brew |

**Annotations:**

| Key | Values |
|-----|--------|
| `pressure_trend` | INCREASING · FLAT · GRADUAL_DECLINE · MODERATE_DECLINE · STEEP_DECLINE |
| `flow_trend` | DECLINING / STABLE / INCREASING |

### Weight / Yield Diagnostics

From BT scale data (when available).

| Field | Unit | Meaning |
|-------|------|---------|
| `rate_avg_g_s` | g/s | Average weight accumulation rate |
| `rate_std_g_s` | g/s | Variability of accumulation rate |
| `scale_connected` | bool | Whether scale data was present |

If `scale_connected` is false, `rate_avg_g_s` and `rate_std_g_s` will be null.

### Profile Compliance

Measures how well the machine followed the programmed target profile.
**Only available when target pressure (tp) data is in the shot.**

| Field | Unit | Meaning |
|-------|------|---------|
| `pressure_rmse_bar` | bar | RMSE between actual and target pressure |
| `flow_rmse_ml_s` | ml/s | RMSE between actual and target flow (null if no tf data) |
| `max_pressure_overshoot_bar` | bar | Largest single overshoot above target |
| `max_pressure_undershoot_bar` | bar | Largest single undershoot below target |
| `max_flow_overshoot_ml_s` | ml/s | Largest flow above target (null if no tf data) |
| `max_flow_undershoot_ml_s` | ml/s | Largest flow below target (null if no tf data) |

**Annotations:**

| Key | Bands |
|-----|-------|
| `pressure_adherence` | EXCELLENT (<0.3) · GOOD (<0.8) · FAIR (<1.5) · POOR |
| `pressure_overshoot` | WITHIN_TOLERANCE (<0.25) · MINOR_OVERSHOOT (<0.5) · NOTABLE_OVERSHOOT (<1.0) · SEVERE_OVERSHOOT |
| `flow_adherence` | Same as pressure_adherence (only present when tf data available) |
| `flow_overshoot` | WITHIN_TOLERANCE (<0.3) · MINOR_DEVIATION (<0.7) · NOTABLE_DEVIATION (<1.5) · SEVERE_DEVIATION *(only when tf data available)* |
| `flow_undershoot` | Same bands as flow_overshoot *(only when tf data available)* |

**Key diagnostic insight:** Flow deviation is a more reliable grind indicator than
pressure overshoot. The Gaggimate/gaggiuino PID actively controls pump power to
maintain target pressure, so pressure overshoot is artificially limited by the
controller. Flow rate is a *consequence* of grind + dose + puck prep and cannot be
masked by the pump. Check `max_flow_overshoot_ml_s` / `max_flow_undershoot_ml_s`
first when diagnosing grind issues.

`max_pressure_overshoot_bar > 0.5` is unusual and worth investigating. `> 1.0` is
highly unlikely in normal operation and almost certainly indicates grind too fine,
excessive dose, or a puck preparation issue. Recommend coarsening grind.

---

## Per-Phase Diagnostics

At `per_phase` and `per_phase_detailed` levels, each phase in the `phases` list includes a
`diagnostics` object with metrics specific to that phase type. Only `per_phase_detailed`
also includes the `samples` array (~5 averaged data points per phase).

### Phase Classification

Phase names from the firmware are classified automatically:

| Phase Type | Matched Names |
|------------|---------------|
| **preinfusion** | preinfusion, pre-infusion, pi, soak, bloom, fill, preinfuse |
| **decline** | decline, taper, ramp-down, ramp down, cool down, cooldown |
| **brew** | Everything else (extraction, brew, main, hold, flat, step N, etc.) |

### Common Fields (all phase types)

| Field | Meaning |
|-------|---------|
| `phase_type` | "preinfusion" / "brew" / "decline" |
| `avg_pressure_bar` | Average pressure in this phase |
| `avg_flow_ml_s` | Average flow in this phase |
| `pressure_rmse_bar` | RMSE vs target pressure for this phase |
| `flow_rmse_ml_s` | RMSE vs target flow for this phase |
| `annotations.pressure_adherence` | EXCELLENT / GOOD / FAIR / POOR |

### Preinfusion-Specific Fields

| Field | Unit | Meaning |
|-------|------|---------|
| `ramp_rate_bar_s` | bar/s | How fast pressure ramps up |
| `saturation_time_s` | s | Time until flow stabilises (puck saturation) |

**Annotations:** `ramp_rate` → GENTLE (<0.5) · MODERATE (<1.5) · BRISK (<3.0) · AGGRESSIVE (<5.0) · VERY_AGGRESSIVE

### Brew-Specific Fields

| Field | Unit | Meaning |
|-------|------|---------|
| `resistance_avg` | dimensionless | Puck resistance in this phase |
| `resistance_slope` | /s | Resistance trend in this phase |
| `channeling_risk` | string | Channeling risk for this phase (may be INSUFFICIENT_DATA — see Channeling Indicators section) |
| `flow_jitter_ml_s` | ml/s | Per-phase flow jitter (first-difference std over steady-state window) |
| `pressure_jitter_bar` | bar | Per-phase pressure jitter (same metric on pressure) |

**Annotations:** `resistance_level`, `resistance_erosion`, `channeling` (risk label), plus the full `channeling_*` namespace mirroring the top-level Channeling Indicators annotations (`channeling_flow_jitter`, `channeling_flow_vs_target`, `channeling_pressure_drop`, `channeling_late_flow_trend`, `channeling_pressure_jitter`, `channeling_flow_shape`, `channeling_window_confidence`, `channeling_primary_signal`, `channeling_guidance`, `channeling_note` when trimming applied).

### Decline-Specific Fields

| Field | Unit | Meaning |
|-------|------|---------|
| `taper_rate_bar_s` | bar/s | Rate of pressure decline (negative values) |
| `taper_smoothness` | bar/s | Std dev of pressure derivatives — lower = smoother taper |

**Annotations:** `taper_smoothness` → VERY_SMOOTH (<0.2) · SMOOTH (<0.5) · MODERATE (<1.0) · ROUGH

---

## Interpretation Cheat Sheet

### Grind Too Fine
- `max_pressure_overshoot_bar` > 1.5 (MODERATE/SIGNIFICANT_OVERSHOOT)
- Resistance avg HIGH or VERY_HIGH
- Flow avg below expected for style
- Channeling risk may be elevated

### Grind Too Coarse
- Pressure never reaches target (`max_pressure_undershoot_bar` high)
- Resistance avg LOW or VERY_LOW
- Flow avg above expected for style
- Fast extraction time

### Channeling
- `channeling_risk` HIGH or VERY_HIGH (ignore INSUFFICIENT_DATA — it means the phase was too short to assess)
- Resistance slope STEEP_DECLINE
- Pressure volatility JITTERY or VOLATILE
- Flow acceleration in late shot

### Temperature Problems
- `stability_std_c` > 1.5 (UNSTABLE) → equipment issue
- `overshoot_c` > 2.0 → thermosiphon or PID issue
- `undershoot_c` > 2.0 → machine not heated properly

### Good Shot Indicators
- Resistance MODERATE, STABLE, GRADUAL_DECLINE
- Channeling LOW
- Temperature VERY_STABLE or STABLE
- Profile adherence EXCELLENT or GOOD
- Overshoot WITHIN_TOLERANCE

---

## Detail Level Strategy

```
User says "what's wrong with my shot?"
  → analyze_shot(shot_id)              # summary first

Summary shows HIGH channeling risk?
  → analyze_shot(shot_id, detail="per_phase")  # which phase has the problem?

Per-phase shows INSUFFICIENT_DATA for channeling?
  → Phase too short for reliable assessment. Check taste + adjacent phases.

Per-phase shows brew phase channeling?
  → analyze_shot(shot_id, detail="per_phase_detailed")  # see the curve shape
  → Correlate with taste, recommend grind adjustment

Need to see pressure/flow trend in a specific phase?
  → analyze_shot(shot_id, detail="per_phase_detailed")  # ~5 averaged samples per phase
```
