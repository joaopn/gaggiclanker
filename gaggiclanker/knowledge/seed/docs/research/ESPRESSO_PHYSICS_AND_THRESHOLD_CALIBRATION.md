# Espresso Physics & Diagnostic Threshold Calibration

Technical reference documenting the physics behind espresso diagnostic metrics and the
evidence used to calibrate band thresholds in `analyze_shot`. This document is intended
for developers maintaining or extending the diagnostic system — it is **not** general
brewing advice for end users.

*Research compiled: February 2025.*

---

## Table of Contents

1. [Pressure](#1-pressure)
2. [Temperature](#2-temperature)
3. [Flow Rate](#3-flow-rate)
4. [Pressure Drop Rate (Derivative)](#4-pressure-drop-rate)
5. [Temperature Overshoot / Undershoot](#5-temperature-overshoot--undershoot)
6. [Pressure Ramp Rate](#6-pressure-ramp-rate)
7. [Puck Resistance](#7-puck-resistance)
8. [Sensor Resolution & Noise](#8-sensor-resolution--noise)
9. [Threshold Calibration Decisions](#9-threshold-calibration-decisions)
10. [Sources](#10-sources)

---

## 1. Pressure

### Standards

The Italian Espresso National Institute specifies 9 ± 0.5 bar as the reference
extraction pressure for traditional espresso (25 ± 2.5 mL in 25 ± 5 s).

> "The water pressure at the coffee cake: 9 ± 0.5 bar."
> — *Istituto Nazionale Espresso Italiano (INEI) certification standard* [1]

Wikipedia's espresso article cites the same 9 bar figure as the widely accepted standard,
with most modern machines operating in the 8–10 bar range. [2]

### Style-Specific Ranges

Not all espresso is brewed at 9 bar. Modern profiling has expanded the range:

| Style | Typical Pressure | Source |
|-------|-----------------|--------|
| Classic 9-bar | 8–10 bar flat | INEI standard [1]; TELEMETRY_PATTERNS.md |
| Turbo | 5–6 bar | espressoaf.com [3]; TELEMETRY_PATTERNS.md |
| Bloom | 7–9 bar (post-bloom) | TELEMETRY_PATTERNS.md |
| Allongé | ~6 bar peak | espressoaf.com [3]; TELEMETRY_PATTERNS.md |
| Lever decline | 8–9 bar peak → 3–5 bar | TELEMETRY_PATTERNS.md |
| Dark / gentle | 7–8 bar | TELEMETRY_PATTERNS.md |

### Machine Capabilities

The Decent Espresso DE1 (a comparable high-precision espresso machine with open
telemetry) operates in the 0–13 bar range with ±1% pressure accuracy. [4]

Gaggimate-equipped Gaggia machines have a similar pressure range via modified OPV
(over-pressure valve) and pump control. The gaggiuino project (on which Gaggimate is
based) enables full pressure profiling on Gaggia Classic hardware. [5]

---

## 2. Temperature

### Standards

The INEI standard specifies 88 ± 2 °C water temperature at the group head. [1]
Wikipedia cites a typical range of 90–96 °C, though many specialty roasters and
competition baristas use temperatures closer to 88–92 °C for lighter roasts. [2]

### Machine Precision

The Decent DE1 advertises ±1 °C temperature accuracy during extraction. [4]
Gaggimate/gaggiuino achieves comparable PID-controlled temperature stability,
though the specific tolerance depends on the PID tuning and thermal mass of the
particular Gaggia model.

### Anomaly Thresholds

Our TELEMETRY_PATTERNS.md (adapted from Charlie Hall's gaggimate-barista project)
uses these thresholds, which informed our diagnostic bands:

| Condition | Threshold | Classification |
|-----------|-----------|----------------|
| Cold start | > 3 °C below target | Anomaly — insufficient pre-heat |
| Temperature drop | > 2 °C during shot | Anomaly — cold portafilter or cups |
| PID overshoot | > 2 °C above target | Anomaly — wait after flush |
| PID oscillation | ± 3 °C swings | Instability — service machine |

**Key insight:** The > 2 °C threshold for overshoot is a practically validated boundary.
Overshoots below 2 °C are generally within PID tolerance and don't noticeably affect
taste. Above 2 °C, the temperature deviation starts to produce detectable bitterness
or astringency, especially with lighter roasts.

---

## 3. Flow Rate

### Typical Ranges by Style

espressoaf.com's profiling guide provides the most granular published reference for
flow rates (expressed as "débit" — water flow entering the puck, not liquid in cup):

| Style | Flow Rate | Source |
|-------|----------|--------|
| Ristretto | < 1 mL/s | espressoaf.com [3] |
| Normale (classic) | ~1 mL/s | espressoaf.com [3] |
| Lungo | ~2 mL/s | espressoaf.com [3] |
| Allongé | > 2 mL/s | espressoaf.com [3] |
| Turbo | ~4.5 mL/s (the "Turbo" style from Jonathan Gagné) | espressoaf.com [3] |
| Preinfusion fill | ~7–8 mL/s | espressoaf.com [3] |

Our TELEMETRY_PATTERNS.md documents similar ranges in a Gaggimate context:

| Style | Extraction Flow | Source |
|-------|----------------|--------|
| Classic 9-bar | 1.5–2.5 mL/s steady | TELEMETRY_PATTERNS.md |
| Bloom | 1.5–2.5 mL/s (post-bloom) | TELEMETRY_PATTERNS.md |
| Turbo | 3–5 mL/s (intentional) | TELEMETRY_PATTERNS.md |
| Allongé | 2–3 mL/s constant | TELEMETRY_PATTERNS.md |

### Flow Anomaly Thresholds

| Condition | Threshold | Source |
|-----------|-----------|--------|
| Too fast (classic) | > 3 mL/s avg | TELEMETRY_PATTERNS.md |
| Choked | < 1 mL/s avg | TELEMETRY_PATTERNS.md |
| Channel opening | Acceleration from 1.5 → 4+ mL/s | TELEMETRY_PATTERNS.md |

---

## 4. Pressure Drop Rate

### What It Measures

`pressure_drop_rate` is the steepest negative first derivative of pressure during the
brew phase, computed as:

```
dp/dt = (P[i] - P[i-1]) / sample_interval
```

where `sample_interval` = 100 ms (0.1 s) for Gaggimate telemetry (confirmed from
parser tests and API WebSocket data).

### Noise Considerations

At 100 ms sample intervals with no smoothing applied, single-sample derivatives are
noisy. A pump oscillation of ±0.05 bar between two consecutive samples produces:

```
dp/dt = 0.05 / 0.1 = 0.5 bar/s
```

For negative direction (pressure dip), this means −0.5 bar/s can appear from noise
alone. This is why the NORMAL threshold must be wider than −0.5 bar/s — otherwise
routine pump pulsation triggers false MODERATE_DROP classifications.

### Physical Interpretation

| Rate | Physical Meaning |
|------|-----------------|
| 0 to −1.0 bar/s | Normal pump regulation, minor fluctuations |
| −1.0 to −2.5 bar/s | Moderate pressure loss — could indicate puck erosion, valve change, or profile transition |
| −2.5 to −5.0 bar/s | Steep drop — likely channeling event or rapid puck failure |
| < −5.0 bar/s | Cliff — catastrophic event (channel blowout, pump shutoff, or end of phase) |

### Calibration Decision

Original thresholds were (−0.5 / −1.5 / −3.0), calibrated to **(−1.0 / −2.5 / −5.0)**.

**Rationale:** The 100 ms unsmoothed derivatives amplify transient noise by roughly 2×
compared to what a 200 ms or smoothed window would produce. Widening by 2× compensates
for this without adding computational complexity. If smoothing is later added (e.g.,
3-sample moving average), thresholds should be tightened back to approximately
(−0.6 / −1.5 / −3.0).

---

## 5. Temperature Overshoot / Undershoot

### What It Measures

`overshoot_c` = max(T_actual − T_target) during brew phase.
`undershoot_c` = max(T_target − T_actual) during brew phase.

These capture the single worst-case deviation from the profile's target temperature.

### Physics

PID-controlled boilers exhibit overshoot when recovering from a flush or when the
thermal mass of the group / portafilter absorbs heat. The Gaggia Classic's small
aluminum boiler is particularly prone to overshoot after flushing.

### Evidence for Thresholds

| Source | Threshold | Classification |
|--------|-----------|----------------|
| TELEMETRY_PATTERNS.md | > 2 °C above target | "PID overshoot" anomaly |
| TELEMETRY_PATTERNS.md | > 3 °C below target | "Cold start" anomaly |
| TELEMETRY_PATTERNS.md | ± 3 °C swings | "PID instability" |
| INEI standard [1] | ± 2 °C from 88 °C | Acceptable range for certification |
| Decent DE1 [4] | ± 1 °C | Advertised accuracy |

### Calibration Decision

Original thresholds: MINIMAL (<0.5) · SLIGHT (<1.5) · MODERATE (<3.0) · SIGNIFICANT.

Calibrated to: **MINIMAL (<0.5) · SLIGHT (<1.0) · MODERATE (<2.0) · SIGNIFICANT**.

**Rationale:** The old MODERATE boundary at 3.0 °C was above the TELEMETRY_PATTERNS.md
anomaly threshold of > 2 °C. This meant an overshoot of 2.5 °C — which should be flagged
as concerning — was classified as merely SLIGHT. The tightened bands align with:

- The INEI ± 2 °C tolerance (anything beyond 2 °C is outside specification)
- The TELEMETRY_PATTERNS.md > 2 °C anomaly threshold
- Practical taste impact: 1–2 °C overshoot produces barely perceptible bitterness; > 2 °C
  is reliably detectable by trained tasters

The MINIMAL band stays at 0.5 °C (within instrument noise / PID dead-band).

---

## 6. Pressure Ramp Rate

### What It Measures

`ramp_rate_bar_s` is the linear slope of pressure during the preinfusion phase,
computed via ordinary least squares over all pressure samples in the phase.

It indicates how quickly the machine builds pressure before the main extraction begins.

### Physical Interpretation

| Rate | Meaning | Typical Context |
|------|---------|----------------|
| < 0.5 bar/s | Very gentle ramp | Long preinfusion, bloom fills, lever starts |
| 0.5–1.5 bar/s | Moderate ramp | Standard preinfusion profiles |
| 1.5–3.0 bar/s | Quick ramp | Short preinfusion, some classic profiles |
| 3.0–5.0 bar/s | Aggressive | Turbo-style, minimal preinfusion |
| > 5.0 bar/s | Very aggressive | Near-instant pressurization |

### Label Naming Decision

Original labels: VERY_SLOW / SLOW / NORMAL / FAST / VERY_FAST.

Renamed to: **GENTLE / MODERATE / BRISK / AGGRESSIVE / VERY_AGGRESSIVE**.

**Rationale:** The slow/fast framing implies a quality judgment — "SLOW" sounds bad, but
a slow preinfusion ramp is actually optimal for many styles (bloom, lever, gentle
preinfusion). This creates confusion for an LLM interpreting the annotations:

- A 0.3 bar/s ramp in a bloom preinfusion labeled "VERY_SLOW" could be misinterpreted
  as a problem, when it's actually the intended behavior
- "GENTLE" correctly conveys that the ramp is intentionally soft without implying a defect
- The GENTLE → AGGRESSIVE scale describes the character of the ramp without value judgment

The numeric thresholds (0.5 / 1.5 / 3.0 / 5.0) were validated against TELEMETRY_PATTERNS.md
style-specific pressure curves and were confirmed appropriate — only the labels changed.

---

## 7. Puck Resistance

### Physics

Puck resistance is modeled as:

```
R = P / F²
```

where P = pressure (bar) and F = flow rate (mL/s). This is a simplified Darcy's Law
analog: higher resistance means finer grind or tighter puck, lower means coarser or
channeling.

### Noise Amplification

Because flow is squared in the denominator, small flow measurement errors are amplified.
At low flow rates (< 0.5 mL/s), the resistance calculation becomes very noisy:

```
If F = 0.5 ± 0.1 mL/s and P = 9 bar:
R_low  = 9 / 0.6² = 25.0
R_high = 9 / 0.4² = 56.3
```

This 2× range from a ±0.1 mL/s flow error explains why resistance stability
(std dev of resistance over time) can appear volatile even in well-prepared shots.

### Resistance Bands

The resistance bands (VERY_LOW < 0.5 · LOW < 1.5 · MODERATE < 3.0 · HIGH < 5.0 · VERY_HIGH)
and erosion bands (INCREASING / FLAT / GRADUAL_DECLINE / MODERATE_DECLINE / STEEP_DECLINE)
were validated against the research and found appropriate. No changes were needed.

---

## 8. Sensor Resolution & Noise

### Gaggimate Hardware

Gaggimate is a Gaggia Classic modification based on the gaggiuino open-source project [5].
It adds:

- Pressure transducer (typically 0–20 bar, ±0.5% accuracy)
- Thermocouple or RTD (±0.5 °C typical)
- Flow meter (hall-effect type, resolution varies)
- Optional BT scale integration (via BLE)

### Telemetry Sample Rate

The Gaggimate WebSocket API transmits telemetry at **100 ms intervals** (10 Hz).
This was confirmed from:

- Parser test fixtures showing 0.1 s spacing between consecutive samples
- WebSocket frame analysis in the test suite

### Implications for Derivatives

At 10 Hz with no smoothing:

| Sensor | Typical Noise | Derivative Noise |
|--------|--------------|-----------------|
| Pressure | ± 0.05 bar | ± 0.5 bar/s |
| Temperature | ± 0.3 °C | ± 3.0 °C/s |
| Flow | ± 0.1 mL/s | ± 1.0 mL/s² |

This derivative noise floor directly informed the pressure drop rate threshold
widening (see [Section 4](#4-pressure-drop-rate)).

---

## 9. Threshold Calibration Decisions

Summary of all threshold changes made based on this research:

### Changes Applied

| Metric | Band | Old Value | New Value | Reason |
|--------|------|-----------|-----------|--------|
| Pressure drop rate | NORMAL | ≥ −0.5 | ≥ −1.0 | Pump noise at 100 ms = ±0.5 bar/s |
| Pressure drop rate | MODERATE_DROP | ≥ −1.5 | ≥ −2.5 | Proportional widening |
| Pressure drop rate | STEEP_DROP | ≥ −3.0 | ≥ −5.0 | Proportional widening |
| Temp overshoot | SLIGHT | < 1.5 | < 1.0 | TELEMETRY_PATTERNS flags > 2 °C as anomaly |
| Temp overshoot | MODERATE | < 3.0 | < 2.0 | Align with INEI ± 2 °C tolerance |
| Ramp rate | Labels | VERY_SLOW…VERY_FAST | GENTLE…VERY_AGGRESSIVE | Avoid negative connotation for slow ramps |

### Validated (No Change Needed)

| Metric | Conclusion |
|--------|------------|
| Pressure stability bands | Well-calibrated. 0.15 bar jitter threshold is comfortably above sensor noise. |
| Flow stability bands | Appropriate. 0.10 mL/s threshold accounts for flow meter resolution. |
| Resistance bands | Reasonable P/F² ranges confirmed by cross-referencing flow and pressure style data. |
| Resistance erosion bands | Slope thresholds validated against expected puck degradation rates. |
| Late flow trend bands | Acceleration thresholds align with channel-opening signatures in TELEMETRY_PATTERNS.md. |
| Temperature stability bands | Std dev bands appropriate — 0.3 °C "VERY_STABLE" matches Decent's ±1 °C spec. |
| Profile adherence bands | RMSE thresholds reasonable against ±0.5 bar pump regulation noise. |
| Pressure overshoot bands | 0.25 / 0.5 / 1.0 bar thresholds calibrated from practitioner input: 0.5 bar can occur occasionally, >1.0 bar is highly unlikely in normal operation. No peer-reviewed source; thresholds are expert-informed. |
| Flow deviation bands | 0.3 / 0.7 / 1.5 ml/s thresholds calibrated from expert input and practical scenario analysis. See section below. |
| Taper smoothness bands | R² residual thresholds in reasonable range. |
| Channeling risk scoring | Composite scoring with point system produces sensible risk tiers. |

### Flow Deviation Bands — Rationale

Flow deviation (overshoot/undershoot vs target) is the more reliable grind indicator
because the Gaggimate PID actively controls pump power to maintain target
pressure. Pressure overshoot is artificially limited by the controller; flow rate is
a *consequence* of grind + dose + puck prep and cannot be masked by the pump.

**Bands:** WITHIN_TOLERANCE (<0.3) · MINOR_DEVIATION (<0.7) · NOTABLE_DEVIATION (<1.5) · SEVERE_DEVIATION

**Scenario analysis** (assuming a normal espresso profile targeting ~1 ml/s flow):

| Scenario | Grind | Expected outcome | Flow deviation | Band |
|----------|-------|-----------------|----------------|------|
| 30 g in 30 s | Correct | On-target | ~0 ml/s | WITHIN_TOLERANCE |
| 30 g in 25 s | Slightly coarse | ~20% fast | ~+0.2 ml/s | WITHIN_TOLERANCE |
| 30 g in 20 s | Coarse | ~50% fast | ~+0.5 ml/s | MINOR_DEVIATION |
| 30 g in 15 s | Very coarse | ~100% fast | ~+1.0 ml/s | NOTABLE_DEVIATION |
| 30 g in 45 s | Slightly fine | ~50% slow | ~−0.33 ml/s | MINOR_DEVIATION |
| 30 g in 60 s | Very fine | ~100% slow | ~−0.5 ml/s | MINOR_DEVIATION |
| Near-choke | Way too fine | Flow near zero | ~−1.0 ml/s | NOTABLE_DEVIATION |

**Threshold justification:**
- **0.3 ml/s** — Normal puck-to-puck variation at well-dialled-in grind settings.
  Represents ~15-30% flow deviation depending on style — a tolerance range where
  shot timing shifts are noticeable but the extraction is still acceptable.
- **0.7 ml/s** — Shot timing is significantly off. For a 1 ml/s target, this is a
  70% deviation. The shot will taste noticeably different from the profile's intent.
- **1.5 ml/s** — Something is dramatically wrong. Flow is more than doubled (overshoot)
  or halved (undershoot). Almost certainly requires grind adjustment.

**Note:** These thresholds are expert-informed, not peer-reviewed. They should be
recalibrated as real-world shot data is collected and analysed.

---

## 10. Sources

1. **Istituto Nazionale Espresso Italiano (INEI)**. Certified Italian Espresso quality
   standard. Specifies 9 ± 0.5 bar, 88 ± 2 °C, 25 ± 2.5 mL in 25 ± 5 s.
   Referenced via multiple secondary sources.

2. **Wikipedia — "Espresso"**. General reference confirming 9 bar standard, 90–96 °C
   range, and extraction parameters.
   https://en.wikipedia.org/wiki/Espresso

3. **espressoaf.com — "Introduction to Profiling"**. Detailed guide on flow profiling
   with style-specific flow rates (ristretto, normale, lungo, allongé, turbo).
   Provides the "débit" (water delivery rate) framework.
   https://espressoaf.com/guides/profiling.html

4. **Decent Espresso — DE1 Overview**. Machine specifications including ±1 °C
   temperature accuracy, ±1% pressure accuracy, 0–13 bar operating range.
   https://decentespresso.com/overview

5. **gaggiuino — Open-source Gaggia modification project**. The hardware platform on
   which Gaggimate is based. Provides pressure profiling, PID temperature control,
   and telemetry via ESP32.
   https://gaggiuino.github.io/

6. **TELEMETRY_PATTERNS.md** (this repository). Style-specific telemetry ranges and
   anomaly thresholds adapted from Charlie Hall's gaggimate-barista project.
   `knowledge/diagnostics/TELEMETRY_PATTERNS.md`

7. **SHOT_DIAGNOSTICS_REFERENCE.md** (this repository). Complete metric reference
   for all diagnostic bands and annotations.
   `knowledge/diagnostics/SHOT_DIAGNOSTICS_REFERENCE.md`

---

*This document should be updated whenever diagnostic thresholds are recalibrated
or new empirical data becomes available from real-world shot analysis.*
