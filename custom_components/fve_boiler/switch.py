"""Hlavní vypínač automatiky."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FveBoilerCoordinator
from .entity import FveBoilerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FveBoilerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FveBoilerAutomationSwitch(coordinator)])


class FveBoilerAutomationSwitch(FveBoilerEntity, SwitchEntity):
    """Zapíná a vypíná celou automatiku (stav přežije restart)."""

    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator: FveBoilerCoordinator) -> None:
        super().__init__(coordinator, "automatika")

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_enabled(False)
        self.async_write_ha_state()
