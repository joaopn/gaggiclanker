"""The `machines` table: the one machine this archive belongs to.

One row, `id = 1`, enforced by a CHECK. It describes whatever host is configured
*now*, and the host is a setting rather than an identity: the firmware reports
no serial number — the whole of `res:ota-settings` is versions, a hardware
string and free space — so keying on the address meant that a display board that
changed IP became a second machine, taking its shots, profiles and Sets with it.
Nothing merged them back.

So connecting to a new address updates this row. Nothing else in the archive
carries a machine id; the analyzer, the starting point and the Device page all
ask for "the machine" and get it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gaggiclanker.db.repos.base import JsonObject, JsonText, dumps, utc_now
from gaggiclanker.db.repository import Repository
from gaggiclanker.domain.models import LiveStatus, OtaSettings

__all__ = ["MACHINE_ID", "MachineRepository", "MachineRow", "MachineUpsert", "identity_to_upsert"]


#: The singleton's primary key. `machines` carries `CHECK (id = 1)`, so this is
#: the whole of "which machine" and it never has to be passed anywhere.
MACHINE_ID = 1


class MachineUpsert(BaseModel):
    """What a connect knows about the machine. Every field but the host is optional.

    Optional because the three sources arrive separately and at different times:
    the host is configuration, the versions come from the identity broadcast,
    and the capability flags only exist on the status *state* frame, which the
    device sends once per connection. A field left ``None`` keeps whatever is
    already stored rather than blanking it — a reconnect that happens to catch
    no state frame must not erase the board's capabilities.
    """

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    name: str | None = None
    hardware_string: str | None = None
    display_version: str | None = None
    controller_version: str | None = None
    has_pressure: bool | None = None
    has_dimming: bool | None = None
    has_gear_pump: bool | None = None
    has_led: bool | None = None
    temperature_offset_c: float | None = None
    pid: str | None = None
    brew_delay_ms: int | None = None
    identity_json: JsonText | None = None
    settings_json: JsonText | None = None


class MachineRow(BaseModel):
    """One row of `machines`, as read back.

    The two JSON columns come out decoded (see :data:`~gaggiclanker.db.repos.base.JsonObject`):
    this model is what `GET /api/machine` answers with, and a JSON string
    nested inside a JSON body is a parse every consumer would have to repeat.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    host: str
    name: str = ""
    hardware_string: str | None = None
    display_version: str | None = None
    controller_version: str | None = None
    has_pressure: bool = False
    has_dimming: bool = False
    has_gear_pump: bool = False
    has_led: bool = False
    temperature_offset_c: float | None = None
    pid: str | None = None
    brew_delay_ms: int | None = None
    #: `res:ota-settings` verbatim, as the machine sent it.
    identity: JsonObject = Field(default=None, validation_alias="identity_json")
    #: `GET /api/settings` verbatim. Read-only here and always will be: the POST
    #: counterpart clears every boolean key absent from its body.
    settings: JsonObject = Field(default=None, validation_alias="settings_json")
    notes: str = ""
    first_seen_at: str
    last_seen_at: str
    created_at: str


#: Columns the upsert may overwrite, in the order the UPDATE builds them.
_UPDATABLE = (
    "name",
    "hardware_string",
    "display_version",
    "controller_version",
    "has_pressure",
    "has_dimming",
    "has_gear_pump",
    "has_led",
    "temperature_offset_c",
    "pid",
    "brew_delay_ms",
    "identity_json",
    "settings_json",
)

#: The SQL default for the columns that are NOT NULL in the schema.
#:
#: An explicit NULL *overrides* a column DEFAULT rather than falling back to it,
#: so a first upsert that knows only the host — which is every first upsert,
#: because the capability flags arrive on a status frame we may not have seen —
#: would fail the NOT NULL constraint without this.
_INSERT_DEFAULTS: dict[str, str] = {
    "name": "''",
    "has_pressure": "0",
    "has_dimming": "0",
    "has_gear_pump": "0",
    "has_led": "0",
}


def _insert_placeholder(column: str) -> str:
    default = _INSERT_DEFAULTS.get(column)
    return f":{column}" if default is None else f"COALESCE(:{column}, {default})"


