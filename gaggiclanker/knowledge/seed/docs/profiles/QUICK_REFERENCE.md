# Gaggimate Profile Quick Reference

Cheat sheet for creating espresso profiles. For detailed documentation, see the other reference files.

---

## Essential Settings by Coffee Type

### Light Roasts (Ethiopia, Kenya, Fruit-forward)
- **Temperature**: 94-96°C
- **Ratio**: 1:2.5 to 1:3
- **Profile**: Bloom + 9 bar
- **Time**: 28-35 seconds
- **Pattern**: Fill → Bloom → Ramp → Hold

### Medium Roasts (Colombia, Brazil, Balanced)
- **Temperature**: 92-94°C
- **Ratio**: 1:2
- **Profile**: Classic 9 bar or slight decline
- **Time**: 25-32 seconds
- **Pattern**: Pre-infusion → Ramp → Hold

### Medium-Dark (Vienna)
- **Temperature**: 90-92°C
- **Ratio**: 1:2
- **Profile**: 8-9 bar with decline
- **Time**: 25-30 seconds
- **Pattern**: Pre-infusion → Ramp → Hold → Taper

### Dark Roasts (French/Italian)
- **Temperature**: 88-90°C
- **Ratio**: 1:1.5 to 1:2
- **Profile**: 7-8 bar or declining
- **Time**: 22-28 seconds
- **Pattern**: Pre-infusion → Ramp → Hold (lower pressure)

---

## Temperature Guidelines

| Coffee Type | Roast Level | Recommended Temp |
|-------------|-------------|------------------|
| Light roast | Nordic style | 94-96°C |
| Medium roast | City/Full City | 92-94°C |
| Medium-dark | Vienna | 90-92°C |
| Dark roast | French/Italian | 88-90°C |

**Adjustment Strategy:**
- Too sour/acidic → Increase 1-2°C
- Too bitter/harsh → Decrease 1-2°C
- Flat/lifeless → Increase temperature
- Astringent/dry → Decrease temperature

---

## Ratio and Timing Guidelines

| Ratio | Dose In | Output | Time Range | Style |
|-------|---------|--------|------------|-------|
| 1:1 | 18g | 18g | 20-25s | Ristretto |
| 1:1.5 | 18g | 27g | 22-28s | Short |
| 1:2 | 18g | 36g | 25-32s | Classic |
| 1:2.5 | 18g | 45g | 28-35s | Lungo |
| 1:3 | 18g | 54g | 30-40s | Allongé |

---

## Phase Duration Guidelines

| Phase Type | Typical Duration | Purpose |
|------------|------------------|---------|
| Fast fill | 2-3 seconds | Quick wetting at low pressure |
| Slow pre-infusion | 4-8 seconds | Even saturation, de-gas |
| Bloom/soak | 5-10 seconds | Enhance sweetness |
| Pressure ramp | 3-5 seconds | Build to extraction pressure |
| Hold phase | 15-30 seconds | Main extraction |
| Decline/taper | 3-6 seconds | Smooth finish |

---

## Flow Rate Guidelines

| Flow Rate | Use Case | Expected Pressure |
|-----------|----------|-------------------|
| 1.5-2.5 ml/s | Very gentle pre-infusion | <3 bar |
| 2.5-4 ml/s | Standard pre-infusion | 3-6 bar |
| 4-5 ml/s | Main extraction (with limit) | 8-9 bar |
| -1 (adaptive) | Final phase | Variable |

---

## Pump Modes Quick Reference

```json
// Pressure mode - maintain specific pressure
{ "target": "pressure", "pressure": 9, "flow": 4 }

// Flow mode - maintain specific flow rate  
{ "target": "flow", "pressure": 9, "flow": 2.5 }

// Adaptive flow - auto-adjust to puck resistance
{ "target": "flow", "pressure": 9, "flow": -1 }

// Power mode (for bloom - pump off)
{ "target": "power", "pressure": 0, "flow": 0 }
```

---

## Transition Types Quick Reference

