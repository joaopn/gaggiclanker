"""Builders for the import suite: the web UI's export shape, from a `Slog`.

The maintainer's two real exports are v5 and carry no `wp`, no
`finalExitReason`, no `brewDelay` and no `transitionReason` — the four fields v7
added. Nobody has a v7 machine to export from yet, so the v7 fixture is built
here and committed, and `test_fixtures.py` re-runs the builder to check the file
on disk still matches.

:func:`export_from_slog` is the inverse of
:func:`~gaggiclanker.domain.exports.shot_export_to_slog`: it renders a parsed
shot exactly the way `HistoryCard.jsx` + `LibraryService.js` write `shot-<id>.json`
(real units, two decimals, `systemInfo` decoded into an object, UI state keys
included). Building the fixture *through* a real encode/parse cycle is what makes
it trustworthy — the numbers in it are numbers the binary format can hold, so a
round-trip test that passes means something.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gaggiclanker.domain.models import PhaseTransition, Sample, SlogHeader, SystemInfo
from gaggiclanker.domain.slog import (
    FIELD_DEFS,
    FIELDS_MASK_ALL,
    Slog,
    encode_slog,
    header_size_for,
    parse_slog,
    sample_size_for,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXPORT_FIXTURES = FIXTURES / "exports"

#: The committed synthetic v7 export, and the multi-profile export beside it.
V7_EXPORT = "shot-v7-synthetic.json"
PROFILE_ARRAY_EXPORT = "profiles-array.json"

#: The v7 shot the builder describes. Small enough to read in a diff, long
#: enough to have four phases and a rising weight curve.
V7_SAMPLE_COUNT = 24
V7_SAMPLE_INTERVAL_MS = 250
V7_SHOT_ID = "412"


def export_from_slog(
    slog: Slog, *, shot_id: str, notes: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Render a parsed shot the way the machine's web UI exports it."""
    mask = slog.header.fields_mask
    names = [fdef.name for fdef in FIELD_DEFS if mask & (1 << fdef.bit) and fdef.name != "t"]

    samples: list[dict[str, Any]] = []
    for sample in slog.samples:
        row: dict[str, Any] = {"t": sample.t}
        for name in names:
            value = getattr(sample, name)
            if name == "si":
                # The UI writes the decoded object; the raw bitfield is one of
                # its keys. Both forms have to import.
                row["systemInfo"] = _system_info(sample.si)
                continue
            row[name] = None if value is None else round(float(value), 2)
        row["phaseNumber"] = sample.phase
        # 1-based, for display. Present in every real export and ignored on the
        # way back in: the transition table is what the phases come from.
        row["phaseDisplayNumber"] = None if sample.phase is None else sample.phase + 1
        samples.append(row)

    export: dict[str, Any] = {
        "id": shot_id,
        "profile": slog.profile_name,
        "profileId": slog.profile_id,
        "timestamp": slog.timestamp,
        "duration": slog.duration_ms,
        "samples": samples,
        "volume": slog.volume_g,
        "incomplete": slog.incomplete,
        "notes": notes,
        # UI state, meaningless here, and present so the importer's leniency is
        # exercised by a fixture rather than only by a unit test.
        "loaded": True,
        "data": None,
        "version": slog.version,
        "sampleInterval": slog.sample_interval,
        "fieldsMask": mask,
        "trailingBytes": slog.trailing_bytes,
        "samplesExpected": slog.header.sample_count,
        "phaseTransitions": [
            {
                "sampleIndex": t.sample_index,
                "phaseNumber": t.phase_number,
                "phaseName": t.phase_name,
                "transitionReason": t.transition_reason,
            }
            for t in slog.transitions
        ],
    }
    if slog.version >= 7:
        export["finalExitReason"] = slog.header.final_exit_reason
        export["finalExitReasonLabel"] = slog.header.final_exit_reason_label
        export["brewDelay"] = slog.header.brew_delay_ms
    return export


