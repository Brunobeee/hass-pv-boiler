"""Sběr vzorků z provozu a jejich předávání modelu.

Odděleno od koordinátoru schválně: nezná Home Assistant, takže se celý
učicí řetězec dá projet simulací a ověřit, že se parametry sbíhají
ke skutečnosti.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .const import DRAW_RATE_THRESHOLD_K_H

# kratší okno / menší pokles = jen šum a zaokrouhlení čidla
MIN_DRAW_WINDOW_H = 0.05
MIN_DRAW_DROP_K = 0.4
from .model import LearnedModel
from .planner import Inputs

_LOGGER = logging.getLogger(__name__)

# delší mezera mezi vzorky = restart HA nebo výpadek; segment je pak nepoužitelný
MAX_SAMPLE_GAP_H = 0.5


class Learner:
    """Drží rozpracované segmenty a hodinové akumulátory."""

    def __init__(self, model: LearnedModel) -> None:
        self.model = model
        self._last_ts: datetime | None = None
        self._last_heater_on: bool | None = None
        self._cycle: dict[str, Any] | None = None
        self._idle: dict[str, Any] | None = None
        self._pre_switch_load_w: float | None = None
        self._pre_switch_ts: datetime | None = None
        self._hour_bucket: int | None = None
        self._hour_actual_wh: float = 0.0
        self._hour_forecast_start: float | None = None
        self._hour_usage_wh: float = 0.0
        self._hour_curtailed: bool = False

    def reset(self) -> None:
        self._cycle = None
        self._idle = None
        self._last_ts = None
        self._last_heater_on = None

    # ------------------------------------------------------------------
    def update(
        self,
        inp: Inputs,
        *,
        heating_from_surplus: bool = True,
        legionella_temp: float = 65.0,
        pv_curtailed: bool = False,
    ) -> bool:
        """Zpracuje jeden vzorek. Vrací True, pokud se model změnil."""
        if pv_curtailed:
            # Střídač právě ořezává výrobu, takže senzor FVE neukazuje potenciál
            # panelů. Hodina s ořezem by předpověď falešně shodila dolů.
            self._hour_curtailed = True
        now = inp.now
        if self._last_ts is None:
            self._last_ts = now
            self._last_heater_on = inp.heater_on
            self._start_segment(inp, now)
            self._hour_bucket = now.hour
            self._hour_forecast_start = inp.forecast_remaining_wh
            return False

        dt_h = (now - self._last_ts).total_seconds() / 3600.0
        self._last_ts = now
        if dt_h <= 0 or dt_h > MAX_SAMPLE_GAP_H:
            self._cycle = None
            self._idle = None
            self._last_heater_on = inp.heater_on
            self._start_segment(inp, now)
            return False

        dirty = False
        dirty |= self._learn_hourly(inp, now, dt_h)
        self._learn_baseload(inp)
        dirty |= self._learn_heater_power(inp, now)

        if inp.tank_temp is not None and inp.tank_temp >= legionella_temp:
            self.model.last_legionella_ts = now.timestamp()
            dirty = True

        dirty |= self._accumulate(inp, now, dt_h)

        if self._last_heater_on != inp.heater_on:
            dirty |= self._close_segment(inp, now, heating_from_surplus)
            self._last_heater_on = inp.heater_on
            self._start_segment(inp, now)

        return dirty

    # ------------------------------------------------------------------
    def _boiler_power(self, inp: Inputs) -> float:
        if inp.boiler_power_w is not None and inp.boiler_power_w > 50:
            return inp.boiler_power_w
        return self.model.heater_power_w

    def _accumulate(self, inp: Inputs, now: datetime, dt_h: float) -> bool:
        if inp.tank_temp is None:
            return False

        if inp.heater_on and self._cycle is not None:
            self._cycle["energy_wh"] += self._boiler_power(inp) * dt_h
            self._cycle["temp_sum"] += inp.tank_temp * dt_h
            self._cycle["hours"] += dt_h
            if inp.ambient_temp is not None:
                self._cycle["ambient_sum"] += inp.ambient_temp * dt_h
                self._cycle["ambient_hours"] += dt_h
            # pokles teploty při zapnuté spirále = odběr teplé vody uprostřed
            # ohřevu; takový cyklus kapacitu nádrže nadhodnotí, tak ho zahodíme.
            # Okno musí být delší než jeden vzorek, jinak rozhoduje šum čidla.
            prev = self._cycle.get("check_temp")
            prev_ts = self._cycle.get("check_ts")
            if prev is not None and prev_ts is not None:
                span_h = (now - prev_ts).total_seconds() / 3600.0
                if span_h >= MIN_DRAW_WINDOW_H:
                    if prev - inp.tank_temp > MIN_DRAW_DROP_K * 0.75:
                        self._cycle["disturbed"] = True
                    self._cycle["check_temp"] = inp.tank_temp
                    self._cycle["check_ts"] = now
            return False

        if not inp.heater_on and self._idle is not None:
            self._idle["temp_sum"] += inp.tank_temp * dt_h
            self._idle["hours"] += dt_h
            if inp.ambient_temp is not None:
                self._idle["ambient_sum"] += inp.ambient_temp * dt_h
                self._idle["ambient_hours"] += dt_h
            return self._detect_draw(inp, now)

        return False

    def _start_segment(self, inp: Inputs, now: datetime) -> None:
        base: dict[str, Any] = {
            "start": now,
            "start_temp": inp.tank_temp,
            "temp_sum": 0.0,
            "hours": 0.0,
            "ambient_sum": 0.0,
            "ambient_hours": 0.0,
        }
        if inp.heater_on:
            self._cycle = {
                **base,
                "energy_wh": 0.0,
                "check_temp": inp.tank_temp,
                "check_ts": now,
                "disturbed": False,
            }
            self._idle = None
        else:
            self._idle = {**base, "last_temp": inp.tank_temp, "last_ts": now}
            self._cycle = None

    def _close_segment(self, inp: Inputs, now: datetime, from_surplus: bool) -> bool:
        dirty = False
        if inp.tank_temp is None:
            self._cycle = None
            self._idle = None
            return False

        cycle = self._cycle
        if (
            cycle is not None
            and cycle.get("start_temp") is not None
            and cycle["hours"] > 0
            and not cycle.get("disturbed")
        ):
            hours = cycle["hours"]
            avg_temp = cycle["temp_sum"] / hours
            ambient = (
                cycle["ambient_sum"] / cycle["ambient_hours"]
                if cycle["ambient_hours"] > 0
                else None
            )
            delta = inp.tank_temp - cycle["start_temp"]
            energy = cycle["energy_wh"]
            if self.model.learn_heating_cycle(delta, hours, energy, avg_temp, ambient):
                _LOGGER.debug(
                    "Cyklus: %+.1f K / %.2f h / %.0f Wh -> %.0f Wh/K",
                    delta,
                    hours,
                    energy,
                    self.model.tank_wh_per_k,
                )
            self.model.total_heating_wh += energy
            if from_surplus:
                self.model.surplus_heating_wh += energy
            else:
                self.model.grid_heating_wh += energy
            dirty = True

        idle = self._idle
        if idle is not None and idle.get("start_temp") is not None:
            # Odběr, který začne až těsně před koncem úseku, se do průběžné
            # detekce nevejde (ta potřebuje delší okno) - dovyhodnotíme ocas.
            tail_ts = idle.get("last_ts")
            tail_temp = idle.get("last_temp")
            tail_is_draw = False
            if tail_ts is not None and tail_temp is not None:
                tail_h = (now - tail_ts).total_seconds() / 3600.0
                tail_drop = tail_temp - inp.tank_temp
                if (
                    tail_h > 0
                    and tail_drop >= MIN_DRAW_DROP_K * 0.75
                    and tail_drop / tail_h > DRAW_RATE_THRESHOLD_K_H
                ):
                    tail_is_draw = True
                    self._hour_usage_wh += tail_drop * self.model.tank_wh_per_k

            # ocas s odběrem do bilance ztrát nepatří - okno uzavřeme před ním
            end_temp = tail_temp if tail_is_draw and tail_temp is not None else inp.tank_temp
            if self._flush_idle_losses(end_temp):
                dirty = True

        if cycle is not None and cycle.get("disturbed") and cycle["hours"] > 0:
            # do statistik patří i cyklus, ze kterého se nedá nic naučit
            self.model.total_heating_wh += cycle["energy_wh"]
            if from_surplus:
                self.model.surplus_heating_wh += cycle["energy_wh"]
            else:
                self.model.grid_heating_wh += cycle["energy_wh"]
            dirty = True

        self._cycle = None
        self._idle = None
        return dirty

    def _flush_idle_losses(self, end_temp: float) -> bool:
        """Vyhodnotí ztráty z právě uzavřeného čistého okna bez odběru."""
        idle = self._idle
        if idle is None or idle.get("start_temp") is None or idle["hours"] <= 0:
            return False
        hours = idle["hours"]
        avg_temp = idle["temp_sum"] / hours
        ambient = (
            idle["ambient_sum"] / idle["ambient_hours"] if idle["ambient_hours"] > 0 else None
        )
        self.model.learn_standby(
            end_temp - idle["start_temp"], hours, avg_temp, ambient, idle["start"].hour
        )
        return True

    def _reset_idle_window(self, temp: float, now: datetime) -> None:
        """Po odběru začíná nové měřicí okno ztrát."""
        idle = self._idle
        if idle is None:
            return
        idle.update(
            start=now,
            start_temp=temp,
            temp_sum=0.0,
            hours=0.0,
            ambient_sum=0.0,
            ambient_hours=0.0,
        )

    def _detect_draw(self, inp: Inputs, now: datetime) -> bool:
        """Rychlý pokles teploty mimo topení = někdo pustil teplou vodu."""
        if self._idle is None or inp.tank_temp is None:
            return False
        last_temp = self._idle.get("last_temp")
        last_ts = self._idle.get("last_ts")
        if last_temp is None or last_ts is None:
            self._idle["last_temp"] = inp.tank_temp
            self._idle["last_ts"] = now
            return False

        span_h = (now - last_ts).total_seconds() / 3600.0
        if span_h < MIN_DRAW_WINDOW_H:
            return False

        dirty = False
        drop = last_temp - inp.tank_temp
        if drop >= MIN_DRAW_DROP_K and drop / span_h > DRAW_RATE_THRESHOLD_K_H:
            # čisté okno skončilo v okamžiku, kdy někdo pustil teplou vodu
            dirty = self._flush_idle_losses(last_temp)
            self._hour_usage_wh += drop * self.model.tank_wh_per_k
            self._reset_idle_window(inp.tank_temp, now)

        self._idle["last_temp"] = inp.tank_temp
        self._idle["last_ts"] = now
        return dirty

    def _learn_baseload(self, inp: Inputs) -> None:
        load = inp.house_load_w
        if load is None and inp.pv_power_w is not None and inp.grid_power_w is not None:
            load = inp.pv_power_w + inp.grid_power_w - (inp.battery_power_w or 0.0)
        if load is None:
            return
        if inp.heater_on:
            load -= self._boiler_power(inp)
        self.model.learn_baseload(inp.now.hour, max(0.0, load))

    def _learn_heater_power(self, inp: Inputs, now: datetime) -> bool:
        """Příkon spirály: přímo z wattmetru, jinak ze skoku spotřeby po sepnutí."""
        if inp.heater_on and inp.boiler_power_w is not None and inp.boiler_power_w > 300:
            self.model.learn_heater_power(inp.boiler_power_w)
            return True

        total = inp.house_load_w
        if total is None and inp.grid_power_w is not None and inp.pv_power_w is not None:
            total = inp.pv_power_w + inp.grid_power_w - (inp.battery_power_w or 0.0)
        if total is None:
            return False

        if not inp.heater_on:
            self._pre_switch_load_w = total
            self._pre_switch_ts = now
            return False

        if self._pre_switch_load_w is None or self._pre_switch_ts is None:
            return False
        elapsed = (now - self._pre_switch_ts).total_seconds()
        if 90 <= elapsed <= 420:
            delta = total - self._pre_switch_load_w
            if delta > 300:
                self.model.learn_heater_power(delta)
                self._pre_switch_load_w = None
                return True
        return False

    def _learn_hourly(self, inp: Inputs, now: datetime, dt_h: float) -> bool:
        """Na přelomu hodiny porovná předpověď se skutečně vyrobenou energií."""
        if inp.pv_power_w is not None:
            self._hour_actual_wh += inp.pv_power_w * dt_h

        if self._hour_bucket == now.hour:
            return False

        dirty = False
        closed_hour = self._hour_bucket if self._hour_bucket is not None else now.hour
        if (
            self._hour_forecast_start is not None
            and inp.forecast_remaining_wh is not None
            and not self._hour_curtailed
        ):
            forecast_hour_wh = self._hour_forecast_start - inp.forecast_remaining_wh
            if forecast_hour_wh > 50:
                self.model.learn_forecast(closed_hour, forecast_hour_wh, self._hour_actual_wh)
                dirty = True

        # profil odběru se aktualizuje i za hodiny bez odběru, jinak by jen rostl
        self.model.observe_usage_hour(closed_hour, self._hour_usage_wh)
        dirty = True

        self._hour_bucket = now.hour
        self._hour_actual_wh = 0.0
        self._hour_forecast_start = inp.forecast_remaining_wh
        self._hour_usage_wh = 0.0
        self._hour_curtailed = False
        return dirty
