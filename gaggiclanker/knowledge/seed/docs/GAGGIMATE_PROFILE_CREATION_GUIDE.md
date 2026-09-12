# Gaggimate Profile Creation Guide

This file provides an overview and quick-start example for creating Gaggimate profiles. Detailed schema documentation, pump modes, transitions, stop conditions, and troubleshooting are maintained in separate reference files under `knowledge/profiles/` — see the Reference Documentation table below for links to each topic.

## Overview

Gaggimate supports two profile types:
- **Gaggimate Standard**: Basic temperature control, volumetric dosing, timed phases
- **Gaggimate Pro**: Advanced pressure/flow profiling, real-time pressure monitoring, complex transitions, multiple stop conditions

---

## Quick Start

Here's a minimal valid Pro profile:

```json
{
  "label": "Simple 9-bar",
  "description": "Classic 9-bar extraction with flow-based pre-infusion. Good starting point for medium roasts.",
  "type": "pro",
  "temperature": 93,
  "phases": [
    {
      "name": "Pre-infusion",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 5,
      "pump": { "target": "flow", "pressure": 4, "flow": 2 },
      "transition": { "type": "instant", "duration": 0, "adaptive": true }
    },
    {
      "name": "Extraction",
      "phase": "brew",
      "valve": 1,
      "duration": 40,
      "pump": { "target": "pressure", "pressure": 9, "flow": 0 },
      "transition": { "type": "linear", "duration": 3, "adaptive": true },
      "targets": [{ "type": "volumetric", "operator": "gte", "value": 36 }]
    }
  ]
}
```

**What this profile does:**
- **Pre-infusion phase** (5s): Targets 2 ml/s flow with 4 bar pressure limit — gently saturates the puck
- **Extraction phase** (up to 40s): Ramps to 9 bar pressure over 3 seconds, stops at 36ml output
- `valve: 1` keeps the 3-way valve **open** (normal brewing — water flows through the puck to the cup). Use `valve: 1` on **every** phase, including bloom / pre-infusion — a bloom comes from the pump turning off, not from closing the valve. `adaptive: true` allows flow/pressure adjustment based on puck resistance

**Before creating or modifying profiles**, load [PROFILE_STRUCTURE.md](gaggimate://knowledge/profiles/PROFILE_STRUCTURE.md) for complete field definitions (top-level fields, phase fields, pump modes, valve states, etc.). The table below shows which reference file covers each topic.

For more examples, see [EXAMPLES.md](gaggimate://knowledge/profiles/EXAMPLES.md).

---

## Reference Documentation

Detailed schema and configuration guides:

| Topic | Reference File |
|-------|---------------|
| **JSON schema & field reference** | [PROFILE_STRUCTURE.md](gaggimate://knowledge/profiles/PROFILE_STRUCTURE.md) |
| **Pump modes & adaptive flow** | [PUMP_AND_TRANSITIONS.md](gaggimate://knowledge/profiles/PUMP_AND_TRANSITIONS.md) |
| **Transition types & curves** | [PUMP_AND_TRANSITIONS.md](gaggimate://knowledge/profiles/PUMP_AND_TRANSITIONS.md) |
| **Stop conditions (targets)** | [STOP_CONDITIONS.md](gaggimate://knowledge/profiles/STOP_CONDITIONS.md) |
| **Flow-based variable pressure** | [FLOW_VARIABLE_PRESSURE.md](gaggimate://knowledge/profiles/FLOW_VARIABLE_PRESSURE.md) |
| **Complete profile examples** | [EXAMPLES.md](gaggimate://knowledge/profiles/EXAMPLES.md) |
| **Troubleshooting profiles** | [TROUBLESHOOTING.md](gaggimate://knowledge/profiles/TROUBLESHOOTING.md) |
| **Quick reference / cheat sheet** | [QUICK_REFERENCE.md](gaggimate://knowledge/profiles/QUICK_REFERENCE.md) |

---

## Phase Naming for Shot Analysis

The `analyze_shot` tool classifies each phase by its **`name`** field (not the `phase` field) to provide phase-specific diagnostics. The shot log binary only stores the free-text `name`, so choosing recognisable names ensures accurate analysis.

### Recognised Keywords (substring matching, case-insensitive)

| Classification | Recognised Keywords | Example Names |
|----------------|---------------------|---------------|
| **preinfusion** | `preinfusion`, `pre-infusion`, `pi`, `soak`, `bloom`, `fill`, `preinfuse` | "Pre-infusion", "Gentle Soak", "Bloom" |
| **decline** | `decline`, `taper`, `ramp-down`, `ramp down`, `cool down`, `cooldown` | "Decline", "Smooth Taper", "Final Cooldown" |
| **brew** | _(everything else)_ | "Extraction", "Hold", "Main", "Ramp", "Step 2" |

### Telemetry Fallback

When the phase name contains **none** of the above keywords, the analyzer falls back to pressure telemetry heuristics:

- **First phase** with low average pressure (< 5 bar) and rising trend → classified as **preinfusion**
- **Last phase** (not the first) with declining pressure slope → classified as **decline**
- **All other unrecognised phases** → classified as **brew**

### Best Practices for Phase Names

1. **Include a keyword** from the table above so the analyzer classifies the phase correctly
2. **Preinfusion**: Use names containing "preinfusion", "pre-infusion", "soak", "bloom", or "fill"
3. **Decline/taper**: Use names containing "decline", "taper", or "cooldown"
4. **Brew phases**: Any name without a preinfusion/decline keyword is treated as brew
5. **Avoid ambiguity**: A name like "Ramp" is fine for brew; avoid it if you mean preinfusion

---

## File Management

### Exporting Profiles

1. Navigate to `http://gaggimate.local/profiles`
2. Click export icon on profile card
3. Save `.json` file

### Importing Profiles

1. Navigate to `http://gaggimate.local/profiles`
2. Click "Import" (top right)
3. Upload `.json` file
4. Click star icon to make available on machine

### Sharing Profiles

- Join Gaggimate Discord: https://discord.gg/APw7rgPGPf
- Share in #profiles channel
- Include coffee recommendations in description
- Test thoroughly before sharing

---

## Resources

- **Documentation**: https://docs.gaggimate.eu/
- **Discord Community**: https://discord.gg/APw7rgPGPf
- **Profile Library**: Discord #profiles channel
- **Web Interface**: http://gaggimate.local/profiles
- **GitHub Repository**: https://github.com/jniebuhr/gaggimate

Related knowledge files:
- [PROFILE_LIBRARY.md](gaggimate://knowledge/PROFILE_LIBRARY.md) — Ready-to-use profile templates
- [PRESSURE_GUIDE.md](gaggimate://knowledge/PRESSURE_GUIDE.md) — Pressure selection by roast and processing
- [ESPRESSO_BREWING_BASICS.md](gaggimate://knowledge/ESPRESSO_BREWING_BASICS.md) — Brewing fundamentals
