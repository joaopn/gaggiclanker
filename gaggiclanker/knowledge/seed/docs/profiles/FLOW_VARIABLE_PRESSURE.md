# Flow-Based Variable Pressure Profiles
Pioneered by modsmthng_57901 on Gaggimate Discord
## Overview

Flow-based variable pressure is an advanced profiling technique where you control **flow rate** as the primary parameter while setting a **pressure ceiling**. This creates self-regulating extraction that adapts to puck resistance automatically.

## The Core Technique

```json
"pump": {
  "target": "flow",      // Primary: maintain this flow rate
  "pressure": 9,         // Secondary: never exceed this pressure  
  "flow": 1.8            // Target flow in g/s
}
```

### How It Works

1. The pump attempts to maintain the target flow rate (e.g., 1.8 g/s)
2. As water flows through the puck, pressure builds based on resistance
3. If pressure reaches the ceiling (e.g., 9 bar), flow may drop below target
4. If resistance is low, pressure stays low and flow is maintained

**Result**: The profile automatically adapts to your grind size without manual pressure adjustments.

---

## Advantages

| Benefit | Explanation |
|---------|-------------|
| **Grind Tolerance** | Forgiving of slight grind inconsistencies |
| **Channeling Prevention** | Lower initial pressure reduces channeling risk |
| **Flavor Balance** | Pressure ceiling prevents over-extraction bitterness |
| **Consistency** | Same profile works across different coffees |
| **Adaptability** | No need to re-dial for every bean |

### The "Second Blooming" Effect

When ground very fine, the transition from a high-pressure ramp phase (12 bar) to a lower extraction ceiling (9 bar) can cause flow to briefly pause. This is **intentional** and often produces rich, chocolatey profiles rather than bitterness.

**Best for**: Chocolatey/nutty roasts at 1:1.5 ratio

---

## Dose Scaling Formulas

When adapting flow-based profiles to different doses:

### Flow Rate
```
Flow = Dose × 2 / 20 seconds
```
| Dose | Flow Rate |
|------|-----------|
| 9g | 0.9 g/s |
| 16g | 1.6 g/s |
| 18g | 1.8 g/s |
| 20g | 2.0 g/s |
| 22g | 2.2 g/s |

### Pre-Infusion Water Volume
```
Water = Dose × 1.3 + Headspace (typically 7.5ml)
```
| Dose | Water Pumped |
|------|--------------|
| 9g | ~19ml |
| 16g | ~28ml |
| 18g | ~31ml |
| 20g | ~34ml |
| 22g | ~36ml |

### Ramp Phase Target Weight
```
Ramp Target = Flow × Phase Duration (typically 6s)
```
| Dose | Ramp Target |
|------|-------------|
| 9g | ~5g |
| 16g | ~10g |
| 18g | ~11g |
| 20g | ~12g |
| 22g | ~13g |

---

## Profile Structure

### Phase 1: Pre-Infusion
- **Goal**: Fill headspace, moisten puck without pressure
- **Flow**: High (20 g/s) for fast fill
- **Pressure Limit**: 2 bar (prevents premature extraction)
- **Stop Triggers**: 
  - `volumetric >= 1g` (first drip in cup)
  - `pumped >= calculated volume`

```json
{
  "name": "Pre-Infusion",
  "pump": { "target": "flow", "pressure": 2, "flow": 20 },
  "targets": [
    { "type": "volumetric", "operator": "gte", "value": 1 },
    { "type": "pumped", "operator": "gte", "value": 31 }
  ]
}
```

### Phase 2: Bloom
- **Goal**: Saturate puck, prevent channeling
- **Flow**: Match main brew flow (1.8 g/s for 18g)
- **Pressure Limit**: 2 bar (pauses pump if saturated)
- **Stop Trigger**: `volumetric >= 1.5g`

```json
{
  "name": "Bloom",
  "pump": { "target": "flow", "pressure": 2, "flow": 1.8 },
  "targets": [
    { "type": "volumetric", "operator": "gte", "value": 1.5 }
  ]
}
```

### Phase 3: Ramp-Up
- **Goal**: Build pressure smoothly
- **Flow**: Same as brew flow
- **Pressure Limit**: 12 bar (accommodates fine grinds)
- **Duration**: 6 seconds max
- **Stop Trigger**: `volumetric >= Flow × 6s`

```json
{
  "name": "Ramp",
  "pump": { "target": "flow", "pressure": 12, "flow": 1.8 },
  "targets": [
    { "type": "volumetric", "operator": "gte", "value": 11 }
  ]
}
```

### Phase 4: Main Extraction
- **Goal**: Extract at constant flow with pressure ceiling
- **Flow**: Calculated for dose (1.8 g/s for 18g)
- **Pressure Limit**: 9 bar (prevents bitterness)
- **Stop Trigger**: Target weight

```json
{
  "name": "Brew",
  "pump": { "target": "flow", "pressure": 9, "flow": 1.8 },
  "targets": [
    { "type": "volumetric", "operator": "gte", "value": 36 }
  ]
}
```

---

## Advanced: Declining Flow

For enhanced sweetness, use a declining flow during extraction:

```json
{
  "name": "Main Extraction",
  "transition": {
    "type": "linear",
    "duration": 40,
    "adaptive": true
  },
  "pump": {
    "target": "flow",
    "pressure": 9,
    "flow": 1.2
  }
}
```

This creates a **40-second linear transition** from the previous flow (e.g., 1.8 g/s) down to 1.2 g/s.

**Benefits**:
- Mimics lever machine behavior
- Produces sweeter, more balanced shots
- Reduces late-extraction harshness

---

## Comparison: v2 vs v3 Approach

| Aspect | v2 (Simple) | v3 (Advanced) |
|--------|-------------|---------------|
| Phases | 4 | 5 (+ Initialization) |
| Pre-Infusion | 2 bar, fast fill | 1 bar, gentler |
| Ramp | Flow-based | Pressure-based |
| Extraction | Constant flow | Declining flow |
| Complexity | Lower | Higher |

---

## Quick Reference

### What Stays Constant Across Doses
- Temperature (91°C default)
- Phase durations (10s, 6s, 6s, 120s)
- Pressure limits (2 → 2 → 12 → 9 bar)
- Bloom stop trigger (1.5g in cup)

### What Scales With Dose
- Flow rate
- Pre-infusion water volume
- Ramp target weight
- Final yield

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| Low pressure throughout | Grind too coarse | Grind finer |
| Shot stalls | Grind too fine | Grind coarser or extend Phase 2 |
| Channeling | Uneven saturation | Increase pre-infusion time |
| Bitter/harsh | Over-extraction | Lower ratio (1:1.5) or lower temp |
| Sour/thin | Under-extraction | Higher ratio (1:2.5+) or higher temp |

---

## Related Resources

- [Automatic Pro Profile Guide](../../knowledge/automatic-pro/AUTOMATIC_PRO_PROFILE_GUIDE.md)
- [How the Automatic Pro Profile works](../../knowledge/automatic-pro/How%20the%20Automatic%20Pro%20Profile%20works.md)
- [Profile Creation Guide](../../knowledge/GAGGIMATE_PROFILE_CREATION_GUIDE.md)
