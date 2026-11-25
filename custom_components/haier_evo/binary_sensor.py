import weakref
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from . import api


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities) -> bool:
    haier_object = config_entry.runtime_data
    haier_object.register_platform("binary_sensor", async_add_entities)
    entities = haier_object.platform_entities("binary_sensor")
    if entities:
        async_add_entities(entities)
        haier_object.write_ha_state()
    return True


class HaierBinarySensor(BinarySensorEntity):
    _attr_should_poll = False
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

    @property
    def is_on(self) -> bool:
        return getattr(self._device, self._device_attr_name, False)


class HaierProblemSensor(HaierBinarySensor):
    # On while the device reports a fault; the texts come from the cloud's error table. A reminder
    # to clean the filter is no fault: it has a sensor of its own. No entity category: one would
    # take the sensor out of actions aimed at an area or a device and out of voice assistants.
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, device: api.HaierDevice) -> None:
        super().__init__(device)
        self._device_attr_name = "problem"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_problem"
        self._attr_translation_key = "problem"

    @property
    def extra_state_attributes(self) -> dict:
        return {"alarms": list(self._device.alarms), "errors": self._device.alarm_texts}


class HaierFilterSensor(HaierBinarySensor):
    # The cloud's filterCleaningAlarm: time to clean the filter.
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:air-filter"

    def __init__(self, device: api.HaierDevice) -> None:
        super().__init__(device)
        self._device_attr_name = "filter_cleaning"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_filter"
        self._attr_translation_key = "filter"


class HaierConnectivitySensor(HaierBinarySensor):
    # ONLINE / OFFLINE as the cloud reports it, with the link quality (GOOD / BAD / LOST) of
    # deviceConnectionStatusEvent as an attribute.
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: api.HaierDevice) -> None:
        super().__init__(device)
        self._device_attr_name = "connected"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_connected"
        self._attr_translation_key = "connectivity"

    @property
    def available(self) -> bool:
        return True  # it is the one entity that has to show "offline"

    @property
    def extra_state_attributes(self) -> dict:
        return {"link": self._device.link_status}


class HaierFirmwareUpdateSensor(HaierBinarySensor):
    # The cloud's "a newer firmware is waiting" flag (the Evo app installs it). The cloud does not
    # say which version waits, so this is a binary sensor and not an update entity.
    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: api.HaierDevice) -> None:
        super().__init__(device)
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_firmware_update"
        self._attr_translation_key = "firmware_update"

    @property
    def is_on(self) -> bool | None:
        return self._device.firmware_update

    @property
    def extra_state_attributes(self) -> dict:
        return {"installed_version": self._device.sw_version, "auto_update": self._device.firmware_auto_update}


class HaierWMChildLockSensor(HaierBinarySensor):
    # On while the child lock holds the buttons (no LOCK class: its "on" means unlocked).
    _attr_icon = "mdi:lock"

    def __init__(self, device: api.HaierWM) -> None:
        super().__init__(device)
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_child_lock"
        self._attr_translation_key = "wm_child_lock"

    @property
    def is_on(self) -> bool:
        return bool(self._device.child_lock)


class HaierWMRemoteControlSensor(HaierBinarySensor):
    _attr_icon = "mdi:remote"

    def __init__(self, device: api.HaierWM) -> None:
        super().__init__(device)
        self._device_attr_name = "remote_control"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_remote_control"
        self._attr_translation_key = "wm_remote_control"


class HaierREFBinarySensor(HaierBinarySensor):
    _attr_icon = "mdi:fridge-outline"


class HaierREFDoorSensor(HaierREFBinarySensor):

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "door_open"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_door_open"
        self._attr_translation_key = "ref_door"


class HaierREFVacationSensor(HaierREFBinarySensor):

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "vacation_mode"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_vacation"
        self._attr_translation_key = "ref_vacation"


class HaierREFSuperFreezeSensor(HaierREFBinarySensor):

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "super_freeze"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_super_freeze"
        self._attr_translation_key = "ref_super_freeze"


class HaierREFSuperCoolingSensor(HaierREFBinarySensor):

    def __init__(self, device: api.HaierREF) -> None:
        super().__init__(device)
        self._device_attr_name = "super_cooling"
        self._attr_unique_id = f"{device.device_id}_{device.device_model}_super_cooling"
        self._attr_translation_key = "ref_super_cooling"
