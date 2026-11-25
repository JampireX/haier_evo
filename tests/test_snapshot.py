"""Characterization snapshot of what the integration does with real cloud descriptions.

For every device of the fixtures: the parsed state, every entity it creates (unique_id, names,
classes, values) and the commands a set of user actions sends. Generated from the threaded
implementation (0.3.28) before the asyncio port; the port must reproduce it exactly, except for
differences reviewed and listed in EXPECTED_CHANGES.

    UPDATE_SNAPSHOT=1 python -m pytest tests/test_snapshot.py   # regenerate tests/fixtures/snapshot.json
"""
from __future__ import annotations

import difflib
import enum
import json
import os
from typing import Any

from custom_components.haier_evo import api

from .conftest import FIXTURES, CommandRecorder, FakeHub, dump_devices, load_fixture, make_device, maybe_await

SNAPSHOT = FIXTURES / "snapshot.json"
PLATFORMS = ("climate", "switch", "select", "sensor", "binary_sensor")
ENTITY_ATTRS = (
    "_attr_unique_id", "_attr_translation_key", "_attr_name", "_attr_icon", "_attr_device_class",
    "_attr_entity_category", "_attr_options", "_attr_native_unit_of_measurement", "_attr_state_class",
    "_device_attr_name",
)
ENTITY_PROPS = {
    "climate": (
        "hvac_mode", "hvac_modes", "fan_mode", "fan_modes", "swing_mode", "swing_modes",
        "swing_horizontal_mode", "swing_horizontal_modes", "preset_mode", "preset_modes",
        "target_temperature", "current_temperature", "min_temp", "max_temp", "target_temperature_step",
        "supported_features", "temperature_unit",
    ),
    "switch": ("is_on",),
    "select": ("options", "current_option"),
    "sensor": ("native_value", "native_unit_of_measurement"),
    "binary_sensor": ("is_on", "extra_state_attributes"),
}


