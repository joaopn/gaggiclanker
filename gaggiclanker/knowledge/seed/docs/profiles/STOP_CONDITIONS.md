# Stop Conditions (Targets) Reference

Complete documentation for phase stop conditions in Gaggimate profiles.

---

## Overview

Stop conditions (targets) allow a phase to exit early when a condition is met. Without targets, phases run for their full `duration`.

**Key behavior:** Phases can have multiple targets. The phase exits when **ANY** condition is met (OR logic).

---

## Target Object Structure

```json
"targets": [
  {
    "type": "volumetric",
    "operator": "gte",
    "value": 36
  }
]
```

### Target Fields

| Field | Type | Required | Description | Valid Values |
|-------|------|----------|-------------|--------------|
| `type` | string | Yes | Measurement type | `"volumetric"`, `"water_pumped"`, `"pressure"`, `"flow"` |
| `operator` | string | Yes | Comparison operator | `"gte"` (≥), `"lte"` (≤), `"gt"` (>), `"lt"` (<) |
| `value` | number | Yes | Threshold value | Depends on type |

---

## Target Types

### 1. Volumetric (`"volumetric"`)

Exit when scale weight reaches target.

- **Requires Bluetooth scale** (or estimates based on pressure/flow)
- Most common for final shot weight
- Value in **grams**

**Use cases:**
- Final phase stop condition (most important!)
- Achieving consistent shot weight
- Ratio-based brewing (e.g., 18g in → 36g out for 1:2)

```json
"targets": [
  {
    "type": "volumetric",
    "operator": "gte",
    "value": 36
  }
]
```

**Common values:**

| Ratio | Dose In | Target Weight |
|-------|---------|---------------|
| 1:1 (Ristretto) | 18g | 18 |
| 1:1.5 (Short) | 18g | 27 |
| 1:2 (Classic) | 18g | 36 |
| 1:2.5 (Lungo) | 18g | 45 |
| 1:3 (Allongé) | 18g | 54 |

---

### 2. Water Pumped (`"water_pumped"`)

Exit when X ml of water has been pumped.

- Independent of scale
- Useful for pre-infusion timing
- Value in **milliliters**

**Use cases:**
- Pre-infusion phase (pump specific amount)
- When scale is not available
- Consistent water dosing regardless of output

```json
"targets": [
  {
    "type": "water_pumped",
    "operator": "gte",
    "value": 40
  }
]
```

**Typical values:**

| Phase | Water Amount | Purpose |
|-------|--------------|---------|
| Fast fill | 5-10 ml | Quick initial wetting |
| Pre-infusion | 10-20 ml | Saturate puck |
| Full extraction | 40-60 ml | Complete shot (no scale) |

---

### 3. Pressure (`"pressure"`)

Exit when pressure crosses a threshold.

- Value in **bars**
- Can use `gte` (above) or `lte` (below)

#### Pressure Above (`"gte"`)

Exit when pressure exceeds threshold.

**Use cases:**
- Ramp phase completion (ensure pressure build is complete)
- Moving to next phase once target pressure reached

```json
"targets": [
  {
    "type": "pressure",
    "operator": "gte",
    "value": 8.5
  }
]
```

#### Pressure Below (`"lte"`)

Exit when pressure drops below threshold.

**Use cases:**
- Spring lever simulation (end when pressure naturally declines)
- Detecting puck degradation
- Safety cutoff

```json
"targets": [
  {
    "type": "pressure",
    "operator": "lte",
    "value": 3
  }
]
```

---

### 4. Flow (`"flow"`)

Exit when flow rate crosses a threshold.

- Value in **ml/s**
- Can use `gte` (above) or `lte` (below)

#### Flow Above (`"gte"`)

Exit when flow exceeds threshold.

**Use cases:**
- Detecting channeling (sudden flow increase)
- Moving to next phase once flow established

