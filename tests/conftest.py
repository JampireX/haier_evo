"""Shared fixtures: real (anonymized) cloud descriptions and a stand-in for the hub.

The device-level helpers work with the threaded implementation (0.3.28) and with the asyncio one,
so the snapshot of the integration's behaviour (tests/test_snapshot.py) can be compared across
the port.
"""
from __future__ import annotations

import copy
import inspect
import json
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from custom_components.haier_evo import api

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def dump_devices() -> list[dict]:
    """Every device description of the fixtures: {mac, title, type, backend_data}."""
    devices = []
    for name in ("evo_dump.json", "laundry_dump.json", "hw70_dump.json", "ref_dump.json"):
        for item in load_fixture(name)["devices"]:
            data = item["backend_data"]
            inner = data.get("smartDeviceControl") if isinstance(data.get("smartDeviceControl"), dict) else data
            model = str((inner.get("info") or {}).get("model") or "")
            kind = "AC" if model.startswith("AS") else "REF" if model.startswith("C4F") else (
                "WH" if model.startswith("ES") else "WM")
            devices.append({
                "mac": item["device_id"], "title": item.get("device_name") or model,
                "type": kind, "model": model, "backend_data": data,
            })
    return devices


class Done:
    """An awaitable no-op: a recorder's result works whether the caller awaits it or not."""

    def __init__(self, value: Any = None) -> None:
        self.value = value

    def __await__(self):
        if False:  # pragma: no cover - makes this a generator
            yield
        return self.value


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class FakeHub:
    """What a device needs of the hub (api.Haier) to be built and to compose commands."""

    def __init__(self, hass, descriptions: dict[str, dict]) -> None:
        self.hass = hass
        self.descriptions = descriptions
        self.disconnect_requested = False
        self.devices: list = []
        self._connected_at = None
        self._last_inbound_at = None
        self.reads = 0

    def pull_device_data(self, mac: str) -> dict:
        self.reads += 1
        return copy.deepcopy(self.descriptions[mac])

    async def async_pull_device_data(self, mac: str) -> dict:
        return self.pull_device_data(mac)


async def make_device(hass, hub: FakeHub, spec: dict):
    """Builds a device the way the hub does (the asyncio factory when there is one)."""
    kwargs = dict(
        haier=hub, device_type=spec["type"], device_mac=spec["mac"],
        device_serial=None, device_title=spec["title"],
    )
    factory = getattr(api.HaierDevice, "async_create", None)
    if factory is not None:
        return await factory(**kwargs)
    return await hass.async_add_executor_job(partial(api.HaierDevice.create, **kwargs))


class CommandRecorder:
    """Replaces the device's send functions: records what would go to the cloud."""

    def __init__(self, device) -> None:
        self.calls: list[dict] = []
        for name in ("_send_commands", "_send_group_command", "_send_single_command"):
            setattr(device, name, partial(self._record, name))

    def _record(self, kind: str, commands, *args, then=None, on_reject=None, **kwargs):
        if kind == "_send_single_command":
            commands = [commands]
        self.calls.append({
            "via": kind,
            "commands": copy.deepcopy(commands),
            "then": copy.deepcopy(then["commands"]) if isinstance(then, dict) else None,
            "on_reject": copy.deepcopy(on_reject),
        })
        return Done()


# --- the cloud, faked: REST through Home Assistant's aiohttp mocker, the websocket in memory ----

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from aiohttp import ClientSession, WSMsgType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.haier_evo import const as C  # noqa: E402

EMAIL = "user@example.com"
PASSWORD = "test-password-123"
LOGIN_URL = f"{C.API_PATH}/v2/ru/users/auth/sign-in"
REFRESH_URL = f"{C.API_PATH}/v2/ru/users/auth/refresh"
DEVICES_URL = f"{C.API_PATH}/{C.API_DEVICES.format(region='ru')}"


def token_answer(token: str = "access-1", refresh: str = "refresh-1", minutes: int = 60) -> dict:
    from datetime import datetime, timedelta, timezone
    msk = timezone(timedelta(hours=3))
    fmt = "%Y-%m-%dT%H:%M:%S%z"
    return {"data": {"token": {
        "accessToken": token,
        "expire": (datetime.now(msk) + timedelta(minutes=minutes)).strftime(fmt),
        "refreshToken": refresh,
        "refreshExpire": (datetime.now(msk) + timedelta(days=30)).strftime(fmt),
    }}}


def devices_page(specs: list[dict]) -> dict:
    items = [
        {"title": s["title"], "action": {"link": f"evo://device?type={s['type']}&deviceId={s['mac'].replace(':', '%3A')}&serialNum=SN{n}"}}
        for n, s in enumerate(specs)
    ]
    return {"data": {"presentation": {"layout": {"scrollContainer": [{
        "trackingData": {"component": {"componentId": C.DEVICE_LIST_COMPONENT_ID, "componentName": "deviceList"}},
        "state": json.dumps({"items": items}),
    }]}}}}


