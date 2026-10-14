"""``scripts/profile_gate.py`` against the fake machine: it makes files of its own and never
overwrites one it did not make, whatever the document it is given says about an id."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from gaggiclanker.device.client import GaggimateClient
from gaggiclanker.device.fake import FakeDevice
from gaggiclanker.domain.profile_policy import DEFAULT_BOUNDS

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "profile_gate.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("profile_gate_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_gate_saves_a_file_of_its_own_and_never_overwrites_one_it_did_not_make(
    fake_device: FakeDevice, tmp_path: Path
) -> None:
    gate = load_script()
    before = copy.deepcopy(fake_device.profiles)
    favorites = set(fake_device.favorite_profile_ids)
    # A document that names an existing file's id and label: a gate that sent it as it stands
    # would overwrite the machine's own profile.
    document = copy.deepcopy(next(p for p in fake_device.profiles if p["id"] == "9bar"))
    document["label"] = "Would have been overwritten"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(document))
    client = GaggimateClient(fake_device.address, timeout=5.0, write_gate=gate.AllowEverything())
    await client.start()
    try:
        assert await client.wait_connected(5.0)
        fake_device.ws_requests.clear()

        passed = await gate.gate_one(path, client, fake_device.address, DEFAULT_BOUNDS, False)
    finally:
        await client.stop()

    assert passed
    assert fake_device.profiles == before, "every file is as it was; the gate's own is gone"
    assert fake_device.favorite_profile_ids == favorites
    assert fake_device.ws_requests.count("req:profiles:save") == 1
    assert fake_device.ws_requests.count("req:profiles:delete") == 1
