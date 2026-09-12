# Telemetry Pattern Interpretation Guide

Detailed reference for interpreting Gaggimate shot telemetry and correlating with taste outcomes.

*Adapted from [gaggimate-barista](https://github.com/charleshall888/gaggimate-barista) by Charlie Hall.*

---

## Bluetooth Scale Artifacts

The BT scale communicates via BLE, and the connection can produce spurious data — especially near the end of a shot.

### Known Artifact Patterns

| Pattern | Signature | How to Handle |
|---------|-----------|---------------|
| **End-of-shot volume spike** | Weight/volume jumps sharply upward in final 1-3 samples | Discard. Use the last stable reading before the spike. |
| **Drop to zero** | Weight drops to 0g in final samples | Discard. Use the last non-zero reading before the drop. |
| **Spike then drop** | Volume spikes up then immediately drops to 0g | Both are artifacts. Use the last stable reading before the spike began. |
| **Null final weight** | `final_weight_g` is null despite liquid in cup | Estimate from last stable weight sample, or use `total_volume_ml × 0.82`. |

### Detection Rules

1. Scan the last 3-5 weight/volume samples. If any differs from preceding trend by >2× the average inter-sample change, treat as artifact.
2. A reading of exactly 0g after non-zero readings is always a disconnect artifact.
3. **Prefer telemetry data for weight**, but if the reading looks unreliable (artifacts, nulls, spikes), ask the user for the actual weight rather than guessing.
4. Do not flag artifact-caused anomalies as extraction problems.
5. Pressure, flow, and temperature data from the machine's sensors are reliable — only weight/volume from the scale is suspect.

---

## Flow Meter vs Cup Weight During Bloom

The flow meter measures water **entering the group head**, not **exiting the basket**. During bloom, these diverge significantly.

| Flow Meter | Cup Weight | Interpretation |
|------------|-----------|----------------|
| 0 ml/s | 0g | Standard bloom — fill water held in puck |
| >1 ml/s | 0g | **Puck absorption** — gravity/residual pressure feeding water; puck absorbing it. **Not channeling.** |
| >1 ml/s | >0g (increasing) | **Through-flow** — water passing through puck. Possible channel or puck too permeable. |

**Never diagnose bloom-phase flow as channeling without corroborating cup weight.**

---

## Pressure Patterns

### Pressure Curve by Style

| Style | Pre-infusion | Extraction Pressure | Curve Shape |
|-------|-------------|---------------------|-------------|
| Classic 9-Bar | 2-4 bar, 4-8s | 8-10 bar flat | Flat hold |
| Bloom | 2-3 bar fill, then pump off | 7-9 bar | Bloom pause → ramp → hold |
| Turbo | 5-6 bar, 2-3s | 5-6 bar | Flat at lower pressure |
| Allongé | 2-3 bar, 5-8s | ~6 bar peak | Flow-controlled, pressure rises then declines |
| Lever Decline | 2-4 bar, 5-8s | 8-9 bar peak → 3-5 bar | Linear decline |
| Dark/Gentle | 2-3 bar, 4-6s | 7-8 bar | Flat or gentle taper |

### Pressure Anomalies

| Pattern | Likely Cause | Taste Impact | Fix |
|---------|-------------|--------------|-----|
| **Early spike** (>10.5 bar in first 5s) | Fines migration | Sour start, bitter finish | Better WDT |
| **Sustained spike** (>10 bar throughout) | Grind too fine | Bitter, harsh | Grind coarser |
| **Never reaches target** | Too coarse (or normal post-bloom ramp) | Thin, sour | Grind finer (verify not post-bloom) |
| **Rapid decay** (9→5 bar mid-shot) | Channel opened | Starts balanced, ends sour | Better puck prep |
| **Slow build** (>8s to reach target) | Too coarse, or normal post-bloom | Under-extraction | Grind finer (if not post-bloom) |

### Pressure-Resistance Physics

**Pressure = pump force vs. puck drainage.** Resistance affects ALL phases:

| Grind | Resistance | Ramp | Hold | Decline |
|-------|-----------|------|------|---------|
| **Finer** | Higher | Pressure builds **faster** | Stays high easily | Drops **slowly** |
| **Coarser** | Lower | Builds **slower** | Pump works harder | Drops **readily** |

**CRITICAL:** Finer grind = pressure builds faster AND drops slower. Coarser = builds slower AND drops faster.

---

## Flow Patterns

### Flow Curve by Style

| Style | Pre-infusion Flow | Extraction Flow | First Drip |
|-------|-------------------|-----------------|------------|
| Classic 9-Bar | 0-1 ml/s | 1.5-2.5 ml/s steady | 4-8s |
| Bloom | 1-2 ml/s fill, ~0 during bloom | 1.5-2.5 ml/s | Delayed by bloom (15-25s from start) |
| Turbo | Brief pre-wet | 3-5 ml/s (intentional) | 2-4s |
| Allongé | 0.5-1 ml/s | 2-3 ml/s constant | 5-10s |
| Lever Decline | 0-1 ml/s | 1.5-2 ml/s, increases as pressure drops | 4-8s |

### Flow Anomalies

| Pattern | Likely Cause | Taste Impact | Fix |
|---------|-------------|--------------|-----|
| **Instant flow** (first drip < 3s) | Coarse grind, channeling | Thin, sour | Grind finer |
| **Delayed drip** (> 10s) | Very fine grind | Risk of bitter | Grind coarser |
| **High flow** (avg > 3 ml/s) | Low resistance | Watery, sour | Grind finer |
| **Choked flow** (avg < 1 ml/s) | Too fine | Bitter, astringent | Grind coarser |
| **Flow acceleration** (1.5→4+ ml/s) | Channel opening | Balanced start, sour finish | Better puck prep |

---

## Temperature Anomalies

| Pattern | Likely Cause | Taste Impact | Fix |
|---------|-------------|--------------|-----|
| **Cold start** (>3°C below target) | Insufficient pre-heat | Sour | Longer warm-up, flush |
| **Temperature drop** (>2°C during shot) | Cold portafilter/cups | Progressive sourness | Pre-heat everything |
| **Overshoot** (>2°C above target) | PID overshoot | Bitter early | Wait after flush |
| **Oscillation** (±3°C swings) | PID instability | Inconsistent | Service machine |

---

## Taste-to-Telemetry Correlation Matrix

| Taste | Primary Suspect | Secondary | Tertiary |
|-------|----------------|-----------|----------|
| **Sour** | Flow above style range | First drip too early | Temperature too low |
| **Bitter** | Time above style range | Temperature too high | Pressure above target |
| **Astringent** | Pressure spike + drop | Channeling (erratic flow) | Over-extracted fines |
| **Watery** | Pressure below style range | Flow above range | Time below range |
| **Flat/Muted** | Temperature drift | Insufficient preinfusion | Stale beans |
| **Thin body** | Flow above range | Time below range | Pressure below range |

---

## Style Detection Fingerprints

Use for Tier 3 detection when neither profile definition nor profile name is available.

| Fingerprint | Detected Style |
|-------------|----------------|
| Total time < 22s AND avg flow > 3 ml/s | **Turbo** |
| Pre-infusion > 12s with near-zero flow pause | **Bloom** |
| Pressure declining through brew (>4 bar drop over >15s) | **Lever Decline** |
| Total time > 38s AND avg pressure < 7 bar AND high yield | **Allongé** |
| Extraction pressure 7-8 bar steady, no bloom/decline | **Dark/Gentle** |
| Extraction pressure 8-10 bar steady, 25-35s total | **Classic 9-Bar** |

---

## Per-Style Diagnostic Notes

### Turbo
- Flow 3-5 ml/s is **EXPECTED**. Only flag > 6 ml/s.
- Total time 12-20s is **EXPECTED**. Not "too short."
- Pressure 5-6 bar is **EXPECTED**. Not "too low."
- Sourness usually means ratio too short (needs 1:2.5-1:3).

### Bloom
- Delayed first drip (15-25s from start) is **EXPECTED**.
- Flow during pump-off does NOT mean channeling — cross-reference cup weight.
- Total time 30-40s is **EXPECTED**.
- Lower extraction pressure (7-8 bar) is **EXPECTED** for naturals.

### Allongé
- Total time 25-50s is **EXPECTED**.
- Pressure at 5-6 bar is **EXPECTED**.
- High yield (1:4-1:5) is **INTENTIONAL**.
- Bitterness usually means temp too high, not over-extraction.

### Lever Decline
- Declining pressure is **INTENTIONAL**.
- Flow increasing as pressure drops is **NORMAL** physics.

### Dark/Gentle
- Pressure 7-8 bar is **EXPECTED**. Not "too low."
- Shorter ratios (1:1.5-1:2) are **NORMAL**.
- Temperature 88-90°C is **EXPECTED**.
