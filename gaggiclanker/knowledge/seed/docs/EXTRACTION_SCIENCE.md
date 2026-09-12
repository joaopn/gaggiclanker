# Extraction Science

Actionable tables for grinder-profile interaction, channeling prevention, pre-infusion mechanics, freshness guidance, and visual diagnosis.

*Adapted from [gaggimate-barista](https://github.com/charleshall888/gaggimate-barista) by Charlie Hall.*

---

## Why Grinder Choice Affects Profile Design

Different grinders produce fundamentally different particle beds, which respond differently to pressure and flow:

| Grinder Type | Profile Considerations |
|--------------|----------------------|
| **High-fines conical** (Sette 270, Niche) | May benefit from 7–8 bar extraction, gentler ramp, or longer pre-infusion to avoid over-compacting fines |
| **Low-fines flat** (EK43, SSP burrs) | Can handle 9 bar consistently; may need finer grind to compensate for lack of fines; pressure decline is less critical |
| **Unimodal grinders** | Often require flow-based profiles rather than pressure-based; turbo-style shots work particularly well |

This is why a profile that works perfectly on one setup may underperform on another — the grinder is part of the system.

---

## Channeling Prevention

### Prevention Hierarchy

**Distribution > Tamping > Pressure**

1. **Distribution (most important)** — WDT breaks up clumps and distributes grounds evenly. No amount of tamping can fix uneven distribution.
2. **Tamping (important but secondary)** — Creates a level surface. Consistent pressure matters more than high pressure (15–30 lbs is fine).
3. **Pressure profile (tertiary)** — Lower pressure is more forgiving. Gentle ramp allows the puck to "set" before full pressure. Pre-infusion saturates the entire bed before extraction force is applied.

### Puck Prep Tools

| Tool | What It Does | What It Doesn't Fix |
|------|--------------|---------------------|
| **WDT (needle tool)** | Breaks clumps, redistributes grounds, creates even density | Can't fix too-fine grind or bad grinder uniformity |
| **Distributor/leveler** | Creates flat surface, mild redistribution | Doesn't break internal clumps (use WDT first) |
| **Paper filter below puck** | Catches migrating fines, prevents compact layer, cleaner cup | Doesn't fix distribution issues |
| **Puck screen on top** | Protects puck from shower screen imprint, prevents surface erosion | Doesn't improve internal distribution |

**Recommended combo:** WDT → light tap to settle → tamp → (optional: puck screen on top)

Paper filters below the puck are particularly effective for high-fines grinders — they prevent fines from accumulating at the basket holes and creating uneven flow.

---

## Pre-Infusion Mechanics

Pre-infusion prevents channeling by achieving **full saturation before extraction pressure** is applied.

**The physics:**
1. Dry coffee is hydrophobic — water prefers to flow around it rather than through it
2. Full pressure on a dry puck → water flows through any weak spot → instant channeling
3. Low-pressure pre-infusion (1–3 bar) gives water time to wet all the coffee evenly
4. Once the puck is saturated, there are no "weak spots" for water to exploit
5. The pressure threshold (~4 bar) marks when the puck compresses — stay below this during pre-infusion

**Bloom phase (extended pre-infusion):**
- Fill at low pressure/flow until puck is wet
- Stop pump, wait 5–15 seconds
- Allows water to fully penetrate all particles
- Particularly effective for fresh beans (allows CO2 to escape before extraction)

---

## Freshness Guidance

| Days Off Roast | Guidance |
|----------------|----------|
| 1–3 days | Very fresh — expect inconsistency. Use long bloom (10–15s), very slow pre-infusion |
| 4–7 days | Fresh — bloom profile recommended, longer pre-infusion |
| 7–14 days | Sweet spot for most espresso. Standard profiles work well |
| 14–21 days | Peak complexity for many coffees. Can use shorter pre-infusion |
| 21–30 days | Still good, flavors may simplify. Standard profiles work fine |
| 30+ days | Watch for staleness, but many coffees remain excellent |

Light roasts degas more slowly than dark roasts (denser structure retains gas longer), so they may need longer rest periods or extended bloom phases.

---

## Visual Diagnosis (Bottomless Portafilter)

| What You See | What It Means | Fix |
|--------------|---------------|-----|
| Even, centered cone of espresso | Good extraction — puck resistance is uniform | Keep doing what you're doing |
| Spurting/side-squirting | Channel formed at that location | Better WDT, check distribution |
| Blonde streaks in dark espresso | Channel is over-extracting one spot | Distribution issue, possibly grind inconsistency |
| Espresso emerging from one side first | Uneven tamp or distribution | Level tamp, redistribute |
| Dead/dry spots on puck bottom (post-shot) | Water completely bypassed that area | Significant channeling — major distribution fix needed |
| Very fast flow with thin crema | Grind too coarse or severe channeling | Finer grind, better prep |

---

*For practical adjustment strategies, see `ESPRESSO_BREWING_BASICS.md`. For pressure selection, see `PRESSURE_GUIDE.md`.*
