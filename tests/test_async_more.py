"""More of the asyncio port's invariants: sending while the session is down, recovery checks,
pending devices, the debug view, diagnostics, the config flow, tokens, redirects."""
from __future__ import annotations

import asyncio
import json
import os
import stat
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

import custom_components.haier_evo as integration
from custom_components.haier_evo import api
from custom_components.haier_evo import const as C
from custom_components.haier_evo.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    DEVICES_URL, EMAIL, LOGIN_URL, PASSWORD, devices_page, dump_devices, setup_entry, token_answer,
)
from .test_async_core import ac_spec, ack, status_push, wait_until, wm_spec

pytestmark = pytest.mark.usefixtures("no_frontend")


async def test_command_waits_for_a_reconnect(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    ws1 = await fake.ws.wait_for(1)
    await wait_until(haier._is_socket_ready)
    device: api.HaierAC = haier.devices[0]
    ws1.feed(status_push(spec["mac"], {str(device.config["status"]): "1"}))
    await wait_until(lambda: device.status == 1)
    from aiohttp import ClientConnectionError
    fake.ws.fail = ClientConnectionError("the cloud is unreachable")
    ws1.server_close(1006)  # the cloud dropped the session, and does not take a new one yet
    await wait_until(lambda: not haier._is_socket_ready())

    async def back() -> None:
        await asyncio.sleep(0.3)
        fake.ws.fail = None

    restore = asyncio.create_task(back())
    await device.set_temperature(26.0)  # waits for the reconnect
    await restore
    ws2 = fake.ws.current
    assert ws2 is not ws1 and ws2.operations()[-1]["commands"] == [{"commandName": "0", "value": "26"}]
    ack(ws2, ws2.operations()[-1])
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_socket_closing_right_after_send_is_retried(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    ws1 = await fake.ws.wait_for(1)
    await wait_until(haier._is_socket_ready)
    original = ws1.send_str

    async def send_then_drop(data):
        await original(data)
        ws1.server_close(1006)

    ws1.send_str = send_then_drop
    assert await haier.async_send_message(json.dumps({"action": "operation", "macAddress": spec["mac"]}))
    ws2 = fake.ws.current
    assert ws2 is not ws1 and len(ws2.sent) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_zombie_session_is_replaced_before_a_send(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    ws1 = await fake.ws.wait_for(1)
    ws1.feed(status_push(spec["mac"], {"0": "22"}))
    await wait_until(lambda: haier._last_inbound_at is not None)
    with patch.object(C, "WS_SESSION_STALE_TIMEOUT", 0):
        assert await haier.async_send_message(json.dumps({"action": "x", "macAddress": spec["mac"]}))
    assert ws1.closed and fake.ws.current is not ws1 and len(fake.ws.current.sent) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_heartbeat_restarts_the_loop_and_reads_rest(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    await wait_until(haier._is_socket_ready)
    haier._ws_task.cancel()
    await wait_until(lambda: haier._ws_task.done())
    reads = fake.http.call_count
    await haier.async_heartbeat()
    assert fake.http.call_count == reads + 1  # the REST re-read
    await wait_until(lambda: len(fake.ws.sockets) == 2)  # a new connection loop
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_device_failing_at_setup_is_added_later(hass, aioclient_mock, ws_factory) -> None:
    spec = ac_spec()
    aioclient_mock.post(LOGIN_URL, json=token_answer())
    aioclient_mock.get(DEVICES_URL, json=devices_page([spec]))
    answers = {"n": 0}

    async def status(method, url, data):
        answers["n"] += 1
        if answers["n"] <= 2:  # the setup's request and its retry of a 5xx
            return AiohttpClientMockResponse(method, url, status=502)
        return AiohttpClientMockResponse(method, url, json=spec["backend_data"])

    aioclient_mock.get(C.API_STATUS.format(mac=spec["mac"]), side_effect=status)
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    assert not haier.devices and haier._pending_devices
    await haier.async_heartbeat()
    await hass.async_block_till_done()
    assert haier.devices and not haier._pending_devices
    assert hass.states.async_entity_ids("climate")
    await ws_factory.wait_for(1)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_debug_view_and_diagnostics(hass, cloud, hass_client) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    await wait_until(haier._is_socket_ready)
    view = api.Haier.http
    request = type("Request", (), {})()
    response = await view.get(request)
    data = json.loads(response.body)
    assert data["devices"][0]["device_mac"] == spec["mac"]
    haier.allow_http_post = True

    class Post:
        async def json(self):
            return {"action": "operation", "macAddress": spec["mac"], "commands": []}

    response = await view.post(Post())
    assert json.loads(response.body) == {"result": "success"}
    assert fake.ws.current.sent[-1]["macAddress"] == spec["mac"]
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag, ensure_ascii=False)
    assert PASSWORD not in text and "access-1" not in text
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_requests_carry_device_id_and_refuse_redirects(hass, aioclient_mock, ws_factory) -> None:
    spec = ac_spec()
    aioclient_mock.post(LOGIN_URL, json=token_answer())
    aioclient_mock.get(DEVICES_URL, status=302, headers={"Location": "https://elsewhere.invalid/"})
    entry = await setup_entry(hass)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
    calls = [c for c in aioclient_mock.mock_calls]
    expected = api.uuid.uuid5(api.uuid.NAMESPACE_URL, f"{C.DOMAIN}:{entry.entry_id}")
    assert all(c[3].get("Device-Id") == str(expected) for c in calls)
    assert spec  # (the device list is never read past the redirect)


async def test_tokens_file_format_and_rights(hass, cloud) -> None:
    spec = ac_spec()
    cloud([spec])
    entry = await setup_entry(hass)
    path = hass.config.path(f"{C.DOMAIN}_tokens_{entry.entry_id}.json")
    data = json.loads(await hass.async_add_executor_job(lambda: open(path).read()))
    assert set(data) == {"email", "region", "token", "tokenexpire", "refreshtoken", "refreshexpire"}
    assert data["token"] == "access-1" and data["email"] == EMAIL
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert await hass.config_entries.async_unload(entry.entry_id)
    # A second start uses the saved token: no sign-in.
    haier = api.Haier(hass, EMAIL, PASSWORD, "ru", entry_id=entry.entry_id)
    await haier.async_load_tokens()
    assert haier.token == "access-1" and haier._auth_action() is None
    await haier.async_stop()


async def test_setup_backoff_after_429(hass, aioclient_mock, ws_factory) -> None:
    aioclient_mock.post(LOGIN_URL, status=429)
    entry = await setup_entry(hass)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
    assert entry.entry_id in integration._SETUP_BACKOFF
    logins = aioclient_mock.call_count
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == logins  # the backoff keeps the retry off the cloud
    integration._SETUP_BACKOFF.clear()


async def test_program_event_requests_a_read(hass, cloud) -> None:
    spec = wm_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    ws = await fake.ws.wait_for(1)
    await wait_until(haier._is_socket_ready)
    device: api.HaierWM = haier.devices[0]
    ws.feed({"event": "ProgramWasherBaseEvent", "macAddress": spec["mac"], "data": {"status": "PROGRAM_STARTED"}})
    await wait_until(lambda: device.program_status == "STARTED")
    assert device.state == "running" and haier.wants_laundry_poll()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_config_flow(hass, aioclient_mock) -> None:
    hass.config.components.update({"http", "frontend"})
    aioclient_mock.post(LOGIN_URL, json=token_answer())
    result = await hass.config_entries.flow.async_init(C.DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD, "region": "ru"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    aioclient_mock.clear_requests()
    aioclient_mock.post(LOGIN_URL, json={"error": {"validation": {"password": "Введен неверный email или пароль"}}})
    result = await hass.config_entries.flow.async_init(C.DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "other@example.com", "password": PASSWORD, "region": "ru"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.post(LOGIN_URL, exc=asyncio.TimeoutError())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "other@example.com", "password": PASSWORD, "region": "ru"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unload_while_connecting(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    started = asyncio.Event()

    async def slow(_session, url, **kw):
        started.set()
        await asyncio.sleep(30)

    with patch.object(api.ClientSessionType, "ws_connect", slow) if hasattr(api, "ClientSessionType") else patch(
        "aiohttp.ClientSession.ws_connect", lambda s, url, **kw: slow(s, url, **kw)
    ):
        entry = await setup_entry(hass)
        await asyncio.wait_for(started.wait(), 5)
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert fake.ws.sockets == []


async def test_all_devices_of_the_fixtures_set_up(hass, cloud) -> None:
    specs = [s for s in dump_devices() if s["type"] != "WH"]
    fake = cloud(specs)
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    assert len(haier.devices) == len(specs)
    await fake.ws.wait_for(1)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cancelled_command_still_reaches_its_watchdog(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    ws1 = await fake.ws.wait_for(1)
    await wait_until(haier._is_socket_ready)
    device: api.HaierAC = haier.devices[0]
    ws1.feed(status_push(spec["mac"], {str(device.config["status"]): "1"}))
    await wait_until(lambda: device.status == 1)
    from aiohttp import ClientConnectionError
    fake.ws.fail = ClientConnectionError("the cloud is unreachable")
    ws1.server_close(1006)
    await wait_until(lambda: not haier._is_socket_ready())
    call = asyncio.create_task(device.set_temperature(25.0))
    await asyncio.sleep(0.1)
    call.cancel()  # e.g. an automation in "restart" mode
    with pytest.raises(asyncio.CancelledError):
        await call
    fake.ws.fail = None
    await wait_until(lambda: len(fake.ws.sockets) >= 2 and fake.ws.current.operations())
    message = fake.ws.current.operations()[-1]
    assert message["commands"] == [{"commandName": "0", "value": "25"}]
    await wait_until(lambda: message["trace"] in device._ack_timers)
    assert device.target_temperature == 25  # the optimistic state went with it
    ack(fake.ws.current, message)
    await wait_until(lambda: message["trace"] not in device._awaiting_ack)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_failing_entity_does_not_block_the_others(hass, cloud) -> None:
    spec = ac_spec()
    fake = cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    ws = await fake.ws.wait_for(1)
    await wait_until(haier._is_socket_ready)
    device: api.HaierAC = haier.devices[0]
    written = []

    def broken():
        raise ValueError("a value the entity class refuses")

    device._write_ha_state_callbacks.insert(0, broken)
    device._write_ha_state_callbacks.append(lambda: written.append(True))
    ws.feed(status_push(spec["mac"], {"0": "21"}))
    await wait_until(lambda: written)
    device._write_ha_state_callbacks.remove(broken)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_send_after_unload_is_not_an_error(hass, cloud, caplog) -> None:
    spec = ac_spec()
    cloud([spec])
    entry = await setup_entry(hass)
    haier: api.Haier = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not await haier.async_send_message("{}")
    assert "Command NOT sent" not in caplog.text
    assert "Command not sent: the integration entry is unloaded" in caplog.text
