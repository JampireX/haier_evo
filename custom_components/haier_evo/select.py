import weakref
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from .const import DOMAIN
from . import api


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities) -> bool:
    haier_object = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    for device in haier_object.devices:
        entities.extend(device.create_entities_select())
    if entities:
        async_add_entities(entities)
        haier_object.write_ha_state()
    return True


class HaierSelect(SelectEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:format-list-bulleted"
    _attr_has_entity_name = True

    def __init__(self, device: api.HaierDevice) -> None:
        self._device = weakref.proxy(device)
        self._device_attr_name = None
        self._attr_options = []

        device.add_write_ha_state_callback(self.async_write_ha_state)

    @property
    def device_info(self) -> dict:
        return self._device.device_info

    @property
    def available(self) -> bool:
        return self._device.available

    @property
    def current_option(self) -> str | None:
        # Return None (empty state in HA) instead of "unknown" or any value outside the
        # options list — otherwise HA logs a warning and shows "unknown".
        value = getattr(self._device, self._device_attr_name, None)
        return value if value in (self._attr_options or []) else None

    async def async_select_option(self, option: str) -> None:
        await self.hass.async_add_executor_job(self.set_option, option)

    def set_option(self, value) -> None:
        method = getattr(self._device, f"set_{self._device_attr_name}", None)
        if method is not None:
            method(value)


class HaierACEcoSensorSelect(HaierSelect):
    _attr_translation_key = "conditioner_eco_sensor"

    def __init__(self, device: api.HaierAC) -> None:
        super().__init__(device)
        self._device_attr_name = "eco_sensor"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_eco_sensor"
        self._attr_options = device.get_eco_sensor_options()


class HaierREFFridgeModeSelect(HaierSelect):
    _attr_translation_key = "ref_fridge_mode"

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "fridge_mode"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_fridge_mode_select"
        self._attr_options = device.get_fridge_mode_options()


class HaierREFFreezerModeSelect(HaierSelect):
    _attr_translation_key = "ref_freezer_mode"

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "freezer_mode"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_freezer_mode_select"
        self._attr_options = device.get_freezer_mode_options()


class HaierREFMyZoneSelect(HaierSelect):
    _attr_translation_key = "ref_my_zone"

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "my_zone"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_my_zone"
        self._attr_options = device.get_my_zone_options()


class HaierWMSelect(HaierSelect):
    # Controls stay available even when remote control is disabled, so the current
    # program / temperature / spin values remain visible in HA. Attempts to *change*
    # a parameter are still blocked with a clear error in HaierWM.set_*
    # (_ensure_remote_control) rather than by hiding the entity.
    pass


class HaierWMProgramSelect(HaierWMSelect):
    # Placeholder for "no program selected": HA cannot render an empty select state
    # without showing "unknown", so we expose an explicit option instead. Its label
    # is taken from the translation files via the select state mapping.
    PLACEHOLDER = "not_selected"
    _attr_translation_key = "wm_program"

    def __init__(self, device: api.HaierWM) -> None:
        super().__init__(device)
        self._device_attr_name = "program"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_program"
        self._attr_options = [self.PLACEHOLDER, *device.get_program_options()]

    @property
    def current_option(self) -> str:
        value = getattr(self._device, self._device_attr_name, None)
        return value if value in self._attr_options else self.PLACEHOLDER

    def set_option(self, value) -> None:
        if value == self.PLACEHOLDER:
            return
        super().set_option(value)


class HaierWMTemperatureSelect(HaierWMSelect):
    _attr_translation_key = "wm_temperature"

    def __init__(self, device: api.HaierWM) -> None:
        super().__init__(device)
        self._device_attr_name = "temperature"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_temperature"
        self._attr_options = device.get_temperature_options()


class HaierWMSpinSpeedSelect(HaierWMSelect):
    _attr_translation_key = "wm_spin_speed"

    def __init__(self, device: api.HaierWM) -> None:
        super().__init__(device)
        self._device_attr_name = "spin_speed"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_spin_speed"
        self._attr_options = device.get_spin_speed_options()
