import weakref
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN
from .logger import _LOGGER
from . import api


# The HTTP debug switches and their device used fixed ids shared by all config entries, so the
# switches of a second entry were rejected as duplicates. They are per entry now.
_LEGACY_HTTP_UNIQUE_IDS = {"get": f"{DOMAIN}_http_switch_get", "post": f"{DOMAIN}_http_switch_post"}
_LEGACY_HTTP_DEVICE = (DOMAIN, f"{DOMAIN}_http_switch")


def http_switch_unique_id(kind: str, entry_id: str) -> str:
    return f"{DOMAIN}_http_switch_{kind}_{entry_id}"


def http_device_identifier(entry_id: str) -> tuple[str, str]:
    return DOMAIN, f"{DOMAIN}_http_switch_{entry_id}"


@callback
def async_migrate_http_switches(hass: HomeAssistant, entry_id: str) -> None:
    """Move this entry's HTTP switches and their device from the old fixed ids to the per-entry
    ones, so that they keep their entity_ids, settings and history."""
    ent_reg = er.async_get(hass)
    for kind, old_unique_id in _LEGACY_HTTP_UNIQUE_IDS.items():
        entity_id = ent_reg.async_get_entity_id("switch", DOMAIN, old_unique_id)
        reg_entry = ent_reg.async_get(entity_id) if entity_id else None
        if reg_entry is None or reg_entry.config_entry_id != entry_id:
            continue  # nothing to move, or another entry's switch (that entry moves it)
        new_unique_id = http_switch_unique_id(kind, entry_id)
        if ent_reg.async_get_entity_id("switch", DOMAIN, new_unique_id) is None:
            _LOGGER.info(f"Migrating {entity_id} to unique_id {new_unique_id}")
            ent_reg.async_update_entity(entity_id, new_unique_id=new_unique_id)
        else:
            # Already moved before (an older version re-created the old one): drop the leftover.
            ent_reg.async_remove(entity_id)
    dev_reg = dr.async_get(hass)
    device = _entry_device(dev_reg, _LEGACY_HTTP_DEVICE, entry_id)
    if device is not None and _entry_device(dev_reg, http_device_identifier(entry_id), entry_id) is None:
        dev_reg.async_update_device(device.id, new_identifiers={http_device_identifier(entry_id)})


def _entry_device(dev_reg, identifier: tuple[str, str], entry_id: str):
    """The config entry's device with this identifier, or None.

    Since HA 2026.8 a device belongs to one config entry and an identifier may name one device
    per entry, so the lookup is scoped to the entry: `async_get_device` is deprecated (removed
    in 2027.8) and so is `DeviceEntry.config_entries` (2027.10). Older HA has neither the
    scoped lookup nor single-entry devices: there one device is shared by its entries."""
    get_by_identifier = getattr(dev_reg, "async_get_device_by_identifier", None)
    if get_by_identifier is not None:
        return get_by_identifier(identifier, entry_id)
    device = dev_reg.async_get_device(identifiers={identifier})
    if device is None or entry_id not in device.config_entries:
        return None
    return device


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities) -> bool:
    haier_object = config_entry.runtime_data
    # Before the entities are added: they must find their registry entries under the new ids.
    async_migrate_http_switches(hass, config_entry.entry_id)
    haier_object.register_platform("switch", async_add_entities)
    entities = []
    for device in haier_object.devices:
        entities.extend(device.create_entities_switch())
    entities.append(HttpSwitch(haier_object, config_entry.entry_id))
    entities.append(HttpSwitchPOST(haier_object, config_entry.entry_id))
    async_add_entities(entities)
    haier_object.write_ha_state()
    return True


