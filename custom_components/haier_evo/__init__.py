from __future__ import annotations
import time
from pathlib import Path
from datetime import timedelta
from requests.exceptions import RequestException
from tenacity import RetryError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.loader import async_get_integration
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers.event import async_track_time_interval
from .logger import _LOGGER
from .const import DOMAIN, WS_HEARTBEAT_INTERVAL, LOGIN_LIMIT_429, LOGIN_LIMIT_MAX
from . import api


PLATFORMS: list[str] = [
    Platform.CLIMATE,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

FRONTEND_URL = f"/{DOMAIN}/haier-evo-ac-card.js"

# entry_id -> (consecutive rate-limited setups, monotonic time before which a setup must not hit
# the cloud). HA retries ConfigEntryNotReady as often as every 80s — without this a 429 at
# startup would become a login attempt every minute and could get the IP blacklisted.
_SETUP_BACKOFF: dict[str, tuple[int, float]] = {}


def _is_rate_limited(exc: BaseException | None) -> bool:
    for _ in range(10):  # walk the cause/context chain (bounded: it can be cyclic)
        if exc is None:
            return False
        response = getattr(exc, "response", None)
        if isinstance(exc, api.ManyRequestsError) or getattr(response, "status_code", None) == 429:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


async def _async_stop(hass: HomeAssistant, haier_object: api.Haier) -> None:
    # stop() ends the websocket session: keep that socket work off the event loop. A failure
    # here must not mask the setup error being raised or fail the unload.
    try:
        await hass.async_add_executor_job(haier_object.stop)
    except Exception as e:
        _LOGGER.warning(f"Error stopping {haier_object!r}: {e!r}")


async def _async_register_frontend(hass: HomeAssistant, version: str) -> None:
    if hass.data.get(f"{DOMAIN}_frontend_registered"):
        return
    hass.data[f"{DOMAIN}_frontend_registered"] = True
    card_path = Path(__file__).parent / "frontend" / "haier-evo-ac-card.js"
    await hass.http.async_register_static_paths([
        StaticPathConfig(FRONTEND_URL, str(card_path), cache_headers=False),
    ])
    add_extra_js_url(hass, f"{FRONTEND_URL}?v={version}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    integration = await async_get_integration(hass, DOMAIN)
    _LOGGER.debug(f'Integration version: {integration.version}')
    await _async_register_frontend(hass, integration.version)
    tries, not_before = _SETUP_BACKOFF.get(entry.entry_id, (0, 0.0))
    if (wait := not_before - time.monotonic()) > 0:
        raise ConfigEntryNotReady(f"Haier Evo rate limit (429), next attempt in {wait:.0f}s")
    username = entry.data.get("email") or ""
    password = entry.data.get("password") or ""
    region = entry.data.get("region") or "ru"
    haier_object = api.Haier(hass, username, password, region, entry_id=entry.entry_id)
    try:
        await hass.async_add_executor_job(haier_object.load_tokens)
        await hass.async_add_executor_job(haier_object.pull_data)
    except Exception as e:
        # Release the half-initialised object, so a retry starts from scratch.
        await _async_stop(hass, haier_object)
        if isinstance(e, api.InvalidCredentials):
            raise ConfigEntryAuthFailed(str(e)) from e
        if not isinstance(e, (api.InvalidAuth, api.InvalidDevicesList, RequestException, RetryError)):
            raise
        # A network/cloud failure (no connectivity at boot, timeout, 429, 5xx) is transient:
        # let HA retry the setup instead of leaving the entry in a permanent setup error.
        if _is_rate_limited(e):
            delay = min(LOGIN_LIMIT_429 * 2 ** tries, LOGIN_LIMIT_MAX)
            _SETUP_BACKOFF[entry.entry_id] = (tries + 1, time.monotonic() + delay)
        raise ConfigEntryNotReady(f"Haier Evo cloud unavailable: {type(e).__name__}: {e}") from e
    _SETUP_BACKOFF.pop(entry.entry_id, None)
    # Credentials rejected later (e.g. the password was changed in the app and the tokens
    # were revoked) start HA's re-auth flow instead of the integration silently stopping.
    haier_object.on_auth_failed = lambda: hass.loop.call_soon_threadsafe(
        entry.async_start_reauth, hass
    )
    entry.runtime_data = haier_object
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Background heartbeat: reconnect a stale/zombie WS session and re-pull state over REST,
    # so devices the user only observes (e.g. an idle washing machine) keep updating instead
    # of silently freezing. Runs off the event loop; auto-cancelled on unload.
    async def _heartbeat(now) -> None:
        await hass.async_add_executor_job(haier_object.heartbeat)

    entry.async_on_unload(
        async_track_time_interval(hass, _heartbeat, timedelta(seconds=WS_HEARTBEAT_INTERVAL))
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        haier_object = entry.runtime_data
        _LOGGER.debug(f'Integration {haier_object} unload...')
        await _async_stop(hass, haier_object)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Forget the entry's tokens: re-adding the integration (maybe with another account)
    # must start with a fresh login.
    _SETUP_BACKOFF.pop(entry.entry_id, None)
    await hass.async_add_executor_job(api.Haier.remove_tokens_file, hass, entry.entry_id)