| Type | Curve | Use Case |
|------|-------|----------|
| `instant` | Step | Phase starts, immediate changes |
| `linear` | Straight line | Standard ramps |
| `ease-in` | Slow→Fast | Gentle pre-infusion start |
| `ease-out` | Fast→Slow | Smooth finish/taper |
| `ease-in-out` | Slow→Fast→Slow | Complex profiles |

---

## Stop Conditions Quick Reference

```json
// Weight-based (requires scale)
{ "type": "volumetric", "operator": "gte", "value": 36 }

// Water volume (no scale needed)
{ "type": "water_pumped", "operator": "gte", "value": 50 }

// Pressure reached
{ "type": "pressure", "operator": "gte", "value": 8.5 }

// Pressure dropped
{ "type": "pressure", "operator": "lte", "value": 3 }

// Flow rate
{ "type": "flow", "operator": "gte", "value": 4 }
```

Operators: `gte` (≥), `lte` (≤), `gt` (>), `lt` (<)

---

## Common Profile Patterns

### Pattern 1: Modern Espresso (Medium Roasts)
```
Pre-infusion (3-4s at 3 ml/s)
→ Ramp (3s to 9 bar)
→ Hold (20-25s at 9 bar)
→ Stop at target weight
```

### Pattern 2: Light Roast Bloom
```
Gentle fill (5s at 2 ml/s)
→ Bloom (8s, pump off)
→ Ramp (4s to 9 bar)
→ Hold (15-20s at 9 bar)
→ Stop at target weight
```

### Pattern 3: Dark Roast Low Pressure
```
Pre-infusion (4s at 2.5 ml/s)
→ Ramp (3s to 7 bar)
→ Hold (25s at 7 bar)
→ Taper (4s to 4 bar)
→ Stop at target weight
```

### Pattern 4: Spring Lever Simulation
```
Pre-infusion (4s at 3 ml/s)
→ Peak (3s to 9 bar)
→ Linear decline (25s from 9→3 bar)
→ Stop at target weight
```

---

## Most Common Mistakes

1. **Too short pre-infusion** → Add 2-4 seconds
2. **No volumetric target** → Always set weight target
3. **Instant transitions everywhere** → Use linear/ease for ramps
4. **Ignoring temperature** → Adjust by 1-2°C increments
5. **Same profile for all coffees** → Customize per bean

---

## Quick Troubleshooting

| Problem | Quick Fix |
|---------|-----------|
| Sour shot | ↑ Temp +2°C, ↑ Time |
| Bitter shot | ↓ Temp -2°C, Add decline |
| Channeling | Longer pre-infusion, Add bloom |
| Inconsistent | Add volumetric target |
| Pump straining | Start with flow mode |

---

## Minimal Valid Profile Template

```json
{
  "label": "My Profile",
  "type": "pro",
  "temperature": 93,
  "phases": [
    {
      "name": "Pre-infusion",
      "phase": "preinfusion",
      "valve": 1,
      "duration": 5,
      "temperature": 0,
      "transition": { "type": "instant", "duration": 0, "adaptive": true },
      "pump": { "target": "flow", "pressure": 9, "flow": 3 }
    },
    {
      "name": "Extraction",
      "phase": "brew",
      "valve": 1,
      "duration": 30,
      "temperature": 0,
      "transition": { "type": "linear", "duration": 3, "adaptive": true },
      "pump": { "target": "pressure", "pressure": 9, "flow": 4 },
      "targets": [{ "type": "volumetric", "operator": "gte", "value": 36 }]
    }
  ]
}
```

---

## Resources

- **Documentation**: https://docs.gaggimate.eu/
- **Discord**: https://discord.gg/APw7rgPGPf
- **Web Interface**: http://gaggimate.local/profiles
- **GitHub**: https://github.com/jniebuhr/gaggimate

---

## File Management

### Export Profile
1. Go to `http://gaggimate.local/profiles`
2. Click export icon on profile card
3. Save `.json` file

### Import Profile
1. Go to `http://gaggimate.local/profiles`
2. Click "Import" (top right)
3. Upload `.json` file
4. Click star icon to enable on machine
