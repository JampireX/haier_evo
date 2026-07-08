from __future__ import annotations
import requests
import json
import time
import threading
import uuid
import socket
import weakref
from aiohttp import web
from enum import Enum
from datetime import datetime, timezone, timedelta
from tenacity import retry, stop_after_attempt, retry_if_exception_type, wait_fixed
from websocket import WebSocketApp, WebSocket
from requests.exceptions import ConnectionError, Timeout, HTTPError
from urllib.parse import urlparse, urljoin, parse_qs
from urllib3.exceptions import NewConnectionError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode, SWING_OFF, PRESET_NONE
from homeassistant.components.http import HomeAssistantView
from .logger import _LOGGER
from .limits import ResettableLimits
from . import config as CFG # noqa
from . import const as C # noqa


class InvalidAuth(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidDevicesList(HomeAssistantError):
    """Error to indicate we cannot connect."""

class AuthError(HTTPError):
    pass

class AuthUserError(HTTPError):
    pass

class AuthValidationError(AuthError):
    pass

class AuthInternalError(AuthError):
    pass

class ManyRequestsError(HTTPError):
    pass


class SocketStatus(Enum):
    PRE_INITIALIZATION = 0
    INITIALIZING = 1
    INITIALIZED = 2
    NOT_INITIALIZED = 3


class HaierAPI(HomeAssistantView):
    url = "/api/haier_evo"
    name = "/api:haier_evo"
    requires_auth = False

    def __init__(self) -> None:
        self.haier = None

    # noinspection PyUnusedLocal
    async def get(self, request):
        if not getattr(self.haier, "allow_http", False):
            return web.Response(text="404: Not found", status=404, content_type="text/plain")
        # re-fetch fresh data from the devices (in an executor so the loop is not blocked)
        try:
            await self.haier.hass.async_add_executor_job(self.haier.refresh_devices)
        except Exception as e:
            _LOGGER.warning(f"Failed to refresh devices on GET: {e}")
        return self.json(self.haier.to_dict())

    async def post(self, request):
        if not getattr(self.haier, "allow_http_post", False):
            return web.Response(text="404: Not found", status=404, content_type="text/plain")
        data = await request.json()
        self.haier.send_message(json.dumps(data))
        return self.json({"result": "success"})


class AuthResponse(object):

    def __init__(self, response: requests.Response):
        self.response = response
        self.json_data = response.json() or {}
        self.data = self.json_data.get("data") or {}
        self.error = self.json_data.get("error")
        self.token = self.data.get("token") or {}

    def __getattr__(self, item):
        if hasattr(self.response, item):
            return getattr(self.response, item)
        raise AttributeError(item)

    def __repr__(self) -> str:
        return self.response.__repr__()

    def raise_for_error(self) -> None:
        if self.error and isinstance(self.error, dict):
            validation = self.error.get("validation") or {}
            if message := validation.get('refreshToken'):
                # noinspection PyTypeChecker
                raise AuthValidationError(message, response=self)
            if message := validation.get('email'):
                # noinspection PyTypeChecker
                raise AuthUserError(message, response=self)
            if message := validation.get('password'):
                # noinspection PyTypeChecker
                raise AuthUserError(message, response=self)
            if message := self.error.get("message"):
                # noinspection PyTypeChecker
                raise AuthInternalError(message, response=self)
            # noinspection PyTypeChecker
            raise AuthError(str(self.error), response=self)
        return None

    @property
    def access_token(self) -> str | None:
        assert "accessToken" in self.token, f"Bad data: refreshToken not found"
        value = self.token["accessToken"]
        assert isinstance(value, str) and value, f"Bad token: {value!r}"
        return value

    @property
    def access_expire(self) -> datetime | None:
        assert "expire" in self.token, f"Bad data: expire not found"
        value = self.token["expire"]
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")

    @property
    def refresh_token(self) -> str | None:
        assert "refreshToken" in self.token, f"Bad data: refreshToken not found"
        value = self.token["refreshToken"]
        assert isinstance(value, str) and value, f"Bad token: {value!r}"
        return value

    @property
    def refresh_expire(self) -> datetime | None:
        assert "refreshExpire" in self.token, f"Bad data: refreshExpire not found"
        value = self.token["refreshExpire"]
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


def _log_send_message_failure(retry_state):
    # Called by tenacity once send_message has exhausted every attempt: at this point the
    # command is lost. We always log it as an error (and dump the payload + last exception)
    # so a dropped command — in particular the first one after an idle period — is never
    # silently swallowed.
    payload = retry_state.args[1] if len(retry_state.args) > 1 else "<unknown>"
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome is not None else None
    _LOGGER.error(
        f"Command NOT sent (lost) after {retry_state.attempt_number} attempt(s). "
        f"Last error: {exc!r}. Payload: {payload}"
    )
    return None


class Haier(object):

    http = HaierAPI()
    connect_limits = ResettableLimits(calls=1, period=5)
    common_limits = ResettableLimits(
        calls=C.COMMON_LIMIT_CALLS,
        period=C.COMMON_LIMIT_PERIOD,
    )
    auth_login_limits = ResettableLimits(
        calls=C.LOGIN_LIMIT_CALLS,
        period=C.LOGIN_LIMIT_PERIOD,
        max=C.LOGIN_LIMIT_MAX
    )
    auth_refresh_limits = ResettableLimits(
        calls=C.REFRESH_LIMIT_CALLS,
        period=C.REFRESH_LIMIT_PERIOD,
        max=C.REFRESH_LIMIT_MAX
    )

    def __init__(
        self,
        hass: HomeAssistant,
        email: str,
        password: str,
        region: str,
        http: bool = C.API_HTTP_ROUTE
    ) -> None:
        self._lock = threading.Lock()
        # Serializes WebSocket writes: HA executor threads and the WS thread (_on_open ->
        # init_if_needed) can otherwise call socket_app.send() concurrently and corrupt frames.
        self._send_lock = threading.Lock()
        self._pull_data = None
        self._last_refresh = None
        self._device_id = str(uuid.uuid4())
        self.hass: HomeAssistant = hass
        self.devices: list[HaierDevice] = []
        self.email: str = email
        self.password: str = password
        self.region: str = region
        self.allow_http: bool = http
        self.allow_http_post: bool = False
        self.token: str | None = None
        self.tokenexpire: datetime | None = None
        self.refreshtoken: str | None = None
        self.refreshexpire: datetime | None = None
        self.socket_app: WebSocketApp | None = None
        self.disconnect_requested = False
        self.socket_status: SocketStatus = SocketStatus.PRE_INITIALIZATION
        self.socket_thread = None
        # Monotonic time the current WS session opened (for command/session diagnostics).
        self._connected_at = None
        # Monotonic time of the last inbound WS message (any device) — see _is_session_stale.
        self._last_inbound_at = None
        self.reset_limits()
        self.register_view()

    def to_dict(self) -> dict:
        return {
            "socket_status": getattr(self.socket_status, "value", None),
            "backend_data": self._pull_data,
            "devices": [device.to_dict() for device in self.devices]
        }

    def load_tokens(self) -> None:
        filename = self.hass.config.path(C.DOMAIN)
        try:
            with open(filename, "r") as f:
                data = json.load(f)
            assert isinstance(data, dict), "Bad saved tokens file"
            self.token = data.get("token", None)
            tokenexpire = data.get("tokenexpire")
            self.tokenexpire = datetime.fromisoformat(tokenexpire) if tokenexpire else None
            self.refreshtoken = data.get("refreshtoken", None)
            refreshexpire = data.get("refreshexpire")
            self.refreshexpire = datetime.fromisoformat(refreshexpire) if refreshexpire else None
            _LOGGER.info(f"Loaded tokens file: {filename}")
        except FileNotFoundError:
            _LOGGER.warning(f"No tokens file: {filename}")
        except Exception as e:
            _LOGGER.error(f"Failed to load tokens file: {e}")

    def save_tokens(self) -> None:
        try:
            filename = self.hass.config.path(C.DOMAIN)
            with open(filename, "w") as f:
                json.dump({
                    "token": self.token,
                    "tokenexpire": str(self.tokenexpire) if self.tokenexpire else None,
                    "refreshtoken": self.refreshtoken,
                    "refreshexpire": str(self.refreshexpire) if self.refreshexpire else None,
                }, f)
        except Exception as e:
            _LOGGER.error(f"Failed to save tokens file: {e}")
        else:
            _LOGGER.debug(f"Saved tokens file: {filename}")

    def clear_tokens(self) -> None:
        self.token = None
        self.tokenexpire = None
        self.refreshtoken = None
        self.refreshexpire = None
        self.save_tokens()

    def reset_limits(self) -> None:
        self.connect_limits.reset()
        self.common_limits.reset()
        self.auth_login_limits.reset()
        self.auth_refresh_limits.reset()

    def get_http_resources(self) -> list:
        http = getattr(self.hass, "http", None)
        app = getattr(http, "app", None)
        router = getattr(app, "router", None)
        resources = getattr(router, "resources", None)
        return resources() if resources else []

    def register_view(self) -> None:
        if self.http.url not in (r.canonical for r in self.get_http_resources()):
            self.hass.http.register_view(self.http)
        self.http.haier = weakref.proxy(self)

    def unregister_view(self) -> None:
        self.http.haier = None

    def stop(self) -> None:
        self.disconnect_requested = True
        self.reset_limits()
        if self.socket_app is not None:
            self.socket_app.close()
        self.unregister_view()

    @common_limits.sleep_and_retry
    @common_limits
    def make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        try:
            assert self.disconnect_requested is False, 'Service already stoped'
            # Setting a default timeout for requests
            kwargs.setdefault('timeout', C.API_TIMEOUT)
            headers = kwargs.setdefault('headers', {})
            headers.setdefault('User-Agent', "evo-mobile")
            headers.setdefault('Platform', "android")
            headers.setdefault('Accept', "*/*")
            resp = requests.request(method, url, **kwargs)
            # _LOGGER.debug(resp.text)
            # Handling 429 Too Many Requests with retry
            if resp.status_code == 429:
                raise ManyRequestsError("429 Too Many Requests", response=resp)
            # Raise for other HTTP errors
            resp.raise_for_status()
            return resp
        except (ConnectionError, NewConnectionError, socket.gaierror) as e:
            _LOGGER.error(f"Network error occurred: {e}")
            raise e  # Re-raise to allow retry mechanisms to handle this
        except Timeout as e:
            _LOGGER.error(f"Request timed out: {e}")
            raise e
        except HTTPError as e:
            _LOGGER.error(f"HTTP error occurred: {e}")
            raise e

    @auth_login_limits.sleep_and_retry
    @auth_login_limits
    def auth_login(self) -> AuthResponse:
        try:
            path = urljoin(C.API_PATH, C.API_LOGIN.format(region=self.region))
            _LOGGER.debug(f"Logging in to {path}")
            response = AuthResponse(self.make_request('POST', path, data={
                'email': self.email,
                'password': self.password
            }))
            # _LOGGER.info(f"Login status code: {response.status_code}")
            response.raise_for_error()
        except ManyRequestsError as e:
            self.auth_login_limits.add_period(C.LOGIN_LIMIT_429)
            raise e
        except AuthInternalError as e:
            _LOGGER.error(str(e))
            self.auth_login_limits.add_period(C.LOGIN_LIMIT_500)
            response = e.response
        except AuthUserError as e:
            self.disconnect_requested = True
            raise e
        else:
            self.auth_login_limits.set_period()
        finally:
            self.auth_refresh_limits.reset()
        return response

    @auth_refresh_limits.sleep_and_retry
    @auth_refresh_limits
    def auth_refresh(self) -> AuthResponse:
        try:
            path = urljoin(C.API_PATH, C.API_TOKEN_REFRESH.format(region=self.region))
            _LOGGER.debug(f"Refreshing token in to {path}")
            response = AuthResponse(self.make_request('POST', path, data={
                'refreshToken': self.refreshtoken
            }))
            # _LOGGER.info(f"Refresh status code: {response.status_code}")
            response.raise_for_error()
        except ManyRequestsError as e:
            self.auth_refresh_limits.add_period(C.REFRESH_LIMIT_429)
            raise e
        except AuthValidationError as e:
            _LOGGER.error(str(e))
            self.clear_tokens()
            raise e
        except AuthInternalError as e:
            _LOGGER.error(str(e))
            self.auth_refresh_limits.add_period(C.REFRESH_LIMIT_500)
            response = e.response
        else:
            self.auth_refresh_limits.set_period()
        finally:
            self.auth_login_limits.reset()
        return response

    @retry(
        retry=retry_if_exception_type(AuthValidationError),
        stop=stop_after_attempt(2),
    )
    def login(self, refresh: bool = False) -> None:
        resp = None
        try:
            if refresh and self.refreshtoken:  # token refresh
                resp = self.auth_refresh()
            else:  # initial login
                resp = self.auth_login()
            assert resp, "No response from login"
            self.token = resp.access_token
            self.tokenexpire = resp.access_expire
            self.refreshtoken = resp.refresh_token
            self.refreshexpire = resp.refresh_expire
            self.save_tokens()
        except AuthValidationError as e:
            raise e
        except AssertionError as e:
            _LOGGER.error(f"Assertion error: {e}")
        except Exception as e:
            _LOGGER.error(
                f"Failed to login/refresh token, "
                f"response was: {resp}, "
                f"err: {e}"
            )
            raise InvalidAuth()
        else:
            _LOGGER.debug(f"Successful update tokens")

    def auth(self) -> None:
        with self._lock:
            tzinfo = timezone(timedelta(hours=+3.0))
            # tzinfo = datetime.now(timezone.utc).astimezone().tzinfo
            now = datetime.now(tzinfo)
            tokenexpire = self.tokenexpire or now
            refreshexpire = self.refreshexpire or now
            if self.token:
                if tokenexpire > now:
                    return None
                elif self.refreshtoken and refreshexpire > now:
                    # _LOGGER.debug(f"Token to be refreshed")
                    return self.login(refresh=True)
            # _LOGGER.debug(f"Token expired or empty")
            return self.login()

    def pull_data_from_api(self) -> dict:
        self.auth()
        response = None
        try:
            devices_path = urljoin(C.API_PATH, C.API_DEVICES.format(region=self.region))
            _LOGGER.debug(f"Getting devices, url: {devices_path}")
            response = requests.get(devices_path, headers={
                'X-Auth-Token': self.token,
                'User-Agent': 'evo-mobile',
                'Platform': 'android',
                'Device-Id': self._device_id,
                'Content-Type': 'application/json'
            }, timeout=C.API_TIMEOUT)
            # _LOGGER.debug(response.text)
            response.raise_for_status()
            data = response.json().get("data", {})
            assert isinstance(data, dict), f"Data is not dict: {data}"
            return data
        except Exception as e:
            _LOGGER.error(f"Failed to get devices {e}, response was: {response}")
            return {}

    @retry(
        retry=retry_if_exception_type(HTTPError),
        stop=stop_after_attempt(2),
    )
    def pull_device_data(self, device_mac: str) -> dict:
        self.auth()
        response = None
        try:
            status_url = C.API_STATUS.format(mac=device_mac)
            _LOGGER.debug(f"Getting initial status of device {device_mac}, url: {status_url}")
            response = requests.get(status_url, headers={
                'X-Auth-Token': self.token,
                'User-Agent': 'evo-mobile',
                'Platform': 'android',
                'Device-Id': self._device_id,
                'Content-Type': 'application/json'
            }, timeout=C.API_TIMEOUT)
            # _LOGGER.debug(f"Update device {device_mac} status code: {response.status_code}")
            # _LOGGER.debug(response.text)s
            response.raise_for_status()
            data = response.json()
            return data
        except Exception as e:
            _LOGGER.error(f"Failed to get status: {e}, response was: {response}")
            raise

    def pull_data(self) -> None:
        self._pull_data = data = self.pull_data_from_api()
        if not self._pull_data:
            raise InvalidDevicesList()
        need_container_id = "72a6d224-cb66-4e6d-b427-2e4609252684"
        presentation = data.setdefault("presentation", {})
        layout = presentation.setdefault("layout", {})
        containers = layout.setdefault("scrollContainer", [])
        for item in containers[:]:
            tracking_data = item.setdefault("trackingData", {})
            component = tracking_data.setdefault("component", {})
            component_id = component.setdefault("componentId", "")
            # _LOGGER.debug(component_id)
            component_name = component.setdefault("componentName", "")
            if not (
                component_name == "deviceList"
                and component_id == need_container_id
            ):
                containers.remove(item)
                continue
            state_data = item.setdefault("state", "{}")
            state_json = item['state'] = (
                json.loads(state_data)
                if isinstance(state_data, str)
                else state_data
            )
            devices = state_json.setdefault("items", [])
            for d in devices:
                device_title = d.get('title', '')
                device_link = d.get('action', {}).get('link', '')
                parsed_link = urlparse(device_link)
                query_params = parse_qs(parsed_link.query)
                device_type = query_params.setdefault('type', ['UNKNOWN'])[0]
                device_mac = query_params.get('deviceId', [''])[0]
                device_mac = device_mac.replace('%3A', ':')
                device_serial = query_params.get('serialNum', [''])[0]
                device = HaierDevice.create(
                    haier=self,
                    device_type=device_type,
                    device_mac=device_mac,
                    device_serial=device_serial,
                    device_title=device_title,
                )
                self.devices.append(device)
                _LOGGER.info(f"Added device: {device}")
        if len(self.devices) > 0:
            self.connect_in_thread()

    def refresh_devices(self, force: bool = False) -> None:
        # Re-fetch the current state of all devices with TTL protection, so that frequent
        # calls to the GET endpoint do not trigger a flood of REST requests.
        if not self.devices:
            return
        now = time.monotonic()
        # Only the TTL check/update is guarded; the network refresh runs outside the lock
        # (pull_device_data -> auth() also takes this lock, so holding it here would deadlock).
        with self._lock:
            if (
                not force
                and self._last_refresh is not None
                and (now - self._last_refresh) < C.API_REFRESH_TTL
            ):
                return
            self._last_refresh = now
        for device in self.devices:
            try:
                device.refresh()
            except Exception as e:
                _LOGGER.warning(f"Failed to refresh device {device.device_id}: {e}")

    def get_device_by_id(self, id_: str) -> HaierDevice | None:
        return next(filter(
            lambda d: d.device_id == id_,
            self.devices
        ), None)

    def _init_ws(self) -> None:
        self.auth()
        url = urljoin(C.API_WS_PATH, self.token)
        if self.socket_app is None:
            self.socket_app = WebSocketApp(
                url=url,
                on_message=self._on_message,
                on_open=self._on_open,
                on_ping=self._on_ping,
                on_close=self._on_close,
            )
        else:
            self.socket_app.url = url

    # noinspection PyUnusedLocal
    def _on_message(self, ws: WebSocket, message: str) -> None:
        self._last_inbound_at = time.monotonic()
        _LOGGER.debug(f"Received WSS message: {message}")
        message_dict: dict = json.loads(message)
        message_device = str(message_dict.get("macAddress")).lower()
        device = self.get_device_by_id(message_device)
        if device is None:
            _LOGGER.error(f"Got a message for a device we don't know about: {message_device}")
        else:
            device.on_message(message_dict)

    # noinspection PyMethodMayBeStatic,PyUnusedLocal
    def _on_open(self, ws: WebSocket) -> None:
        self.socket_status = SocketStatus.INITIALIZED
        self._connected_at = time.monotonic()
        _LOGGER.debug("Websocket opened")
        for device in self.devices:
            device.reset_session_state()
            device.init_if_needed()

    # noinspection PyUnusedLocal
    def _on_ping(self, ws: WebSocket) -> None:
        # During reconnects socket_app.sock can be None — guard against AttributeError.
        sock = getattr(self.socket_app, "sock", None)
        if sock is None:
            return
        try:
            sock.pong()
        except Exception as e:
            _LOGGER.debug(f"Failed to send pong: {e}")

    # noinspection PyMethodMayBeStatic,PyUnusedLocal
    def _on_close(self, ws: WebSocket, close_code: int, close_message: str) -> None:
        # Reflect that the socket is down so connect_if_needed/_wait_websocket do not
        # treat a connection that is being re-established as still INITIALIZED.
        self.socket_status = SocketStatus.NOT_INITIALIZED
        _LOGGER.debug(f"Websocket closed. Code: {close_code}, message: {close_message}")

    def _wait_websocket(self, timeout: float) -> None:
        current = time.time()
        while time.time() <= (current + timeout):
            if self.socket_status == SocketStatus.INITIALIZED:
                _LOGGER.info(
                    f"_wait_websocket: socket became INITIALIZED after "
                    f"{time.time() - current:.2f}s"
                )
                return
            time.sleep(0.1)
        _LOGGER.warning(
            f"_wait_websocket: timed out after {timeout}s waiting for the websocket to "
            f"become INITIALIZED (socket_status="
            f"{getattr(self.socket_status, 'name', self.socket_status)}); "
            f"a command sent now is likely to be lost"
        )

    def write_ha_state(self) -> None:
        for device in self.devices:
            device.write_ha_state()

    def is_socket_connected(self) -> bool:
        sock = getattr(self.socket_app, "sock", None)
        return bool(sock is not None and getattr(sock, "connected", False))

    def _is_socket_ready(self) -> bool:
        # Ready to send only when both signals agree: the status flag is INITIALIZED
        # (set by _on_open / cleared by _on_close) and the underlying sock is connected.
        return self.socket_status == SocketStatus.INITIALIZED and self.is_socket_connected()

    def _is_session_stale(self) -> bool:
        # Zombie-session detector: the socket passes ping/pong but the server has not
        # delivered a single application message for a long time — a command sent into
        # such a session is likely to be silently lost.
        last_activity = max(
            (t for t in (self._last_inbound_at, self._connected_at) if t is not None),
            default=None,
        )
        if last_activity is None:
            return False
        return (time.monotonic() - last_activity) > C.WS_SESSION_STALE_TIMEOUT

    def _force_reconnect(self) -> None:
        # Closing the socket makes run_forever() return; the connect() loop in the
        # socket thread then re-establishes the session (refreshing the token).
        try:
            self.socket_app.close()
        except Exception as e:
            _LOGGER.debug(f"Error closing stale socket: {e}")
        # _on_close fires asynchronously; wait until the stale INITIALIZED state is
        # gone so the follow-up connect_if_needed() waits for the NEW session instead
        # of returning immediately on the old one.
        deadline = time.monotonic() + 1.0
        while self._is_socket_ready() and time.monotonic() < deadline:
            time.sleep(0.05)

    def connect_if_needed(self, timeout: float = 4.0) -> None:
        if self.socket_thread and self.socket_thread.is_alive():
            _LOGGER.info(
                "connect_if_needed: socket thread is alive, waiting for it to come up "
                f"(socket_status={getattr(self.socket_status, 'name', self.socket_status)})"
            )
            return self._wait_websocket(timeout)
        # No live thread: start one. Note that connect_in_thread does NOT block, so the
        # caller may re-check the connection before it is actually up — a frequent reason
        # the very first command after startup/idle is lost.
        _LOGGER.info(
            "connect_if_needed: no live socket thread, starting a new connection thread "
            "(connection will come up asynchronously)"
        )
        return self.connect_in_thread()

    def connect(self) -> None:
        self.socket_status = SocketStatus.NOT_INITIALIZED
        while not self.disconnect_requested:
            self.run_forever()
        _LOGGER.debug("Connection stoped")

    def connect_in_thread(self) -> None:
        self.socket_thread = thread = threading.Thread(target=self.connect)
        thread.daemon = True
        thread.start()

    @connect_limits.sleep_and_retry
    @connect_limits
    def run_forever(self) -> None:
        _LOGGER.debug(f"Connecting to websocket ({C.API_WS_PATH})")
        try:
            self.socket_status = SocketStatus.INITIALIZING
            self._init_ws()
            # ping_timeout is required to detect a half-open (silently dead) connection:
            # without it run_forever blocks forever on a dead socket and never reconnects.
            self.socket_app.run_forever(
                ping_interval=C.WS_PING_INTERVAL,
                ping_timeout=C.WS_PING_TIMEOUT,
            )
        except Exception as e:
            _LOGGER.error(f"Error connecting to websocket: {e}")

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(2),
        retry_error_callback=_log_send_message_failure,
        wait=wait_fixed(1.0),
    )
    def send_message(self, payload: str) -> None:
        # A long-idle socket may be down or half-open: a send() into it can either raise or —
        # worse — return successfully and then the socket closes a few ms later, silently
        # losing the command (no exception, so the retry never fires). To avoid that we:
        #   1) serialize all sends (concurrent writes corrupt frames),
        #   2) fail fast on a dead socket before sending (reconnect, else raise -> retry),
        #   3) re-check right after sending and, if the socket just closed, raise -> retry
        #      (which reconnects and resends; all our commands carry absolute values, so a
        #       duplicate resend is harmless).
        with self._send_lock:
            if not self._is_socket_ready():
                _LOGGER.warning(
                    f"Socket not ready before send "
                    f"(socket_status={getattr(self.socket_status, 'name', self.socket_status)}, "
                    f"connected={self.is_socket_connected()}), reconnecting before sending"
                )
                self.connect_if_needed()
                if not self._is_socket_ready():
                    raise ConnectionError("Socket not ready after reconnect attempt")
            elif self._is_session_stale():
                _LOGGER.warning(
                    f"WS session stale: no inbound messages for over "
                    f"{C.WS_SESSION_STALE_TIMEOUT}s — reconnecting before send"
                )
                self._force_reconnect()
                self.connect_if_needed()
                if not self._is_socket_ready():
                    raise ConnectionError("Socket not ready after stale-session reconnect")
            _LOGGER.info(f"Sending message: {payload}")
            try:
                self.socket_app.send(payload)
            except Exception as e:
                _LOGGER.warning(f"Failed to send message (will retry): {e!r}. Payload: {payload}")
                self.connect_if_needed()
                raise e
            # Post-send check: catch a silent loss when the socket closes right after send()
            # returned (observed ~25ms after send in debug logs). A short window is enough.
            time.sleep(C.WS_POST_SEND_CHECK)
            if not self._is_socket_ready():
                _LOGGER.warning(
                    f"Socket closed right after send — message likely lost, retrying. "
                    f"Payload: {payload}"
                )
                self.connect_if_needed()
                raise ConnectionError("Socket closed right after send")


