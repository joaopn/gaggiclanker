"""What can go wrong when talking to the machine, and whether to try again.

Every failure here answers one question for the caller: **is it worth
retrying?** The sync engine runs unattended, so "the device is mid-OTA"
and "the profile does not exist" have to be different exceptions or the loop
either gives up on a shot it could have had, or spins forever on one it cannot.

These are :class:`~gaggiclanker.infra.errors.AppError` subclasses so a route
that calls the device straight through lands in the response envelope with a
sensible status, without a translation layer in between.
"""

from __future__ import annotations

from typing import Any

from gaggiclanker.infra.errors import AppError

__all__ = [
    "DeviceBusy",
    "DeviceError",
    "DeviceProtocolError",
    "DeviceTimeout",
    "DeviceUnavailable",
]

#: The exact error string the firmware answers every `req:profiles:*` and
#: `req:history*` with while an update is running (`WebSocketHandler.cpp:173`).
#: Matched case-insensitively as a substring, because it is the only signal —
#: there is no code, and the frame is otherwise a normal `res:*`.
OTA_ERROR_TEXT = "update in progress"


class DeviceError(AppError):
    """The machine answered, and the answer was a refusal.

    A `res:*` frame carrying `error`, or an HTTP status the firmware chose.
    Not retryable by default: the device understood the request and said no.
    """

    status = 502
    code = "DEVICE_ERROR"
    #: Whether a caller should come back later. Read by the sync loop.
    retryable = False

    def __init__(self, message: str, *, details: Any = None, **kwargs: Any) -> None:
        super().__init__(message, details=details, **kwargs)


class DeviceUnavailable(DeviceError):
    """There is no connection to send the request on.

    Raised rather than queued. A queued request would be answered minutes later
    against a machine whose state has moved on, and the caller would have no
    way to tell that from a slow reply — so the socket being down is a fact the
    caller gets immediately and can decide about.
    """

    status = 503
    code = "DEVICE_UNAVAILABLE"
    retryable = True

    def __init__(self, message: str = "The machine is not connected", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class DeviceBusy(DeviceError):
    """The machine is mid-OTA, or otherwise temporarily refusing.

    Every `/api/history/*` request answers `503 Update in progress` during an
    update, and every `req:history*`/`req:profiles:*` frame comes back with
    `error: "Update in progress"`. Both mean *wait*, not *fail*: an update takes
    a couple of minutes and the shot is still there afterwards.
    """

    status = 503
    code = "DEVICE_BUSY"
    retryable = True

    def __init__(
        self, message: str = "The machine is busy (update in progress)", **kwargs: Any
    ) -> None:
        super().__init__(message, **kwargs)


class DeviceProtocolError(DeviceError):
    """The machine answered with something that is not what we asked for.

    Almost always the SPA: `ESPAsyncWebServer` serves the embedded web UI for
    any path it does not recognise, and `/api/history` has been seen answering
    HTML under memory pressure. A 200 whose body is an HTML page is therefore a
    transient *device* failure, not a missing file — retryable, and worth
    saying out loud, because "expected binary, got HTML" is the one message
    that points at the real cause.
    """

    status = 502
    code = "DEVICE_PROTOCOL_ERROR"
    retryable = True


class DeviceTimeout(DeviceError):
    """A request was sent and nothing came back in time.

    Distinct from :class:`DeviceUnavailable`: the socket was up, so the machine
    is reachable but not answering — usually heap pressure while it serves the
    browser UI as well.
    """

    status = 504
    code = "DEVICE_TIMEOUT"
    retryable = True

    def __init__(self, message: str = "The machine did not answer in time", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


def classify_device_error(message: str, *, details: Any = None) -> DeviceError:
    """Turn a `res:*` `error` string into the right exception.

    One string, `"Update in progress"`, is the difference between "come back in
    two minutes" and "this will never work"; everything else is a real refusal.
    """
    if OTA_ERROR_TEXT in message.lower():
        return DeviceBusy(message, details=details)
    return DeviceError(message, details=details)
