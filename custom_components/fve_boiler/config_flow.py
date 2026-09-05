"""Config flow - všechny entity se vybírají z rozbalovacích seznamů."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_GRID,
    CONF_AMBIENT_TEMP,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_POWER,
    CONF_BATTERY_SOC,
    CONF_BOILER_POWER,
    CONF_CHEAP_TARIFF,
    CONF_DEADLINE_HOUR,
    CONF_FORECAST_REMAINING,
    CONF_FORECAST_TODAY,
    CONF_GRID_EXPORT_POSITIVE,
    CONF_GRID_POWER,
    CONF_HEATER_SWITCH,
    CONF_HOUSE_LOAD,
    CONF_LEGIONELLA_DAY,
    CONF_LEGIONELLA_TEMP,
    CONF_MAX_TEMP,
    CONF_MIN_OFF_MIN,
    CONF_MIN_RUN_MIN,
    CONF_MIN_SOC,
    CONF_MIN_TEMP,
    CONF_PV_POWER,
    CONF_RESERVE_SOC,
    CONF_SCAN_INTERVAL,
    CONF_START_THRESHOLD,
    CONF_TARGET_TEMP,
    CONF_TEMP_HYSTERESIS,
    CONF_TEMP_SENSOR,
    DEFAULTS,
    DOMAIN,
)


def _entity(domains: list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))


def _number(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


ENTITY_FIELDS = (
    CONF_HEATER_SWITCH,
    CONF_TEMP_SENSOR,
    CONF_PV_POWER,
    CONF_BATTERY_SOC,
    CONF_BATTERY_POWER,
    CONF_GRID_POWER,
    CONF_HOUSE_LOAD,
    CONF_BOILER_POWER,
    CONF_FORECAST_REMAINING,
    CONF_FORECAST_TODAY,
    CONF_AMBIENT_TEMP,
    CONF_CHEAP_TARIFF,
)


def entities_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Krok 1: napojení na existující entity."""

    def d(key: str) -> dict[str, Any]:
        value = defaults.get(key)
        return {"default": value} if value else {}

    return vol.Schema(
        {
            vol.Required(CONF_HEATER_SWITCH, **d(CONF_HEATER_SWITCH)): _entity(
                ["switch", "input_boolean"]
            ),
            vol.Required(CONF_TEMP_SENSOR, **d(CONF_TEMP_SENSOR)): _entity(
                ["sensor", "input_number", "number"]
            ),
            vol.Required(CONF_PV_POWER, **d(CONF_PV_POWER)): _entity(["sensor"]),
            vol.Optional(CONF_BATTERY_SOC, **d(CONF_BATTERY_SOC)): _entity(["sensor"]),
            vol.Optional(CONF_BATTERY_POWER, **d(CONF_BATTERY_POWER)): _entity(["sensor"]),
            vol.Optional(CONF_GRID_POWER, **d(CONF_GRID_POWER)): _entity(["sensor"]),
            vol.Optional(CONF_HOUSE_LOAD, **d(CONF_HOUSE_LOAD)): _entity(["sensor"]),
            vol.Optional(CONF_BOILER_POWER, **d(CONF_BOILER_POWER)): _entity(["sensor"]),
            vol.Optional(CONF_FORECAST_REMAINING, **d(CONF_FORECAST_REMAINING)): _entity(
                ["sensor"]
            ),
            vol.Optional(CONF_FORECAST_TODAY, **d(CONF_FORECAST_TODAY)): _entity(["sensor"]),
            vol.Optional(CONF_AMBIENT_TEMP, **d(CONF_AMBIENT_TEMP)): _entity(["sensor"]),
            vol.Optional(CONF_CHEAP_TARIFF, **d(CONF_CHEAP_TARIFF)): _entity(
                ["binary_sensor", "switch", "input_boolean"]
            ),
            vol.Required(
                CONF_GRID_EXPORT_POSITIVE,
                default=defaults.get(
                    CONF_GRID_EXPORT_POSITIVE, DEFAULTS[CONF_GRID_EXPORT_POSITIVE]
                ),
            ): selector.BooleanSelector(),
        }
    )


