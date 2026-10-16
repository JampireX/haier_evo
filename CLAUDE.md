# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`haier_evo` is a Home Assistant **custom component** (distributed via HACS) that integrates Haier appliances controlled through the **Haier Evo** cloud app (RU / KZ / BY markets). It is cloud-polling + WebSocket push: device discovery and initial state come over REST, then realtime updates and command sending happen over a WebSocket. `iot_class` is `cloud_polling`.

All user-facing strings, attribute names, and value mappings are in **Russian** — see "Attribute/value translation" below, it is central to how the integration works.

## Project layout

Everything lives under `custom_components/haier_evo/`. There is no `src/`, no package root above it.

- `__init__.py` — HA entry points (`async_setup_entry` / `async_unload_entry`).
- `api.py` (~1450 lines) — the whole client + device model. Most work happens here.
- `config.py` — device-config layer: YAML loading, attribute merging, and the Russian→canonical name/value translation tables.
- `const.py` — API endpoints + rate-limit constants.
- `limits.py` — `ResettableLimits`, a rate-limit decorator with exponential backoff.
- `config_flow.py` — HA config/options UI (email, password, region).
- `climate.py`, `switch.py`, `select.py`, `sensor.py`, `binary_sensor.py` — thin HA entity wrappers, one per platform.
- `devices/*.yaml` — per-model device descriptions (attribute IDs + mappings). `default.yaml` is the fallback. File names are the model sanitized and **cut to 11 chars**. `devices/known_versions.json` lists the sha256 of every version of each file ever shipped (see the copy policy below).
- `diagnostics.py` — HA diagnostics download (entry + `Haier.to_dict()`, email/password/tokens/serials redacted).
- `frontend/haier-evo-ac-card.js` — the Lovelace card `custom:haier-evo-ac-card`, auto-registered by `__init__._async_register_frontend` (`add_extra_js_url`, cache-busted with `?v=<version>`). Vanilla JS, no build step, wrapped in an IIFE; minimal by default, every option is documented in its header comment and in README; visual editor via `static getConfigElement()` → `haier-evo-ac-card-editor`, a small element wrapping `ha-form` (shows the real defaults, keeps YAML-only values). Value labels come from HA translations (`formatEntityState` / `formatEntityAttributeValue`). `www/haier-ac-card.js` (`custom:haier-ac-card`) is an older, manually installed card.
- `translations/{ru,en}.json` — HA UI translations.

## Architecture

### Object model (all in `api.py`)
- **`Haier`** — one per config entry. Owns auth/token lifecycle, the REST calls, the device list, and the single WebSocket connection (run in a daemon thread). Holds class-level rate limiters shared across instances.
- **`HaierDevice`** base + **`HaierAC`** (air conditioner), **`HaierREF`** (refrigerator), **`HaierWM`** (washing machine) subclasses. Each holds the device's live state as plain attributes, knows how to translate WS status messages into those attributes (`_set_attribute_value`), and builds HA entities (`create_entities_*`). `HaierDevice.create()` is the factory that maps the API `device_type` string (`"AC"`, `"REF"`, `"WM"`) to the subclass.
- **`HaierAPI`** (a `HomeAssistantView`) — debug HTTP endpoint, see below.
- **`AuthResponse`** — wraps the login/refresh JSON and raises typed auth errors.

### `config.py` — device config + translation
- **`HaierDeviceConfig`** + `HaierACConfig` / `HaierREFConfig` / `HaierWMConfig`. On init it loads `devices/<model>.yaml` (or `default.yaml`), and **copies it to a user-editable file** at `<hass_config>/haier_evo.<model>.yaml` so end users can override mappings without touching the package. Copy policy (`_find_config_file`): a copy whose sha256 is in `known_versions.json` (an untouched copy of any shipped version, or of `default.yaml`) is replaced by the current package file, which is used; a copy the user edited wins and is logged once. **When you change a `devices/*.yaml` (or add one), add its new sha256 to `known_versions.json`** — otherwise copies of it would count as user edits after the next change. It then merges the static YAML attributes with the live attribute list fetched from the API (`merge_attributes`).
- **`Attribute` / `Range` / `Item`** — `Item` subclasses (`Mode`, `FanMode`, `SwingMode`, `EcoSensor`, `Temperature`, …) carry the Russian→canonical value `mappings`. `Attribute.name` carries the Russian→canonical *attribute-name* dict.
- **`Constraint`** — some commands require sidecar commands; `Constraint.apply()` PREPENDs/APPENDs them based on `pendingCondition`/`additionalCommands` returned by the API.

