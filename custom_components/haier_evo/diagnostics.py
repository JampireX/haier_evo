"""Diagnostics for a Haier Evo config entry.

The data the debug endpoint GET /api/haier_evo shows (what the cloud returned and how the
integration parsed it), with personal data removed, so that it can be attached to an issue.
MAC addresses are kept: they tie the data to the log lines.
"""
from __future__ import annotations
import json
import re
from typing import Any
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


REDACTED = "**REDACTED**"
TOKEN_KEYS = {"token", "accessToken", "refreshToken", "refreshtoken", "access_token", "refresh_token"}
SERIAL_KEYS = {"serialNumber", "serialNum", "serial_number", "serial", "device_serial"}
# Redacted wherever they appear, at any depth.
TO_REDACT = {"email", "password", "phone", *TOKEN_KEYS, *SERIAL_KEYS}
# Serial numbers also travel inside the devices' deep links (evo://...&deviceId=..&serialNum=..).
_SERIAL_PARAM = re.compile(r"(serialNum(?:ber)?=)[^&#\s\"']+", re.IGNORECASE)
# Shorter values are not searched for inside other strings: too likely to match something else.
_MIN_SECRET_LEN = 6


def _collect(value: Any, keys: set[str], found: set[str]) -> None:
    """Collect the values stored under `keys`, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, (str, int)) and len(str(item)) >= _MIN_SECRET_LEN:
                found.add(str(item))
            _collect(item, keys, found)
    elif isinstance(value, list):
        for item in value:
            _collect(item, keys, found)


def _scrub(value: Any, secrets: re.Pattern | None) -> Any:
    """Remove secrets embedded in longer strings (deep links, titles, the unique_id)."""
    if isinstance(value, dict):
        return {key: _scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, secrets) for item in value]
    if isinstance(value, str):
        value = _SERIAL_PARAM.sub(lambda m: m.group(1) + REDACTED, value)
        return secrets.sub(REDACTED, value) if secrets is not None else value
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    haier = getattr(entry, "runtime_data", None)
    state: Any = None
    if haier is not None:
        try:
            # A JSON round trip gives a private, JSON-safe copy of the state that the websocket
            # thread keeps updating.
            state = json.loads(json.dumps(haier.to_dict(), default=str))
        except Exception as e:  # still return the entry part
            state = {"error": f"{type(e).__name__}: {e}"}
    found: set[str] = set()
    if email := str(entry.data.get("email") or "").strip():
        found.add(email)
    _collect(state, TOKEN_KEYS | SERIAL_KEYS, found)
    secrets = re.compile(
        "|".join(re.escape(s) for s in sorted(found, key=len, reverse=True)), re.IGNORECASE
    ) if found else None
    data = {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "version": entry.version,
            "minor_version": getattr(entry, "minor_version", None),
            "state": getattr(entry.state, "value", str(entry.state)),
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "haier": state,
    }
    return async_redact_data(_scrub(data, secrets), TO_REDACT)