def parameters_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Krok 2: provozní parametry."""

    def d(key: str) -> Any:
        return defaults.get(key, DEFAULTS[key])

    return vol.Schema(
        {
            vol.Required(CONF_TARGET_TEMP, default=d(CONF_TARGET_TEMP)): _number(30, 80, 1, "°C"),
            vol.Required(CONF_MIN_TEMP, default=d(CONF_MIN_TEMP)): _number(20, 65, 1, "°C"),
            vol.Required(CONF_MAX_TEMP, default=d(CONF_MAX_TEMP)): _number(50, 90, 1, "°C"),
            vol.Required(CONF_TEMP_HYSTERESIS, default=d(CONF_TEMP_HYSTERESIS)): _number(
                1, 15, 0.5, "K"
            ),
            vol.Required(CONF_MIN_SOC, default=d(CONF_MIN_SOC)): _number(0, 100, 1, "%"),
            vol.Required(CONF_RESERVE_SOC, default=d(CONF_RESERVE_SOC)): _number(0, 100, 1, "%"),
            vol.Required(CONF_BATTERY_CAPACITY, default=d(CONF_BATTERY_CAPACITY)): _number(
                0, 200, 0.1, "kWh"
            ),
            vol.Required(CONF_START_THRESHOLD, default=d(CONF_START_THRESHOLD)): _number(
                0.3, 1.5, 0.05
            ),
            vol.Required(CONF_MIN_RUN_MIN, default=d(CONF_MIN_RUN_MIN)): _number(1, 60, 1, "min"),
            vol.Required(CONF_MIN_OFF_MIN, default=d(CONF_MIN_OFF_MIN)): _number(1, 60, 1, "min"),
            vol.Required(CONF_DEADLINE_HOUR, default=d(CONF_DEADLINE_HOUR)): _number(
                0, 23, 1, "h"
            ),
            vol.Required(CONF_ALLOW_GRID, default=d(CONF_ALLOW_GRID)): selector.BooleanSelector(),
            vol.Required(CONF_LEGIONELLA_DAY, default=d(CONF_LEGIONELLA_DAY)): _number(
                0, 30, 1, "dnů"
            ),
            vol.Required(CONF_LEGIONELLA_TEMP, default=d(CONF_LEGIONELLA_TEMP)): _number(
                50, 80, 1, "°C"
            ),
            vol.Required(CONF_SCAN_INTERVAL, default=d(CONF_SCAN_INTERVAL)): _number(
                10, 300, 5, "s"
            ),
        }
    )


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if user_input[CONF_MIN_TEMP] >= user_input[CONF_TARGET_TEMP]:
        errors[CONF_MIN_TEMP] = "min_above_target"
    elif user_input[CONF_TARGET_TEMP] > user_input[CONF_MAX_TEMP]:
        errors[CONF_TARGET_TEMP] = "target_above_max"
    elif user_input[CONF_RESERVE_SOC] > user_input[CONF_MIN_SOC]:
        errors[CONF_RESERVE_SOC] = "reserve_above_min"
    return errors


class FveBoilerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Průvodce prvotním nastavením."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_parameters()

        return self.async_show_form(step_id="user", data_schema=entities_schema({}))

    async def async_step_parameters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                self._data.update(user_input)
                return self.async_create_entry(
                    title="FVE Bojler", data={}, options=self._data
                )

        return self.async_show_form(
            step_id="parameters",
            data_schema=parameters_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FveBoilerOptionsFlow()


class FveBoilerOptionsFlow(OptionsFlow):
    """Pozdější úpravy - stejné dva kroky, předvyplněné."""

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["entities", "parameters"])

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            merged = dict(self._current)
            # nevyplněná volitelná pole se musí dát i zrušit
            for field in ENTITY_FIELDS:
                merged.pop(field, None)
            merged.update(user_input)
            return self.async_create_entry(title="", data=merged)

        return self.async_show_form(
            step_id="entities", data_schema=entities_schema(self._current)
        )

    async def async_step_parameters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                merged = dict(self._current)
                merged.update(user_input)
                return self.async_create_entry(title="", data=merged)

        return self.async_show_form(
            step_id="parameters",
            data_schema=parameters_schema({**self._current, **(user_input or {})}),
            errors=errors,
        )