def _system_info(raw: int | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    info = SystemInfo.from_raw(raw)
    return {
        "raw": info.raw,
        "shotStartedVolumetric": info.shot_started_volumetric,
        "currentlyVolumetric": info.currently_volumetric,
        "bluetoothScaleConnected": info.bluetooth_scale_connected,
        "volumetricAvailable": info.volumetric_available,
        "extendedRecording": info.extended_recording,
    }


def synthetic_v7_slog() -> Slog:
    """A v7 shot, round-tripped through the binary format.

    Encoded and re-parsed on purpose: what comes back holds only values the
    format can actually store, so the export rendered from it is one a v7
    machine could have written rather than one with impossible precision in it.
    """
    transitions = [
        PhaseTransition(sample_index=0, phase_number=0, transition_reason=0, phase_name="fill"),
        PhaseTransition(sample_index=4, phase_number=1, transition_reason=2, phase_name="soak"),
        PhaseTransition(sample_index=8, phase_number=2, transition_reason=5, phase_name="ramp"),
        PhaseTransition(sample_index=14, phase_number=3, transition_reason=3, phase_name="taper"),
    ]

    samples: list[Sample] = []
    pumped = 0.0
    weight = 0.0
    for i in range(V7_SAMPLE_COUNT):
        pressure = min(9.0, 1.0 + i * 0.7)
        flow = max(0.4, 2.6 - i * 0.08)
        pumped += flow * V7_SAMPLE_INTERVAL_MS / 1000
        weight = max(0.0, pumped - 8.0)
        samples.append(
            Sample.model_validate(
                {
                    # Not a clean multiple of the interval: from v6 the field is
                    # a real millisecond count, and a fixture that only ever
                    # used exact multiples would not prove it survives.
                    "t": i * V7_SAMPLE_INTERVAL_MS + (i % 3),
                    "tt": 93.0,
                    "ct": 92.0 + (i % 4) * 0.2,
                    "tp": 9.0,
                    "cp": pressure,
                    "fl": flow,
                    "tf": 3.0,
                    "pf": flow * 0.8,
                    "vf": flow * 0.7,
                    "v": weight,
                    "ev": weight * 1.02,
                    "pr": pressure / max(flow, 0.1),
                    "si": 0x000F,
                    "wp": pumped,
                }
            )
        )

    header = SlogHeader(
        version=7,
        sample_size=sample_size_for(7, FIELDS_MASK_ALL),
        header_size=header_size_for(7),
        sample_interval=V7_SAMPLE_INTERVAL_MS,
        fields_mask=FIELDS_MASK_ALL,
        sample_count=len(samples),
        duration_ms=(V7_SAMPLE_COUNT - 1) * V7_SAMPLE_INTERVAL_MS + 137,
        start_epoch=1_789_100_000,
        profile_id="dCs4AOOcBn",
        profile_name="Cremina v2",
        final_weight_g=round(weight, 1),
        transitions=transitions,
        final_exit_reason=1,
        brew_delay_ms=900,
    )
    return parse_slog(encode_slog(Slog(header=header, samples=samples)), "000412")


def synthetic_v7_export() -> dict[str, Any]:
    """The committed `shot-v7-synthetic.json`, rebuilt."""
    return export_from_slog(
        synthetic_v7_slog(),
        shot_id=V7_SHOT_ID,
        notes={
            "id": V7_SHOT_ID,
            "rating": 4,
            "beanType": "Nucleus Lima",
            "doseIn": "16",
            "doseOut": "32.4",
            "ratio": "2.0",
            "grindSetting": "4.2",
            "balanceTaste": "balanced",
            "notes": "even flow, no spritzing",
        },
    )


def firmware_profile_form(document: dict[str, Any], profile_id: str) -> dict[str, Any]:
    """A profile document as the firmware's own `writeProfile` emits it.

    The web UI strips `id`, `selected` and `favorite` on export; the firmware
    puts all three back, spells out a phase `temperature` of 0, and always emits
    a `transition` carrying the undocumented `target` (firmware report §3.3).
    Both forms describe the same profile, and
    :func:`~gaggiclanker.domain.models.canonical_profile_json` drops exactly
    what differs — so importing either one twice is one stored version.
    """
    out: dict[str, Any] = {"id": profile_id, **{k: v for k, v in document.items() if k != "id"}}
    out.setdefault("description", "")
    out.setdefault("utility", False)
    out["favorite"] = True
    out["selected"] = False
    phases = []
    for phase in out["phases"]:
        rendered = dict(phase)
        rendered.setdefault("temperature", 0)
        transition = dict(
            rendered.get("transition") or {"type": "instant", "duration": 0, "adaptive": False}
        )
        transition.setdefault("target", "time")
        rendered["transition"] = transition
        phases.append(rendered)
    out["phases"] = phases
    return out


def profile_array_export() -> list[dict[str, Any]]:
    """The committed `profiles-array.json`, rebuilt.

    A JSON array is how the UI exports more than one profile (firmware report
    §3.5). Both entries are in the firmware's own `writeProfile` shape, and the
    first is the same profile as `profile-dCs4AOOcBn.json` — so importing the
    array after the single file creates one version and skips one.
    """
    cremina = load_fixture("profile-dCs4AOOcBn.json")
    ninebar = json.loads((FIXTURES / "profiles" / "firmware-9bar.json").read_text())
    return [
        firmware_profile_form(cremina, "dCs4AOOcBn"),
        firmware_profile_form(ninebar, "9bar"),
    ]


def load_fixture(name: str) -> Any:
    """Read a JSON fixture from `tests/fixtures/exports`."""
    return json.loads((EXPORT_FIXTURES / name).read_text())


def fixture_bytes(name: str) -> bytes:
    """The fixture's bytes, as an upload would carry them."""
    return (EXPORT_FIXTURES / name).read_bytes()
