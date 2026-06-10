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
- `devices/*.yaml` — per-model device descriptions (attribute IDs + mappings). `default.yaml` is the fallback.
- `translations/{ru,en}.json` — HA UI translations.

## Architecture

### Object model (all in `api.py`)
- **`Haier`** — one per config entry. Owns auth/token lifecycle, the REST calls, the device list, and the single WebSocket connection (run in a daemon thread). Holds class-level rate limiters shared across instances.
- **`HaierDevice`** base + **`HaierAC`** (air conditioner), **`HaierREF`** (refrigerator), **`HaierWM`** (washing machine) subclasses. Each holds the device's live state as plain attributes, knows how to translate WS status messages into those attributes (`_set_attribute_value`), and builds HA entities (`create_entities_*`). `HaierDevice.create()` is the factory that maps the API `device_type` string (`"AC"`, `"REF"`, `"WM"`) to the subclass.
- **`HaierAPI`** (a `HomeAssistantView`) — debug HTTP endpoint, see below.
- **`AuthResponse`** — wraps the login/refresh JSON and raises typed auth errors.

### `config.py` — device config + translation
- **`HaierDeviceConfig`** + `HaierACConfig` / `HaierREFConfig` / `HaierWMConfig`. On init it loads `devices/<model>.yaml` (or `default.yaml`), and **copies it to a user-editable file** at `<hass_config>/.<model>.yaml` so end users can override mappings without touching the package. It then merges the static YAML attributes with the live attribute list fetched from the API (`merge_attributes`).
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
- **Realtime in**: WS message → `Haier._on_message` → routed by mac to `HaierDevice.on_message` → `_set_attribute_value` per property → `write_ha_state()` (pushed to HA via `hass.loop.call_soon_threadsafe`, because the WS runs off the event loop).
- **Commands out**: HA entity method → `device.set_*` → `get_commands` (resolves attr code + value code, applies `Constraint`) → `_send_commands` → `_send_group_command` (if the config has a `command_name`) or `_send_single_command` → WS `send`. Device state is also optimistically updated locally.

### Auth & rate limiting
- Tokens (access + refresh, with expiry) are persisted to `<hass_config>/haier_evo` (a JSON file named after `DOMAIN`). `auth()` refreshes or re-logs-in based on expiry; timezone is hardcoded to UTC+3.
- `ResettableLimits` (in `limits.py`) wraps each network operation. On `429`/`5xx` the period is extended (`add_period`) and backed off; on success it's reset. Login and refresh limiters reset each other so the two paths don't starve.

### Debug HTTP endpoint
`HaierAPI` registers `GET/POST /api/haier_evo`:
- **GET** returns the full state (`Haier.to_dict()` → socket status, raw backend payload, and every device's parsed config + current values). This is the single best tool for diagnosing device support — it shows exactly what the API returned and how the integration parsed it.
- **POST** sends a raw JSON message straight to the WebSocket.
Both are gated by per-instance toggles (`allow_http` / `allow_http_post`) surfaced as the "Haier Evo HTTP GET/POST" switches; GET defaults on (`API_HTTP_ROUTE`), POST off.

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
- **Releasing (RULE): every time a push is requested, FIRST bump `version` in `custom_components/haier_evo/manifest.json`, then push to `main`.** The `release.yml` workflow reads the manifest version and auto-creates a matching GitHub Release (which HACS consumes). Do NOT create git tags manually — the release owns its tag. Minimum HA version is declared in `hacs.json` (`2024.12.0`). Runtime deps (`requests`, `websocket-client`, `ratelimit`) are in the manifest's `requirements`; the code also imports `tenacity` and `aiohttp` (provided by HA).
- Commit messages in this repo are typically prefixed with the GitHub issue number (e.g. `#74 ...`).