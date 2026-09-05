"""FVE Bojler - adaptivní ohřev TUV z fotovoltaických přebytků."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_DURATION,
    ATTR_TARGET_TEMP,
    DOMAIN,
    PLATFORMS,
    SERVICE_BOOST,
    SERVICE_RESET_LEARNING,
    SERVICE_SET_MODEL,
)
from .coordinator import FveBoilerCoordinator

_LOGGER = logging.getLogger(__name__)

BOOST_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional(ATTR_DURATION, default=60): vol.All(int, vol.Range(min=1, max=720)),
        vol.Optional(ATTR_TARGET_TEMP): vol.All(
            vol.Coerce(float), vol.Range(min=20, max=90)
        ),
    }
)

RESET_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})

SET_MODEL_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("heater_power_w"): vol.All(
            vol.Coerce(float), vol.Range(min=300, max=12000)
        ),
        vol.Optional("tank_wh_per_k"): vol.All(
            vol.Coerce(float), vol.Range(min=40, max=900)
        ),
        vol.Optional("tank_liters"): vol.All(vol.Coerce(float), vol.Range(min=20, max=1000)),
        vol.Optional("loss_w_per_k"): vol.All(vol.Coerce(float), vol.Range(min=0.05, max=12)),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Nastavení jedné instance integrace."""
    coordinator = FveBoilerCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: FveBoilerCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_save()
        if not hass.data[DOMAIN]:
            for service in (SERVICE_BOOST, SERVICE_RESET_LEARNING, SERVICE_SET_MODEL):
                hass.services.async_remove(DOMAIN, service)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Nové hodnoty z nastavení mají přednost před ručním doladěním posuvníky."""
    coordinator: FveBoilerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        await coordinator.async_clear_number_overrides()
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_BOOST):
        return

    def _targets(call: ServiceCall) -> list[FveBoilerCoordinator]:
        entry_id = call.data.get("entry_id")
        store: dict[str, FveBoilerCoordinator] = hass.data.get(DOMAIN, {})
        if entry_id:
            coordinator = store.get(entry_id)
            return [coordinator] if coordinator else []
        return list(store.values())

    async def _boost(call: ServiceCall) -> None:
        for coordinator in _targets(call):
            await coordinator.async_boost(
                call.data[ATTR_DURATION], call.data.get(ATTR_TARGET_TEMP)
            )

    async def _reset(call: ServiceCall) -> None:
        for coordinator in _targets(call):
            await coordinator.async_reset_learning()

    async def _set_model(call: ServiceCall) -> None:
        from .const import WH_PER_LITER_K
        from .const import MAX_CONFIDENCE

        for coordinator in _targets(call):
            model = coordinator.model
            data: dict[str, Any] = call.data
            if "heater_power_w" in data:
                model.heater_power_w = float(data["heater_power_w"])
                model.heater_conf = max(model.heater_conf, MAX_CONFIDENCE / 4)
            if "tank_liters" in data:
                model.tank_wh_per_k = float(data["tank_liters"]) * WH_PER_LITER_K
                model.tank_conf = max(model.tank_conf, 2.0)
            if "tank_wh_per_k" in data:
                model.tank_wh_per_k = float(data["tank_wh_per_k"])
                model.tank_conf = max(model.tank_conf, 2.0)
            if "loss_w_per_k" in data:
                model.loss_w_per_k = float(data["loss_w_per_k"])
                model.loss_conf = max(model.loss_conf, 2.0)
            await coordinator.async_save()
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_BOOST, _boost, schema=BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET_LEARNING, _reset, schema=RESET_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_MODEL, _set_model, schema=SET_MODEL_SCHEMA)
