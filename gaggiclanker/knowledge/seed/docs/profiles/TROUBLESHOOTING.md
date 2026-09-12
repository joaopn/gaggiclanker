# Troubleshooting Profiles

Diagnose and fix common espresso extraction issues through profile adjustments.

---

## Issue: Shot Channeling

**Symptoms:**
- Fast flow rate
- Under-extracted taste
- Sour, thin, watery shot
- Visible spurting from portafilter
- Uneven extraction (blonde spots in puck)

### Profile Fixes

#### 1. Increase Pre-infusion Duration

**Before:**
```json
{
  "name": "Pre-infusion",
  "duration": 3,
  "pump": { "target": "flow", "pressure": 9, "flow": 3.5 }
}
```

**After:**
```json
{
  "name": "Pre-infusion",
  "duration": 6,
  "pump": { "target": "flow", "pressure": 9, "flow": 2.5 }
}
```

Longer, gentler pre-infusion allows more even saturation.

#### 2. Reduce Pre-infusion Flow

Lower flow rate reduces initial puck disturbance:
- **Current**: 3-4 ml/s → **Try**: 2-2.5 ml/s

#### 3. Add Bloom Phase

Insert a bloom phase after pre-infusion:

```json
{
  "name": "Bloom",
  "phase": "preinfusion",
  "valve": 1,
  "duration": 8,
  "temperature": 0,
  "transition": { "type": "instant", "duration": 0, "adaptive": true },
  "pump": { "target": "power", "pressure": 0, "flow": 0 }
}
```

6-8 seconds with pump off allows even water distribution.

---

## Issue: Over-Extraction

**Symptoms:**
- Bitter, harsh taste
- Astringent, dry mouthfeel
- Dark, thin crema
- Burnt or ashy notes
- Unpleasant aftertaste

### Profile Fixes

#### 1. Reduce Temperature

| Current | Adjustment |
|---------|------------|
| 94°C | Try 92°C |
| 92°C | Try 90°C |
| 90°C | Try 88°C |

```json
{
  "temperature": 90
}
```

#### 2. Shorten Extraction Time

Reduce the hold phase duration or lower the volumetric target:

**Before:**
```json
{
  "name": "Hold",
  "duration": 30,
  "targets": [{ "type": "volumetric", "operator": "gte", "value": 40 }]
}
```

**After:**
```json
{
  "name": "Hold",
  "duration": 25,
  "targets": [{ "type": "volumetric", "operator": "gte", "value": 34 }]
}
```

#### 3. Add Declining Pressure Phase

Reduce extraction intensity at the end:

```json
{
  "name": "Decline",
  "phase": "decline",
  "valve": 1,
  "duration": 6,
  "temperature": 0,
  "transition": { "type": "ease-out", "duration": 5, "adaptive": true },
  "pump": { "target": "pressure", "pressure": 5, "flow": 0 }
}
```

#### 4. Lower Hold Pressure

Reduce extraction pressure from 9 bar to 7-8 bar:

```json
"pump": { "target": "pressure", "pressure": 7.5, "flow": 4 }
```

---

## Issue: Under-Extraction

**Symptoms:**
- Sour, acidic taste
- Weak, thin body
- Lack of sweetness
- Pale crema
- Quick shot time (<20s)

### Profile Fixes

#### 1. Increase Temperature

| Current | Adjustment |
|---------|------------|
| 90°C | Try 92°C |
| 92°C | Try 94°C |
| 94°C | Try 95°C |

```json
{
  "temperature": 94
}
```

#### 2. Extend Extraction Time

Increase volumetric target or extend duration:

**Before:**
```json
{
  "targets": [{ "type": "volumetric", "operator": "gte", "value": 32 }]
}
```

**After:**
```json
{
  "targets": [{ "type": "volumetric", "operator": "gte", "value": 38 }]
}
```

#### 3. Increase Hold Pressure

If pressure is low, increase to 9 bar:

```json
"pump": { "target": "pressure", "pressure": 9, "flow": 4 }
```

#### 4. Add Bloom Phase

Better saturation leads to more even extraction:

```json
{
  "name": "Bloom",
  "phase": "preinfusion",
  "duration": 6,
  "pump": { "target": "power", "pressure": 0, "flow": 0 }
}
```

---

## Issue: Inconsistent Shots

**Symptoms:**
- Different results with same profile
- Varying extraction times
- Unpredictable taste
- Some shots good, some bad

### Profile Fixes

#### 1. Add Volumetric Stop Condition

Always use weight-based stopping on final phase:

```json
"targets": [
  { "type": "volumetric", "operator": "gte", "value": 36 }
]
```

**Requires Bluetooth scale for best results.**

#### 2. Enable Adaptive Transitions

Use `adaptive: true` to respond to actual conditions:

```json
"transition": { "type": "linear", "duration": 3, "adaptive": true }
```

#### 3. Add Pressure-Based Targets

Add pressure targets to ramp phases:

```json
{
  "name": "Ramp",
  "targets": [
    { "type": "pressure", "operator": "gte", "value": 8.5 }
  ]
}
```