### Attribute/value translation (the key concept)
Raw Haier data uses numeric codes and Russian labels. The integration normalizes both:
1. **Attribute name**: the Russian `attrname`/description (e.g. `"Режимы"`, `"Целевая температура"`) is mapped to a canonical name (`"mode"`, `"target_temperature"`) via the dict in `Attribute.__init__` (`config.py`). Unmapped → `"unknown"`.
2. **Value**: each option's Russian description (e.g. `"Охлаждение"`) is mapped to a canonical value (`"cool"`) via the `mappings` of the matching `Item` subclass.
3. Device classes only ever switch on the **canonical** name in `_set_attribute_value`.

So **adding support for a new feature or device usually means editing these tables**, not the control flow.

### Data flow
- **Setup**: `load_tokens` → `pull_data` (REST: parse the SDUI "smartHome" page to extract each device's type/mac/serial from deep-links) → per device `pull_device_data` (REST: detailed config + current values) → build device objects → `connect_in_thread` (open WebSocket).
- **Realtime in**: WS message → `Haier._on_message` → routed by mac to `HaierDevice.on_message` → `_set_attribute_value` per property → `write_ha_state()` (pushed to HA via `hass.loop.call_soon_threadsafe`, because the WS runs off the event loop). The cloud pushes a full status snapshot right after a session opens; a session nothing arrives over within `WS_SILENT_SESSION_TIMEOUT` is logged as not served (`_check_silent_session`).
- **Commands out**: HA entity method → `device.set_*` → `get_commands` (resolves attr code + value code, applies `Constraint`) → `_send_commands` → `_send_group_command` (if the config has a `command_name`) or `_send_single_command` → WS `send`. Device state is also optimistically updated locally. For the AC everything goes through `HaierAC._command(commands, names)`: it drops a command for a requested attribute (`names`) that the API **modifiers** (`config.Modifier`: `W` read-only / `V` value range, evaluated on the raw state `_raw`) forbid right now — never the side commands the constraints add, the app sends those too — and applies the sent commands (side effects included) to the local state. Power-on from off sends the power command alone and the rest as a follow-up (`then`) once it is acknowledged (`_send_followup`); `turn_on` never resends the current mode. Record + send are serialized per device by `_cmd_lock`. The cloud answers every command with a `command_response` (same `trace`, `errNo`) in ~1 s; the ACK watchdog (`_expect_ack` / `_on_ack_timeout`, `WS_CMD_ACK_TIMEOUT`) re-establishes the WS session and resends an unanswered command once (not values the device already reports or a newer command replaced), then logs `CMD LOST` and re-pulls the real state.

### Auth & rate limiting
- Tokens (access + refresh, with expiry) are persisted per config entry to `<hass_config>/haier_evo_tokens_<entry_id>.json`, bound to the entry's email + region (ignored after a credentials change, deleted in `async_remove_entry`; the legacy shared `<hass_config>/haier_evo` file is removed on setup). `auth()` refreshes or re-logs-in based on the *local* expiry; timezone is hardcoded to UTC+3.
- The server can reject a token before its local expiry: REST answers `401`, the WS gateway accepts the upgrade (`101`) and then closes with `1007 "Auth token not valid!"`. Both call `invalidate_token()`, which expires it locally so the next `auth()` refreshes it (not for a token younger than `TOKEN_REJECT_MIN_AGE`, to avoid a refresh loop). A REST `401` for the token the WS session was opened with also restarts that session (`restart_session`).
- Setup errors: rejected email/password → `ConfigEntryAuthFailed` (reauth flow; also triggered at runtime via `on_auth_failed`), network / 429 / 5xx → `ConfigEntryNotReady` (HA retries; after a 429 an extra per-entry backoff `_SETUP_BACKOFF` keeps retries off the cloud).
- `ResettableLimits` (in `limits.py`) wraps each network operation. On `429`/`5xx` (and other HTTP errors of sign-in/refresh) the period is extended (`add_period`) and backed off; on success it's reset. Login and refresh limiters reset each other so the two paths don't starve. The limiters are class-level (one source IP): `_reset_limits_if_alone()` resets them only when no other entry is running. `auth()` does the login/refresh under `_auth_lock`, outside `self._lock`.
- WS reconnects back off (5 s → `WS_RECONNECT_BACKOFF_MAX`) after sessions that fail or close before delivering anything; sessions we drop ourselves (`_drop_session`: `keep_running=False` + `abort()`, never `WebSocketApp.close()` from another thread — in websocket-client 1.9.2 it can wipe the next session's socket) don't count. Errors reach the log through `on_error` (`_on_error`); the `websocket` logger stays muted. A REST path answering 401 even with a fresh token is remembered (`_non_token_401`) and no longer forces token refreshes.

### Debug HTTP endpoint
`HaierAPI` registers `GET/POST /api/haier_evo` (requires HA authentication; one view serves all entries via `instances`, GET returns `{"entries": …}` when several allow it, POST is routed by `macAddress`):
- **GET** returns the full state (`Haier.to_dict()` → socket status, raw backend payload, and every device's parsed config + current values). This is the single best tool for diagnosing device support — it shows exactly what the API returned and how the integration parsed it.
- **POST** sends a raw JSON message straight to the WebSocket.
Both are gated by per-instance toggles (`allow_http` / `allow_http_post`) surfaced as the "Haier Evo HTTP GET/POST" switches (per entry, state restored); GET defaults on (`API_HTTP_ROUTE`), POST off. For users the HA diagnostics download replaces it.

## Device YAML format (`devices/<MODEL>.yaml`)
The model name is the API `model` field, sanitized (`-` and `/` stripped, truncated to 11 chars) in `HaierDevice._get_status`.

```yaml
command_name: "6"          # optional; if set, commands are batched in one "operation" message
attributes:
  - name: status           # canonical attribute name (must match the dicts in config.py)
    id: "19"               # Haier attribute code from the API
    mappings:              # optional; canonical value <-> raw haier code
      - haier: "false"
        value: "off"
      - haier: "true"
        value: "on"
commands:                  # optional; named hardcoded command lists, looked up by get_command_by_name
preset_modes:              # optional (AC only); extra preset-mode names
```
A blank `id: ""` means "rely on whatever the API reports" (see `default.yaml`).

## Working in this repo

- **No build, no test suite, no linter** is configured. CI runs in `.github/workflows/`: `validate.yml` (HACS + hassfest metadata validation) and `release.yml` (auto-release, see below) — neither checks the Python.
- **To run/iterate**: copy (or symlink) `custom_components/haier_evo` into a real Home Assistant `config/custom_components/`, restart HA, add the integration with Evo credentials, and watch HA logs (`custom_components.haier_evo` logger, set to debug). The GET debug endpoint above is the fastest way to inspect parsed state.
- **Releasing (RULE): every time a push is requested, FIRST bump `version` in `custom_components/haier_evo/manifest.json`, then push to `main`.** The `release.yml` workflow reads the manifest version and auto-creates a matching GitHub Release (which HACS consumes). Do NOT create git tags manually — the release owns its tag. Minimum HA version is declared in `hacs.json` (`2024.12.0`). Runtime deps in the manifest's `requirements`: `websocket-client>=1.9.2` (1.9.1 breaks `WebSocketApp.close()`), `ratelimit==2.2.1` (unmaintained; `limits.py` relies on its internals) and `tenacity` (NOT an HA core dependency — it is only present in Docker/OS images through other integrations, a Core venv may lack it). `requests` and `aiohttp` come with HA core and hassfest rejects listing them. Keep shared libraries unpinned or with low minimums: they share HA's environment with every other integration, and a conflicting pin makes the requirement install (and the whole integration) fail.
- Commit messages in this repo are typically prefixed with the GitHub issue number (e.g. `#74 ...`).