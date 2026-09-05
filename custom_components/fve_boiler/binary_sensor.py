"""Binární senzory: topí / bude potřeba dohřev / čeká na slunce."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, STATE_WAIT_SUN
from .coordinator import FveBoilerCoordinator
from .entity import FveBoilerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FveBoilerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            FveBoilerHeatingSensor(coordinator),
            FveBoilerDeficitSensor(coordinator),
            FveBoilerWaitingSensor(coordinator),
        ]
    )


class FveBoilerHeatingSensor(FveBoilerEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, coordinator: FveBoilerCoordinator) -> None:
        super().__init__(coordinator, "topi")

    @property
    def is_on(self) -> bool:
        return self.coordinator.heater_is_on

    @property
    def extra_state_attributes(self) -> dict:
        return {"duvod": self.coordinator.decision.reason}


class FveBoilerDeficitSensor(FveBoilerEntity, BinarySensorEntity):
    """Zapnutý, pokud slunce dnes na nahřátí podle předpovědi nestačí."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator: FveBoilerCoordinator) -> None:
        super().__init__(coordinator, "hrozi_dohrev")

    @property
    def is_on(self) -> bool:
        return self.coordinator.decision.deficit_wh > 0

    @property
    def extra_state_attributes(self) -> dict:
        d = self.coordinator.decision
        return {
            "chybi_wh": round(d.deficit_wh),
            "potreba_wh": round(d.need_wh),
            "ocekavany_prebytek_wh": round(d.expected_surplus_wh),
        }


class FveBoilerWaitingSensor(FveBoilerEntity, BinarySensorEntity):
    _attr_icon = "mdi:weather-sunny-alert"

    def __init__(self, coordinator: FveBoilerCoordinator) -> None:
        super().__init__(coordinator, "ceka_na_slunce")

    @property
    def is_on(self) -> bool:
        return self.coordinator.decision.state == STATE_WAIT_SUN
