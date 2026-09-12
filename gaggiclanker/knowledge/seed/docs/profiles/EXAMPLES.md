# Complete Profile Examples

Working examples of Gaggimate profiles for different coffee types and extraction styles.

---

## Example 1: Classic 9-Bar Profile

Simple, reliable extraction for medium roasts.

**Best for:** Medium roasts, balanced coffees, everyday espresso

```json
{
  "label": "Classic 9-Bar",
  "type": "pro",
  "description": "Standard 9-bar extraction with pre-infusion",
  "temperature": 93,
  "phases": [
    {
      "name": "Pre-infusion",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 4,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "flow",
        "pressure": 9,
        "flow": 3
      }
    },
    {
      "name": "Ramp",
      "phase": "brew",
      "valve": 1,
      "duration": 4,
      "temperature": 0,
      "transition": {
        "type": "linear",
        "duration": 3,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 9,
        "flow": 0
      },
      "targets": [
        {
          "type": "pressure",
          "operator": "gte",
          "value": 8.5
        }
      ]
    },
    {
      "name": "Hold",
      "phase": "brew",
      "valve": 1,
      "duration": 25,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 9,
        "flow": 4
      },
      "targets": [
        {
          "type": "volumetric",
          "operator": "gte",
          "value": 36
        }
      ]
    }
  ]
}
```

**Phase breakdown:**
1. **Pre-infusion (4s)**: Gentle wetting at 3 ml/s flow to saturate the puck
2. **Ramp (up to 4s)**: Linear ramp to 9 bar, exits early when 8.5 bar reached
3. **Hold (up to 25s)**: Maintain 9 bar until 36g output

---

## Example 2: Blooming Profile

Enhanced sweetness with soak phase. Great for fruit-forward coffees.

**Best for:** Light roasts, Ethiopian/Kenyan origins, fruit-forward coffees

```json
{
  "label": "Bloom Profile",
  "type": "pro",
  "description": "Gentle bloom for fruit-forward coffees",
  "temperature": 94,
  "phases": [
    {
      "name": "Fill",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 5,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "flow",
        "pressure": 9,
        "flow": 2.5
      }
    },
    {
      "name": "Bloom",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 8,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "power",
        "pressure": 0,
        "flow": 0
      }
    },
    {
      "name": "Ramp",
      "phase": "brew",
      "valve": 1,
      "duration": 5,
      "temperature": 0,
      "transition": {
        "type": "ease-in-out",
        "duration": 4,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 9,
        "flow": 0
      },
      "targets": [
        {
          "type": "pressure",
          "operator": "gte",
          "value": 8.5
        }
      ]
    },
    {
      "name": "Hold",
      "phase": "brew",
      "valve": 1,
      "duration": 20,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 9,
        "flow": 4
      },
      "targets": [
        {
          "type": "volumetric",
          "operator": "gte",
          "value": 36
        }
      ]
    },
    {
      "name": "Taper",
      "phase": "decline",
      "valve": 1,
      "duration": 6,
      "temperature": 0,
      "transition": {
        "type": "linear",
        "duration": 5,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 5,
        "flow": 0
      }
    }
  ]
}
```

**Phase breakdown:**
1. **Fill (5s)**: Gentle wetting at 2.5 ml/s
2. **Bloom (8s)**: Pump off - allows CO2 to escape, enhances sweetness
3. **Ramp (up to 5s)**: Smooth ease-in-out transition to 9 bar
4. **Hold (up to 20s)**: Main extraction at 9 bar
5. **Taper (6s)**: Gentle decline to 5 bar for smooth finish

---

## Example 3: Spring Lever Simulation

Declining pressure profile mimicking manual lever machines like the La Pavoni or Cremina.

**Best for:** Any roast, creates smooth body with less bitterness

