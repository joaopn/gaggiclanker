# Diagnostic Decision Trees

Taste-based decision trees for diagnosing espresso extraction issues. All thresholds are **relative to the identified shot style** — load expected ranges from `PRESSURE_GUIDE.md` and `PROFILE_LIBRARY.md`.

*Adapted from [gaggimate-barista](https://github.com/charleshall888/gaggimate-barista) by Charlie Hall.*

---

## SOUR Shot Diagnosis

```
SOUR (under-extracted)
├── Time to first drip below style's expected minimum?
│   ├── YES → Channeling or too coarse
│   │   ├── Flow rate erratic or uneven? → Channeling (uneven puck density)
│   │   │   └── FIX: Improve puck prep (WDT, distribution, even tamp). Do NOT grind finer.
│   │   │         Grinding slightly coarser may help (less resistance). Extend pre-infusion.
│   │   └── Pressure below style's expected range? → Too coarse
│   │       └── FIX: Grind 1-2 steps finer
│   └── NO → Continue...
├── Flow rate above style's expected range?
│   └── YES → Resistance too low for this style
│       └── FIX: Grind finer, check dose (may need +0.5g)
├── Temperature < target by >2°C?
│   └── YES → Equipment issue (not fully heated)
│       └── FIX: Longer warm-up, flush before shot
├── Preinfusion shorter than profile specifies?
│   └── YES → Insufficient saturation
│       └── FIX: Extend preinfusion phase in profile
└── None of above?
    └── Extraction time within style range but sour?
        └── FIX: Increase temperature +1-2°C, or grind finer
```

## BITTER Shot Diagnosis

```
BITTER (over-extracted)
├── Total time well above style's expected range?
│   └── YES → Over-extracted (too much contact time)
│       └── FIX: Grind coarser, or reduce ratio
├── Temperature > target by >2°C?
│   └── YES → Running hot
│       └── FIX: Reduce brew temp -2°C, check PID calibration
├── Pressure sustained above style's target through extraction?
│   └── YES → Aggressive extraction
│       └── FIX: Use declining pressure profile, or lower target pressure
├── Flow rate well below style's expected range for extended period?
│   └── YES → Choked flow, prolonged contact
│       └── FIX: Grind coarser, reduce dose -0.5g
└── None of above?
    └── May be bean-related (dark roast, over-roasted)
        └── FIX: Lower temp, shorter ratio, declining pressure
```

## FLAT/MUTED Shot Diagnosis

```
FLAT (lacks vibrancy, dull)
├── Temperature drifting >2°C during shot?
│   └── YES → Thermal instability
│       └── FIX: Better pre-heating, temperature surfing
├── Preinfusion shorter than profile specifies or skipped?
│   └── YES → Poor saturation, uneven extraction
│       └── FIX: Add proper preinfusion phase, or extend existing one
├── Beans > 4 weeks from roast?
│   └── YES → Stale, CO2 depleted
│       └── FIX: Fresh beans; compensate with higher temp, finer grind
├── Extraction time well below style's expected range?
│   └── YES → Under-developed, too fast
│       └── FIX: Grind finer, extend extraction
└── None of above?
    └── May need more extraction
        └── FIX: Higher temp +1-2°C, longer ratio, consider bloom phase
```

## INCONSISTENT Shots Diagnosis

```
INCONSISTENT (variable results)
├── Compare multiple shots via list_recent_shots
├── Check for patterns:
│   ├── Time to first drip varies >3s between shots?
│   │   └── YES → Puck prep inconsistency
│   │       └── FIX: Standardize WDT, distribution, tamping
│   ├── Pressure curves differ significantly?
│   │   └── YES → Grind retention or distribution issues
│   │       └── FIX: Purge grinder, single-dose, better WDT
│   ├── Temperature varies >1.5°C between shots?
│   │   └── YES → Machine thermal management
│   │       └── FIX: Consistent wait time between shots, flush routine
│   └── Flow patterns different?
│       └── YES → Channeling (random), check basket prep
└── Document which variables are changing for pattern recognition
```

## BITTER + FAST Shot Diagnosis

```
BITTER + FAST (shot runs fast but tastes bitter — unusual combination)
├── Bean-related?
│   ├── Dark/over-roasted beans? → Inherent bitterness, not extraction issue
│   │   └── FIX: Lower temp, shorter ratio, declining pressure
│   ├── Beans stale (>4 weeks)? → Degraded oils create harsh bitterness
│   │   └── FIX: Fresh beans; if unavailable, lower temp, shorter ratio
│   └── Robusta blend or very dark roast? → High CGA content
│       └── FIX: Lower pressure (6-7 bar), declining profile, lower temp
├── Water quality issue?
│   └── High TDS, chlorine, or mineral imbalance?
│       └── FIX: Check water recipe, use filtered water
└── None of above?
    └── May be fines-related bitterness (dark roasts produce more fines)
        └── FIX: Coarser grind, better distribution, check grinder alignment
```

## BALANCED BUT THIN Shot Diagnosis

```
BALANCED BUT THIN (taste is correct, but lacking body/mouthfeel)
├── Ratio too long?
│   └── >1:3 for traditional style? → Diluted body
│       └── FIX: Shorter ratio (pull less liquid), keep same grind
├── Grind too coarse for desired body?
│   └── Shot running faster than style target?
│       └── FIX: Finer grind (increases body and extraction)
├── Pressure too low?
│   └── Below 6 bar during main extraction?
│       └── FIX: Increase target pressure, or switch from turbo to traditional style
├── Basket leaking around edges?
│   └── Worn gasket, basket not seated properly, or damaged basket rim?
│       └── FIX: Check group gasket, re-seat basket, inspect for damage
└── None of above?
    └── May be bean characteristic (some origins are naturally lighter-bodied)
        └── Consider: shorter ratio, higher temp, or finer grind for more body
```