class MachineRepository(Repository):
    """Reads and writes the one row of `machines`."""

    async def get(self) -> MachineRow | None:
        """The machine. ``None`` only on a database older than 0016 ran on."""
        row = await self.db.fetch_one("SELECT * FROM machines WHERE id = ?", (MACHINE_ID,))
        return self.to_model(MachineRow, row)

    async def update_identity(self, machine: MachineUpsert) -> MachineRow:
        """Store what a connect learned, the host included.

        The host is on this path deliberately: pointing the container at a new
        address updates the one row rather than inserting a second machine, so
        an archive cannot split when a display board moves.

        ``COALESCE(excluded.x, machines.x)`` on every other column is the "None
        keeps the stored value" rule from :class:`MachineUpsert`, expressed where
        it cannot be forgotten — a reconnect that happened to catch no state
        frame must not blank the board's capabilities.

        The INSERT half only fires on a database whose singleton row somehow went
        missing; 0016 guarantees one exists on every install, fresh or upgraded.
        """
        values: dict[str, Any] = {"id": MACHINE_ID, "host": machine.host, "now": utc_now()}
        for column in _UPDATABLE:
            values[column] = getattr(machine, column)

        assignments = ", ".join(
            f"{column} = COALESCE(:{column}, machines.{column})" for column in _UPDATABLE
        )
        columns = ", ".join(_UPDATABLE)
        placeholders = ", ".join(_insert_placeholder(column) for column in _UPDATABLE)
        await self.db.execute(
            f"""
            INSERT INTO machines (id, host, {columns}, first_seen_at, last_seen_at, created_at)
            VALUES (:id, :host, {placeholders}, :now, :now, :now)
            ON CONFLICT(id) DO UPDATE SET
                host = :host, {assignments}, last_seen_at = :now
            """,  # noqa: S608 - column names are the module constant above, never input
            values,
        )
        row = await self.get()
        if row is None:  # pragma: no cover - the upsert above guarantees it
            raise RuntimeError("the machine row vanished between write and read")
        return row

    async def update_editable(
        self, *, name: str | None = None, notes: str | None = None
    ) -> MachineRow | None:
        """Change the two fields a person owns on this row.

        Everything else here is the machine's own account of itself and is
        rewritten by the next sync pass, so an editable `hardware_string` would
        be a field that silently reverts. `name` and `notes` are the exceptions:
        the firmware has no concept of either, so nothing overwrites them and
        "the kitchen one" is a better label than an IP address.

        ``None`` means "leave it alone", matching :class:`MachineUpsert`.
        """
        assignments = []
        values: dict[str, Any] = {"id": MACHINE_ID}
        if name is not None:
            assignments.append("name = :name")
            values["name"] = name
        if notes is not None:
            assignments.append("notes = :notes")
            values["notes"] = notes
        if assignments:
            cursor = await self.db.execute(
                f"UPDATE machines SET {', '.join(assignments)} WHERE id = :id",  # noqa: S608 - the assignments are the literals above
                values,
            )
            if cursor.rowcount == 0:
                return None
        return await self.get()

    async def touch(self) -> None:
        """Record that we have just heard from the machine."""
        await self.db.execute(
            "UPDATE machines SET last_seen_at = ? WHERE id = ?", (utc_now(), MACHINE_ID)
        )


def identity_to_upsert(
    host: str,
    identity: OtaSettings | None = None,
    status: LiveStatus | None = None,
    settings: dict[str, Any] | None = None,
) -> MachineUpsert:
    """Fold `res:ota-settings`, the status state frame and `GET /api/settings` into one upsert.

    Three sources, three different shapes, one row:

    * ``identity`` (:class:`~gaggiclanker.domain.models.OtaSettings`) has the
      hardware string and the two firmware versions;
    * ``status`` (:class:`~gaggiclanker.domain.models.LiveStatus`) carries the
      capability flags ``cp``/``cd``/``gp``/``led``, and only on the state frame
      — the 2 Hz telemetry frame omits them, which the merge in
      ``device/events.py`` is what makes them survive;
    * ``settings`` is the machine's own `GET /api/settings` body, where the
      temperature offset, the PID string and the brew delay live.

    Everything absent stays ``None`` and therefore keeps whatever is stored.
    """
    upsert = MachineUpsert(host=host)
    if identity is not None:
        upsert.hardware_string = identity.hardware
        upsert.display_version = identity.display_version
        upsert.controller_version = identity.controller_version
        upsert.identity_json = dumps(identity.model_dump(by_alias=True, mode="json"))
    if status is not None:
        upsert.has_pressure = status.cp
        upsert.has_dimming = status.cd
        upsert.has_gear_pump = status.gp
        upsert.has_led = status.led
    if settings:
        upsert.settings_json = dumps(settings)
        upsert.temperature_offset_c = _as_float(settings.get("temperatureOffset"))
        pid = settings.get("pid")
        upsert.pid = str(pid) if pid is not None else None
        upsert.brew_delay_ms = _as_int(settings.get("brewDelay"))
    return upsert


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
