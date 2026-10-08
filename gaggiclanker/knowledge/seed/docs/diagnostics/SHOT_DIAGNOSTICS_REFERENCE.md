# Shot Diagnostics Reference

Comprehensive reference for all diagnostic metrics computed by `analyze_shot`.
Use this to understand what each metric means, and when to request which detail level.

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
| `resistance_avg` | float | Average puck resistance (the machine's own measurement squared, or P/F² when the shot has none; `resistance_source` says which). Higher = finer grind or tighter puck. |
| `resistance_slope` | float | How resistance changes over the shot. Negative = erosion (normal). Steep negative = possible channeling. |
| `temperature_stability_c` | float | Std deviation of brew temp. Lower = more stable. |
| `pressure_rmse_bar` | float | RMSE between actual and target pressure (profile compliance). 0 = perfect adherence. |
| `max_overshoot_bar` | float | Largest pressure overshoot above target. >1.0 bar is highly unusual and almost certainly = grind too fine. |
| `flow_rmse_ml_s` | float? | RMSE between actual and target flow (when target flow data available). 0 = perfect adherence. |
| `max_flow_overshoot_ml_s` | float? | Largest pump flow above the flow target, over the phases that steer by flow (see note below). |
| `scale_connected` | bool | Whether BT scale data was present. |

> **Flow vs pressure for grind diagnosis:** Neither adherence is a grind signal by
> itself. The controller drives pump power to hold the pressure target, so pressure
> error says how well it did. The pump flow (`fl`) that flow adherence compares with
> its target is the pump model's estimate for the power the controller chose, not a
> measurement: it leaves a flow target only when the pump runs out of power, a
> pressure limit takes over, or the smoothing lags. What the puck did is in the puck
> flow and the puck resistance.

---

## Full Diagnostics (per_phase / per_phase_detailed)

Returned as an object with these sub-sections:

### Resistance (Puck Resistance)

The **master diagnostic metric**. Computed as R = P / F² (quadratic Darcy model).
Captures grind fineness, puck prep quality, channeling, and erosion.

*gaggiclanker note:* when the shot carries the firmware's own per-sample puck
resistance `pr` (which is `sqrt(P) / Q_puck` on the machine's compensated puck
flow), R is `pr²` instead: the same model on the same scale. Ours (P / F²) is the fallback, and
`source` (`machine` or `computed`) says which one a shot has.

| Field | Unit | Meaning |
|-------|------|---------|
| `avg` | dimensionless | Average resistance across brew phase |
| `std` | dimensionless | Variability of resistance |
| `slope` | /s | Change rate — negative = erosion, steep negative = channeling |
| `peak` | dimensionless | Maximum resistance recorded |
| `peak_timing_pct` | 0–1 | When peak occurred (0 = start, 1 = end of brew) |

### Temperature Diagnostics

Temperature stability vs target during brew.

| Field | Unit | Meaning |
|-------|------|---------|
| `overshoot_c` | °C | Maximum temperature above target |
| `undershoot_c` | °C | Maximum temperature below target |
| `stability_std_c` | °C | Std dev of brew temperature |

### Extraction Metrics

Brew quality indicators.

| Field | Unit | Meaning |
|-------|------|---------|
| `pressure_auc_bar_s` | bar·s | Area under pressure curve (total energy delivered) |
| `pressure_slope_brew_bar_s` | bar/s | Pressure trend during brew |
| `flow_slope_brew_ml_s2` | ml/s² | Flow trend during brew |
| `flow_avg_brew_ml_s` | ml/s | Average puck flow during brew (the pump model's estimate) |
| `cup_flow_avg_brew_g_s` | g/s | Average cup flow during brew, as the scale measured it (null without a scale) |

**Cup flow and puck flow are different signals.** Cup flow is what reached the cup, worked
out from the scale's weight; it integrates back to the cup weight. Puck flow is an estimate
from the pump model: in steady extraction it runs at about the pump flow, well above the cup
flow, and it does not track when coffee reaches the cup (on most shots it starts seconds
after the cup has begun to fill, on a long pre-infusion much earlier). The
summary's `cup_first_drip_s` is when the cup's weight first rose half a gram above its first
reading (null without a scale); `time_to_first_drip_s` is the first puck flow. A shot with a
scale is read by its cup numbers; one without has only the puck estimates.

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

**Key diagnostic insight:** Adherence says how well the machine held the profile,
not what the puck did. The controller drives pump power to hold the pressure target,
so pressure error is limited by the controller; the pump flow that flow adherence
reads is the pump model's own estimate, so it leaves its target only when the pump
runs out of power or a pressure limit takes over. For the grind, read the puck
resistance and the puck flow (`flow_avg_brew_ml_s`).

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

### Preinfusion-Specific Fields

| Field | Unit | Meaning |
|-------|------|---------|
| `ramp_rate_bar_s` | bar/s | How fast pressure ramps up |
| `saturation_time_s` | s | Time until flow stabilises (puck saturation) |

### Brew-Specific Fields

| Field | Unit | Meaning |
|-------|------|---------|
| `resistance_avg` | dimensionless | Puck resistance in this phase |
| `resistance_slope` | /s | Resistance trend in this phase |

### Decline-Specific Fields

| Field | Unit | Meaning |
|-------|------|---------|
| `taper_rate_bar_s` | bar/s | Rate of pressure decline (negative values) |
| `taper_smoothness` | bar/s | Std dev of pressure derivatives — lower = smoother taper |

---

## Interpretation Cheat Sheet

### Grind Too Fine
- `max_pressure_overshoot_bar` > 1.5
- Flow avg below expected for style

### Grind Too Coarse
- Pressure never reaches target (`max_pressure_undershoot_bar` high)
- Flow avg above expected for style
- Fast extraction time

### Temperature Problems
- `stability_std_c` > 1.5 → equipment issue
- `overshoot_c` > 2.0 → thermosiphon or PID issue
- `undershoot_c` > 2.0 → machine not heated properly

---

## Detail Level Strategy

```
User says "what's wrong with my shot?"
  → analyze_shot(shot_id)              # summary first

Need to see pressure/flow trend in a specific phase?
  → analyze_shot(shot_id, detail="per_phase_detailed")  # ~5 averaged samples per phase
```
