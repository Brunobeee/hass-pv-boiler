"""Diagnostické senzory - co si model myslí a proč se tak rozhodl."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
    UnitOfVolume,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FveBoilerCoordinator
from .entity import FveBoilerEntity


@dataclass(frozen=True, kw_only=True)
class FveSensorDescription(SensorEntityDescription):
    """Popis senzoru včetně způsobu výpočtu hodnoty."""

    value_fn: Callable[[FveBoilerCoordinator], Any]
    attrs_fn: Callable[[FveBoilerCoordinator], dict[str, Any]] | None = None


def _status_attrs(c: FveBoilerCoordinator) -> dict[str, Any]:
    d = c.decision
    i = c.inputs
    attrs: dict[str, Any] = {
        "duvod": d.reason,
        "cilova_teplota": round(d.active_target, 1),
        "potreba_wh": round(d.need_wh),
        "ocekavany_prebytek_wh": round(d.expected_surplus_wh),
        "chybi_wh": round(d.deficit_wh),
        "volny_vykon_w": round(d.free_power_w),
        "zdroj_vypoctu": d.free_power_source,
        "doba_ohrevu_min": round(d.heating_minutes_needed),
        "blokovano_casovacem": d.blocked_by_timer,
        "orez_vyroby": d.pv_curtailed,
    }
    if d.latest_start is not None:
        attrs["nejzazsi_start"] = d.latest_start.isoformat()
    if c.boost_until is not None:
        attrs["boost_do"] = c.boost_until.isoformat()
    if i is not None:
        attrs["teplota_bojleru"] = i.tank_temp
        attrs["baterie_soc"] = i.battery_soc
        attrs["fve_vykon_w"] = i.pv_power_w
        attrs["predpoved_zbytek_wh"] = i.forecast_remaining_wh
    return attrs


def _model_attrs(c: FveBoilerCoordinator) -> dict[str, Any]:
    m = c.model
    return {
        "prikon_ohrevu_w": round(m.heater_power_w),
        "kapacita_wh_na_k": round(m.tank_wh_per_k, 1),
        "odhad_objemu_l": round(m.estimated_liters),
        "ztraty_w_na_k": round(m.loss_w_per_k, 2),
        "topnych_cyklu": m.heating_cycles,
        "vzorku_kapacita": round(m.tank_conf, 1),
        "vzorku_prikon": round(m.heater_conf, 1),
        "vzorku_ztraty": round(m.loss_conf, 1),
        "profil_odberu_wh": [round(v) for v in m.usage_wh_by_hour],
        "korekce_predpovedi": [round(v, 2) for v in m.forecast_bias_by_hour],
        "baseload_w": [round(v) for v in m.baseload_w_by_hour],
    }


SENSORS: tuple[FveSensorDescription, ...] = (
    FveSensorDescription(
        key="stav",
        icon="mdi:state-machine",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "vypnuto",
            "necinnost",
            "ohrev_z_prebytku",
            "ohrev_z_oriznute_vyroby",
            "ohrev_z_baterie",
            "nouzovy_ohrev",
            "antilegionella",
            "rucni_boost",
            "ceka_na_slunce",
            "nahrato",
            "blokovano",
            "chybi_data",
        ],
        value_fn=lambda c: c.decision.state,
        attrs_fn=_status_attrs,
    ),
    FveSensorDescription(
        key="potreba_energie",
        icon="mdi:lightning-bolt",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.decision.need_wh, 1),
    ),
    FveSensorDescription(
        key="ocekavany_prebytek",
        icon="mdi:solar-power-variant",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.decision.expected_surplus_wh, 1),
    ),
    FveSensorDescription(
        key="volny_vykon",
        icon="mdi:flash",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.decision.free_power_w, 1),
        attrs_fn=lambda c: {"zdroj": c.decision.free_power_source},
    ),
    FveSensorDescription(
        key="doba_ohrevu",
        icon="mdi:timer-sand",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.decision.heating_minutes_needed, 1),
    ),
    FveSensorDescription(
        key="naucen_prikon",
        icon="mdi:heating-coil",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.model.heater_power_w, 1),
        attrs_fn=lambda c: {"vzorku": round(c.model.heater_conf, 1)},
    ),
    FveSensorDescription(
        key="naucena_kapacita",
        icon="mdi:water-thermometer",
        native_unit_of_measurement="Wh/K",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda c: round(c.model.tank_wh_per_k, 2),
        attrs_fn=lambda c: {"vzorku": round(c.model.tank_conf, 1)},
    ),
    FveSensorDescription(
        key="odhad_objemu",
        icon="mdi:propane-tank-outline",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.model.estimated_liters, 1),
    ),
    FveSensorDescription(
        key="naucene_ztraty",
        icon="mdi:thermometer-minus",
        native_unit_of_measurement="W/K",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda c: round(c.model.loss_w_per_k, 3),
        attrs_fn=lambda c: {"vzorku": round(c.model.loss_conf, 1)},
    ),
    FveSensorDescription(
        key="korekce_predpovedi",
        icon="mdi:chart-bell-curve",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.model.forecast_bias * 100.0, 1),
        attrs_fn=lambda c: {
            "po_hodinach": [round(v, 2) for v in c.model.forecast_bias_by_hour]
        },
    ),
    FveSensorDescription(
        key="duvera_modelu",
        icon="mdi:school",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda c: c.model.confidence_pct,
        attrs_fn=_model_attrs,
    ),
    FveSensorDescription(
        key="ohrev_z_prebytku_celkem",
        icon="mdi:solar-power",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.model.surplus_heating_wh, 1),
    ),
    FveSensorDescription(
        key="ohrev_ze_site_celkem",
        icon="mdi:transmission-tower",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.model.grid_heating_wh, 1),
    ),
    FveSensorDescription(
        key="podil_slunce",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: (
            round(c.model.surplus_heating_wh / c.model.total_heating_wh * 100.0, 1)
            if c.model.total_heating_wh > 0
            else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: FveBoilerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(FveBoilerSensor(coordinator, desc) for desc in SENSORS)


class FveBoilerSensor(FveBoilerEntity, SensorEntity):
    entity_description: FveSensorDescription

    def __init__(
        self, coordinator: FveBoilerCoordinator, description: FveSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator)