#### 4. Increase Pre-infusion Time

Longer pre-infusion allows more consistent puck saturation:

```json
{
  "name": "Pre-infusion",
  "duration": 6
}
```

---

## Issue: Pump Strain / Loud Operation

**Symptoms:**
- Grinding or straining sounds
- Pressure spikes
- Pump cycling rapidly
- Excessive vibration

### Profile Fixes

#### 1. Start with Flow Mode

Don't start with pressure mode on dry puck:

**Before (stressful):**
```json
{
  "name": "Start",
  "pump": { "target": "pressure", "pressure": 9, "flow": 0 }
}
```

**After (gentle):**
```json
{
  "name": "Pre-infusion",
  "pump": { "target": "flow", "pressure": 9, "flow": 3 }
}
```

#### 2. Use Gentler Transitions

Use `ease-in` instead of `instant` for pressure changes:

```json
"transition": { "type": "ease-in", "duration": 4, "adaptive": true }
```

#### 3. Reduce Target Pressure

Try 8-8.5 bar instead of 9 bar:

```json
"pump": { "target": "pressure", "pressure": 8.5, "flow": 4 }
```

#### 4. Check Grind (Non-Profile)

If grind is too fine, the pump struggles. Coarsen slightly and compensate with:
- Longer pre-infusion
- Higher temperature
- Bloom phase

---

## Issue: Slow/Choking Shot

**Symptoms:**
- Very slow drip
- Excessive extraction time (>45s)
- Pressure builds but little flow
- Very dark, thick output

### Profile Fixes

#### 1. Lower Target Pressure

Reduce pressure to allow flow:

```json
"pump": { "target": "pressure", "pressure": 7, "flow": 0 }
```

#### 2. Use Flow Mode

Switch to flow-based control:

```json
"pump": { "target": "flow", "pressure": 9, "flow": 2 }
```

#### 3. Add Adaptive Flow

Let the machine adjust to resistance:

```json
"pump": { "target": "flow", "pressure": 9, "flow": -1 }
```

#### 4. Shorten Pre-infusion

If over-saturating, reduce pre-infusion:

```json
{
  "name": "Pre-infusion",
  "duration": 3,
  "pump": { "target": "flow", "pressure": 9, "flow": 2.5 }
}
```

---

## Issue: Sour AND Bitter (Unbalanced)

**Symptoms:**
- Both sour and bitter notes
- Harsh acidity with dry finish
- Unpleasant complexity
- Channeling likely

### Profile Fixes

This usually indicates channeling (under-extracted channels + over-extracted areas).

#### 1. Extend Pre-infusion Significantly

```json
{
  "name": "Pre-infusion",
  "duration": 8,
  "pump": { "target": "flow", "pressure": 9, "flow": 2 }
}
```

#### 2. Add Bloom Phase

```json
{
  "name": "Bloom",
  "phase": "preinfusion",
  "duration": 6,
  "pump": { "target": "power", "pressure": 0, "flow": 0 }
}
```

#### 3. Use Lower Initial Flow

```json
"pump": { "target": "flow", "pressure": 6, "flow": 2 }
```

#### 4. Lower Extraction Pressure

Use 7-8 bar instead of 9 bar:

```json
"pump": { "target": "pressure", "pressure": 7.5, "flow": 3.5 }
```

---

## Quick Diagnosis Chart

| Symptom | Likely Cause | Profile Fix |
|---------|--------------|-------------|
| Sour only | Under-extraction | ↑ Temp, ↑ Time, ↑ Pressure |
| Bitter only | Over-extraction | ↓ Temp, ↓ Time, Add decline |
| Sour + Bitter | Channeling | Longer pre-infusion, Add bloom |
| Fast shot | Channeling | ↑ Pre-infusion, ↓ Flow rate |
| Slow shot | Too fine/over-dose | ↓ Pressure, Use flow mode |
| Inconsistent | Variable extraction | Add volumetric, Use adaptive |
| Pump strain | Too much resistance | Flow mode start, Ease-in ramp |

---

## Temperature Adjustment Strategy

| Taste Issue | Direction | Amount |
|-------------|-----------|--------|
| Too sour/acidic | Increase | +1-2°C |
| Too bitter/harsh | Decrease | -1-2°C |
| Flat/lifeless | Increase | +1-2°C |
| Astringent/dry | Decrease | -1-2°C |

**General rule:** Adjust in 1°C increments and taste the difference before adjusting further.

---

## Profile Adjustment Checklist

When troubleshooting, adjust ONE thing at a time:

1. [ ] Temperature (±1-2°C)
2. [ ] Pre-infusion duration (±2-3s)
3. [ ] Pre-infusion flow rate (±0.5 ml/s)
4. [ ] Add/remove bloom phase
5. [ ] Hold pressure (±0.5-1 bar)
6. [ ] Volumetric target (±2-4g)
7. [ ] Add/adjust decline phase
8. [ ] Transition types and durations

Document changes and taste results to build understanding of your setup.