def plain(value: Any) -> Any:
    """JSON-friendly, order-stable form."""
    if isinstance(value, enum.Enum):
        return plain(value.value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(plain(v) for v in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def describe_entity(platform: str, entity) -> dict:
    out = {"class": type(entity).__name__}
    for attr in ENTITY_ATTRS:
        if hasattr(entity, attr):
            out[attr] = plain(getattr(entity, attr))
    for prop in ENTITY_PROPS[platform]:
        try:
            out[prop] = plain(getattr(entity, prop))
        except Exception as e:  # noqa: BLE001 - recorded, part of the behaviour
            out[prop] = f"<{type(e).__name__}>"
    return out


def device_state(device, with_config: bool = True) -> dict:
    state = device.to_dict()
    state.pop("backend_data", None)
    if not with_config:
        state.pop("config", None)
    return plain(state)


def describe_device(device) -> dict:
    out = {
        "class": type(device).__name__,
        "model": device.device_model,
        "name": device.device_name,
        "state": device_state(device),
        "entities": {},
    }
    for platform in PLATFORMS:
        try:
            entities = getattr(device, f"create_entities_{platform}")()
        except Exception as e:  # noqa: BLE001
            out["entities"][platform] = f"<{type(e).__name__}: {e}>"
            continue
        out["entities"][platform] = [describe_entity(platform, e) for e in entities]
    out["hidden_entities"] = plain(getattr(device, "hidden_entities", []))
    return out


async def run_action(hass, hub, spec, prepare, action) -> dict:
    """A fresh device, put in a state (prepare), one user action: what goes out and what shows."""
    device = await make_device(hass, hub, spec)
    prepare(device)
    recorder = CommandRecorder(device)
    error = None
    try:
        await maybe_await(action(device))
    except Exception as e:  # noqa: BLE001 - the error the user sees is part of the behaviour
        error = getattr(e, "translation_key", None) or type(e).__name__
    return plain({"sent": recorder.calls, "error": error, "state": device_state(device, with_config=False)})


def ac_scenarios(device) -> list[tuple[str, Any]]:
    actions = [("set_temperature 23", lambda d: d.set_temperature(23.0)),
               ("set_temperature 24 cool", lambda d: d.set_temperature(24.0, "cool")),
               ("switch_on", lambda d: d.switch_on()),
               ("switch_off", lambda d: d.switch_off())]
    for mode in device.get_hvac_modes():
        if str(mode) != "off":
            actions.append((f"switch_on {mode}", lambda d, m=mode: d.switch_on(m)))
    for fan in device.get_fan_modes():
        actions.append((f"fan {fan}", lambda d, f=fan: d.set_fan_mode(f)))
    for swing in device.get_swing_modes():
        actions.append((f"swing {swing}", lambda d, s=swing: d.set_swing_mode(s)))
    for swing in device.get_swing_horizontal_modes():
        actions.append((f"swing_h {swing}", lambda d, s=swing: d.set_swing_horizontal_mode(s)))
    for preset in device.get_preset_modes():
        actions.append((f"preset {preset}", lambda d, p=preset: d.set_preset_mode(p)))
    for feature in ("light", "sound", "quiet", "turbo", "health", "comfort", "cleaning", "antifreeze", "autohumidity"):
        if device.config[feature] is not None:
            for value in (True, False):
                actions.append((f"{feature} {value}", lambda d, f=feature, v=value: getattr(d, f"set_{f}_on")(v)))
    for option in device.get_eco_sensor_options():
        actions.append((f"eco {option}", lambda d, o=option: d.set_eco_sensor(o)))
    return actions


def wm_scenarios(device) -> list[tuple[str, Any]]:
    actions = []
    for program in device.get_program_options()[:3]:
        actions.append((f"program {program}", lambda d, p=program: d.set_program(p)))
    for option in device.get_temperature_options():
        actions.append((f"temperature {option}", lambda d, o=option: d.set_temperature(o)))
    for option in device.get_spin_speed_options():
        actions.append((f"spin {option}", lambda d, o=option: d.set_spin_speed(o)))
    return actions


def ref_scenarios(device) -> list[tuple[str, Any]]:
    actions = []
    for name in ("super_cooling", "super_freeze", "vacation_mode"):
        for value in (True, False):
            actions.append((f"{name} {value}", lambda d, n=name, v=value: getattr(d, f"set_{n}")(v)))
    for name in ("fridge_mode", "freezer_mode", "my_zone"):
        for option in getattr(device, f"get_{name}_options")():
            actions.append((f"{name} {option}", lambda d, n=name, o=option: getattr(d, f"set_{n}")(o)))
    return actions


def set_code(device, name: str, value: str) -> None:
    code = device.config[name]
    if code is not None:
        device._set_attribute_value(str(code), value)


async def build_snapshot(hass) -> dict:
    specs = dump_devices()
    hub = FakeHub(hass, {s["mac"]: s["backend_data"] for s in specs})
    snapshot: dict[str, Any] = {
        "device_list": {
            "evo_dump": plain(api.Haier._parse_device_specs(load_fixture("evo_dump.json")["backend_data"])[0]),
            "devices_page": plain(api.Haier._parse_device_specs(load_fixture("devices_page.json")["data"])[0]),
        },
        "devices": {},
        "actions": {},
        "messages": {},
    }
    for spec in specs:
        device = await make_device(hass, hub, spec)
        snapshot["devices"][f"{spec['model']} {spec['mac']}"] = describe_device(device)
    seen_models = set()
    for spec in specs:
        if spec["model"] in seen_models or spec["type"] == "WH":
            continue
        seen_models.add(spec["model"])
        probe = await make_device(hass, hub, spec)
        key = spec["model"]
        if spec["type"] == "AC":
            states = {
                "as_is": lambda d: None,
                "off": lambda d: set_code(d, "status", "0"),
                "on": lambda d: set_code(d, "status", "1"),
            }
            scenarios = ac_scenarios(probe)
        elif spec["type"] == "WM":
            states = {
                "as_is": lambda d: None,
                "remote_off": lambda d: setattr(d, "remote_control", False),
            }
            scenarios = wm_scenarios(probe)
        else:
            states = {"as_is": lambda d: None}
            scenarios = ref_scenarios(probe)
        for state_name, prepare in states.items():
            for name, action in scenarios:
                snapshot["actions"][f"{key} [{state_name}] {name}"] = await run_action(
                    hass, hub, spec, prepare, action
                )
        # A status push: the values of the first attributes changed.
        device = await make_device(hass, hub, spec)
        properties = {}
        for attr in device.config.attrs[:6]:
            if attr.current is not None:
                properties[str(attr.code)] = "1" if str(attr.current) in ("0", "false") else "0"
        device.on_message({"event": "status", "macAddress": spec["mac"],
                           "payload": {"statuses": [{"properties": properties}]}})
        snapshot["messages"][key] = plain({"pushed": properties, "state": device_state(device, with_config=False)})
    return snapshot


async def test_snapshot(hass) -> None:
    snapshot = json.loads(json.dumps(await build_snapshot(hass), ensure_ascii=False))
    if os.environ.get("UPDATE_SNAPSHOT"):
        SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if snapshot != expected:
        diff = "\n".join(difflib.unified_diff(
            json.dumps(expected, ensure_ascii=False, indent=1, sort_keys=True).splitlines(),
            json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True).splitlines(),
            "expected", "now", lineterm="", n=2,
        ))
        raise AssertionError(f"behaviour changed:\n{diff[:20000]}")
