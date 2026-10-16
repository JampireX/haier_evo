import weakref
from homeassistant.components.climate import ClimateEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.components.climate.const import HVACMode
from .logger import _LOGGER
from . import api


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities) -> bool:
    haier_object = config_entry.runtime_data
    haier_object.register_platform("climate", async_add_entities)
    entities = []
    for device in haier_object.devices:
        entities.extend(device.create_entities_climate())
    if entities:
        async_add_entities(entities)
        haier_object.write_ha_state()
    return True


# https://developers.home-assistant.io/docs/core/entity/climate
class HaierACEntity(ClimateEntity):
    _attr_translation_key = "conditioner"
    _attr_should_poll = False
    _attr_icon = "mdi:air-conditioner"
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, device: api.HaierAC) -> None:
        self._device = weakref.proxy(device)
        self._attr_unique_id = f"{device.device_id}_{device.device_model}"
        self._attr_supported_features = device.get_supported_features()
        self._attr_hvac_modes = device.get_hvac_modes()
        self._attr_swing_horizontal_modes = device.get_swing_horizontal_modes()
        self._attr_swing_modes = device.get_swing_modes()
        # https://developers.home-assistant.io/blog/2024/01/24/climate-climateentityfeatures-expanded/
        self._enable_turn_on_off_backwards_compatibility = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Subscribe only once HA has added the entity: a disabled entity is never added (its
        # hass stays None), so writing its state on every update would raise RuntimeError.
        self.async_on_remove(self._device.add_write_ha_state_callback(self.async_write_ha_state))

    @property
    def hvac_mode(self) -> str | None:
        """Return hvac operation ie. heat, cool mode."""
        if self._device.status == 1:
            # A mode the integration cannot map (or none reported) is unknown, not OFF — and
            # never a value outside hvac_modes, which made HA raise on every state write.
            mode = self._device.mode
            return mode if mode in self.hvac_modes else None
        return HVACMode.OFF

    @property
    def fan_modes(self) -> list[str]:
        # Follows the device's current restrictions (e.g. no "auto" in fan-only mode).
        return self._device.get_fan_modes()

    @property
    def preset_modes(self) -> list[str]:
        # sleep/boost are not offered while the device makes them read-only (auto, dry, fan).
        return self._device.get_preset_modes()

    @property
    def swing_horizontal_mode(self) -> str:
        return self._device.swing_horizontal_mode

    @property
    def swing_mode(self) -> str:
        return self._device.swing_mode

    @property
    def preset_mode(self) -> str:
        return self._device.preset_mode

    @property
    def fan_mode(self) -> str:
        return self._device.fan_mode

    @property
    def native_temperature_unit(self) -> str:
        return UnitOfTemperature.CELSIUS

    @property
    def max_temp(self) -> float:
        return self._device.max_temperature

    @property
    def min_temp(self) -> float:
        return self._device.min_temperature

    @property
    def native_current_temperature(self) -> float:
        return self._device.current_temperature

    @property
    def native_target_temperature(self) -> float:
        return self._device.target_temperature

    # HA 2026.11 renamed these to native_* (the old names stop working in 2027.11); older HA
    # (down to the 2024.12 minimum) only knows the old names. Providing both keeps the entity
    # working everywhere and HA uses the native ones without a deprecation warning.
    temperature_unit = native_temperature_unit
    current_temperature = native_current_temperature
    target_temperature = native_target_temperature

    @property
    def target_temperature_step(self) -> float:
        return 1.0

    @property
    def available(self) -> bool:
        return self._device.available

    @property
    def device_info(self) -> dict:
        return self._device.device_info

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature (and the mode, if the service call names one)."""
        temp = kwargs.get("temperature", self.target_temperature)
        await self.hass.async_add_executor_job(self.set_temperature, temp, kwargs.get("hvac_mode"))

    def set_temperature(self, temp: float, hvac_mode: str | None = None) -> None:
        """Set new target temperature."""
        _LOGGER.debug(f"Setting target temperature to {temp} (hvac_mode={hvac_mode})")
        self._device.set_temperature(temp, hvac_mode)

    def set_hvac_mode(self, hvac_mode: str) -> None:
        """Set new target hvac mode."""
        _LOGGER.debug(f"Setting HVAC mode to {hvac_mode}")
        if hvac_mode == HVACMode.OFF:
            self._device.switch_off()
        else:
            self._device.switch_on(hvac_mode)

    def set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        _LOGGER.debug(f"Setting fan mode to {fan_mode}")
        self._device.set_fan_mode(fan_mode)

    def set_swing_horizontal_mode(self, swing_mode: str) -> None:
        """Set new target swing horizontal mode."""
        _LOGGER.debug(f"Setting swing horizontal mode to {swing_mode}")
        self._device.set_swing_horizontal_mode(swing_mode)

    def set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        _LOGGER.debug(f"Setting swing mode to {swing_mode}")
        self._device.set_swing_mode(swing_mode)

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        _LOGGER.debug(f"Setting preset mode to {preset_mode}")
        self._device.set_preset_mode(preset_mode)

    def turn_on(self) -> None:
        self._device.switch_on()

    def turn_off(self) -> None:
        self._device.switch_off()
