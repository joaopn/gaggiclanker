# Espresso Physics & Diagnostic Threshold Calibration

Technical reference documenting the physics behind espresso diagnostic metrics and the
evidence used to calibrate thresholds in `analyze_shot`. This document is intended
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
uses these thresholds:

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
alone.

### Physical Interpretation

| Rate | Physical Meaning |
|------|-----------------|
| 0 to −1.0 bar/s | Normal pump regulation, minor fluctuations |
| −1.0 to −2.5 bar/s | Moderate pressure loss — could indicate puck erosion, valve change, or profile transition |
| −2.5 to −5.0 bar/s | Steep drop — likely channeling event or rapid puck failure |
| < −5.0 bar/s | Cliff — catastrophic event (channel blowout, pump shutoff, or end of phase) |

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

*gaggiclanker note:* the firmware computes the same quantity itself (and logs it
per sample) as
`pr = sqrt(P) / Q_puck`, from a compensated estimate of the flow through the
puck, so `pr²` equals `P / Q_puck²`. gaggiclanker uses `pr²` as R whenever a shot
carries it and P / F² from the logged flow otherwise.

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

7. **SHOT_DIAGNOSTICS_REFERENCE.md** (this repository). Complete metric reference.
   `knowledge/diagnostics/SHOT_DIAGNOSTICS_REFERENCE.md`

---

*This document should be updated whenever diagnostic thresholds are recalibrated
or new empirical data becomes available from real-world shot analysis.*
