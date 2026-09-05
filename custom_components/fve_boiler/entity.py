"""Společný základ pro entity integrace."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FveBoilerCoordinator


class FveBoilerEntity(CoordinatorEntity[FveBoilerCoordinator]):
    """Základní entita napojená na koordinátor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FveBoilerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="FVE Bojler",
            manufacturer="Vlastní integrace",
            model="Adaptivní ohřev TUV z přebytků",
        )
