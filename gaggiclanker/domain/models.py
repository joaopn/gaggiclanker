"""Strict models for every document exchanged with the GaggiMate device.

Nothing here imports FastAPI or the database: the domain layer is pure Python
plus pydantic, so parsers, diagnostics and the device client can all share it.

Two rules shape the whole file:

1. **The firmware's parser wins over the firmware's schema.** Where
   `schema/profile.json` and `src/display/models/profile.h` disagree, the C++
   parser is the truth. The clearest case is `transition.target` — emitted by
   `writeProfile` on every save, absent from the schema — so a validator that
   trusted the schema would reject the device's own output.
2. **We validate more narrowly than the device parses.** The firmware silently
   drops an unknown `targets[].type` and silently reads any unrecognised
   `operator` spelling as `lte`. Both are data loss we would rather see as a
   validation error, so this model rejects them.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

# ── shared base ──────────────────────────────────────────────────────

#: Every device document is closed: an unexpected key is a firmware change we
#: have not read yet, and silently dropping it would hide that.
_STRICT = ConfigDict(extra="forbid", populate_by_name=True)


class DeviceModel(BaseModel):
    """Base for documents that travel to or from the machine."""

    model_config = _STRICT


# ── profiles ─────────────────────────────────────────────────────────

TransitionType = Literal["instant", "linear", "ease-in", "ease-out", "ease-in-out"]
TransitionTarget = Literal["time", "volumetric", "pumped"]
TargetType = Literal["volumetric", "pressure", "flow", "pumped"]
TargetOperator = Literal["gte", "lte"]
PumpTarget = Literal["pressure", "flow"]
PhaseKind = Literal["preinfusion", "brew"]
ProfileType = Literal["standard", "pro"]

#: Filesystem-safe and short enough to survive the `.slog` header's
#: `char profileId[32]` (31 chars + NUL).
PROFILE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,31}$"

#: Sentinel on `pump.pressure` / `pump.flow`: hold whatever was measured at
#: phase entry instead of driving to a fixed setpoint (BrewProcess.h:206-209).
HOLD_MEASURED = -1.0


class Transition(DeviceModel):
    """How the pump setpoint ramps into a phase."""

    type: TransitionType
    duration: float = Field(default=0.0, ge=0)
    adaptive: bool = False
    # Undocumented but always written by `writeProfile` (profile.h:363-371).
    # Absent from schema/profile.json, which is a schema bug, not a device one.
    target: TransitionTarget | None = None


class Pump(DeviceModel):
    """Closed-loop pump control — the advanced (object) form of `phase.pump`.

    `target` names the closed-loop setpoint; the other field is a soft limit
    where `0` means "no limit". `-1` on either means "hold the measured value".
    """

    target: PumpTarget
    pressure: float
    flow: float

    @field_validator("pressure")
    @classmethod
    def _check_pressure(cls, value: float) -> float:
        if value == HOLD_MEASURED:
            return value
        if not 0 <= value <= 12:
            raise ValueError("pressure must be 0..12 bar or the -1 hold sentinel")
        return value

    @field_validator("flow")
    @classmethod
    def _check_flow(cls, value: float) -> float:
        if value == HOLD_MEASURED:
            return value
        if not 0 <= value <= 15:
            raise ValueError("flow must be 0..15 g/s or the -1 hold sentinel")
        return value


#: `phase.pump` is either the simple form — an integer duty-cycle percent — or
#: the advanced :class:`Pump` object. It must stay an *integer*: the firmware
#: branches on `p["pump"].is<int>()`, so `100.0` is parsed as an advanced
#: object with every field zero and the pump never runs. StrictInt is what
#: stops a float from being coerced back into that trap.
PumpSetting = StrictInt | Pump


class Target(DeviceModel):
    """One OR-combined stop condition for a phase."""

    type: TargetType
    # The firmware's default when `operator` is present but unrecognised is
    # LTE, not GTE (profile.h:291) — a typo silently inverts the condition,
    # which is why only the two exact spellings validate here.
    operator: TargetOperator = "gte"
    value: float = Field(ge=0)


class Phase(DeviceModel):
    """One step of a profile."""

    name: str = Field(min_length=1)
    phase: PhaseKind
    valve: Literal[0, 1]
    # 300 s is the firmware-wide per-phase cap (BREW_SAFETY_DURATION_MS).
    duration: float = Field(ge=0.5, le=300)
    # 0 is the sentinel for "inherit the profile temperature".
    temperature: float = Field(default=0.0, ge=0, le=160)
    transition: Transition | None = None
    pump: PumpSetting
    targets: list[Target] = Field(default_factory=list)

    @field_validator("pump")
    @classmethod
    def _check_pump_percent(cls, value: StrictInt | Pump) -> StrictInt | Pump:
        if isinstance(value, int) and not 0 <= value <= 100:
            raise ValueError("pump percent must be 0..100")
        return value


class Profile(BaseModel):
    """A GaggiMate brew profile.

    Top-level `_`-prefixed keys are the schema's reserved user-annotation
    space; they are collected into :attr:`annotations` rather than rejected,
    so a profile carrying `_source` or `_bean` still validates.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str | None = Field(default=None, pattern=PROFILE_ID_PATTERN)
    label: str = Field(min_length=1)
    type: ProfileType
    description: str = ""
    temperature: float = Field(default=0.0, ge=0, le=150)
    favorite: bool = False
    selected: bool = False
    utility: bool = False
    phases: list[Phase] = Field(min_length=1)
    #: The `^_` annotation keys, kept so an import round-trips what it read.
    annotations: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _collect_annotations(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # `annotations` is where we *keep* the `^_` keys, not a key the device
        # or a profile author may write. Accepting it would let a hand-authored
        # profile smuggle a second annotation namespace past `extra="forbid"`,
        # and the schema allows exactly one: keys starting with an underscore.
        if "annotations" in data:
            raise ValueError(
                "`annotations` is not a profile key; "
                "user annotations use a leading underscore (_notes, _bean)"
            )
        underscored = {k: v for k, v in data.items() if isinstance(k, str) and k.startswith("_")}
        if not underscored:
            return data
        rest = {k: v for k, v in data.items() if k not in underscored}
        rest["annotations"] = underscored
        return rest

    def to_device(self) -> dict[str, Any]:
        """Serialise back to the on-wire shape, annotations re-expanded."""
        body = self.model_dump(exclude={"annotations", "id"}, exclude_none=True)
        if self.id is not None:
            body = {"id": self.id, **body}
        body.update(self.annotations)
        return body


#: What `writeProfile` emits for a phase that was authored without a transition
#: (profile.h:363-371). Indistinguishable from no transition at all, so the
#: canonical form treats the two as the same profile.
_FIRMWARE_DEFAULT_TRANSITION: dict[str, Any] = {"type": "instant", "duration": 0, "adaptive": False}


def canonical_profile_json(profile: Profile) -> str:
    """Return a stable JSON string identifying a profile *by what it brews*.

    The job of this function is to survive a trip through the machine. Send a
    profile to the device, read it back, and `writeProfile` hands you something
    that is textually different but behaviourally identical: it always emits a
    `transition` even where the author wrote none, always adds
    `transition.target` (defaulting to `"time"`), always spells out a phase
    `temperature` of `0`, and stamps its own `id`, `favorite` and `selected`.
    If any of that reached the hash, every profile would appear to change the
    first time the machine touched it and the archive would fill with phantom
    versions.

    So the canonical form drops, in order: the device-owned fields (`id`,
    `favorite`, `selected` — the firmware overwrites all three at load and the
    web UI strips them on export); user annotations, which describe a profile
    rather than being part of it; `transition.target` when it is absent or the
    `"time"` default; a `transition` that is only the firmware default; and a
    phase `temperature` of `0`, which is the sentinel for "inherit the profile
    temperature" and means exactly what leaving it out means.

    What remains is normalised — integral floats collapsed so `28.0` and `28`
    agree, keys sorted — leaving output that is byte-stable across pydantic and
    JSON round trips.
    """
    body = profile.model_dump(
        exclude={"id", "favorite", "selected", "annotations"},
        exclude_none=True,
        mode="json",
    )
    body["phases"] = [_canonical_phase(phase) for phase in body["phases"]]
    return json.dumps(
        _normalise_numbers(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _canonical_phase(phase: dict[str, Any]) -> dict[str, Any]:
    """Strip the parts of a phase that the firmware adds back for free."""
    out = dict(phase)

    # 0 and absent are the same instruction: inherit the profile temperature.
    if not out.get("temperature"):
        out.pop("temperature", None)

    transition = out.get("transition")
    if transition is not None:
        trimmed = dict(transition)
        if trimmed.get("target") in (None, "time"):
            trimmed.pop("target", None)
        if _normalise_numbers(trimmed) == _FIRMWARE_DEFAULT_TRANSITION:
            out.pop("transition", None)
        else:
            out["transition"] = trimmed

    return out


def profile_content_hash(profile: Profile) -> str:
    """sha256 of :func:`canonical_profile_json` — the profile's content identity."""
    return hashlib.sha256(canonical_profile_json(profile).encode("utf-8")).hexdigest()


def _normalise_numbers(value: Any) -> Any:
    """Collapse integral floats to ints so 28.0 and 28 hash the same."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _normalise_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise_numbers(v) for v in value]
    return value


# ── shot notes ───────────────────────────────────────────────────────

BalanceTaste = Literal["bitter", "balanced", "sour"]

_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


class ParsedNotes(BaseModel):
    """The numeric reading of :class:`ShotNotes`. Unparseable fields are None."""

    model_config = ConfigDict(extra="forbid")

    rating: int
    bean_type: str | None
    dose_in: float | None
    dose_out: float | None
    ratio: float | None
    grind_setting: str | None
    balance_taste: BalanceTaste
    notes: str
    timestamp: int | None


class ShotNotes(DeviceModel):
    """The device's `/h/<id>.json` note document.

    Open rather than closed, unlike the rest of this file: `saveNotes` stores
    whatever object it is handed, verbatim, so another client's extra key is
    already on the machine and rejecting it here would quarantine a shot over
    somebody else's field. Extras are carried through :meth:`to_device`
    untouched.

    Numeric fields are **strings** here on purpose: the web UI stores raw form
    values, and the firmware only honours `doseOut` as an override for the
    index volume when it arrives as a non-empty *string*
    (`notes["doseOut"].is<String>()`, ShotHistoryPlugin.cpp:557). Sending
    `36.5` instead of `"36.5"` silently does nothing. Use :attr:`parsed` to
    read them as numbers.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    rating: int = Field(default=0, ge=0, le=5)
    bean_type: str | None = Field(default=None, alias="beanType")
    dose_in: str = Field(default="", alias="doseIn")
    dose_out: str = Field(default="", alias="doseOut")
    ratio: str = ""
    grind_setting: str = Field(default="", alias="grindSetting")
    balance_taste: BalanceTaste = Field(default="balanced", alias="balanceTaste")
    notes: str = Field(default="", max_length=200)
    #: The firmware never sets this; a client that wants it must write it.
    timestamp: int | None = None

    @field_validator("dose_in", "dose_out", "ratio", "grind_setting", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> Any:
        """Accept a number where the UI would have written a string.

        Never lose a note to a type mismatch: another client may well have
        written `doseIn: 18`. It is normalised to the device's string form so
        what we send back is what the firmware can actually read.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return f"{value:g}"
        return value

    @property
    def parsed(self) -> ParsedNotes:
        """Numeric view of the string fields; None where empty or unparseable."""
        return ParsedNotes(
            rating=self.rating,
            bean_type=self.bean_type or None,
            dose_in=_as_float(self.dose_in),
            dose_out=_as_float(self.dose_out),
            ratio=_as_float(self.ratio),
            grind_setting=self.grind_setting or None,
            balance_taste=self.balance_taste,
            notes=self.notes,
            timestamp=self.timestamp,
        )

    def to_device(self) -> dict[str, Any]:
        """Serialise back to exactly the shape the device stores."""
        return self.model_dump(by_alias=True, exclude_none=True)


def _as_float(text: str) -> float | None:
    if not _NUMERIC.match(text.strip()):
        return None
    return float(text)


# ── shot log (.slog) ─────────────────────────────────────────────────

#: Why a phase (or the shot) ended. Codes 0-7 from shot_log_format.h; 8 is
#: emitted by BrewProcess for hold-to-flush but never made it into the header.
PHASE_EXIT_REASONS: dict[int, str] = {
    0: "Unknown",
    1: "Volumetric target",
    2: "Pressure target",
    3: "Flow target",
    4: "Pumped target",
    5: "Duration",
    6: "Safety timeout",
    7: "Aborted",
    8: "Hold released",
}


class SystemInfo(BaseModel):
    """Decoded view of a sample's `si` bitfield."""

    model_config = ConfigDict(extra="forbid")

    raw: int
    shot_started_volumetric: bool
    currently_volumetric: bool
    bluetooth_scale_connected: bool
    volumetric_available: bool
    extended_recording: bool

    @classmethod
    def from_raw(cls, raw: int) -> Self:
        return cls(
            raw=raw,
            shot_started_volumetric=bool(raw & 0x0001),
            currently_volumetric=bool(raw & 0x0002),
            bluetooth_scale_connected=bool(raw & 0x0004),
            volumetric_available=bool(raw & 0x0008),
            extended_recording=bool(raw & 0x0010),
        )


class Sample(BaseModel):
    """One recorded sample, in real units.

    A field is ``None`` when its `fieldsMask` bit was not set — that is "the
    firmware never recorded this", which is a different fact from "it recorded
    zero", and the diagnostics rely on the distinction (a Standard board
    records `cp` as a real zero, an old file may not record it at all).
    """

    model_config = ConfigDict(extra="forbid")

    t: int | None = None
    """Elapsed milliseconds since shot start."""

    tt: float | None = None  # target temperature, °C
    ct: float | None = None  # current temperature, °C
    tp: float | None = None  # target/limit pressure, bar
    cp: float | None = None  # current pressure, bar
    fl: float | None = None  # pump flow, ml/s
    tf: float | None = None  # target/limit flow, ml/s
    pf: float | None = None  # estimated puck flow, ml/s
    vf: float | None = None  # scale flow, g/s
    v: float | None = None  # scale weight, g
    ev: float | None = None  # estimated weight, g
    pr: float | None = None  # puck resistance
    si: int | None = None  # system info bitfield
    wp: float | None = None  # cumulative water pumped, ml (v7)

    phase: int | None = None
    """0-based phase number, derived from the header's transition table."""

    phase_name: str | None = None

    @property
    def system_info(self) -> SystemInfo | None:
        """Decoded `si` bits, or None when the field was not recorded."""
        return None if self.si is None else SystemInfo.from_raw(self.si)


class PhaseTransition(BaseModel):
    """A row of the `.slog` header's 12-entry transition table (v5+)."""

    model_config = ConfigDict(extra="forbid")

    sample_index: int = Field(ge=0)
    phase_number: int = Field(ge=0)
    #: Why the *previous* phase ended. Reserved padding before v6, so v5 files
    #: read 0 ("Unknown") here rather than a wrong answer.
    transition_reason: int = Field(default=0, ge=0, le=255)
    phase_name: str = ""

    @property
    def transition_reason_label(self) -> str:
        return PHASE_EXIT_REASONS.get(self.transition_reason, "Unknown")


class SlogHeader(BaseModel):
    """The `.slog` header, 128 bytes through v4 and 512 bytes from v5."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, le=255)
    #: `reserved0`: the sample size in bytes the device actually wrote. 0 in
    #: files old enough to predate the field; then it must be derived.
    sample_size: int = Field(ge=0, le=255)
    header_size: int
    sample_interval: int = Field(ge=0)
    fields_mask: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    """As written in the header. 0 means the file was never finalised."""

    duration_ms: int = Field(ge=0)
    start_epoch: int = Field(ge=0)
    profile_id: str = ""
    profile_name: str = ""
    final_weight_g: float | None = None
    transitions: list[PhaseTransition] = Field(default_factory=list)
    final_exit_reason: int = Field(default=0, ge=0, le=255)
    brew_delay_ms: int = Field(default=0, ge=0)

    @property
    def final_exit_reason_label(self) -> str:
        return PHASE_EXIT_REASONS.get(self.final_exit_reason, "Unknown")


# ── shot index (index.bin) ───────────────────────────────────────────

SHOT_FLAG_COMPLETED = 0x01
SHOT_FLAG_DELETED = 0x02
SHOT_FLAG_HAS_NOTES = 0x04


class IndexEntry(BaseModel):
    """One 128-byte row of `/h/index.bin`.

    Deletions are flag-only — the firmware never removes a row and duplicate
    ids can exist — so a consumer must filter on :attr:`deleted` itself.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    timestamp: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    volume_g: float | None = None
    rating: int = Field(default=0, ge=0, le=255)
    flags: int = Field(default=0, ge=0, le=255)
    profile_id: str = ""
    profile_name: str = ""
    #: Per-shot aggregates carved out of the former reserved padding. 0 on the
    #: wire means "this firmware did not record it", hence None rather than 0.
    avg_temp_c: float | None = None
    max_pressure_bar: float | None = None
    avg_flow_ml_s: float | None = None

    @property
    def completed(self) -> bool:
        return bool(self.flags & SHOT_FLAG_COMPLETED)

    @property
    def deleted(self) -> bool:
        return bool(self.flags & SHOT_FLAG_DELETED)

    @property
    def has_notes(self) -> bool:
        return bool(self.flags & SHOT_FLAG_HAS_NOTES)

    @property
    def incomplete(self) -> bool:
        return not self.completed


class IndexHeader(BaseModel):
    """The 32-byte `index.bin` header."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    entry_size: int = Field(ge=0)
    entry_count: int = Field(ge=0)
    next_id: int = Field(ge=0)


class ShotIndex(BaseModel):
    """A parsed `index.bin`."""

    model_config = ConfigDict(extra="forbid")

    header: IndexHeader
    entries: list[IndexEntry] = Field(default_factory=list)

    def live(self) -> list[IndexEntry]:
        """Non-deleted entries, newest first — the device UI's own ordering."""
        return sorted(
            (e for e in self.entries if not e.deleted),
            key=lambda e: e.timestamp,
            reverse=True,
        )


# ── device settings and live status ──────────────────────────────────


class OtaSettings(DeviceModel):
    """`res:ota-settings` — the machine's identity message.

    The single best "what is this device" frame: firmware versions on both
    halves plus the controller board name, which is how we learn whether a
    pressure sensor exists at all.

    Open rather than closed, unlike most of this file, and the simulator is why
    the difference was not theoretical: the real frame also carries a
    diagnostics block (`spiffs*`, `heap*`, `controllerTaskHealth`,
    `uiTaskHealth`, and `sd*` only when a card is mounted) whose membership
    depends on the build — `uiTaskHealth` is absent on headless — so a closed
    model rejected the whole frame and the device page went dark with no
    identity at all. The documented members are typed below; anything a later
    firmware adds is kept rather than fatal.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    latest_version: str | None = Field(default=None, alias="latestVersion")
    display_version: str | None = Field(default=None, alias="displayVersion")
    controller_version: str | None = Field(default=None, alias="controllerVersion")
    hardware: str | None = None
    display_update_available: bool | None = Field(default=None, alias="displayUpdateAvailable")
    controller_update_available: bool | None = Field(
        default=None, alias="controllerUpdateAvailable"
    )
    channel: str | None = None
    updating: bool | None = None

    # The diagnostics block. Typed because the hardware page wants it, all
    # optional because which of them arrives depends on the build and on
    # whether an SD card is mounted.
    spiffs_total: int | None = Field(default=None, alias="spiffsTotal")
    spiffs_used: int | None = Field(default=None, alias="spiffsUsed")
    spiffs_free: int | None = Field(default=None, alias="spiffsFree")
    spiffs_used_pct: float | None = Field(default=None, alias="spiffsUsedPct")
    heap_free: int | None = Field(default=None, alias="heapFree")
    heap_largest: int | None = Field(default=None, alias="heapLargest")
    heap_total: int | None = Field(default=None, alias="heapTotal")
    controller_task_health: bool | None = Field(default=None, alias="controllerTaskHealth")
    ui_task_health: bool | None = Field(default=None, alias="uiTaskHealth")
    sd_total: int | None = Field(default=None, alias="sdTotal")
    sd_used: int | None = Field(default=None, alias="sdUsed")
    sd_free: int | None = Field(default=None, alias="sdFree")
    sd_used_pct: float | None = Field(default=None, alias="sdUsedPct")


class ProcessStatus(DeviceModel):
    """The `process` object inside `evt:status`.

    Open for the same reason as :class:`LiveStatus`, and more urgently: this is
    the object the firmware actually grows across releases, and it is nested
    inside a frame that arrives twice a second. A closed model here would fail
    the *whole* status frame over one new key in `process`.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    a: int | None = None  # active 1/0
    s: str | None = None  # "brew" | "infusion" | "grind"
    label: str | None = Field(default=None, alias="l")
    e: int | None = None  # elapsed ms
    u: int | None = None  # utility profile
    tt: str | None = None  # "volumetric" | "time"
    pt: float | None = None  # phase target (g or ms)
    pp: float | None = None  # phase progress


class SystemStatus(DeviceModel):
    """The `sys` object inside `evt:status`. Open: see :class:`ProcessStatus`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    s: str | None = None  # starting|waiting|ready|updating|autotuning|mismatch|error
    m: str | None = None  # message
    c: int | None = None  # controller error code


class DeviceWarning(DeviceModel):
    """One entry of `warn` inside `evt:status`. Open: see :class:`ProcessStatus`.

    The firmware sends every warning type it knows about, so a new one arrives
    as a new array entry rather than a new key — but the entry's own shape has
    changed before, and one added field must not cost us the frame.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    k: str | None = None  # kind
    level: int | None = Field(default=None, alias="l")  # 0 ignore, 1 warn, 2 error
    a: bool | None = None  # active


class LiveStatus(DeviceModel):
    """The merged `evt:status` state.

    Two different frames share the `evt:status` type — a 500 ms telemetry frame
    and an on-change state frame — and each carries only part of the picture.
    The device's own UI merges them onto one object: an absent key means
    unchanged, an explicit `null` means clear. Every field is therefore
    optional, and a `LiveStatus` is only ever the result of that merge.

    Open rather than closed, unlike the rest of this file. A firmware update
    that adds one telemetry key would otherwise make *every* `evt:status` frame
    fail validation, and the live device connection would go dark over a field
    we did not need — the wrong trade for a frame that arrives twice a second.
    Unknown keys are kept so they show up in a log rather than vanishing.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # telemetry
    ct: float | None = None
    tt: float | None = None
    pr: float | None = None
    fl: float | None = None
    pt: float | None = None
    wl: int | None = None
    tof: int | None = None
    rssi: int | None = None
    lat: int | None = None
    pw: float | None = None
    hp: float | None = None
    bw: float | None = None
    cw: float | None = None
    process: ProcessStatus | None = None
    pkr: float | None = None
    pf: float | None = None
    tf: float | None = None

    # state
    m: int | None = None  # mode: 0 standby, 1 brew, 2 steam, 3 water, 4 grind
    p: str | None = None  # selected profile label
    puid: str | None = None  # selected profile id
    cp: bool | None = None  # capability: pressure sensor (Pro boards only)
    cd: bool | None = None  # capability: pump dimming
    gp: bool | None = None  # gear-pump addon
    led: bool | None = None
    tw: float | None = None
    bta: int | None = None
    bt: int | None = None
    btd: float | None = None
    gtd: int | None = None
    gtv: float | None = None
    gt: int | None = None
    gact: int | None = None
    up: bool | None = None
    sys: SystemStatus | None = None
    bc: bool | None = None
    sbat: int | None = None
    warn: list[DeviceWarning] | None = None

    @property
    def has_pressure(self) -> bool:
        """Whether pressure-derived telemetry means anything on this machine.

        Standard boards have no pressure sensor and report a hard 0 for `pr`,
        `cp` and everything derived from them. Every pressure diagnostic must
        be gated on this or it produces confident nonsense.
        """
        return bool(self.cp)
