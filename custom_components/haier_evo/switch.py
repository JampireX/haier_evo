import weakref
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from .const import DOMAIN
from . import api


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities) -> bool:
    haier_object = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    for device in haier_object.devices:
        entities.extend(device.create_entities_switch())
    entities.append(HttpSwitch(haier_object))
    entities.append(HttpSwitchPOST(haier_object))
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

        device.add_write_ha_state_callback(self.async_write_ha_state)

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


class HttpSwitch(SwitchEntity):
    _attr_icon = "mdi:toggle-switch"
    _attr_has_entity_name = True
    _attr_translation_key = "http_get"

    def __init__(self, haier):
        self._haier = weakref.proxy(haier)
        self._attr_unique_id = f"{DOMAIN}_http_switch_get"

    @property
    def is_on(self) -> bool:
        return self._haier.allow_http

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{DOMAIN}_http_switch")},
            "name": "Haier Evo HTTP",
            "manufacturer": "Haier"
        }

    async def async_turn_on(self, **kwargs):
        self._haier.allow_http = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._haier.allow_http = False
        self.async_write_ha_state()


class HttpSwitchPOST(HttpSwitch):
    _attr_translation_key = "http_post"

    def __init__(self, haier):
        super().__init__(haier)
        self._attr_unique_id = f"{DOMAIN}_http_switch_post"

    @property
    def is_on(self) -> bool:
        return self._haier.allow_http_post

    async def async_turn_on(self, **kwargs):
        self._haier.allow_http_post = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._haier.allow_http_post = False
        self.async_write_ha_state()
