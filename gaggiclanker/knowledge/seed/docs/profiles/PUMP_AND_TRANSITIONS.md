# Pump Configuration and Transitions Reference

Complete documentation for pump control modes and transition types in Gaggimate profiles.

---

## Pump Configuration

The `pump` object controls how the pump operates during a phase.

### Pump Object Structure

```json
"pump": {
  "target": "pressure",
  "pressure": 9,
  "flow": 0
}
```

### Pump Fields

| Field | Type | Required | Description | Valid Values |
|-------|------|----------|-------------|--------------|
| `target` | string | Yes | Control mode | `"pressure"`, `"flow"`, `"power"`, `"off"` |
| `pressure` | number | Yes | Target/limit pressure in bars | 0-12 (typical: 6-9) |
| `flow` | number | Yes | Target/limit flow in ml/s | 0-10 (typical: 2-5), `-1` = adaptive |

---

## Pump Target Modes

### 1. Pressure Mode (`"pressure"`)

Controls pump to maintain a specific pressure.

- `pressure`: Target pressure in bars
- `flow`: Optional flow limit (0 = no limit)

**Use cases:**
- Main extraction phase (hold at 9 bar)
- Pressure profiling (different pressures per phase)
- Any phase where consistent pressure matters

```json
"pump": {
  "target": "pressure",
  "pressure": 9,
  "flow": 4
}
```

**With flow limit:**
```json
"pump": {
  "target": "pressure",
  "pressure": 9,
  "flow": 4
}
```
This targets 9 bar but limits flow to 4 ml/s maximum.

---

### 2. Flow Mode (`"flow"`)

Controls pump to maintain a specific flow rate.

- `flow`: Target flow in ml/s
- `pressure`: Optional pressure limit (prevents over-pressurization)

**Use cases:**
- Pre-infusion (gentle, consistent wetting)
- Flow profiling recipes
- When puck resistance varies

```json
"pump": {
  "target": "flow",
  "pressure": 9,
  "flow": 2.5
}
```

**Typical flow rates:**

| Flow Rate | Use Case | Expected Pressure |
|-----------|----------|-------------------|
| 1.5-2.5 ml/s | Very gentle pre-infusion | <3 bar |
| 2.5-4 ml/s | Standard pre-infusion | 3-6 bar |
| 4-5 ml/s | Main extraction (with flow limit) | 8-9 bar |

---

### 3. Power Mode (`"power"`)

Runs pump at fixed percentage (Standard version primarily).

- `pressure`: Pump power as percentage (0-100)
- `flow`: Ignored in power mode

**Use cases:**
- Bloom phase (pump off with pressure: 0)
- Legacy compatibility
- Direct pump control

**Pump OFF (for bloom/soak):**
```json
"pump": {
  "target": "power",
  "pressure": 0,
  "flow": 0
}
```

**Full power:**
```json
"pump": {
  "target": "power",
  "pressure": 100,
  "flow": 0
}
```

---

### 4. Adaptive Flow (`flow: -1`)

Automatically adjusts flow based on puck resistance.

- Maintains consistent extraction across different conditions
- Ideal for final extraction phase
- Compensates for grind variations

```json
"pump": {
  "target": "flow",
  "pressure": 9,
  "flow": -1
}
```

**Benefits:**
- Consistent extraction across different grind settings
- Adapts to varying bean freshness levels
- Reduces need for grind adjustments
- More forgiving of puck prep variations

---

## Transition Configuration

Transitions control how the pump moves from one phase's settings to the next.

### Transition Object Structure

```json
"transition": {
  "type": "linear",
  "duration": 3,
  "adaptive": true
}
```

### Transition Fields

| Field | Type | Required | Description | Valid Values |
|-------|------|----------|-------------|--------------|
| `type` | string | Yes | Ramp curve shape | `"instant"`, `"linear"`, `"ease-in"`, `"ease-out"`, `"ease-in-out"` |
| `duration` | number | Yes | Ramp duration in seconds | 0-10 (0 = instant) |
| `adaptive` | boolean | Yes | Start from current or previous target | `true` = current, `false` = previous target |

---

## Transition Types Explained

### Instant (`"instant"`)

Immediate jump to new target. No ramping.

**Use for:**
- Phase starts
- Step changes
- When you want immediate pressure/flow change