```json
{
  "label": "Cremina Lever",
  "type": "pro",
  "description": "Spring lever-style declining pressure",
  "temperature": 90,
  "phases": [
    {
      "name": "Pre-infusion",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 5,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "flow",
        "pressure": 9,
        "flow": 3
      }
    },
    {
      "name": "Peak Pressure",
      "phase": "brew",
      "valve": 1,
      "duration": 5,
      "temperature": 0,
      "transition": {
        "type": "linear",
        "duration": 3,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 9,
        "flow": 0
      },
      "targets": [
        {
          "type": "pressure",
          "operator": "gte",
          "value": 8.5
        }
      ]
    },
    {
      "name": "Decline",
      "phase": "brew",
      "valve": 1,
      "duration": 30,
      "temperature": 0,
      "transition": {
        "type": "linear",
        "duration": 25,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 3,
        "flow": 0
      },
      "targets": [
        {
          "type": "volumetric",
          "operator": "gte",
          "value": 36
        }
      ]
    }
  ]
}
```

**Phase breakdown:**
1. **Pre-infusion (5s)**: Standard wetting at 3 ml/s
2. **Peak Pressure (up to 5s)**: Ramp to 9 bar peak
3. **Decline (up to 30s)**: 25-second linear decline from 9 to 3 bar, mimicking spring lever

---

## Example 4: Dark Roast Low Pressure

Gentler extraction for dark roasts to avoid bitterness and over-extraction.

**Best for:** Dark roasts, Italian/French roasts, reducing bitterness

```json
{
  "label": "Dark Roast Gentle",
  "type": "pro",
  "description": "Low pressure extraction for dark roasts",
  "temperature": 89,
  "phases": [
    {
      "name": "Pre-infusion",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 4,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "flow",
        "pressure": 8,
        "flow": 2.5
      }
    },
    {
      "name": "Ramp",
      "phase": "brew",
      "valve": 1,
      "duration": 4,
      "temperature": 0,
      "transition": {
        "type": "ease-in",
        "duration": 3,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 7,
        "flow": 0
      },
      "targets": [
        {
          "type": "pressure",
          "operator": "gte",
          "value": 6.5
        }
      ]
    },
    {
      "name": "Hold",
      "phase": "brew",
      "valve": 1,
      "duration": 25,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 7,
        "flow": 3.5
      },
      "targets": [
        {
          "type": "volumetric",
          "operator": "gte",
          "value": 32
        }
      ]
    },
    {
      "name": "Taper",
      "phase": "decline",
      "valve": 1,
      "duration": 5,
      "temperature": 0,
      "transition": {
        "type": "ease-out",
        "duration": 4,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 4,
        "flow": 0
      }
    }
  ]
}
```

**Key differences from standard:**
- Lower temperature (89°C vs 93°C)
- Lower pressure (7 bar vs 9 bar)
- Shorter ratio (1:1.8 instead of 1:2)
- Gentler flow rates

---

## Example 5: Turbo Shot

Fast, high-extraction shot for light roasts. Higher flow, shorter time.

**Best for:** Very light roasts, when you want bright acidity

```json
{
  "label": "Turbo Shot",
  "type": "pro",
  "description": "Fast extraction for light roasts",
  "temperature": 96,
  "phases": [
    {
      "name": "Pre-infusion",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 3,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "flow",
        "pressure": 9,
        "flow": 4
      }
    },
    {
      "name": "Extraction",
      "phase": "brew",
      "valve": 1,
      "duration": 20,
      "temperature": 0,
      "transition": {
        "type": "linear",
        "duration": 2,
        "adaptive": true
      },
      "pump": {
        "target": "flow",
        "pressure": 9,
        "flow": -1
      },
      "targets": [
        {
          "type": "volumetric",
          "operator": "gte",
          "value": 45
        }
      ]
    }
  ]
}
```

**Key features:**
- Very high temperature (96°C)
- Short pre-infusion
- Adaptive flow for main extraction
- Longer ratio (1:2.5)
- Total shot time: 15-20 seconds

---

## Example 6: Allongé / Long Shot

Extended extraction for a lighter, longer drink.

**Best for:** Medium roasts, when you want more volume, milk drinks

