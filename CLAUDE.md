# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`haier_evo` is a Home Assistant **custom component** (distributed via HACS) that integrates Haier appliances controlled through the **Haier Evo** cloud app (RU / KZ / BY markets). It is cloud-polling + WebSocket push: device discovery and initial state come over REST, then realtime updates and command sending happen over a WebSocket. `iot_class` is `cloud_polling`.

All user-facing strings, attribute names, and value mappings are in **Russian** — see "Attribute/value translation" below, it is central to how the integration works.

## Project layout

Everything lives under `custom_components/haier_evo/`. There is no `src/`, no package root above it.

- `__init__.py` — HA entry points (`async_setup_entry` / `async_unload_entry`; `async_remove_config_entry_device` lets a device the cloud no longer lists be deleted in the UI).
- `api.py` (~1450 lines) — the whole client + device model. Most work happens here.
- `config.py` — device-config layer: YAML loading, attribute merging, and the Russian→canonical name/value translation tables.
- `const.py` — API endpoints + rate-limit constants.
- `limits.py` — `ResettableLimits`, an asyncio rate limiter (N calls per window, window lengthened on 429/5xx, reset wakes the waiting calls).
- `config_flow.py` — HA config/options UI (email, password, region).
- `climate.py`, `switch.py`, `select.py`, `sensor.py`, `binary_sensor.py` — thin HA entity wrappers, one per platform.
- `devices/*.yaml` — per-model device descriptions (attribute IDs + mappings). `default.yaml` is the fallback. File names are the model sanitized and **cut to 11 chars**. `devices/known_versions.json` lists the sha256 of every version of each file ever shipped (see the copy policy below).
- `diagnostics.py` — HA diagnostics download (entry + `Haier.to_dict()`, email/password/tokens/serials redacted).
- `frontend/haier-evo-ac-card.js` — the Lovelace card `custom:haier-evo-ac-card`, auto-registered by `__init__._async_register_frontend` (`add_extra_js_url`, cache-busted with `?v=<version>`). Vanilla JS, no build step, wrapped in an IIFE; minimal by default, every option is documented in its header comment and in README; visual editor via `static getConfigElement()` → `haier-evo-ac-card-editor`, a small element wrapping `ha-form` (shows the real defaults, keeps YAML-only values). Value labels come from HA translations (`formatEntityState` / `formatEntityAttributeValue`). `www/haier-ac-card.js` (`custom:haier-ac-card`) is an older, manually installed card.
- `translations/{ru,en}.json` — HA UI translations.

## Architecture

### Execution model
Everything runs in Home Assistant's event loop (asyncio since 0.4.0; up to 0.3.28 the integration used `requests` in executor threads and websocket-client in a daemon thread). REST and the websocket go through HA's shared aiohttp session (`async_get_clientsession`). No own threads, no thread locks: device state is changed only between awaits; `asyncio.Lock`s guard what spans an await (`_cmd_lock` record + send of a command, `_send_lock` websocket writes, `_auth_lock` sign-in). Timers are `loop.call_later` handles, background work is tasks created through `_create_task` (hub and device keep them in `_tasks`; `async_stop` / `cancel_pending` cancel them on unload; nothing may outlive an unloaded entry). File work (tokens, device YAML) runs in the executor: the YAML of a model is read by `config.async_preload` before a description is parsed, the configs built in the loop take a deep copy of it.

