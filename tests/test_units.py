"""Unit-level checks of helpers and guards (no cloud needed, or the fake one)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.haier_evo as integration
from custom_components.haier_evo import api
from custom_components.haier_evo import config as CFG
from custom_components.haier_evo import const as C

from .conftest import FakeHub, dump_devices, make_device
from .test_async_core import status_push


def test_parse_expire() -> None:
    p = api.parse_expire
    assert p("2026-11-25T15:04:05+0300") == datetime(2026, 11, 25, 15, 4, 5, tzinfo=timezone(timedelta(hours=3)))
    assert p("2026-11-25T15:04:05+03:00") and p("2026-11-25T15:04:05.123Z")
    assert p("2026-11-25T15:04:05").tzinfo is not None
    assert p("9999-99-99T00:00:00+0300") is None and p(12345) is None and p("") is None and p(None) is None


def test_number_text() -> None:
    assert api.number_text(23.0) == "23" and api.number_text(23.5) == "23.5" and api.number_text("24") == "24"


@pytest.mark.parametrize("model", ["../../x", "a\x00b", "..", "a b", ""])
def test_unsafe_model_names_use_the_default_description(tmp_path, model) -> None:
    config = CFG.HaierDeviceConfig.__new__(CFG.HaierDeviceConfig)
    config._model, config._userpath = model, str(tmp_path / "haier_evo")
    assert config._find_config_file().endswith("default.yaml")
    assert not any(tmp_path.iterdir())


async def test_rest_read_does_not_revert_a_newer_push(hass) -> None:
    spec = next(s for s in dump_devices() if s["type"] == "AC")
    hub = FakeHub(hass, {spec["mac"]: spec["backend_data"]})
    device = await make_device(hass, hub, spec)
    code = str(device.config["target_temperature"])
    rest_value = next(a.current for a in device.config.attrs if str(a.code) == code)
    pushed = str(int(float(rest_value)) + 3)

    async def pull_with_a_push_meanwhile(mac):
        data = hub.pull_device_data(mac)
        device.on_message(status_push(spec["mac"], {code: pushed}))
        return data

    hub.async_pull_device_data = pull_with_a_push_meanwhile
    await device.async_refresh()
    assert device.target_temperature == float(pushed)
    hub.async_pull_device_data = lambda mac: _value(hub.pull_device_data(mac))
    await device.async_refresh()  # nothing pushed meanwhile: the REST value applies
    assert device.target_temperature == float(rest_value)


async def _value(data):
    return data


async def test_washing_machine_poll_timing(hass) -> None:
    spec = next(s for s in dump_devices() if s["model"] == "HW60-BP12929AE")
    hub = FakeHub(hass, {spec["mac"]: spec["backend_data"]})
    device: api.HaierWM = await make_device(hass, hub, spec)
    device.status = "on"
    now = time.monotonic()
    assert not device.needs_poll(now)  # just read over REST
    assert device.needs_poll(now + C.WM_ACTIVE_POLL_INTERVAL)
    phase = device._phase_attr
    device.on_message(status_push(spec["mac"], {phase: "3"}))  # a push of the cycle postpones it
    assert not device.needs_poll(time.monotonic() + C.WM_ACTIVE_POLL_INTERVAL - 2)
    device.status = "off"
    assert not device.needs_poll(now + 3600)
    device._poll_requested = True  # a program event: read even when off
    assert device.needs_poll(now)


async def test_missing_devices_retry_the_setup_three_times(hass) -> None:
    entry = MockConfigEntry(domain=C.DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    for mac in ("aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"):
        registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(C.DOMAIN, mac)})
    hub = SimpleNamespace(listed_macs={"aa:aa:aa:aa:aa:01"})
    for attempt in (1, 2, 3):
        with pytest.raises(api.InvalidDevicesList, match=f"attempt {attempt}/3"):
            integration._check_complete(hass, entry, hub)
    integration._check_complete(hass, entry, hub)  # the 4th starts without it
    integration._check_complete(hass, entry, hub)  # and does not wait for it again
    assert integration._GIVEN_UP[entry.entry_id] == {"aa:aa:aa:aa:aa:02"}
    integration._GIVEN_UP.clear()


async def test_device_removal(hass) -> None:
    entry = MockConfigEntry(domain=C.DOMAIN, data={})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    class Hub(api.Haier):
        def __init__(self):
            pass

    hub = Hub()
    hub.devices = [SimpleNamespace(device_id="aa:aa:aa:aa:aa:01")]
    hub._pending_devices = []
    entry.runtime_data = hub
    device = lambda ident: SimpleNamespace(identifiers={(C.DOMAIN, ident)})  # noqa: E731
    assert await integration.async_remove_config_entry_device(hass, entry, device("aa:aa:aa:aa:aa:02"))
    assert not await integration.async_remove_config_entry_device(hass, entry, device("aa:aa:aa:aa:aa:01"))
    assert not await integration.async_remove_config_entry_device(hass, entry, device("haier_evo_http_switch_x"))
