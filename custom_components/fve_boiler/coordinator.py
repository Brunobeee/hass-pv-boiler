"""Koordinátor: čte entity, učí model, rozhoduje a spíná bojler."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

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
    STATE_HEAT_LEGIONELLA,
    STATE_HEAT_SURPLUS,
    STORAGE_KEY_TMPL,
    STORAGE_VERSION,
)
from .learner import Learner
from .model import LearnedModel
from .planner import Decision, Inputs, Settings, decide

_LOGGER = logging.getLogger(__name__)

# nastavení laditelná za běhu přes number entity
RUNTIME_NUMBERS = (
    CONF_TARGET_TEMP,
    CONF_MIN_TEMP,
    CONF_MIN_SOC,
    CONF_RESERVE_SOC,
    CONF_DEADLINE_HOUR,
    CONF_START_THRESHOLD,
)


class FveBoilerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Mozek integrace."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self._store: Store = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_TMPL.format(entry_id=entry.entry_id)
        )
        self.model = LearnedModel()
        self.decision = Decision()
        self.inputs: Inputs | None = None

        # runtime stav
        self.enabled: bool = True
        self.boost_until: datetime | None = None
        self.boost_target: float | None = None
        self.last_switch_change: datetime | None = None
        self.number_overrides: dict[str, float] = {}

        self.learner = Learner(self.model)
        self._dirty: bool = False

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULTS[CONF_SCAN_INTERVAL]))
            ),
        )

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    async def async_load(self) -> None:
        """Načte model z disku; bez něj by integrace po restartu zapomněla vše."""
        data = await self._store.async_load()
        if data:
            self.model = LearnedModel.from_dict(data.get("model"))
            self.enabled = bool(data.get("enabled", True))
            numbers = data.get("numbers")
            if isinstance(numbers, dict):
                self.number_overrides = {
                    str(k): float(v) for k, v in numbers.items() if v is not None
                }
            self.learner = Learner(self.model)
            _LOGGER.debug(
                "Model načten: %.0f W, %.1f Wh/K, důvěra %.0f %%",
                self.model.heater_power_w,
                self.model.tank_wh_per_k,
                self.model.confidence_pct,
            )

    async def async_save(self) -> None:
        await self._store.async_save(
            {
                "model": self.model.as_dict(),
                "enabled": self.enabled,
                "numbers": dict(self.number_overrides),
            }
        )
        self._dirty = False

    async def async_reset_learning(self) -> None:
        self.model = LearnedModel()
        self.learner = Learner(self.model)
        await self.async_save()
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # nastavení
    # ------------------------------------------------------------------
    def _opt(self, key: str) -> Any:
        if key in self.number_overrides:
            return self.number_overrides[key]
        return self.entry.options.get(key, self.entry.data.get(key, DEFAULTS.get(key)))

    def _conf_entity(self, key: str) -> str | None:
        value = self.entry.options.get(key, self.entry.data.get(key))
        return value or None

    @property
    def settings(self) -> Settings:
        return Settings(
            enabled=self.enabled,
            target_temp=float(self._opt(CONF_TARGET_TEMP)),
            min_temp=float(self._opt(CONF_MIN_TEMP)),
            max_temp=float(self._opt(CONF_MAX_TEMP)),
            hysteresis=float(self._opt(CONF_TEMP_HYSTERESIS)),
            min_soc=float(self._opt(CONF_MIN_SOC)),
            reserve_soc=float(self._opt(CONF_RESERVE_SOC)),
            start_threshold=float(self._opt(CONF_START_THRESHOLD)),
            min_run_minutes=int(self._opt(CONF_MIN_RUN_MIN)),
            min_off_minutes=int(self._opt(CONF_MIN_OFF_MIN)),
            deadline_hour=int(self._opt(CONF_DEADLINE_HOUR)),
            allow_grid_backup=bool(self._opt(CONF_ALLOW_GRID)),
            battery_capacity_kwh=float(self._opt(CONF_BATTERY_CAPACITY)),
            legionella_days=int(self._opt(CONF_LEGIONELLA_DAY)),
            legionella_temp=float(self._opt(CONF_LEGIONELLA_TEMP)),
        )

    # ------------------------------------------------------------------
    # čtení entit
    # ------------------------------------------------------------------
    def _read_float(self, key: str, *, scale_kw_to_w: bool = False) -> float | None:
        entity_id = self._conf_entity(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "", None):
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        unit = (state.attributes.get("unit_of_measurement") or "").lower()
        if scale_kw_to_w and unit in ("kw", "kwh"):
            value *= 1000.0
        return value

    def _read_bool(self, key: str) -> bool:
        entity_id = self._conf_entity(key)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == STATE_ON

    @property
    def heater_is_on(self) -> bool:
        entity_id = self._conf_entity(CONF_HEATER_SWITCH)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == STATE_ON

    def _build_inputs(self, now: datetime) -> Inputs:
        grid = self._read_float(CONF_GRID_POWER, scale_kw_to_w=True)
        if grid is not None and bool(self._opt(CONF_GRID_EXPORT_POSITIVE)):
            # normalizujeme na konvenci "+ = odběr ze sítě"
            grid = -grid
        return Inputs(
            now=now,
            heater_on=self.heater_is_on,
            tank_temp=self._read_float(CONF_TEMP_SENSOR),
            ambient_temp=self._read_float(CONF_AMBIENT_TEMP),
            pv_power_w=self._read_float(CONF_PV_POWER, scale_kw_to_w=True),
            battery_soc=self._read_float(CONF_BATTERY_SOC),
            battery_power_w=self._read_float(CONF_BATTERY_POWER, scale_kw_to_w=True),
            grid_power_w=grid,
            house_load_w=self._read_float(CONF_HOUSE_LOAD, scale_kw_to_w=True),
            boiler_power_w=self._read_float(CONF_BOILER_POWER, scale_kw_to_w=True),
            forecast_remaining_wh=self._read_float(CONF_FORECAST_REMAINING, scale_kw_to_w=True),
            forecast_today_wh=self._read_float(CONF_FORECAST_TODAY, scale_kw_to_w=True),
            cheap_tariff=self._read_bool(CONF_CHEAP_TARIFF),
        )

    # ------------------------------------------------------------------
    # hlavní smyčka
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.now()
        inp = self._build_inputs(now)
        self.inputs = inp

        if self.learner.update(
            inp,
            heating_from_surplus=self.decision.state
            in (STATE_HEAT_SURPLUS, STATE_HEAT_LEGIONELLA),
            legionella_temp=float(self._opt(CONF_LEGIONELLA_TEMP)),
        ):
            self._dirty = True

        legionella_due = self._legionella_due(now)
        decision = decide(
            inp,
            self.model,
            self.settings,
            last_change=self.last_switch_change,
            boost_until=self.boost_until,
            boost_target=self.boost_target,
            legionella_due=legionella_due,
        )
        self.decision = decision

        await self._apply(decision, inp, now)

        if self._dirty:
            await self.async_save()

        return {
            "state": decision.state,
            "reason": decision.reason,
            "heat": decision.heat,
            "legionella_due": legionella_due,
        }

    def _legionella_due(self, now: datetime) -> bool:
        days = int(self._opt(CONF_LEGIONELLA_DAY))
        if days <= 0:
            return False
        if self.model.last_legionella_ts <= 0:
            # při prvním běhu spustíme odpočet ode dneška; kdybychom čekali na
            # první dosažení teploty, u cíle pod hranicí legionelly by nenastalo
            self.model.last_legionella_ts = now.timestamp()
            self._dirty = True
            return False
        last = datetime.fromtimestamp(self.model.last_legionella_ts, tz=now.tzinfo)
        return (now - last) > timedelta(days=days)

    # ------------------------------------------------------------------
    # akce
    # ------------------------------------------------------------------
    async def _apply(self, decision: Decision, inp: Inputs, now: datetime) -> None:
        entity_id = self._conf_entity(CONF_HEATER_SWITCH)
        if not entity_id:
            return
        if decision.heat == inp.heater_on:
            return

        service = SERVICE_TURN_ON if decision.heat else SERVICE_TURN_OFF
        _LOGGER.info(
            "Bojler %s: %s", "ZAPÍNÁM" if decision.heat else "VYPÍNÁM", decision.reason
        )
        try:
            await self.hass.services.async_call(
                "homeassistant", service, {ATTR_ENTITY_ID: entity_id}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 - nedostupné relé nesmí shodit koordinátor
            _LOGGER.warning("Relé %s se nepodařilo přepnout: %s", entity_id, err)
            return
        self.last_switch_change = now

    async def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._dirty = True
        await self.async_save()
        await self.async_request_refresh()

    async def async_boost(self, duration_minutes: int, target: float | None) -> None:
        self.boost_until = dt_util.now() + timedelta(minutes=duration_minutes)
        self.boost_target = target
        await self.async_request_refresh()

    async def async_cancel_boost(self) -> None:
        self.boost_until = None
        self.boost_target = None
        await self.async_request_refresh()

    async def async_set_number(self, key: str, value: float) -> None:
        """Ruční doladění parametru přes number entitu."""
        self.number_overrides[key] = value
        await self.async_save()
        await self.async_request_refresh()

    async def async_clear_number_overrides(self) -> None:
        """Po změně nastavení integrace mají přednost nové hodnoty z options."""
        self.number_overrides.clear()
        await self.async_save()