class HaierDevice(object):

    def __init__(
        self,
        haier: Haier,
        device_mac: str,
        device_serial: str = None,
        device_title: str = None,
        backend_data: dict = None,
    ) -> None:
        self._haier = weakref.proxy(haier)
        self.device_id = device_mac
        self.device_serial = device_serial
        self.device_name = device_title
        self.device_model = "UNKNOWN"
        self.sw_version = None
        self._write_ha_state_callbacks = []
        self._available = True
        self._config = None
        self._status_data = backend_data
        # Guards config rebuild (refresh) against concurrent reads from the WS thread.
        self._lock = threading.Lock()
        # True while a post-rejection refresh is scheduled/running (see on_message).
        self._reject_refresh_pending = False
        # Diagnostics: last value we sent per attribute code, and when this WS session
        # first received any inbound message (proxy for "cloud attached our session").
        self._sent_commands: dict[str, dict] = {}
        self._session_ready_at = None
        # True once a full status snapshot (all attributes at once) has been received
        # over WS — see init_if_needed/_init_snapshot_fallback.
        self._snapshot_seen = False

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"{self.device_id!r},"
            f"name={self.device_name!r},"
            f"serial={self.device_serial!r},"
            f"model={self.device_model!r},"
            f"config={self.config!r}"
            f")"
        )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(C.DOMAIN, self.device_id)},
            name=self.device_name,
            sw_version=self.sw_version,
            model=self.device_model,
            manufacturer="Haier"
        )

    @property
    def device_mac(self) -> str:
        return self.device_id

    @property
    def available(self) -> bool:
        # return self._available
        # this works very bad
        return True

    @available.setter
    def available(self, value: bool | str):
        if not isinstance(value, bool):
            self._available = False if str(value).upper() == 'OFFLINE' else True
        else:
            self._available = value

    @property
    def status_data(self) -> dict:
        return self._status_data

    @property
    def hass(self) -> HomeAssistant:
        return self._haier.hass

    @property
    def config(self) -> CFG.HaierDeviceConfig:
        return self._config

    @property
    def constraint(self) -> CFG.Constraint:
        return self.config.constraint

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "device_id": self.device_id,
            "device_mac": self.device_mac,
            "device_name": self.device_name,
            "device_serial": self.device_serial,
            "sw_version": self.sw_version,
            "config": self.config.to_dict() if self.config else None,
            "backend_data": self.status_data,
        }

    def _get_status(self, data: dict) -> dict:
        self._status_data = data = (data or {})
        # WM returns info/status/settings/attributes nested inside smartDeviceControl,
        # while control/allProgram sit next to it at the top level of backend_data.
        # AC/REF return everything at the top level. status_data is always kept complete.
        inner = data.get("smartDeviceControl")
        inner = inner if isinstance(inner, dict) else data
        info = inner.setdefault("info", {})
        self.device_serial = info.setdefault("serialNumber", self.device_serial)
        device_model = info.setdefault("model", "AC")
        device_model = device_model.replace('-','').replace('/', '')[:11]
        self.device_model = device_model
        self.available = inner.setdefault("status", "ONLINE")
        settings = inner.setdefault("settings", {})
        self.device_name = settings.setdefault("name", {}).setdefault("name", self.device_name)
        self.sw_version = settings.setdefault('firmware', {}).setdefault('value', None)
        # read config and current values (inner view: info/attributes/businessAttributes)
        self._load_config_from_attributes(inner)
        return data

    def _load_config_from_attributes(self, data: dict) -> None:
        pass

    def refresh(self) -> None:
        # Re-fetch the device's fresh state over REST and re-parse it.
        # The network call stays outside the lock; only the config rebuild is guarded
        # so a concurrent WS status update cannot read a half-rebuilt config.
        data = self._haier.pull_device_data(self.device_id)
        with self._lock:
            self._get_status(data)
        self.write_ha_state()

    def _schedule_refresh_after_rejection(self) -> None:
        # Called from the WS thread; refresh() is a blocking REST call (API_TIMEOUT=15s)
        # and must not run here — it would stall ping/pong and message dispatch.
        if self._reject_refresh_pending:
            return
        self._reject_refresh_pending = True
        threading.Thread(target=self._refresh_after_rejection, daemon=True).start()

    def _refresh_after_rejection(self) -> None:
        try:
            time.sleep(C.COMMAND_REJECT_REFRESH_DELAY)
            self.refresh()
        except Exception as e:
            _LOGGER.warning(
                f"Failed to refresh {self.device_name} after command rejection: {e}"
            )
        finally:
            self._reject_refresh_pending = False

    def _set_attribute_value(self, code: str, value: str) -> None:
        pass

    def _handle_status_update(self, received_message: dict) -> None:
        statuses = received_message.get("payload", {}).get("statuses") or [{}]
        properties = (statuses[0] or {}).get("properties") or {}
        if len(properties) >= C.WS_FULL_SNAPSHOT_MIN_PROPS:
            self._snapshot_seen = True
        # Visible at INFO so it is clear whether realtime updates actually arrive and which
        # attributes they carry (the raw "Received WSS message" log is only at DEBUG).
        _LOGGER.info(f"WS status update for {self.device_name}: {properties}")
        self._log_state_correlation(properties)
        with self._lock:
            for key, value in properties.items():
                # A status snapshot that arrives right after (re)connect still carries the
                # PRE-command state and would revert the optimistic value in the UI while
                # the command is still in flight. Skip values that contradict a recently
                # sent, not-yet-confirmed command (see _should_suppress_stale).
                if self._should_suppress_stale(key, value):
                    continue
                self._set_attribute_value(key, value)
        self.available = True
        self.write_ha_state()

    def _handle_device_status_update(self, received_message: dict) -> None:
        status = received_message.get("payload", {}).get("status")
        self.available = status
        self.write_ha_state()

    def _handle_info(self, received_message: dict) -> None:
        payload = received_message.get("payload", {})
        self.sw_version = payload.get("swVersion") or self.sw_version

    def _send_message(self, message: dict) -> None:
        self._haier.send_message(json.dumps(message))

    def _send_commands(self, commands: list[dict]) -> None:
        self._send_group_command(commands)

    def _send_group_command(self, commands: list[dict]) -> None:
        if self.config.command_name:
            trace = str(uuid.uuid4())
            self._record_sent(commands, trace)
            self._send_message({
                "action": "operation",
                "macAddress": self.device_id,
                "commandName": self.config.command_name,
                "commands": commands,
                "trace": trace,
            })
        else:
            for c in commands:
                self._send_single_command(c)

    def _send_single_command(self, command: dict) -> None:
        trace = str(uuid.uuid4())
        self._record_sent([command], trace)
        self._send_message({
            "action": "command",
            "macAddress": self.device_id,
            "command": command,
            "trace": trace,
        })

    # --- diagnostics: command / state correlation -----------------------------
    # These only log; they do not change behaviour. The goal is to confirm whether
    # the first command is sent before the cloud has attached our session (race),
    # and whether a later status update reverts a value we just commanded.
    @staticmethod
    def _norm_value(value) -> str:
        return {"true": "1", "false": "0"}.get(str(value), str(value))

    def _session_age(self) -> float | None:
        started = getattr(self._haier, "_connected_at", None)
        return (time.monotonic() - started) if started else None

    def reset_session_state(self) -> None:
        # Called on every (re)connect: readiness is re-evaluated per WS session.
        self._session_ready_at = None

    def _note_session_ready(self) -> None:
        if self._session_ready_at is None:
            self._session_ready_at = time.monotonic()
            age = self._session_age()
            when = f"{age:.2f}s after connect" if age is not None else "received"
            _LOGGER.info(f"WS SESSION READY [{self.device_name}]: first inbound message {when}")

    def _record_sent(self, commands: list[dict], trace: str) -> None:
        now = time.monotonic()
        summary = {}
        for c in commands:
            code = str(c.get("commandName"))
            val = self._norm_value(c.get("value"))
            self._sent_commands[code] = {"value": val, "ts": now, "trace": trace}
            summary[code] = val
        age = self._session_age()
        ready = (
            f"{now - self._session_ready_at:.2f}s ago"
            if self._session_ready_at else "NOT-READY-YET"
        )
        age_s = f"{age:.2f}s" if age is not None else "n/a"
        _LOGGER.info(
            f"CMD OUT [{self.device_name}] trace={trace} cmds={summary} "
            f"session_age={age_s} device_ready={ready}"
        )

    def _ack_latency(self, trace) -> str:
        now = time.monotonic()
        ts = next((v["ts"] for v in self._sent_commands.values() if v["trace"] == trace), None)
        return f"{now - ts:.2f}s" if ts is not None else "n/a"

    @classmethod
    def _values_equal(cls, a, b) -> bool:
        a, b = cls._norm_value(a), cls._norm_value(b)
        if a == b:
            return True
        # The cloud may report numbers formatted differently from what we sent
        # (e.g. "24" vs "24.0") — compare numerically when both parse.
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False

    def _should_suppress_stale(self, code: str, value) -> bool:
        # True when an inbound status value must NOT be applied because it contradicts a
        # command we sent within WS_CMD_CORRELATION_WINDOW that the device has not yet
        # confirmed. Once the device reports the commanded value, the pending entry is
        # dropped and updates for that attribute flow normally again.
        info = self._sent_commands.get(str(code))
        if not info:
            return False
        dt = time.monotonic() - info["ts"]
        if dt > C.WS_CMD_CORRELATION_WINDOW:
            self._sent_commands.pop(str(code), None)
            return False
        if self._values_equal(value, info["value"]):
            self._sent_commands.pop(str(code), None)
            return False
        _LOGGER.warning(
            f"STALE STATUS SUPPRESSED [{self.device_name}] code={code}: device reports "
            f"{self._norm_value(value)} but we sent {info['value']} {dt:.2f}s ago "
            f"(trace={info['trace']}) — keeping the optimistic value until confirmation"
        )
        return True

    def _clear_sent_by_trace(self, trace) -> None:
        # After an explicit rejection the optimistic value is wrong — stop protecting it
        # so the follow-up refresh() can restore the real device state in the UI.
        for code in [c for c, v in self._sent_commands.items() if v["trace"] == trace]:
            self._sent_commands.pop(code, None)

    def _log_state_correlation(self, properties: dict) -> None:
        if not properties or not self._sent_commands:
            return
        now = time.monotonic()
        for code, info in self._sent_commands.items():
            if code not in properties:
                continue
            dt = now - info["ts"]
            if dt > C.WS_CMD_CORRELATION_WINDOW:
                continue
            got = self._norm_value(properties.get(code))
            if self._values_equal(got, info["value"]):
                _LOGGER.info(
                    f"CMD CONFIRMED [{self.device_name}] code={code} value={got} "
                    f"after {dt:.2f}s (trace={info['trace']})"
                )
            # A mismatch is not logged here: the same value goes through
            # _should_suppress_stale right after, which logs it exactly once
            # (STALE STATUS SUPPRESSED) and keeps the optimistic value.

    def init_if_needed(self) -> None:
        pass

    def get_commands(self, name: str, value: str | bool) -> list[dict]:
        value = str({True: "on", False: "off", None: "off"}.get(value, value))
        if custom := self.config.get_command_by_name(f"{name}_{value}"):
            return custom
        attr = self.config.get_attr_by_name(name)
        if attr is None:
            return []
        item_code = attr.get_item_code(value)
        if item_code is None:
            # Unmapped value — do not send a "None" command to the device.
            _LOGGER.warning(f"No mapping for {name}={value!r} on {self.device_name}, command skipped")
            return []
        return self.constraint.apply([{
            "commandName": str(attr.code),
            "value": item_code,
        }])

    def on_message(self, message_dict: dict) -> None:
        # First inbound on a fresh session marks it "ready" (cloud attached us).
        self._note_session_ready()
        message_type = message_dict.get("event", "")
        if message_type == "status":
            self._handle_status_update(message_dict)
        elif message_type == "command_response":
            # The device acknowledges every command here; errNo != 0 means it rejected it.
            # Surface rejections at WARNING so a dropped/refused command is easy to diagnose.
            err_no = message_dict.get("errNo", 0)
            trace = message_dict.get("trace")
            latency = self._ack_latency(trace)
            if err_no not in (0, "0", None):
                _LOGGER.warning(
                    f"CMD REJECTED [{self.device_name}] trace={trace} errNo={err_no} "
                    f"latency={latency}: {message_dict}"
                )
                # set_* already applied the value optimistically — re-pull the real
                # state so HA does not keep showing what the device refused to do.
                self._clear_sent_by_trace(trace)
                self._schedule_refresh_after_rejection()
            else:
                _LOGGER.info(
                    f"CMD ACK [{self.device_name}] trace={trace} errNo=0 latency={latency}"
                )
        elif message_type == "info":
            self._handle_info(message_dict)
        elif message_type == "deviceStatusEvent":
            self._handle_device_status_update(message_dict)
        else:
            _LOGGER.warning(f"Got unknown message: {message_dict}")

    def write_ha_state(self) -> None:
        for callback in self._write_ha_state_callbacks:
            self.hass.loop.call_soon_threadsafe(callback)

    def add_write_ha_state_callback(self, callback) -> None:
        if callback not in self._write_ha_state_callbacks:
            self._write_ha_state_callbacks.append(callback)

    # noinspection PyMethodMayBeStatic
    def create_entities_climate(self) -> list:
        return []

    # noinspection PyMethodMayBeStatic
    def create_entities_switch(self) -> list:
        return []

    # noinspection PyMethodMayBeStatic
    def create_entities_select(self) -> list:
        return []

    # noinspection PyMethodMayBeStatic
    def create_entities_sensor(self) -> list:
        return []

    # noinspection PyMethodMayBeStatic
    def create_entities_binary_sensor(self) -> list:
        return []

    @classmethod
    def create(
        cls,
        haier: Haier,
        device_type: str,
        device_mac: str,
        device_serial: str = None,
        device_title: str = None,
    ) -> HaierDevice:
        backend_data = haier.pull_device_data(device_mac)
        device_cls = {
            "AC": HaierAC,
            "REF": HaierREF,
            "WM": HaierWM,
        }.get(device_type, cls)
        if device_cls is cls:
            # type not recognized from the deep-link — try to infer it from the response structure
            if isinstance(backend_data, dict) and "smartDeviceControl" in backend_data:
                device_cls = HaierWM
                _LOGGER.warning(f"Unknown device type {device_type!r}, detected as WM by payload")
            else:
                _LOGGER.warning(f"Unknown device type: {device_type}")
        return device_cls(
            haier=haier,
            device_mac=device_mac,
            device_serial=device_serial,
            device_title=device_title,
            backend_data=backend_data,
        )


