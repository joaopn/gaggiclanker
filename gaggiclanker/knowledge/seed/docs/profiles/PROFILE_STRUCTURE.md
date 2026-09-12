# Gaggimate Profile Structure Reference

Complete JSON schema and field reference for Gaggimate espresso profiles.

## Profile Versions

- **Gaggimate Standard**: Basic temperature control, volumetric dosing, timed phases
- **Gaggimate Pro**: Advanced pressure/flow profiling, real-time pressure monitoring, complex transitions, multiple stop conditions

---

## JSON Profile Structure

### Top-Level Properties

```json
{
  "label": "Profile Name",
  "type": "pro",
  "description": "Optional description of the profile",
  "temperature": 93,
  "phases": [...]
}
```

### Top-Level Fields

| Field | Type | Required | Description | Valid Values |
|-------|------|----------|-------------|--------------|
| `label` | string | Yes | Display name shown on machine | Any string |
| `type` | string | Yes | Profile complexity level | `"simple"` or `"pro"` |
| `description` | string | No | Optional notes about the profile | Any string |
| `temperature` | number | Yes | Global target temperature in °C | 70-100 (typical: 88-96) |
| `phases` | array | Yes | Ordered list of extraction phases | Array of phase objects |

---

## Phase Structure

Each phase represents a distinct stage in the extraction process.

### Phase Object Schema

```json
{
  "name": "Phase Name",
  "phase": "brew",
  "valve": 1,
  "duration": 25,
  "temperature": 0,
  "transition": {...},
  "pump": {...},
  "targets": [...]
}
```

### Phase Fields Reference

| Field | Type | Required | Description | Valid Values |
|-------|------|----------|-------------|--------------|
| `name` | string | Yes | Display name shown during brew | Any string (e.g., "Pre-infusion", "Ramp", "Hold") |
| `phase` | string | Yes | Phase category for display | `"preinfusion"`, `"brew"`, `"decline"` |
| `valve` | number | Yes | Three-way valve position | `0` = closed, `1` = open |
| `duration` | number | Yes | Maximum phase duration in seconds | 1-60 (typical: 3-30) |
| `temperature` | number | No | Override global temp (0 = use global) | 0 or 70-100 |
| `transition` | object | Yes (Pro) | How to transition to target values | See PUMP_AND_TRANSITIONS.md |
| `pump` | object | Yes | Pump control configuration | See PUMP_AND_TRANSITIONS.md |
| `targets` | array | No | Stop conditions (exits phase early) | See STOP_CONDITIONS.md |

### Phase Type Guidelines

#### `preinfusion`
- Low-flow wetting phase
- Typically 2-8 seconds
- Use flow mode at 2-3 ml/s
- Helps saturate the puck evenly before pressure builds

#### `brew`
- Main extraction phase
- Typically 15-30 seconds
- Use pressure mode at target pressure (usually 7-9 bar)
- Where most extraction happens

#### `decline`
- Pressure taper/finish phase (optional)
- Typically 3-6 seconds
- Gradually reduces pressure for smoother finish
- Helps reduce astringency in the cup

---

## Complete Phase Example

```json
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
```

---

## Valve Configuration

The `valve` field controls the three-way solenoid valve:

| Value | Position | Use Case |
|-------|----------|----------|
| `1` | Open | **All normal brewing — use this on every phase.** Water flows through the puck to the cup. |
| `0` | Closed | Almost never. Seals water in the group head so the shot **cannot pour** — not a brewing setting. |

**Important — always use `valve: 1` on every phase, including bloom / pre-infusion / soak phases.** A bloom or soak is created by turning the **pump off** during that phase (`pump`: `target` `"power"`, `pressure` `0`, `flow` `0` — the documented pump-off form in `PUMP_AND_TRANSITIONS.md` and every `EXAMPLES.md` bloom) — **not** by closing the valve. The valve stays open (`valve: 1`) for the entire shot. Every working reference profile in `EXAMPLES.md`, including the dedicated bloom templates, uses `valve: 1` on every phase. Setting `valve: 0` on a brew phase traps water in the group head, so the shot never pours and a volumetric stop target never triggers — a common and silent failure.

---

## Temperature Override

The phase-level `temperature` field allows overriding the global temperature:

- `0` = Use the profile's global temperature (most common)
- `70-100` = Override with specific temperature for this phase

**Note**: Per-phase temperature profiling requires hardware support. Most users should leave this at `0` and rely on the global temperature setting.

---

## Field Validation Rules

### Required Fields Checklist

Every phase MUST have:
- [ ] `name` - String, any value
- [ ] `phase` - Must be `"preinfusion"`, `"brew"`, or `"decline"`
- [ ] `valve` - Must be `0` or `1`
- [ ] `duration` - Number between 1-60
- [ ] `pump` - Object with `target`, `pressure`, `flow`
- [ ] `transition` - Object with `type`, `duration`, `adaptive` (Pro profiles)

### Common Validation Errors

1. **Missing `transition` object** - Required for Pro profiles
2. **Invalid `phase` value** - Must be one of the three valid strings
3. **Duration out of range** - Must be 1-60 seconds
4. **Missing pump fields** - All three fields required even if 0