class FakeWebSocket:
    """In-memory stand-in for aiohttp's ClientWebSocketResponse (what api.Haier uses of it)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.sent: list[dict] = []
        self.closed = False
        self.close_code: int | None = None
        self.fail_send = False
        self._queue: asyncio.Queue = asyncio.Queue()

    def feed(self, message: dict | str) -> None:
        data = message if isinstance(message, str) else json.dumps(message, ensure_ascii=False)
        self._queue.put_nowait(SimpleNamespace(type=WSMsgType.TEXT, data=data, extra=None))

    def server_close(self, code: int = 1000, message: str = "") -> None:
        self._queue.put_nowait(SimpleNamespace(type=WSMsgType.CLOSE, data=code, extra=message))

    async def receive(self):
        if self.closed and self._queue.empty():
            return SimpleNamespace(type=WSMsgType.CLOSED, data=None, extra=None)
        msg = await self._queue.get()
        if msg.type == WSMsgType.CLOSE:
            self.closed = True
            self.close_code = msg.data
        return msg

    async def send_str(self, data: str) -> None:
        if self.closed or self.fail_send:
            raise ConnectionResetError("Cannot write to closing transport")
        self.sent.append(json.loads(data))

    async def close(self) -> bool:
        if not self.closed:
            self.closed = True
            self.close_code = 1000
            self._queue.put_nowait(SimpleNamespace(type=WSMsgType.CLOSED, data=None, extra=None))
        return True

    def exception(self):
        return None

    def operations(self) -> list[dict]:
        return [m for m in self.sent if m.get("action") in ("operation", "command")]


class WebSocketFactory:
    """Replaces ClientSession.ws_connect: every connection is a new FakeWebSocket."""

    def __init__(self) -> None:
        self.sockets: list[FakeWebSocket] = []
        self.fail: Exception | None = None
        self.on_connect = None  # callable(ws): what the cloud sends right after the upgrade

    async def __call__(self, _session, url: str, **kwargs) -> FakeWebSocket:
        if self.fail is not None:
            raise self.fail
        ws = FakeWebSocket(str(url))
        self.sockets.append(ws)
        if self.on_connect is not None:
            self.on_connect(ws)
        return ws

    @property
    def current(self) -> FakeWebSocket:
        return self.sockets[-1]

    async def wait_for(self, count: int, timeout: float = 5) -> FakeWebSocket:
        async with asyncio.timeout(timeout):
            while len(self.sockets) < count:
                await asyncio.sleep(0.01)
        return self.sockets[count - 1]


@pytest.fixture
def ws_factory():
    factory = WebSocketFactory()
    with patch.object(ClientSession, "ws_connect", lambda session, url, **kw: factory(session, url, **kw)):
        yield factory


@pytest.fixture(autouse=True)
def fast_limits():
    """The class-level limiters and the reconnect delays, short for the tests."""
    from custom_components.haier_evo.api import Haier
    limiters = (Haier.connect_limits, Haier.common_limits, Haier.auth_login_limits, Haier.auth_refresh_limits)
    saved = [(lim.period, lim._init_period) for lim in limiters]
    for lim in limiters:
        lim._init_period = lim.period = 0.01
        lim.reset()
    with patch.object(C, "WS_RECONNECT_BACKOFF_MIN", 0.05), patch.object(C, "WS_RECONNECT_BACKOFF_MAX", 0.2):
        yield
    for lim, (period, init) in zip(limiters, saved):
        lim.period, lim._init_period = period, init
        lim.reset()


@pytest.fixture
def cloud(aioclient_mock, ws_factory):
    """The happy-path cloud for a list of device specs (see dump_devices)."""

    def setup(specs: list[dict]):
        aioclient_mock.post(LOGIN_URL, json=token_answer())
        aioclient_mock.post(REFRESH_URL, json=token_answer("access-2", "refresh-2"))
        aioclient_mock.get(DEVICES_URL, json=devices_page(specs))
        for spec in specs:
            aioclient_mock.get(C.API_STATUS.format(mac=spec["mac"]), json=spec["backend_data"])
        return SimpleNamespace(http=aioclient_mock, ws=ws_factory, specs=specs)

    return setup


@pytest.fixture
def no_frontend():
    """The http/frontend dependencies count as set up; the card is not registered."""
    with patch("custom_components.haier_evo._async_register_frontend", AsyncMock()):
        yield


async def setup_entry(hass, entry_data: dict | None = None) -> MockConfigEntry:
    hass.config.components.update({"http", "frontend"})
    entry = MockConfigEntry(
        domain=C.DOMAIN,
        data=entry_data or {"email": EMAIL, "password": PASSWORD, "region": "ru"},
        unique_id=f"{EMAIL}_ru",
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