class HaierAC(HaierDevice):

    def __init__(
        self,
        backend_data: dict = None,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.current_temperature = 0
        self.target_temperature = 0
        self.status = None
        self.mode = None
        self.fan_mode = None
        self.swing_horizontal_mode = None
        self.swing_mode = None
        self._preset_mode = None
        self.min_temperature = 7
        self.max_temperature = 35
        self.light_on = True
        self.sound_on = True
        self.quiet_on = False
        self.turbo_on = False
        self.health_on = False
        self.comfort_on = False
        self.cleaning_on = False
        self.antifreeze_on = False
        self.autohumidity_on = False
        self.eco_sensor = None
        self._get_status(backend_data)
        self._inited = False

    @property
    def config(self) -> CFG.HaierACConfig:
        return self._config

    @property
    def preset_mode(self) -> str:
        if self._preset_mode not in ("none", "sleep", "boost"):
            return self._preset_mode
        elif self.quiet_on:
            return "sleep"
        elif self.turbo_on:
            return "boost"
        return "none"

    @preset_mode.setter
    def preset_mode(self, preset_mode: str) -> None:
        self._preset_mode = preset_mode

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({
            "current_temperature": self.current_temperature,
            "target_temperature": self.target_temperature,
            "max_temperature": self.max_temperature,
            "min_temperature": self.min_temperature,
            "status": self.status,
            "mode": self.mode,
            "fan_mode": self.fan_mode,
            "swing_horizontal_mode": self.swing_horizontal_mode,
            "swing_mode": self.swing_mode,
            "preset_mode": self.preset_mode,
            "light_on": self.light_on,
            "sound_on": self.sound_on,
            "quiet_on": self.quiet_on,
            "turbo_on": self.turbo_on,
            "health_on": self.health_on,
            "comfort_on": self.comfort_on,
            "cleaning_on": self.cleaning_on,
            "antifreeze_on": self.antifreeze_on,
            "autohumidity_on": self.autohumidity_on,
            "eco_sensor": self.eco_sensor,
        })
        return data

    def _set_attribute_value(self, code: str, value: str) -> None:
        attr = self.config.get_attr_by_code(code)
        if not (attr and value is not None):
            return
        elif attr.name == "current_temperature":
            self.current_temperature = float(value)
        elif attr.name == "status":
            self.status = int(value)
        elif attr.name == "target_temperature":
            self.target_temperature = float(value)
        elif attr.name == "mode":
            self.mode = attr.get_item_name(value)
        elif attr.name == "fan_mode":
            self.fan_mode = attr.get_item_name(value)
        elif attr.name == "swing_horizontal_mode":
            self.swing_horizontal_mode = attr.get_item_name(value)
        elif attr.name == "swing_mode":
            self.swing_mode = attr.get_item_name(value)
        elif attr.name == "light":
            self.light_on = parsebool(attr.get_item_name(value))
        elif attr.name == "sound":
            self.sound_on = parsebool(attr.get_item_name(value))
        elif attr.name == "quiet":
            self.quiet_on = parsebool(attr.get_item_name(value))
        elif attr.name == "turbo":
            self.turbo_on = parsebool(attr.get_item_name(value))
        elif attr.name == "health":
            self.health_on = parsebool(attr.get_item_name(value))
        elif attr.name == "comfort":
            self.comfort_on = parsebool(attr.get_item_name(value))
        elif attr.name == "cleaning":
            self.cleaning_on = parsebool(attr.get_item_name(value))
        elif attr.name == "antifreeze":
            self.antifreeze_on = parsebool(attr.get_item_name(value))
        elif attr.name == "autohumidity":
            self.autohumidity_on = parsebool(attr.get_item_name(value))
        elif attr.name == "eco_sensor":
            self.eco_sensor = attr.get_item_name(value)

    def _load_config_from_attributes(self, data: dict) -> None:
        self._config = CFG.HaierACConfig(self.device_model, self.hass.config.path(C.DOMAIN))
        attributes = data.setdefault("attributes", [])
        sensors = data.setdefault("sensors", {}).get("items", [])
        sensor_curr_temp = next(filter(lambda i: (
            isinstance(i, dict)
            and isinstance(i.get("value"), dict)
            and i.get("value", {}).get("description") == "indoorTemperature"
        ), sensors), {}).get("value", {}).get("name")
        attrs = list(sorted(map(lambda x: CFG.Attribute(x), attributes), key=lambda x: x.code))
        for attr in attrs:
            if attr.name == "current_temperature" and str(attr.code) != sensor_curr_temp:
                continue
            self.config.attrs.append(attr)
        self.config.merge_attributes()
        for attr in self.config.attrs:
            self._set_attribute_value(str(attr.code), attr.current)
            if attr.name == "target_temperature" and attr.range is not None:
                # The API may omit the range or report non-numeric bounds; guard so a
                # single odd device does not crash the whole setup (keep the defaults).
                min_value = parsefloat(attr.range.min_value)
                max_value = parsefloat(attr.range.max_value)
                if min_value is not None:
                    self.min_temperature = min_value
                if max_value is not None:
                    self.max_temperature = max_value
            _LOGGER.debug(f"{self.device_name}: {attr}")
        self.constraint.extend(data.setdefault("constraint", []))

    def _get_status(self, data: dict) -> dict:
        data = super()._get_status(data)
        if self.swing_horizontal_mode is None:
            self.swing_horizontal_mode = SWING_OFF
        if self.swing_mode is None:
            self.swing_mode = SWING_OFF
        if self.preset_mode is None:
            self.preset_mode = PRESET_NONE
        self.write_ha_state()
        return data

    def init_if_needed(self) -> None:
        if self._inited:
            return
        self._inited = True
        if next(filter(
            lambda a: (not a.name.startswith("preset_mode_") and a.current is None),
            self.config.attrs
        ), None) is None:
            return
        # Some attribute values are missing from the REST config (swing, light, health);
        # they only come in a full WS status snapshot, which the cloud pushes on its own
        # shortly after the session opens. Previously a target-temperature command was
        # sent here to force that snapshot — but any operation command makes the AC beep,
        # the cloud occasionally rejects it (errNo=-1), and the snapshot arrives (or not)
        # regardless of it. So: just wait, and fall back to a silent REST refresh.
        threading.Thread(target=self._init_snapshot_fallback, daemon=True).start()

    def _init_snapshot_fallback(self) -> None:
        time.sleep(C.WS_INIT_SNAPSHOT_TIMEOUT)
        if self._snapshot_seen:
            return
        _LOGGER.info(
            f"No full status snapshot within {C.WS_INIT_SNAPSHOT_TIMEOUT}s after connect "
            f"for {self.device_name} — refreshing over REST instead"
        )
        try:
            self.refresh()
        except Exception as e:
            _LOGGER.warning(
                f"Failed to refresh {self.device_name} after missing snapshot: {e}"
            )

    def get_commands(self, name: str, value: str | bool) -> list[dict]:
        if name != "preset_mode":
            return super().get_commands(name, value)
        func = getattr(self, f"get_preset_mode_{value}", None)
        if func is not None:
            return func()
        return self.get_preset_mode_command(value)

    def get_preset_mode_none(self) -> list[dict]:
        if custom := self.config.get_command_by_name('preset_mode_none'):
            return custom
        return [{
            "commandName": str(attr.code),
            "value": attr.get_item_code("off", "0"),
        } for attr in filter(
            lambda a: a.name.startswith("preset_mode"),
            self.config.attrs
        )]

    def get_preset_mode_command(self, mode: str) -> list[dict]:
        if custom := self.config.get_command_by_name(f'preset_mode_{mode}'):
            return custom
        attr = self.config.get_attr_by_name(f"preset_mode_{mode}")
        return self.constraint.apply([{
            "commandName": str(attr.code),
            "value": attr.get_item_code("on", "1")
        }] if attr else [])

    def get_supported_features(self) -> ClimateEntityFeature:
        value = (
            ClimateEntityFeature.TARGET_TEMPERATURE |
            ClimateEntityFeature.TURN_OFF |
            ClimateEntityFeature.TURN_ON |
            ClimateEntityFeature.FAN_MODE
        )
        if self.config['swing_horizontal_mode'] is not None:
            value = value | ClimateEntityFeature.SWING_HORIZONTAL_MODE
        if self.config['swing_mode'] is not None:
            value = value | ClimateEntityFeature.SWING_MODE
        if self.config.preset_mode is True:
            value = value | ClimateEntityFeature.PRESET_MODE
        return ClimateEntityFeature(value)

    def get_hvac_modes(self) -> list[HVACMode]:
        modes = []
        for mode in self.config.get_values('mode'):
            try:
                modes.append(HVACMode(mode))
            except ValueError:
                pass
        return modes + [HVACMode.OFF]

    def get_fan_modes(self) -> list[str]:
        return self.config.get_values('fan_mode')

    def get_swing_horizontal_modes(self) -> list[str]:
        return self.config.get_values('swing_horizontal_mode')

    def get_swing_modes(self) -> list[str]:
        return self.config.get_values('swing_mode')

    def get_preset_modes(self) -> list[str]:
        return ["none"] + self.config.get_preset_modes()

    def get_eco_sensor_options(self) -> list[str]:
        return self.config.get_values('eco_sensor')

    def set_temperature(self, value: float) -> None:
        self._send_commands([
            {
                "commandName": self.config['target_temperature'],
                "value": str(value)
            }
        ])
        self.target_temperature = value
        self.write_ha_state()

    def _get_status_commands(self, turn_on: bool) -> list[dict]:
        # Power on/off command, with a guaranteed numeric fallback. The value is normally
        # resolved from the option list, but a device whose firmware reports non-standard
        # labels (non-Russian / numeric) may have no usable mapping — in that case fall back
        # to the well-known codes (1=on, 0=off) so the power command is never silently dropped.
        target = "on" if turn_on else "off"
        fallback_value = "1" if turn_on else "0"
        cmds = self.get_commands("status", target)
        if status_code := self.config['status']:
            if not cmds or any(c.get("value") in (None, "None") for c in cmds):
                _LOGGER.warning(
                    f"status mapping for {target!r} not found on {self.device_name}, "
                    f"using fallback value {fallback_value!r}"
                )
                cmds = self.constraint.apply([{
                    "commandName": str(status_code),
                    "value": fallback_value,
                }])
        return cmds

    def switch_on(self, value: str = None) -> None:
        value = value or self.mode or HVACMode.AUTO
        # Always send status=on (do not skip on a cached self.status): the cached value can be
        # stale after an optimistic update or a lost command, which left the AC not turning on.
        self._send_commands([
            *self._get_status_commands(turn_on=True),
            *self.get_commands("mode", value),
        ])
        self.status = 1
        self.mode = value
        self.write_ha_state()

    def switch_off(self) -> None:
        self._send_commands([
            *self._get_status_commands(turn_on=False),
        ])
        self.status = 0
        self.write_ha_state()

    def set_fan_mode(self, value: str) -> None:
        if commands := self.get_commands("fan_mode", value):
            self._send_commands(commands)
            self.fan_mode = value
            self.write_ha_state()

    def set_swing_horizontal_mode(self, value: str) -> None:
        if commands := self.get_commands("swing_horizontal_mode", value):
            self._send_commands(commands)
            self.swing_horizontal_mode = value
            self.write_ha_state()

    def set_swing_mode(self, value: str) -> None:
        if commands := self.get_commands("swing_mode", value):
            self._send_commands(commands)
            self.swing_mode = value
            self.write_ha_state()

    def set_preset_mode(self, value: str) -> None:
        if commands := self.get_commands("preset_mode", value):
            self._send_commands(commands)
            self.preset_mode = value
            self.write_ha_state()

    def set_light_on(self, value: bool) -> None:
        if commands := self.get_commands("light", value):
            self._send_commands(commands)
            self.light_on = value
            self.write_ha_state()

    def set_sound_on(self, value: bool) -> None:
        if commands := self.get_commands("sound", value):
            self._send_commands(commands)
            self.sound_on = value
            self.write_ha_state()

    def set_quiet_on(self, value: bool) -> None:
        if commands := self.get_commands("quiet", value):
            self._send_commands(commands)
            self.quiet_on = value
            self.write_ha_state()

    def set_health_on(self, value: bool) -> None:
        if commands := self.get_commands("health", value):
            self._send_commands(commands)
            self.health_on = value
            self.write_ha_state()

    def set_turbo_on(self, value: bool) -> None:
        if commands := self.get_commands("turbo", value):
            self._send_commands(commands)
            self.turbo_on = value
            self.write_ha_state()

    def set_comfort_on(self, value: bool) -> None:
        if commands := self.get_commands("comfort", value):
            self._send_commands(commands)
            self.comfort_on = value
            self.write_ha_state()

    def set_cleaning_on(self, value: bool) -> None:
        if commands := self.get_commands("cleaning", value):
            self._send_commands(commands)
            self.cleaning_on = value
            self.write_ha_state()

    def set_antifreeze_on(self, value: bool) -> None:
        if commands := self.get_commands("antifreeze", value):
            self._send_commands(commands)
            self.antifreeze_on = value
            self.write_ha_state()

    def set_autohumidity_on(self, value: bool) -> None:
        if commands := self.get_commands("autohumidity", value):
            self._send_commands(commands)
            self.autohumidity_on = value
            self.write_ha_state()

    def set_eco_sensor(self, value: str) -> None:
        if commands := self.get_commands("eco_sensor", value):
            self._send_commands(commands)
            self.eco_sensor = value
            self.write_ha_state()

    def create_entities_climate(self) -> list:
        from . import climate
        return [climate.HaierACEntity(self)]
    
    def create_entities_switch(self) -> list:
        from . import switch
        entities = []
        if self.config['light'] is not None:
            entities.append(switch.HaierACLightSwitch(self))
        if self.config['sound'] is not None:
            entities.append(switch.HaierACSoundSwitch(self))
        if self.config['quiet'] is not None:
            entities.append(switch.HaierACQuietSwitch(self))
        if self.config['turbo'] is not None:
            entities.append(switch.HaierACTurboSwitch(self))
        if self.config['health'] is not None:
            entities.append(switch.HaierACHealthSwitch(self))
        if self.config['comfort'] is not None:
            entities.append(switch.HaierACComfortSwitch(self))
        if self.config['cleaning'] is not None:
            entities.append(switch.HaierACCleaningSwitch(self))
        if self.config['antifreeze'] is not None:
            entities.append(switch.HaierACAntiFreezeSwitch(self))
        if self.config['autohumidity'] is not None:
            entities.append(switch.HaierACAutoHumiditySwitch(self))
        return entities

    def create_entities_select(self) -> list:
        from . import select
        entities = []
        if self.config['eco_sensor'] is not None:
            entities.append(select.HaierACEcoSensorSelect(self))
        return entities


class HaierREF(HaierDevice):

    def __init__(
        self,
        backend_data: dict = None,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.current_fridge_temperature = 0
        self.current_freezer_temperature = 0
        self.current_temperature = 0
        self.fridge_mode = None
        self.freezer_mode = None
        self.my_zone = None
        self.super_cooling = False
        self.super_freeze = False
        self.vacation_mode = False
        self.door_open = False
        self._get_status(backend_data)

    @property
    def config(self) -> CFG.HaierREFConfig:
        return self._config

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({
            "current_fridge_temperature": self.current_fridge_temperature,
            "current_freezer_temperature": self.current_freezer_temperature,
            "current_temperature": self.current_temperature,
            "fridge_mode": self.fridge_mode,
            "freezer_mode": self.freezer_mode,
            "my_zone": self.my_zone,
            "super_cooling": self.super_cooling,
            "super_freeze": self.super_freeze,
            "vacation_mode": self.vacation_mode,
            "door_open": self.door_open,
        })
        return data

    def _load_config_from_attributes(self, data: dict) -> None:
        self._config = CFG.HaierREFConfig(self.device_model, self.hass.config.path(C.DOMAIN))
        attributes = data.setdefault("attributes", [])
        attrs = list(sorted(map(lambda x: CFG.Attribute(x), attributes), key=lambda x: x.code))
        for attr in attrs:
            self.config.attrs.append(attr)
        self.config.merge_attributes()
        for attr in self.config.attrs:
            self._set_attribute_value(str(attr.code), attr.current)
            _LOGGER.debug(f"{self.device_name}: {attr}")

    def _set_attribute_value(self, code: str, value: str) -> None:
        attr = self.config.get_attr_by_code(code)
        if not (attr and value is not None):
            return
        elif attr.name == "current_fridge_temperature":
            self.current_fridge_temperature = float(value)
        elif attr.name == "current_freezer_temperature":
            self.current_freezer_temperature = float(value)
        elif attr.name == "current_temperature":
            self.current_temperature = float(value)
        elif attr.name == "fridge_mode":
            self.fridge_mode = attr.get_item_name(value)
        elif attr.name == "freezer_mode":
            self.freezer_mode = attr.get_item_name(value)
        elif attr.name == "my_zone":
            self.my_zone = attr.get_item_name(value)
        elif attr.name == "super_cooling":
            self.super_cooling = parsebool(attr.get_item_name(value))
        elif attr.name == "super_freeze":
            self.super_freeze = parsebool(attr.get_item_name(value))
        elif attr.name == "vacation_mode":
            self.vacation_mode = parsebool(attr.get_item_name(value))
        elif attr.name == "door_open":
            self.door_open = parsebool(attr.get_item_name(value))

    def get_fridge_mode_options(self) -> list[str]:
        return self.config.get_values('fridge_mode')

    def get_freezer_mode_options(self) -> list[str]:
        return self.config.get_values('freezer_mode')

    def get_my_zone_options(self) -> list[str]:
        return self.config.get_values('my_zone')

    def set_super_cooling(self, value: bool) -> None:
        if commands := self.get_commands("super_cooling", value):
            self._send_single_command(commands[0])
            self.super_cooling = value

    def set_super_freeze(self, value: bool) -> None:
        if commands := self.get_commands("super_freeze", value):
            self._send_single_command(commands[0])
            self.super_freeze = value

    def set_vacation_mode(self, value: bool) -> None:
        if commands := self.get_commands("vacation_mode", value):
            self._send_single_command(commands[0])
            self.vacation_mode = value

    def set_fridge_mode(self, value: str) -> None:
        if commands := self.get_commands("fridge_mode", value):
            self._send_single_command(commands[0])
            self.fridge_mode = value

    def set_freezer_mode(self, value: str) -> None:
        if commands := self.get_commands("freezer_mode", value):
            self._send_single_command(commands[0])
            self.freezer_mode = value

    def set_my_zone(self, value: str) -> None:
        if commands := self.get_commands("my_zone", value):
            self._send_single_command(commands[0])
            self.my_zone = value

    def create_entities_switch(self) -> list:
        from . import switch
        entities = []
        if self.config['super_cooling'] is not None:
            entities.append(switch.HaierREFSuperCoolingSwitch(self))
        if self.config['super_freeze'] is not None:
            entities.append(switch.HaierREFSuperFreezeSwitch(self))
        if self.config['vacation_mode'] is not None:
            entities.append(switch.HaierREFVacationSwitch(self))
        return entities

    def create_entities_select(self) -> list:
        from . import select
        entities = []
        if self.config['fridge_mode'] is not None:
            entities.append(select.HaierREFFridgeModeSelect(self))
        if self.config['freezer_mode'] is not None:
            entities.append(select.HaierREFFreezerModeSelect(self))
        if self.config['my_zone'] is not None:
            entities.append(select.HaierREFMyZoneSelect(self))
        return entities

    def create_entities_sensor(self) -> list:
        from . import sensor
        entities = []
        if self.config['current_temperature'] is not None:
            entities.append(sensor.HaierREFTemperatureSensor(self))
        if self.config['current_fridge_temperature'] is not None:
            entities.append(sensor.HaierREFFridgeTemperatureSensor(self))
        if self.config['current_freezer_temperature'] is not None:
            entities.append(sensor.HaierREFFreezerTemperatureSensor(self))
        if self.config['fridge_mode'] is not None:
            entities.append(sensor.HaierREFFridgeModeSensor(self))
        if self.config['freezer_mode'] is not None:
            entities.append(sensor.HaierREFFreezerModeSensor(self))
        return entities

    def create_entities_binary_sensor(self) -> list:
        from . import binary_sensor
        entities = []
        if self.config['super_cooling'] is not None:
            entities.append(binary_sensor.HaierREFSuperCoolingSensor(self))
        if self.config['super_freeze'] is not None:
            entities.append(binary_sensor.HaierREFSuperFreezeSensor(self))
        if self.config['vacation_mode'] is not None:
            entities.append(binary_sensor.HaierREFVacationSensor(self))
        if self.config['door_open'] is not None:
            entities.append(binary_sensor.HaierREFDoorSensor(self))
        return entities


class HaierWM(HaierDevice):

    def __init__(
        self,
        backend_data: dict = None,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.status = None
        self.program = None
        self.temperature = None
        self.spin_speed = None
        self.remaining_hours = None
        self.remaining_minutes = None
        self.remote_control = None
        self.programs: list[dict] = []
        self._get_status(backend_data)

    @property
    def config(self) -> CFG.HaierWMConfig:
        return self._config

    @property
    def remaining_time(self) -> float | None:
        # A switched-off machine keeps reporting the last remaining time (e.g. 33 min) —
        # that is a stale value, so when status == "off" we show 0.
        if self.status == "off":
            return 0
        if self.remaining_hours is None and self.remaining_minutes is None:
            return None
        return (self.remaining_hours or 0) * 60 + (self.remaining_minutes or 0)

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({
            "status": self.status,
            "program": self.program,
            "temperature": self.temperature,
            "spin_speed": self.spin_speed,
            "remaining_hours": self.remaining_hours,
            "remaining_minutes": self.remaining_minutes,
            "remaining_time": self.remaining_time,
            "remote_control": self.remote_control,
            "programs": [p["name"] for p in self.programs],
        })
        return data

    def _load_config_from_attributes(self, data: dict) -> None:
        self._config = CFG.HaierWMConfig(self.device_model, self.hass.config.path(C.DOMAIN))
        attributes = data.setdefault("attributes", [])
        attrs = list(sorted(map(lambda x: CFG.Attribute(x), attributes), key=lambda x: x.code))
        for attr in attrs:
            self.config.attrs.append(attr)
        self.config.merge_attributes()
        for attr in self.config.attrs:
            self._set_attribute_value(str(attr.code), attr.current)
            _LOGGER.debug(f"{self.device_name}: {attr}")
        # programs are parsed from the full backend_data: allProgram/control sit next to smartDeviceControl
        self._parse_programs(self.status_data)

    def _set_attribute_value(self, code: str, value: str) -> None:
        attr = self.config.get_attr_by_code(code)
        if not (attr and value is not None):
            return
        elif attr.name == "status":
            self.status = attr.get_item_name(value)
        elif attr.name == "program":
            self.program = attr.get_item_name(value)
        elif attr.name == "temperature":
            self.temperature = attr.get_item_name(value)
        elif attr.name == "spin_speed":
            self.spin_speed = attr.get_item_name(value)
        elif attr.name == "remaining_hours":
            self.remaining_hours = parseint(value)
        elif attr.name == "remaining_minutes":
            self.remaining_minutes = parseint(value)
        elif attr.name == "remote_control":
            self.remote_control = parsebool(attr.get_item_name(value, value))

    def _parse_programs(self, data: dict) -> None:
        data = data or {}
        # businessAttributes sit inside smartDeviceControl, while allProgram/control sit next to it.
        sdc = data.get("smartDeviceControl") if isinstance(data.get("smartDeviceControl"), dict) else data
        business = sdc.get("businessAttributes") or data.get("businessAttributes") or []
        all_program = data.get("allProgram") or sdc.get("allProgram") or {}
        control = data.get("control") or sdc.get("control") or {}
        # Program definitions: businessAttributes[].name (link) -> set of commands
        definitions = {}
        for ba in business:
            link = ba.get("name")
            params = ba.get("commandParameters") or {}
            commands = [
                {"commandName": str(a.get("name")), "value": str(a.get("defaultValue"))}
                for a in (params.get("attrNameList") or [])
                if a.get("name") is not None and a.get("defaultValue") is not None
            ]
            if link and commands:
                definitions[link] = commands
        # UI catalog: allProgram.blocks[].programs[] -> Russian names and links
        programs, seen = [], set()
        for block in all_program.get("blocks", []) or []:
            for prog in block.get("programs", []) or []:
                link = ((prog.get("programConfig") or {}).get("link") or {}).get("name")
                name = (prog.get("preview") or {}).get("name")
                commands = definitions.get(link)
                if not (name and commands) or name in seen:
                    continue
                seen.add(name)
                programs.append({
                    "name": name,
                    "link": link,
                    "template_id": prog.get("templateId"),
                    "commands": commands,
                })
        self.programs = programs
        # Current program, if the device reports it
        current = (control.get("currentProgram") or {}).get("title")
        if current:
            self.program = current

    def _ensure_remote_control(self) -> None:
        # When remote control is disabled the machine (like the native app) only allows
        # viewing the parameters, not changing them.
        # remote_control is None means the model does not report this attribute — do not block then.
        if self.remote_control is False:
            raise HomeAssistantError(
                translation_domain=C.DOMAIN,
                translation_key="remote_control_disabled",
            )

    def get_program_options(self) -> list[str]:
        if self.programs:
            return [p["name"] for p in self.programs]
        return self.config.get_values('program')

    def get_temperature_options(self) -> list[str]:
        return self.config.get_values('temperature')

    def get_spin_speed_options(self) -> list[str]:
        return self.config.get_values('spin_speed')

    def set_program(self, value: str) -> None:
        self._ensure_remote_control()
        program = next((p for p in self.programs if p["name"] == value), None)
        if program is not None:
            if program["commands"]:
                self._send_commands(program["commands"])
                self.program = value
            return
        # fallback: a single attribute from YAML
        if commands := self.get_commands("program", value):
            self._send_single_command(commands[0])
            self.program = value

    def set_temperature(self, value: str) -> None:
        self._ensure_remote_control()
        if commands := self.get_commands("temperature", value):
            self._send_single_command(commands[0])
            self.temperature = value

    def set_spin_speed(self, value: str) -> None:
        self._ensure_remote_control()
        if commands := self.get_commands("spin_speed", value):
            self._send_single_command(commands[0])
            self.spin_speed = value

    def create_entities_select(self) -> list:
        from . import select
        entities = []
        if self.programs or self.config['program'] is not None:
            entities.append(select.HaierWMProgramSelect(self))
        if self.config['temperature'] is not None:
            entities.append(select.HaierWMTemperatureSelect(self))
        if self.config['spin_speed'] is not None:
            entities.append(select.HaierWMSpinSpeedSelect(self))
        return entities

    def create_entities_sensor(self) -> list:
        from . import sensor
        entities = []
        if self.config['remaining_minutes'] is not None or self.config['remaining_hours'] is not None:
            entities.append(sensor.HaierWMRemainingTimeSensor(self))
        if self.config['status'] is not None:
            entities.append(sensor.HaierWMStatusSensor(self))
        return entities

    def create_entities_binary_sensor(self) -> list:
        from . import binary_sensor
        entities = []
        if self.config['remote_control'] is not None:
            entities.append(binary_sensor.HaierWMRemoteControlSensor(self))
        return entities


def parsebool(value) -> bool:
    if value in ("on", 1, True, "true", "enable", "1"):
        return True
    return False


def parseint(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parsefloat(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