```json
{
  "label": "Allongé",
  "type": "pro",
  "description": "Long extraction for extended drinks",
  "temperature": 92,
  "phases": [
    {
      "name": "Pre-infusion",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 5,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "flow",
        "pressure": 9,
        "flow": 3
      }
    },
    {
      "name": "Ramp",
      "phase": "brew",
      "valve": 1,
      "duration": 5,
      "temperature": 0,
      "transition": {
        "type": "linear",
        "duration": 4,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 8,
        "flow": 0
      },
      "targets": [
        {
          "type": "pressure",
          "operator": "gte",
          "value": 7.5
        }
      ]
    },
    {
      "name": "Hold",
      "phase": "brew",
      "valve": 1,
      "duration": 35,
      "temperature": 0,
      "transition": {
        "type": "instant",
        "duration": 0,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 8,
        "flow": 5
      },
      "targets": [
        {
          "type": "volumetric",
          "operator": "gte",
          "value": 54
        }
      ]
    },
    {
      "name": "Finish",
      "phase": "decline",
      "valve": 1,
      "duration": 8,
      "temperature": 0,
      "transition": {
        "type": "ease-out",
        "duration": 6,
        "adaptive": true
      },
      "pump": {
        "target": "pressure",
        "pressure": 4,
        "flow": 0
      }
    }
  ]
}
```

**Key features:**
- 1:3 ratio (18g in, 54g out)
- Slightly lower pressure (8 bar)
- Longer extraction time
- Extended decline phase

---

## Advanced: Multi-Stage Extraction

Complex flavor profile with 7 phases for maximum control.

```json
{
  "label": "Multi-Stage Complex",
  "type": "pro",
  "description": "7-phase extraction for complex flavor development",
  "temperature": 93,
  "phases": [
    {
      "name": "Fast Fill",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 2,
      "temperature": 0,
      "transition": { "type": "instant", "duration": 0, "adaptive": true },
      "pump": { "target": "flow", "pressure": 9, "flow": 4 }
    },
    {
      "name": "Bloom",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 6,
      "temperature": 0,
      "transition": { "type": "instant", "duration": 0, "adaptive": true },
      "pump": { "target": "power", "pressure": 0, "flow": 0 }
    },
    {
      "name": "Ramp",
      "phase": "brew",
      "valve": 1,
      "duration": 4,
      "temperature": 0,
      "transition": { "type": "ease-in", "duration": 4, "adaptive": true },
      "pump": { "target": "pressure", "pressure": 9, "flow": 0 },
      "targets": [{ "type": "pressure", "operator": "gte", "value": 8.5 }]
    },
    {
      "name": "High Pressure",
      "phase": "brew",
      "valve": 1,
      "duration": 8,
      "temperature": 0,
      "transition": { "type": "instant", "duration": 0, "adaptive": true },
      "pump": { "target": "pressure", "pressure": 9, "flow": 4 }
    },
    {
      "name": "Medium Pressure",
      "phase": "brew",
      "valve": 1,
      "duration": 12,
      "temperature": 0,
      "transition": { "type": "linear", "duration": 3, "adaptive": true },
      "pump": { "target": "pressure", "pressure": 7, "flow": 0 }
    },
    {
      "name": "Low Pressure",
      "phase": "brew",
      "valve": 1,
      "duration": 8,
      "temperature": 0,
      "transition": { "type": "linear", "duration": 3, "adaptive": true },
      "pump": { "target": "pressure", "pressure": 5, "flow": 0 },
      "targets": [{ "type": "volumetric", "operator": "gte", "value": 36 }]
    },
    {
      "name": "Taper",
      "phase": "decline",
      "valve": 1,
      "duration": 4,
      "temperature": 0,
      "transition": { "type": "ease-out", "duration": 4, "adaptive": true },
      "pump": { "target": "pressure", "pressure": 3, "flow": 0 }
    }
  ]
}
```

**Phase purposes:**
1. **Fast Fill**: Quick saturation
2. **Bloom**: CO2 release, sweetness enhancement
3. **Ramp**: Gentle pressure build
4. **High Pressure (9 bar)**: Extract body and oils
5. **Medium Pressure (7 bar)**: Extract sweetness
6. **Low Pressure (5 bar)**: Extract acidity
7. **Taper**: Smooth finish

---

## Flow-Based Variable Pressure (Automatic Pro) by modsmthng_57901 on Gaggimate Discord