```json
"targets": [
  {
    "type": "flow",
    "operator": "gte",
    "value": 4
  }
]
```

#### Flow Below (`"lte"`)

Exit when flow drops below threshold.

**Use cases:**
- Detecting puck choking
- End of extraction detection

```json
"targets": [
  {
    "type": "flow",
    "operator": "lte",
    "value": 1
  }
]
```

---

## Multiple Stop Conditions

Phases can have multiple targets. The phase exits when **ANY** condition is met (OR logic).

**Example: Exit on weight OR low pressure**
```json
"targets": [
  {
    "type": "volumetric",
    "operator": "gte",
    "value": 36
  },
  {
    "type": "pressure",
    "operator": "lte",
    "value": 2
  }
]
```

This will stop the phase when:
- Weight reaches 36g, **OR**
- Pressure drops below 2 bar (whichever comes first)

---

## Operator Reference

| Operator | Symbol | Meaning | Example |
|----------|--------|---------|---------|
| `gte` | ≥ | Greater than or equal to | Stop when weight ≥ 36g |
| `lte` | ≤ | Less than or equal to | Stop when pressure ≤ 3 bar |
| `gt` | > | Greater than | Stop when flow > 5 ml/s |
| `lt` | < | Less than | Stop when flow < 1 ml/s |

---

## Common Target Patterns

### Standard Extraction Stop (with scale)
```json
"targets": [
  { "type": "volumetric", "operator": "gte", "value": 36 }
]
```

### Standard Extraction Stop (no scale)
```json
"targets": [
  { "type": "water_pumped", "operator": "gte", "value": 50 }
]
```

### Ramp Phase Completion
```json
"targets": [
  { "type": "pressure", "operator": "gte", "value": 8.5 }
]
```

### Lever Simulation (pressure decline)
```json
"targets": [
  { "type": "volumetric", "operator": "gte", "value": 36 },
  { "type": "pressure", "operator": "lte", "value": 2 }
]
```

### Pre-infusion Complete
```json
"targets": [
  { "type": "water_pumped", "operator": "gte", "value": 15 },
  { "type": "pressure", "operator": "gte", "value": 4 }
]
```

---

## Volumetric Estimation (No Scale)

Without a Bluetooth scale, Gaggimate estimates volume using:
- Pressure curve analysis
- Flow rate integration
- Time-based modeling

### Calibration Process

1. **First shot**: Let profile run with volumetric target
2. **Weigh actual output** on regular scale
3. **Adjust profile's volumetric value**:
   - If 30g target → 35g actual: Change to 27g
   - If 30g target → 25g actual: Change to 33g
4. **Re-test and fine-tune**

After 2-3 shots, estimation typically becomes accurate within ±2g.

### Calibration Formula

```
New Target = Current Target × (Desired Output / Actual Output)
```

**Example:**
- Target: 36g
- Actual: 40g
- New Target: 36 × (36 / 40) = 32.4g ≈ 32g

---

## Best Practices

### Always Add Volumetric Stop

On the final extraction phase, always include a volumetric target:
```json
"targets": [
  { "type": "volumetric", "operator": "gte", "value": 36 }
]
```

This ensures consistent shot weight regardless of extraction time variations.

### Use Pressure Targets for Ramps

On ramp phases, add a pressure target to move to the next phase once pressure is reached:
```json
"targets": [
  { "type": "pressure", "operator": "gte", "value": 8.5 }
]
```

### Combine Targets for Safety

For long-running phases, combine targets to prevent over-extraction:
```json
"targets": [
  { "type": "volumetric", "operator": "gte", "value": 36 },
  { "type": "pressure", "operator": "lte", "value": 2 }
]
```

### Duration as Fallback

Remember that `duration` acts as the maximum time if no target is met. Set it appropriately:
- Pre-infusion: 5-10s max
- Ramp: 5-8s max  
- Hold/extraction: 30-40s max (target should trigger first)
- Decline: 10-15s max