class HaierSwitch(SwitchEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:toggle-switch"
    _attr_has_entity_name = True

    def __init__(self, device: api.HaierDevice) -> None:
        self._device = weakref.proxy(device)
        self._device_attr_name = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Subscribe only once added: a disabled entity is never added (hass stays None).
        self.async_on_remove(self._device.add_write_ha_state_callback(self.async_write_ha_state))

    @property
    def device_info(self) -> dict:
        return self._device.device_info

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_turn_on(self, **kwargs):
        await self.hass.async_add_executor_job(self.turn_on)

    def turn_on(self) -> None:
        method = getattr(self._device, f"set_{self._device_attr_name}", None)
        if method is not None:
            method(True)

    async def async_turn_off(self, **kwargs):
        await self.hass.async_add_executor_job(self.turn_off)

    def turn_off(self, **kwargs) -> None:
        method = getattr(self._device, f"set_{self._device_attr_name}", None)
        if method is not None:
            method(False)

    @property
    def is_on(self) -> bool:
        return bool(getattr(self._device, self._device_attr_name, None))


class HaierACLightSwitch(HaierSwitch):
    _attr_icon = "mdi:lightbulb"

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "light_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_light"
        self._attr_translation_key = "ac_light"


class HaierACSoundSwitch(HaierSwitch):

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "sound_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_sound"
        self._attr_translation_key = "ac_sound"


class HaierACQuietSwitch(HaierSwitch):

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "quiet_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_quiet"
        self._attr_translation_key = "ac_quiet"


class HaierACTurboSwitch(HaierSwitch):

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "turbo_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_turbo"
        self._attr_translation_key = "ac_turbo"


class HaierACHealthSwitch(HaierSwitch):

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "health_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_health"
        self._attr_translation_key = "ac_health"


class HaierACComfortSwitch(HaierSwitch):

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "comfort_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_comfort"
        self._attr_translation_key = "ac_comfort"


class HaierACCleaningSwitch(HaierSwitch):

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "cleaning_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_cleaning"
        self._attr_translation_key = "ac_cleaning"


class HaierACAntiFreezeSwitch(HaierSwitch):

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "antifreeze_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_antifreeze"
        self._attr_translation_key = "ac_antifreeze"


class HaierACAutoHumiditySwitch(HaierSwitch):

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "autohumidity_on"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_autohumidity"
        self._attr_translation_key = "ac_autohumidity"


class HaierREFSuperCoolingSwitch(HaierSwitch):

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "super_cooling"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_super_cooling_switch"
        self._attr_translation_key = "ref_super_cooling"


class HaierREFSuperFreezeSwitch(HaierSwitch):

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "super_freeze"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_super_freeze_switch"
        self._attr_translation_key = "ref_super_freeze"


class HaierREFVacationSwitch(HaierSwitch):

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "vacation_mode"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_vacation_mode_switch"
        self._attr_translation_key = "ref_vacation"


class HttpSwitch(SwitchEntity, RestoreEntity):
    """Allows this entry's data on GET /api/haier_evo (the debug endpoint)."""
    _attr_icon = "mdi:toggle-switch"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "http_get"
    _kind = "get"
    _flag = "allow_http"  # the Haier attribute the view checks

    def __init__(self, haier, entry_id: str):
        self._haier = weakref.proxy(haier)
        self._attr_unique_id = http_switch_unique_id(self._kind, entry_id)
        self._attr_device_info = DeviceInfo(
            identifiers={http_device_identifier(entry_id)},
            name="Haier Evo HTTP",
            manufacturer="Haier",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The flag lives on the Haier object, which is created anew on every (re)load: keep
        # the user's choice instead of falling back to the default.
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            setattr(self._haier, self._flag, last.state == STATE_ON)

    @property
    def is_on(self) -> bool:
        return bool(getattr(self._haier, self._flag, False))

    async def async_turn_on(self, **kwargs):
        setattr(self._haier, self._flag, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        setattr(self._haier, self._flag, False)
        self.async_write_ha_state()


class HttpSwitchPOST(HttpSwitch):
    """Allows POST /api/haier_evo (raw websocket messages) for this entry's devices."""
    _attr_translation_key = "http_post"
    _kind = "post"
    _flag = "allow_http_post"