### Object model (all in `api.py`)
- **`Haier`** — one per config entry. Owns auth/token lifecycle, the REST calls, the device list, and the single WebSocket connection (an asyncio task, `_async_ws_loop`). Holds class-level rate limiters shared across instances.
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
- **Setup**: `async_load_tokens` → `async_pull_data` (REST: parse the SDUI "smartHome" page to extract each device's type/mac/serial from deep-links) → per device `HaierDevice.async_create` (REST `async_pull_device_data`: detailed config + current values; YAML preload) → `start_websocket` (the connection loop task).
- **Realtime in**: WS message → `Haier._on_message` → routed by mac to `HaierDevice.on_message` → `_set_attribute_value` per property → `write_ha_state()` (calls the entities' `async_write_ha_state` directly: everything is in the loop). The cloud pushes a full status snapshot right after a session opens; a session nothing arrives over within `WS_SILENT_SESSION_TIMEOUT` is dropped and re-established (`_check_silent_session`; `connect()` backs off while sessions stay silent, the `WS_SILENT_SESSIONS_FRESH_TOKEN`-th one in a row goes with a refreshed token, and a command that needs the session cuts the backoff wait short via `_wake`).
- **REST vs push**: a REST read (`refresh`) applied after a WS push of the same code that arrived once the read was requested keeps the pushed value (`_pushed_at` / `_read_started` in `_stale_codes`), just like values of commands still in flight.
- **Washing machine polling**: the cloud pushes the phase / remaining time of a wash only every few minutes, so while a `HaierWM` is on (`cycle_active`) it is re-read over REST (`refresh`) once nothing about the cycle arrived for `WM_ACTIVE_POLL_INTERVAL` s (`needs_poll`; a WS push of its state/phase/time codes postpones it). A `WM_ACTIVE_POLL_TICK` timer in `__init__` checks `Haier.wants_laundry_poll()` and runs `async_poll_active_laundry`; failed reads back off up to `WM_ACTIVE_POLL_BACKOFF_MAX`.
- **Commands out**: HA entity method → `device.set_*` → `get_commands` (resolves attr code + value code, applies `Constraint`) → `_send_commands` → `_send_group_command` (if the config has a `command_name`) or `_send_single_command` → WS `send`. Device state is also optimistically updated locally. For the AC everything goes through `HaierAC._command(commands, names)`: it drops a command for a requested attribute (`names`) that the API **modifiers** (`config.Modifier`: `W` read-only / `V` value range, evaluated on the raw state `_raw`) forbid right now — never the side commands the constraints add, the app sends those too — and applies the sent commands (side effects included) to the local state. Power-on from off sends the power command alone and the rest as a follow-up (`then`) once it is acknowledged (`_send_followup`); `turn_on` never resends the current mode. Record + send are serialized per device by `_cmd_lock`. The cloud answers every command with a `command_response` (same `trace`, `errNo`) in ~1 s; the ACK watchdog (`_expect_ack` / `_on_ack_timeout`, `WS_CMD_ACK_TIMEOUT`) re-establishes the WS session and resends an unanswered command once (not values the device already reports or a newer command replaced), then logs `CMD LOST` and re-pulls the real state.

### Device state beyond attributes
- **Alarms** (`HaierDevice._set_alarms`): REST `alarms`, WS `payload.statuses[].alarms` and the `alarms` event; `filterCleaningAlarm` is maintenance (`filter_cleaning`), everything else is a fault (`problem`), texts from the description's `errors.alarmToResult` (`alarm_texts`). `connected` is the cloud's ONLINE/OFFLINE, `link_status` the GOOD/BAD/LOST of `deviceConnectionStatusEvent`, `firmware_update` the description's `settings.firmware.updateRequired`.
- **App screens** (`config.app_screens`): the AC description's `modes.itemsV2` (+ top-level blocks) name the controls the Evo app has. AC switches take the app's title; a switch whose code has no screen is not created and its old registry entry is removed (`hidden_entities`, `Haier._device_entities`). No screens in the description → all switches, translated names.
- **Washing machine codes by meaning** (`laundry_codes`, `WM_MEANINGS`): the app's screens link codes to meanings (`remainingTimeHH`, `onOffStatus`, `childLockStatus`, `cyclePhase`…); used for the attributes the model YAML does not name (`_meaning_names`, `_code_of`). `state` (off/idle/running/paused/finished/cancelled) comes from `control.currentProgram.status` / program events, else the phase.
- **AC setpoint while off**: refused by the modifiers → kept in `_deferred_temperature`, shown, and sent in the power-on follow-up (`switch_on`); cleared when the unit turns on elsewhere or reports a new setpoint. A rejected power+mode group of a running unit is resent without the power command (`_rejected_rest`). User requests whose requested commands are all refused raise `HomeAssistantError` (`command_refused`).

### Auth & rate limiting
- Tokens (access + refresh, with expiry) are persisted per config entry to `<hass_config>/haier_evo_tokens_<entry_id>.json`, bound to the entry's email + region (ignored after a credentials change, deleted in `async_remove_entry`; the legacy shared `<hass_config>/haier_evo` file is removed on setup). `auth()` refreshes or re-logs-in based on the *local* expiry; timezone is hardcoded to UTC+3.
- The server can reject a token before its local expiry: REST answers `401`, the WS gateway accepts the upgrade (`101`) and then closes with `1007 "Auth token not valid!"`. Both call `invalidate_token()`, which expires it locally so the next `auth()` refreshes it (not for a token younger than `TOKEN_REJECT_MIN_AGE`, to avoid a refresh loop). A REST `401` for the token the WS session was opened with also restarts that session (`restart_session`).
- The `Device-Id` header is `uuid5(NAMESPACE_URL, "haier_evo:<entry_id>")` (stable per entry, also on sign-in); tokens are renewed `TOKEN_RENEW_MARGIN` s early; HTTP redirects are refused (`allow_redirects=False`, `refuse_redirect`) so the token/password never follow one.
- Setup errors: rejected email/password → `ConfigEntryAuthFailed` (reauth flow; also triggered at runtime via `on_auth_failed`), network / 429 / 5xx, and an Evo page without devices for an entry that has devices in the device registry (`no_devices_listed`; seen during a cloud failure), or one missing some of them (`_check_complete`, up to `SETUP_INCOMPLETE_RETRIES` per HA start) → `ConfigEntryNotReady` (HA retries; after a 429 an extra per-entry backoff `_SETUP_BACKOFF` keeps retries off the cloud).
- `ResettableLimits` (in `limits.py`) wraps each network operation. On `429`/`5xx` (and other HTTP errors of sign-in/refresh) the period is extended (`add_period`) and backed off; on success it's reset. Login and refresh limiters reset each other so the two paths don't starve. The limiters are class-level (one source IP): `_reset_limits_if_alone()` resets them only when no other entry is running. `auth()` does the login/refresh under `_auth_lock`.
- WS reconnects back off (`WS_RECONNECT_BACKOFF_MIN` 5 s → `WS_RECONNECT_BACKOFF_MAX`) after sessions that fail, close before delivering anything, or are dropped for silence; sessions we drop ourselves for other reasons (`_drop_session`: the session counts as down at once, its socket is closed by a task) don't count. `ws_connect` runs under `asyncio.timeout(WS_CONNECT_TIMEOUT)` (aiohttp has no limit of its own for the TCP connect, TLS handshake and upgrade of a websocket) with `heartbeat=WS_PING_INTERVAL` (no pong within half of it closes the socket) and a close that waits `WS_CLOSE_TIMEOUT` at most for the cloud's answer. The websocket URL holds the access token: errors are logged without it (`_scrub`, `WSServerHandshakeError` by status only). A stopped instance (`disconnect_requested`: entry unloaded or sign-in rejected) refuses commands at once instead of waiting for a session. A REST path answering 401 even with a fresh token is remembered (`_non_token_401`) and no longer forces token refreshes.

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

- **No build, no test suite, no linter** — on purpose: the user wants no automated tests in the repo; changes are checked by hand. CI runs in `.github/workflows/`: `validate.yml` (HACS + hassfest metadata validation) and `release.yml` (auto-release, see below) — neither checks the Python.
- **To run/iterate**: copy (or symlink) `custom_components/haier_evo` into a real Home Assistant `config/custom_components/`, restart HA, add the integration with Evo credentials, and watch HA logs (`custom_components.haier_evo` logger, set to debug — `logger.py` keeps it at INFO only when no level is configured; debug messages need the integration's debug logging). The GET debug endpoint above is the fastest way to inspect parsed state.
- **Releasing (RULE): every time a push is requested, FIRST bump `version` in `custom_components/haier_evo/manifest.json`, then push to `main`.** Versions are regular (`0.3.26`, `0.3.27`, …) — no `-beta`/`-rc` suffix: `release.yml` makes those pre-releases, which HACS/HA offer only to users who enabled betas, and the user wants HA's update notifications to work. The `release.yml` workflow reads the manifest version and auto-creates a matching GitHub Release (which HACS consumes). Do NOT create git tags manually — the release owns its tag. Minimum HA version is declared in `hacs.json` (`2024.12.0`). The manifest's `requirements` are empty since 0.4.0: everything the integration uses (aiohttp) comes with HA core, and hassfest rejects listing it. (Up to 0.3.28: websocket-client, ratelimit, tenacity.) Keep shared libraries unpinned or with low minimums: they share HA's environment with every other integration, and a conflicting pin makes the requirement install (and the whole integration) fail.
- Commit messages in this repo are typically prefixed with the GitHub issue number (e.g. `#74 ...`).