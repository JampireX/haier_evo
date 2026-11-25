from __future__ import annotations
import re
import time
from pathlib import Path
from datetime import timedelta
from requests.exceptions import RequestException
from tenacity import RetryError
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.loader import async_get_integration
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from .logger import _LOGGER
from .const import (
    DOMAIN, WS_HEARTBEAT_INTERVAL, LOGIN_LIMIT_429, LOGIN_LIMIT_MAX, WM_ACTIVE_POLL_TICK,
)
from . import api


PLATFORMS: list[str] = [
    Platform.CLIMATE,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

FRONTEND_URL = f"/{DOMAIN}/haier-evo-ac-card.js"

# A device of this integration in the device registry is identified by its MAC (the HTTP debug
# switches have a device of their own, with another identifier).
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
# The cloud sometimes leaves a unit out of the list right after an outage: a setup that misses
# devices the entry has is retried this many times before it starts with what was listed.
SETUP_INCOMPLETE_RETRIES = 3
_INCOMPLETE_SETUPS: dict[str, int] = {}
# entry_id -> the MACs a setup gave up waiting for (not waited for again until HA restarts).
_GIVEN_UP: dict[str, set[str]] = {}

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


def _known_macs(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    # MACs of the Haier devices this entry created before.
    return {
        str(identifier).lower()
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for domain, identifier in device.identifiers
        if domain == DOMAIN and _MAC.match(str(identifier).lower())
    }


def _check_complete(hass: HomeAssistant, entry: ConfigEntry, haier_object: api.Haier) -> None:
    # Fewer devices listed than the entry has: retry the setup a few times, instead of starting
    # without them (their entities would be unavailable until the next restart). A unit that stays
    # away is waited for once per HA start; deleting it in HA ends the wait for good.
    missing = _known_macs(hass, entry) - haier_object.listed_macs
    if not missing or missing <= _GIVEN_UP.get(entry.entry_id, set()):
        _INCOMPLETE_SETUPS.pop(entry.entry_id, None)
        return
    attempts = _INCOMPLETE_SETUPS.get(entry.entry_id, 0) + 1
    if attempts <= SETUP_INCOMPLETE_RETRIES:
        _INCOMPLETE_SETUPS[entry.entry_id] = attempts
        raise api.InvalidDevicesList(
            f"the Evo «Мой дом» page did not list {len(missing)} device(s) of this entry "
            f"(attempt {attempts}/{SETUP_INCOMPLETE_RETRIES})"
        )
    _INCOMPLETE_SETUPS.pop(entry.entry_id, None)
    _GIVEN_UP[entry.entry_id] = missing
    _LOGGER.warning(
        f"Haier Evo still does not list {', '.join(sorted(missing))}: starting with the "
        f"{len(haier_object.listed_macs)} device(s) it listed. If a device is gone from the Evo app, "
        f"delete it in Home Assistant (its page, the menu, Delete)."
    )


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
        if haier_object.no_devices_listed and dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        ):
            # The Evo page listed none of the devices this entry has: the cloud is failing (in a
            # user's log the setup 4 s later found them all). HA retries the setup, instead of the
            # entry loading without devices until a manual reload.
            raise api.InvalidDevicesList("the Evo «Мой дом» page listed none of this entry's devices")
        _check_complete(hass, entry, haier_object)
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

    # A washing machine that is on is re-read over REST every ~15 s: the cloud pushes the phase
    # and the remaining time of a wash only every few minutes (see Haier.poll_active_laundry).
    # The check itself is cheap and stays on the event loop while every machine is off.
    async def _poll_laundry(now) -> None:
        if haier_object.wants_laundry_poll():
            await hass.async_add_executor_job(haier_object.poll_active_laundry)

    entry.async_on_unload(
        async_track_time_interval(hass, _poll_laundry, timedelta(seconds=WM_ACTIVE_POLL_TICK))
    )
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    # A device may be deleted in the UI once the cloud no longer lists it (without this hook HA
    # offers no way to delete it, and a unit removed from the Evo account stayed for good). Not
    # while the entry is not loaded: there is no telling which units the cloud still has.
    haier_object = getattr(entry, "runtime_data", None)
    if entry.state is not ConfigEntryState.LOADED or not isinstance(haier_object, api.Haier):
        return False
    macs = {
        str(identifier).lower() for domain, identifier in device_entry.identifiers
        if domain == DOMAIN and _MAC.match(str(identifier).lower())
    }
    if not macs:
        return False  # the HTTP debug switches' device: it belongs to the entry itself
    removable = not (macs & haier_object.listed_macs)
    if removable:
        for mac in macs:
            _GIVEN_UP.get(entry.entry_id, set()).discard(mac)
    return removable


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
    _INCOMPLETE_SETUPS.pop(entry.entry_id, None)
    _GIVEN_UP.pop(entry.entry_id, None)
    await hass.async_add_executor_job(api.Haier.remove_tokens_file, hass, entry.entry_id)