Self-regulating profile that adapts to grind size automatically. Uses flow control with pressure ceiling.

**Best for:** All roasts, beginners, when you want consistency without constant adjustment

**Key technique:** Target a flow rate with a pressure limit—pressure becomes variable based on puck resistance.

```json
{
  "label": "Automatic Pro v2 18g",
  "type": "pro",
  "description": "Flow-based variable pressure - adapts to grind automatically",
  "temperature": 91,
  "phases": [
    {
      "name": "Pre-Infusion",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 10,
      "temperature": 0,
      "transition": { "type": "instant", "duration": 0, "adaptive": true },
      "pump": {
        "target": "flow",
        "pressure": 2,
        "flow": 20
      },
      "targets": [
        { "type": "volumetric", "operator": "gte", "value": 1 },
        { "type": "pumped", "operator": "gte", "value": 31 }
      ]
    },
    {
      "name": "Bloom",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 6,
      "temperature": 0,
      "transition": { "type": "instant", "duration": 0, "adaptive": true },
      "pump": {
        "target": "flow",
        "pressure": 2,
        "flow": 1.8
      },
      "targets": [
        { "type": "volumetric", "operator": "gte", "value": 1.5 }
      ]
    },
    {
      "name": "Ramp",
      "phase": "brew",
      "valve": 1,
      "duration": 6,
      "temperature": 0,
      "transition": { "type": "instant", "duration": 0, "adaptive": true },
      "pump": {
        "target": "flow",
        "pressure": 12,
        "flow": 1.8
      },
      "targets": [
        { "type": "volumetric", "operator": "gte", "value": 11 }
      ]
    },
    {
      "name": "Brew",
      "phase": "brew",
      "valve": 1,
      "duration": 120,
      "temperature": 0,
      "transition": { "type": "instant", "duration": 0, "adaptive": true },
      "pump": {
        "target": "flow",
        "pressure": 9,
        "flow": 1.8
      },
      "targets": [
        { "type": "volumetric", "operator": "gte", "value": 36 }
      ]
    }
  ]
}
```

**How it works:**
1. **Pre-Infusion**: Fast fill (20 g/s) at low pressure (2 bar) until puck saturated
2. **Bloom**: Maintain brew flow (1.8 g/s) at 2 bar ceiling—pauses if puck already pressurized
3. **Ramp**: Same flow, but 12 bar ceiling allows pressure to build through resistance
4. **Brew**: Flow-controlled at 1.8 g/s with 9 bar ceiling—prevents over-extraction

**Advantages:**
- **Grind Tolerance**: Forgiving of slight grind inconsistencies
- **Channeling Prevention**: Low initial pressure reduces channeling
- **Flavor Balance**: 9 bar ceiling prevents bitterness
- **Second Blooming**: Fine grinds may cause brief pause at phase transition—enhances chocolatey/nutty notes

**Scaling to other doses** (see [FLOW_VARIABLE_PRESSURE.md](./FLOW_VARIABLE_PRESSURE.md)):
- Flow = `Dose × 2 / 20s` (9g→0.9, 16g→1.6, 18g→1.8, 22g→2.2)
- Pre-infusion water = `Dose × 1.3 + 7.5ml`
- Ramp target = `Flow × 6s`

---

## Profile Comparison Summary

| Profile | Temperature | Max Pressure | Total Time | Best For |
|---------|-------------|--------------|------------|----------|
| Classic 9-Bar | 93°C | 9 bar | 25-35s | Medium roasts |
| Bloom Profile | 94°C | 9 bar | 35-45s | Light roasts |
| Lever Simulation | 90°C | 9→3 bar | 30-40s | Any roast |
| Dark Roast | 89°C | 7 bar | 25-35s | Dark roasts |
| Turbo Shot | 96°C | 9 bar | 15-20s | Light roasts |
| Allongé | 92°C | 8 bar | 35-50s | Long drinks |
| Multi-Stage | 93°C | 9→3 bar | 40-50s | Complex cups |
| **Automatic Pro** | 91°C | Variable (≤9) | 30-40s | All roasts, consistency |
