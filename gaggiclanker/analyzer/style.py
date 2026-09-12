"""What kind of shot is this? Three tiers, in descending order of trust.

The style decides which expectations apply — a turbo shot at 15 s is on target
and a classic one at 15 s is a gusher — so getting it from the *profile* rather
than from the numbers matters: the profile is what the person meant, and the
telemetry is only what happened.

gaggimate-mcp's `diagnose` skill states the rule as a cascade over the profile
definition, and that is tier 1
here, with two additions of our own:

* **utility first.** A backflush profile is a series of pump-on/pump-off blocks
  with no stop condition anywhere, which the bloom rule would happily call a
  bloom. It brews nothing, so every dial-in expectation about it is nonsense,
  and calling it `utility` is the only honest answer. (The firmware's own
  `utility` flag catches most of them; the structural test catches the ones
  written before the flag existed.)
* **brew phases only.** A flow of 10 ml/s during "Fill Headspace" is the machine
  filling the space above the puck, not a turbo shot. Tier 1 reads the phases
  that actually extract.

Tier 2 is the **names** — the profile's phase names when there is a profile, and
otherwise the name the `.slog` header recorded, which every shot has whether or
not the profile mirror ever caught up. That second half is not a nicety: the
maintainer's own archive is full of shots brewed with "Amigo Alturas Bloom [AI]"
and nothing else to go on, and reading them off the telemetry alone files them
as lever or classic.

Tier 3 is the telemetry, for a shot with neither — the archive holds shots from
before the profile sync existed, and "unknown" for all of them would make the
whole style tier useless on exactly the shots that are hardest to read.

Every answer carries its evidence. The UI shows it, and it is the difference
between "the analyzer thinks this is a lever shot" and "the analyzer thinks this
is a lever shot *because* pressure declines 6 bar over 50 s".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gaggiclanker.domain.vocab import ShotStyle

__all__ = ["StyleVerdict", "detect_style"]

#: A brew phase commanding at least this much flow is a turbo shot
#: (gaggimate-mcp §8).
TURBO_FLOW_ML_S = 3.5

#: A pressure fall of at least this much, over at least this long, is a lever
#: profile's declining ramp rather than an ordinary end-of-shot taper.
LEVER_DECLINE_BAR = 4.0
LEVER_DECLINE_SECONDS = 15.0

#: At or below this pressure with a long volumetric target, the profile is an
#: allongé rather than a short low-pressure shot.
ALLONGE_MAX_BAR = 7.0
ALLONGE_RATIO = 3.5

#: Below this brew pressure, with no bloom and no decline, the profile is built
#: for a dark roast.
DARK_MAX_BAR = 9.0

#: Phase-name keywords, tier 2. Ordered: the first style whose keyword appears
#: in any phase name wins, so "bloom" beats "ramp down" in a profile that has
#: both, the same way tier 1 checks bloom first.
_NAME_KEYWORDS: tuple[tuple[ShotStyle, tuple[str, ...]], ...] = (
    ("utility", ("backflush", "flush", "clean", "rinse", "descal")),
    ("bloom", ("bloom", "soak", "steep")),
    ("turbo", ("turbo",)),
    ("lever", ("lever", "decline", "ramp down", "ramp-down", "taper")),
    ("allonge", ("allonge", "allongé", "lungo")),
)


@dataclass(frozen=True, slots=True)
class StyleVerdict:
    """The detected style, how it was reached, and what pointed at it."""

    style: ShotStyle = "unknown"
    #: `profile`, `phase_names`, `telemetry`, or `none` when nothing did.
    tier: str = "none"
    evidence: list[str] = field(default_factory=list)

    def render(self) -> str:
        reasons = "; ".join(self.evidence) if self.evidence else "no evidence"
        return f"{self.style} (from {self.tier}: {reasons})"


def detect_style(
    profile: dict[str, Any] | None,
    *,
    dose_g: float | None = None,
    summary: dict[str, Any] | None = None,
    duration_s: float | None = None,
    profile_name: str = "",
) -> StyleVerdict:
    """Classify a shot. ``profile`` is the Set version's profile document.

    ``profile_name`` is the name the shot's own `.slog` header recorded — the
    one thing every shot has, even one whose profile the mirror never captured.
    It is read after the profile's own numbers and before the telemetry, because
    "Bloom" in a name is what the person meant and a curve is only what happened.

    ``summary`` is the shot's own `summary` block from the diagnostics blob,
    used only when the names say nothing. ``dose_g`` is needed for the allongé
    test, which is a ratio; without a dose that test is skipped rather than
    guessed at, because a volumetric target of 60 g is an allongé on 17 g of
    coffee and an ordinary double on 30.
    """
    if profile:
        verdict = _from_profile(profile, dose_g)
        if verdict is not None:
            return verdict
        named = _from_phase_names(profile)
        if named is not None:
            return named
    named = _from_name(profile_name)
    if named is not None:
        return named
    return _from_telemetry(summary, duration_s)


# ── tier 1: the profile's own numbers ─────────────────────────────────


def _from_profile(profile: dict[str, Any], dose_g: float | None) -> StyleVerdict | None:
    phases = [p for p in profile.get("phases", []) if isinstance(p, dict)]
    if not phases:
        return None

    if _is_utility(profile, phases):
        return StyleVerdict(
            style="utility",
            tier="profile",
            evidence=[_utility_evidence(profile, phases)],
        )

    brew = [p for p in phases if str(p.get("phase", "brew")) == "brew"] or phases

    # Bloom first: a pump-off phase is the most specific thing a profile can
    # say, and a bloom profile usually also has a 9 bar hold that every later
    # test would match.
    bloom = _bloom_phase(phases)
    if bloom is not None:
        return StyleVerdict(
            style="bloom",
            tier="profile",
            evidence=[f"phase {bloom!r} runs the pump at zero — a bloom, not a closed valve"],
        )

    flow = _max_brew_flow(brew)
    if flow is not None and flow >= TURBO_FLOW_ML_S:
        return StyleVerdict(
            style="turbo",
            tier="profile",
            evidence=[f"a brew phase commands {flow:g} ml/s (turbo is >= {TURBO_FLOW_ML_S:g})"],
        )

    decline = _decline(phases)
    if decline is not None:
        drop, seconds = decline
        return StyleVerdict(
            style="lever",
            tier="profile",
            evidence=[
                f"pressure declines {drop:g} bar over {seconds:g} s "
                f"(lever is >= {LEVER_DECLINE_BAR:g} bar over >= {LEVER_DECLINE_SECONDS:g} s)"
            ],
        )

    ceiling = _max_brew_pressure(brew)
    volumetric = _max_volumetric(phases)
    if (
        ceiling is not None
        and ceiling <= ALLONGE_MAX_BAR
        and volumetric is not None
        and dose_g
        and volumetric >= dose_g * ALLONGE_RATIO
    ):
        return StyleVerdict(
            style="allonge",
            tier="profile",
            evidence=[
                f"brew pressure caps at {ceiling:g} bar with a {volumetric:g} g target "
                f"on a {dose_g:g} g dose (1:{volumetric / dose_g:.1f})"
            ],
        )

    if ceiling is not None and ceiling < DARK_MAX_BAR:
        return StyleVerdict(
            style="dark",
            tier="profile",
            evidence=[
                f"brew pressure caps at {ceiling:g} bar, under {DARK_MAX_BAR:g}, "
                "with no bloom and no declining ramp"
            ],
        )

    if ceiling is not None:
        return StyleVerdict(
            style="classic",
            tier="profile",
            evidence=[
                f"brew pressure caps at {ceiling:g} bar with no bloom, turbo flow or decline"
            ],
        )
    return None


def _is_utility(profile: dict[str, Any], phases: list[dict[str, Any]]) -> bool:
    """A profile that cleans the machine rather than brewing coffee.

    Two tests, either of which is enough. The firmware's own `utility` flag is
    authoritative when it is set; the structural test — no stop condition
    anywhere, and every phase an open-loop pump percentage — catches a
    backflush written before the flag existed, which the bloom rule would
    otherwise classify as a bloom because "Depressurize" runs the pump at zero.
    """
    if bool(profile.get("utility")):
        return True
    has_target = any(phase.get("targets") for phase in phases)
    all_power = all(isinstance(phase.get("pump"), int) for phase in phases)
    return not has_target and all_power and len(phases) > 1


def _utility_evidence(profile: dict[str, Any], phases: list[dict[str, Any]]) -> str:
    if bool(profile.get("utility")):
        return "the profile carries the firmware's own `utility` flag"
    return (
        f"{len(phases)} open-loop pump phases and no stop condition anywhere — "
        "a backflush or flush, not a shot"
    )


def _pump(phase: dict[str, Any]) -> dict[str, Any] | int | None:
    pump = phase.get("pump")
    if isinstance(pump, dict | int):
        return pump
    return None


def _bloom_phase(phases: list[dict[str, Any]]) -> str | None:
    """The first phase that turns the pump off.

    A bloom is the pump at zero with the valve still open (the firmware's own
    convention, as gaggimate-mcp's diagnose workflow states it), which is either
    a pump percentage of 0 or a closed-loop `off` target. A phase shorter than a
    second is a hammer or a settle, not a soak.
    """
    for phase in phases:
        if float(phase.get("duration", 0) or 0) < 1:
            continue
        pump = _pump(phase)
        if isinstance(pump, int) and pump == 0:
            return str(phase.get("name", "?"))
        if isinstance(pump, dict):
            if str(pump.get("target", "")) == "off":
                return str(phase.get("name", "?"))
            # A pressure target of zero with no flow target is the pump off in
            # all but name, which is how several published bloom profiles spell
            # it.
            if float(pump.get("pressure", 0) or 0) == 0 and float(pump.get("flow", 0) or 0) == 0:
                return str(phase.get("name", "?"))
    return None


def _max_brew_flow(brew: list[dict[str, Any]]) -> float | None:
    """The highest *commanded* flow among the brew phases.

    Only a `flow`-targeted phase counts. A pressure-targeted phase's `flow` is a
    soft limit ("do not exceed"), and reading it as a setpoint would make every
    profile with a 3.5 ml/s ceiling a turbo shot.
    """
    flows = [
        float(pump.get("flow", 0) or 0)
        for phase in brew
        if isinstance(pump := _pump(phase), dict) and str(pump.get("target", "")) == "flow"
        # -1 is the firmware's "hold whatever was measured" sentinel, not a rate.
        if float(pump.get("flow", 0) or 0) > 0
    ]
    return max(flows) if flows else None


def _max_brew_pressure(brew: list[dict[str, Any]]) -> float | None:
    """The highest pressure the brew phases can reach.

    A flow-targeted phase's `pressure` is its ceiling, and the ceiling is the
    right number here: a flow-led profile capped at 9 bar is a classic shot
    however it is spelled.
    """
    pressures: list[float] = []
    for phase in brew:
        pump = _pump(phase)
        if isinstance(pump, dict):
            value = float(pump.get("pressure", 0) or 0)
            if value > 0:
                pressures.append(value)
        elif isinstance(pump, int) and pump > 0:
            # An open-loop percentage has no setpoint. Full power is the pump's
            # own maximum, which on this machine is about 9 bar into a puck.
            pressures.append(9.0 * pump / 100.0)
    return max(pressures) if pressures else None


def _decline(phases: list[dict[str, Any]]) -> tuple[float, float] | None:
    """The size and length of the profile's declining tail, if it has one.

    Measured from the highest commanded pressure to the last phase's, over the
    duration of everything after the peak. That is deliberately generous: the
    Cremina profile spells its decline as four separate 10 s phases stepping
    9 -> 8 -> 7 -> 6 -> 5, and a test that looked at one phase at a time would
    see four 1 bar steps and no lever.
    """
    setpoints: list[tuple[float, float]] = []
    for phase in phases:
        pump = _pump(phase)
        if not isinstance(pump, dict):
            continue
        if str(pump.get("target", "")) not in ("pressure", "flow"):
            continue
        pressure = float(pump.get("pressure", 0) or 0)
        if pressure <= 0:
            continue
        setpoints.append((pressure, float(phase.get("duration", 0) or 0)))
    if len(setpoints) < 2:
        return None

    peak = max(range(len(setpoints)), key=lambda i: setpoints[i][0])
    tail = setpoints[peak + 1 :]
    if not tail:
        return None
    drop = setpoints[peak][0] - min(pressure for pressure, _ in tail)
    seconds = sum(duration for _, duration in tail)
    if drop >= LEVER_DECLINE_BAR and seconds >= LEVER_DECLINE_SECONDS:
        return drop, seconds
    return None


def _max_volumetric(phases: list[dict[str, Any]]) -> float | None:
    values = [
        float(target.get("value", 0) or 0)
        for phase in phases
        for target in phase.get("targets") or []
        if isinstance(target, dict) and str(target.get("type", "")) == "volumetric"
    ]
    return max(values) if values else None


# ── tier 2: what the author called the phases ─────────────────────────


def _from_phase_names(profile: dict[str, Any]) -> StyleVerdict | None:
    haystack = " ".join(
        str(phase.get("name", "")).lower()
        for phase in profile.get("phases", [])
        if isinstance(phase, dict)
    )
    label = str(profile.get("label", "")).lower()
    return _keyword_verdict(f"{label} {haystack}", "a phase or the label")


def _from_name(profile_name: str) -> StyleVerdict | None:
    """The name the shot's header recorded, for a shot with no stored profile.

    Firmware profile names carry the style far more often than not — the
    maintainer's own archive is "Amigo Alturas Bloom [AI]", "Gratus 16:32 trad"
    — and a name the person chose beats a fingerprint we inferred.
    """
    return _keyword_verdict(profile_name.lower(), "the shot's profile name")


def _keyword_verdict(haystack: str, source: str) -> StyleVerdict | None:
    for style, keywords in _NAME_KEYWORDS:
        for keyword in keywords:
            if keyword in haystack:
                return StyleVerdict(
                    style=style,
                    tier="phase_names",
                    evidence=[f"{source} mentions {keyword!r}"],
                )
    return None


# ── tier 3: what actually happened ────────────────────────────────────


def _from_telemetry(summary: dict[str, Any] | None, duration_s: float | None) -> StyleVerdict:
    """The last resort: classify from the curve.

    Only for a shot whose profile the archive never captured. The fingerprints
    are gaggimate-mcp's (`§7 — Telemetry → diagnosis`) reduced to the two that
    are unambiguous without the profile: a fast high-flow shot is a turbo, and a
    long shot that peaked well under 9 bar is a lever or a dark profile — which
    those numbers cannot tell apart, so it says lever only when the shot also
    ran long.
    """
    if not summary:
        return StyleVerdict()

    flow = summary.get("flow") or {}
    pressure = summary.get("pressure") or {}
    extraction = summary.get("extraction") or {}
    total = duration_s or float(extraction.get("total_time_s", 0) or 0)
    avg_flow = float(flow.get("avg_flow_ml_s", 0) or 0)
    peak_bar = float(pressure.get("max_bar", 0) or 0) if pressure else 0.0

    if avg_flow >= TURBO_FLOW_ML_S and 0 < total <= 20:
        return StyleVerdict(
            style="turbo",
            tier="telemetry",
            evidence=[f"{avg_flow:.1f} ml/s average over {total:.0f} s, with no profile recorded"],
        )
    if peak_bar and peak_bar < DARK_MAX_BAR and total >= 28:
        return StyleVerdict(
            style="lever",
            tier="telemetry",
            evidence=[f"peaked at {peak_bar:.1f} bar over {total:.0f} s, with no profile recorded"],
        )
    if peak_bar >= DARK_MAX_BAR:
        return StyleVerdict(
            style="classic",
            tier="telemetry",
            evidence=[f"peaked at {peak_bar:.1f} bar, with no profile recorded"],
        )
    return StyleVerdict(
        tier="none",
        evidence=["no profile was recorded and the telemetry does not fingerprint a style"],
    )