```json
"transition": {
  "type": "instant",
  "duration": 0,
  "adaptive": true
}
```

**Pressure curve shape:**
```
Pressure
    |     ┌────────
    |     │
    |─────┘
    └──────────────── Time
```

---

### Linear (`"linear"`)

Constant rate change. Predictable, smooth.

**Use for:**
- Standard pressure ramps
- Declining pressure profiles
- Predictable transitions

```json
"transition": {
  "type": "linear",
  "duration": 4,
  "adaptive": true
}
```

**Pressure curve shape:**
```
Pressure
    |        ────────
    |      /
    |    /
    |  /
    |/
    └──────────────── Time
```

---

### Ease-In (`"ease-in"`)

Slow start, fast finish. Gradual pressure build.

**Use for:**
- Gentle pre-infusion to main extraction
- Reducing initial puck disturbance
- Smooth ramp-up

```json
"transition": {
  "type": "ease-in",
  "duration": 3,
  "adaptive": true
}
```

**Pressure curve shape:**
```
Pressure
    |           ─────
    |         /
    |       /
    |     _/
    |____/
    └──────────────── Time
```

---

### Ease-Out (`"ease-out"`)

Fast start, slow finish. Smooth pressure decline.

**Use for:**
- Tapering at end of shot
- Declining pressure phases
- Smooth finish

```json
"transition": {
  "type": "ease-out",
  "duration": 5,
  "adaptive": true
}
```

**Pressure curve shape:**
```
Pressure
    |──────\_
    |        \_
    |          \
    |           \
    |            ────
    └──────────────── Time
```

---

### Ease-In-Out (`"ease-in-out"`)

Slow start and finish, fast middle. Most natural feeling.

**Use for:**
- Complex pressure profiles
- When smooth transitions at both ends matter
- Professional-style profiles

```json
"transition": {
  "type": "ease-in-out",
  "duration": 4,
  "adaptive": true
}
```

**Pressure curve shape:**
```
Pressure
    |          ──────
    |        _/
    |      _/
    |    _/
    |___/
    └──────────────── Time
```

---

## Adaptive Behavior

The `adaptive` field controls whether transitions start from the actual current value or the previous phase's target value.

### `adaptive: true` (Start from current value)

- If Phase 1 targets 3 bar but only reaches 2 bar
- Phase 2 ramps from **2 bar** → 7 bar (actual value)
- More responsive to puck resistance
- Recommended for most use cases

```json
"transition": {
  "type": "linear",
  "duration": 3,
  "adaptive": true
}
```

### `adaptive: false` (Start from previous target)

- If Phase 1 targets 3 bar but only reaches 2 bar
- Phase 2 ramps from **3 bar** → 7 bar (target value)
- More predictable, ignores actual performance
- Use when you want consistent pressure curves

```json
"transition": {
  "type": "linear",
  "duration": 3,
  "adaptive": false
}
```

---

## Transition Selection Guide

| Scenario | Recommended Type | Duration |
|----------|-----------------|----------|
| Phase start (first phase) | `instant` | 0 |
| Pre-infusion → Main extraction | `ease-in` or `linear` | 3-5s |
| Pressure increase | `linear` | 3-4s |
| Pressure decrease/taper | `ease-out` | 4-6s |
| Complex profile transitions | `ease-in-out` | 4-5s |
| Step change (immediate) | `instant` | 0 |

---

## Common Pump + Transition Combinations

### Pre-infusion Start
```json
"pump": { "target": "flow", "pressure": 9, "flow": 3 },
"transition": { "type": "instant", "duration": 0, "adaptive": true }
```

### Ramp to Extraction Pressure
```json
"pump": { "target": "pressure", "pressure": 9, "flow": 0 },
"transition": { "type": "linear", "duration": 3, "adaptive": true }
```

### Hold at Pressure
```json
"pump": { "target": "pressure", "pressure": 9, "flow": 4 },
"transition": { "type": "instant", "duration": 0, "adaptive": true }
```

### Decline Phase
```json
"pump": { "target": "pressure", "pressure": 4, "flow": 0 },
"transition": { "type": "ease-out", "duration": 5, "adaptive": true }
```

### Bloom (Pump Off)
```json
"pump": { "target": "power", "pressure": 0, "flow": 0 },
"transition": { "type": "instant", "duration": 0, "adaptive": true }
```
