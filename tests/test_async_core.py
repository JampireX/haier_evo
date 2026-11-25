"""The asyncio core in a real Home Assistant: setup/unload, the websocket session, commands and
their acknowledgements, the recovery paths. The cloud is faked (tests/conftest.py)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.haier_evo import api
from custom_components.haier_evo import const as C

from .conftest import LOGIN_URL, REFRESH_URL, dump_devices, setup_entry, token_answer

pytestmark = pytest.mark.usefixtures("no_frontend")


def ac_spec() -> dict:
    return next(s for s in dump_devices() if s["type"] == "AC")


def wm_spec(model: str = "HW60-BP12929AE") -> dict:
    return next(s for s in dump_devices() if s["model"] == model)


async def wait_until(predicate, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def ack(ws, message: dict, err: int = 0) -> None:
    ws.feed({"event": "command_response", "macAddress": message["macAddress"],
             "trace": message["trace"], "errNo": err})


def status_push(mac: str, properties: dict) -> dict:
    return {"event": "status", "macAddress": mac, "payload": {"statuses": [{"properties": properties}]}}


async def test_setup_entities_and_unload(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED
    haier: api.Haier = entry.runtime_data
    ws = await fake.ws.wait_for(1)
    assert ws.url.endswith("/access-1")
    await wait_until(haier._is_socket_ready)
    device = haier.devices[0]
    assert isinstance(device, api.HaierAC)
    registry = er.async_get(hass)
    entities = [e for e in registry.entities.values() if e.config_entry_id == entry.entry_id]
    platforms = {e.domain for e in entities}
    assert {"climate", "switch", "select", "sensor", "binary_sensor"} <= platforms
    # A status push reaches the entity state.
    climate = next(e.entity_id for e in entities if e.domain == "climate")
    temp_code = str(device.config["current_temperature"])
    ws.feed(status_push(spec["mac"], {temp_code: "27"}))
    await wait_until(lambda: hass.states.get(climate).attributes.get("current_temperature") == 27)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert ws.closed
    assert haier._ws_task is None and not haier._tasks


async def test_turn_on_from_off_sends_mode_after_ack(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    ws = await fake.ws.wait_for(1)
    await wait_until(haier._is_socket_ready)
    device: api.HaierAC = haier.devices[0]
    status_code = str(device.config["status"])
    ws.feed(status_push(spec["mac"], {status_code: "0"}))
    await wait_until(lambda: device.status == 0)
    climate = next(e.entity_id for e in er.async_get(hass).entities.values()
                   if e.config_entry_id == entry.entry_id and e.domain == "climate")
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": "cool"}, blocking=True)
    power = ws.operations()[-1]
    assert [c["commandName"] for c in power["commands"]][0] == status_code
    assert all(c["commandName"] != str(device.config["mode"]) for c in power["commands"])
    # The mode waits for the cloud's answer to the power command.
    await asyncio.sleep(0.05)
    assert len(ws.operations()) == 1
    ack(ws, power)
    await wait_until(lambda: len(ws.operations()) == 2)
    follow = ws.operations()[1]
    assert {"commandName": str(device.config["mode"]), "value": "1"} in follow["commands"]
    ack(ws, follow)
    await hass.async_block_till_done()
    assert hass.states.get(climate).state == "cool"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unanswered_command_is_resent_in_a_new_session_then_lost(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    with patch.object(C, "WS_CMD_ACK_TIMEOUT", 0.2), patch.object(C, "COMMAND_REJECT_REFRESH_DELAY", 0.01):
        entry = await setup_entry(hass)
        haier: api.Haier = entry.runtime_data
        ws1 = await fake.ws.wait_for(1)
        ws1.feed(status_push(spec["mac"], {"0": "22"}))  # the session is served (attached)
        await wait_until(haier._is_socket_ready)
        device: api.HaierAC = haier.devices[0]
        status_code = str(device.config["status"])
        ws1.feed(status_push(spec["mac"], {status_code: "1"}))
        await wait_until(lambda: device.status == 1)
        reads = fake.http.call_count
        await device.set_temperature(24.0)
        assert ws1.operations()[-1]["commands"] == [{"commandName": "0", "value": "24"}]
        # No answer: the session is re-established and the command sent again there.
        ws2 = await fake.ws.wait_for(2)
        assert ws1.closed
        await wait_until(lambda: ws2.operations())
        assert ws2.operations()[-1]["commands"] == [{"commandName": "0", "value": "24"}]
        # No answer to that either: the command is lost, the real state is read back.
        await wait_until(lambda: fake.http.call_count > reads, timeout=5)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_rejected_command_reads_the_real_state(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    with patch.object(C, "COMMAND_REJECT_REFRESH_DELAY", 0.01):
        entry = await setup_entry(hass)
        haier: api.Haier = entry.runtime_data
        ws = await fake.ws.wait_for(1)
        await wait_until(haier._is_socket_ready)
        device: api.HaierAC = haier.devices[0]
        ws.feed(status_push(spec["mac"], {str(device.config["status"]): "1"}))
        await wait_until(lambda: device.status == 1)
        before = device.target_temperature
        reads = fake.http.call_count
        await device.set_temperature(before + 1)
        assert device.target_temperature == before + 1  # optimistic
        ack(ws, ws.operations()[-1], err=-1)
        await wait_until(lambda: fake.http.call_count > reads)
        await wait_until(lambda: device.target_temperature == before)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_silent_session_is_replaced_and_backs_off(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    # (A token younger than TOKEN_REJECT_MIN_AGE is not refreshed: real silent sessions take
    # minutes, these 0.1 s.)
    with patch.object(C, "WS_SILENT_SESSION_TIMEOUT", 0.1), patch.object(C, "TOKEN_REJECT_MIN_AGE", 0):
        entry = await setup_entry(hass)
        haier: api.Haier = entry.runtime_data
        await fake.ws.wait_for(1)
        # Nothing comes over the sessions: each is dropped, the 2nd with a fresh token.
        ws3 = await fake.ws.wait_for(3, timeout=5)
        assert haier._silent_sessions >= 2
        assert any(str(call[0]).upper() == "POST" and str(call[1]) == REFRESH_URL for call in fake.http.mock_calls)
        # A session that is served resets the count.
        ws3.feed(status_push(spec["mac"], {"0": "23"}))
        await wait_until(lambda: haier._silent_sessions == 0)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_close_1007_renews_the_token(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    with patch.object(C, "TOKEN_REJECT_MIN_AGE", 0):
        entry = await setup_entry(hass)
        haier: api.Haier = entry.runtime_data
        ws1 = await fake.ws.wait_for(1)
        await wait_until(haier._is_socket_ready)
        ws1.server_close(1007, "Auth token not valid!")
        ws2 = await fake.ws.wait_for(2)
        assert ws2.url.endswith("/access-2")
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_rest_401_renews_the_token_and_retries(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    with patch.object(C, "TOKEN_REJECT_MIN_AGE", 0):
        entry = await setup_entry(hass)
        haier: api.Haier = entry.runtime_data
        await wait_until(haier._is_socket_ready)
        device = haier.devices[0]
        status_url = C.API_STATUS.format(mac=spec["mac"])
        fake.http.clear_requests()
        fake.http.post(REFRESH_URL, json=token_answer("access-2", "refresh-2"))
        answers = iter([401, 200])

        async def status(method, url, data):
            from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
            code = next(answers)
            return AiohttpClientMockResponse(method, url, status=code, json=spec["backend_data"] if code == 200 else {})

        fake.http.get(status_url, side_effect=status)
        await device.async_refresh()
        assert haier.token == "access-2"
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_bad_password_starts_reauth(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    fake.http.clear_requests()
    fake.http.post(LOGIN_URL, json={"error": {"validation": {"password": "Введен неверный email или пароль"}}})
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())


async def test_cloud_429_retries_setup_later(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    fake.http.clear_requests()
    fake.http.post(LOGIN_URL, status=429)
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_washing_machine_is_polled_while_on(hass, cloud) -> None:
    spec = wm_spec()
    fake = cloud([spec])
    with patch.object(C, "WM_ACTIVE_POLL_INTERVAL", 0.05):
        entry = await setup_entry(hass)
        haier: api.Haier = entry.runtime_data
        await wait_until(haier._is_socket_ready)
        device: api.HaierWM = haier.devices[0]
        device.status = "on"
        device._cycle_seen_at = 0
        reads = fake.http.call_count
        assert haier.wants_laundry_poll()
        await haier.async_poll_active_laundry()
        assert fake.http.call_count == reads + 1
        assert await hass.config_entries.async_unload(entry.entry_id)
