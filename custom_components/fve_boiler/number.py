"""Číselné vstupy pro každodenní ladění bez zásahu do nastavení integrace."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEADLINE_HOUR,
    CONF_MIN_SOC,
    CONF_MIN_TEMP,
    CONF_RESERVE_SOC,
    CONF_START_THRESHOLD,
    CONF_TARGET_TEMP,
    DEFAULTS,
    DOMAIN,
)
from .coordinator import FveBoilerCoordinator
from .entity import FveBoilerEntity


@dataclass(frozen=True, kw_only=True)
class FveNumberDescription(NumberEntityDescription):
    """Popis číselné entity navázané na klíč nastavení."""

    setting_key: str
    default_fn: Callable[[FveBoilerCoordinator], float] | None = None


NUMBERS: tuple[FveNumberDescription, ...] = (
    FveNumberDescription(
        key="cilova_teplota",
        setting_key=CONF_TARGET_TEMP,
        icon="mdi:thermometer-high",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=30,
        native_max_value=80,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    FveNumberDescription(
        key="minimalni_teplota",
        setting_key=CONF_MIN_TEMP,
        icon="mdi:thermometer-low",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=20,
        native_max_value=65,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    FveNumberDescription(
        key="min_soc_baterie",
        setting_key=CONF_MIN_SOC,
        icon="mdi:battery-charging-70",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    FveNumberDescription(
        key="rezerva_baterie",
        setting_key=CONF_RESERVE_SOC,
        icon="mdi:battery-alert-variant-outline",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    FveNumberDescription(
        key="nejzazsi_hodina",
        setting_key=CONF_DEADLINE_HOUR,
        icon="mdi:clock-alert-outline",
        native_unit_of_measurement="h",
        native_min_value=0,
        native_max_value=23,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    FveNumberDescription(
        key="prah_startu",
        setting_key=CONF_START_THRESHOLD,
        icon="mdi:gauge",
        native_min_value=0.3,
        native_max_value=1.5,
        native_step=0.05,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FveBoilerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(FveBoilerNumber(coordinator, desc) for desc in NUMBERS)


class FveBoilerNumber(FveBoilerEntity, NumberEntity):
    """Zdrojem pravdy je koordinátor - hodnota přežije restart i reload.

    Dokud uživatel posuvníkem nehne, entita ukazuje hodnotu z nastavení
    integrace; změna v options flow tedy nezůstane přebitá starým posuvníkem.
    """

    entity_description: FveNumberDescription

    def __init__(
        self, coordinator: FveBoilerCoordinator, description: FveNumberDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _config_value(self) -> float:
        key = self.entity_description.setting_key
        entry = self.coordinator.entry
        return float(entry.options.get(key, entry.data.get(key, DEFAULTS[key])))

    @property
    def native_value(self) -> float:
        key = self.entity_description.setting_key
        return float(self.coordinator.number_overrides.get(key, self._config_value()))

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_number(self.entity_description.setting_key, value)
        self.async_write_ha_state()
