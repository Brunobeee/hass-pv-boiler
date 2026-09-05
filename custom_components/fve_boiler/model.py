"""Adaptivní model bojleru a predikce přebytku FVE.

Model se učí za provozu ze čtyř zdrojů:
  * topné cykly    -> příkon spirály a tepelná kapacita nádrže (Wh/K)
  * klidové úseky  -> tepelné ztráty nádrže (W/K rozdílu vůči okolí)
  * rychlé poklesy -> profil odběru TUV po hodinách dne
  * běh dne        -> korekce chyby předpovědi Forecast.Solar po hodinách

Všechny parametry se drží jako EWMA s adaptivní alfou: první vzorky mají
velkou váhu (rychle se odlepíme od defaultu), s rostoucí důvěrou se model
uklidňuje a nekmitá.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from .const import (
    DEFAULT_HEATER_POWER_W,
    DEFAULT_LOSS_W_PER_K,
    DEFAULT_TANK_WH_PER_K,
    DRAW_RATE_THRESHOLD_K_H,
    EWMA_MIN_ALPHA,
    EWMA_START_ALPHA,
    MAX_CONFIDENCE,
    WH_PER_LITER_K,
)

_LOGGER = logging.getLogger(__name__)


def _alpha(confidence: float) -> float:
    """Adaptivní EWMA koeficient - klesá s počtem nasbíraných vzorků."""
    a = EWMA_START_ALPHA / (1.0 + confidence)
    return max(EWMA_MIN_ALPHA, min(EWMA_START_ALPHA, a))


def _ewma(old: float, new: float, confidence: float) -> float:
    a = _alpha(confidence)
    return old * (1.0 - a) + new * a


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_outlier(current: float, sample: float, confidence: float, factor: float = 2.2) -> bool:
    """Jakmile model něco ví, hrubě odlehlý vzorek radši zahodí.

    V reálném provozu jde o nedetekovaný odběr uprostřed měření, výpadek
    čidla nebo ruční zásah do relé - jeden takový vzorek by jinak model
    rozhodil na několik dní.
    """
    if confidence < 4.0 or current <= 0:
        return False
    return sample > current * factor or sample < current / factor


@dataclass
class LearnedModel:
    """Naučené parametry soustavy."""

    # příkon topné spirály [W]
    heater_power_w: float = DEFAULT_HEATER_POWER_W
    heater_conf: float = 0.0

    # kolik Wh elektřiny reálně zvedne nádrž o 1 K (včetně účinnosti)
    tank_wh_per_k: float = DEFAULT_TANK_WH_PER_K
    tank_conf: float = 0.0

    # tepelné ztráty nádrže [W na 1 K rozdílu vůči okolí]
    loss_w_per_k: float = DEFAULT_LOSS_W_PER_K
    loss_conf: float = 0.0

    # průměrný odběr TUV v dané hodině dne, v kWh tepla [24]
    usage_wh_by_hour: list[float] = field(default_factory=lambda: [0.0] * 24)
    usage_conf_by_hour: list[float] = field(default_factory=lambda: [0.0] * 24)

    # poměr skutečná výroba / předpověď v dané hodině [24]
    forecast_bias_by_hour: list[float] = field(default_factory=lambda: [1.0] * 24)
    forecast_conf_by_hour: list[float] = field(default_factory=lambda: [0.0] * 24)

    # průměrná spotřeba domu (bez bojleru) v dané hodině [W] [24]
    baseload_w_by_hour: list[float] = field(default_factory=lambda: [0.0] * 24)
    baseload_conf_by_hour: list[float] = field(default_factory=lambda: [0.0] * 24)

    # statistiky
    total_heating_wh: float = 0.0
    surplus_heating_wh: float = 0.0
    grid_heating_wh: float = 0.0
    heating_cycles: int = 0
    last_legionella_ts: float = 0.0

    # ------------------------------------------------------------------
    # (de)serializace
    # ------------------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        return {
            "heater_power_w": self.heater_power_w,
            "heater_conf": self.heater_conf,
            "tank_wh_per_k": self.tank_wh_per_k,
            "tank_conf": self.tank_conf,
            "loss_w_per_k": self.loss_w_per_k,
            "loss_conf": self.loss_conf,
            "usage_wh_by_hour": list(self.usage_wh_by_hour),
            "usage_conf_by_hour": list(self.usage_conf_by_hour),
            "forecast_bias_by_hour": list(self.forecast_bias_by_hour),
            "forecast_conf_by_hour": list(self.forecast_conf_by_hour),
            "baseload_w_by_hour": list(self.baseload_w_by_hour),
            "baseload_conf_by_hour": list(self.baseload_conf_by_hour),
            "total_heating_wh": self.total_heating_wh,
            "surplus_heating_wh": self.surplus_heating_wh,
            "grid_heating_wh": self.grid_heating_wh,
            "heating_cycles": self.heating_cycles,
            "last_legionella_ts": self.last_legionella_ts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LearnedModel":
        model = cls()
        if not data:
            return model

        def _list(key: str, default: float) -> list[float]:
            raw = data.get(key)
            if isinstance(raw, list) and len(raw) == 24:
                try:
                    return [float(v) for v in raw]
                except (TypeError, ValueError):
                    return [default] * 24
            return [default] * 24

        model.heater_power_w = float(data.get("heater_power_w", DEFAULT_HEATER_POWER_W))
        model.heater_conf = float(data.get("heater_conf", 0.0))
        model.tank_wh_per_k = float(data.get("tank_wh_per_k", DEFAULT_TANK_WH_PER_K))
        model.tank_conf = float(data.get("tank_conf", 0.0))
        model.loss_w_per_k = float(data.get("loss_w_per_k", DEFAULT_LOSS_W_PER_K))
        model.loss_conf = float(data.get("loss_conf", 0.0))
        model.usage_wh_by_hour = _list("usage_wh_by_hour", 0.0)
        model.usage_conf_by_hour = _list("usage_conf_by_hour", 0.0)
        model.forecast_bias_by_hour = _list("forecast_bias_by_hour", 1.0)
        model.forecast_conf_by_hour = _list("forecast_conf_by_hour", 0.0)
        model.baseload_w_by_hour = _list("baseload_w_by_hour", 0.0)
        model.baseload_conf_by_hour = _list("baseload_conf_by_hour", 0.0)
        model.total_heating_wh = float(data.get("total_heating_wh", 0.0))
        model.surplus_heating_wh = float(data.get("surplus_heating_wh", 0.0))
        model.grid_heating_wh = float(data.get("grid_heating_wh", 0.0))
        model.heating_cycles = int(data.get("heating_cycles", 0))
        model.last_legionella_ts = float(data.get("last_legionella_ts", 0.0))
        return model

    # ------------------------------------------------------------------
    # učení
    # ------------------------------------------------------------------
    def learn_heater_power(self, measured_w: float) -> None:
        """Změřený příkon spirály (z wattmetru nebo z rozdílu odběru domu)."""
        if not 300.0 <= measured_w <= 12000.0:
            return
        self.heater_power_w = _clamp(
            _ewma(self.heater_power_w, measured_w, self.heater_conf), 300.0, 12000.0
        )
        self.heater_conf = min(MAX_CONFIDENCE, self.heater_conf + 1.0)

    def learn_heating_cycle(
        self,
        delta_temp_k: float,
        duration_h: float,
        energy_wh: float,
        avg_temp_c: float,
        ambient_c: float | None,
    ) -> bool:
        """Naučí Wh/K z dokončeného topného cyklu.

        Do bilance započítá i ztráty, které během topení odtekly, takže
        výsledek je čistá kapacita nádrže, ne kapacita zkreslená ztrátami.
        """
        if duration_h < 0.15 or delta_temp_k < 0.6 or energy_wh <= 0:
            # příliš krátký/malý cyklus - poměr signál/šum čidla je mizerný
            return False

        loss_wh = 0.0
        if ambient_c is not None:
            loss_wh = max(0.0, self.loss_w_per_k * (avg_temp_c - ambient_c) * duration_h)

        useful_wh = energy_wh - loss_wh
        if useful_wh <= 0:
            return False

        wh_per_k = useful_wh / delta_temp_k
        # sanity: nádrž 50-500 l -> zhruba 55-800 Wh/K i s mizernou účinností
        if not 40.0 <= wh_per_k <= 900.0:
            _LOGGER.debug("Zahozený vzorek kapacity: %.1f Wh/K", wh_per_k)
            return False
        if _is_outlier(self.tank_wh_per_k, wh_per_k, self.tank_conf, factor=1.8):
            _LOGGER.debug("Odlehlý vzorek kapacity zahozen: %.1f Wh/K", wh_per_k)
            return False

        self.tank_wh_per_k = _ewma(self.tank_wh_per_k, wh_per_k, self.tank_conf)
        self.tank_conf = min(MAX_CONFIDENCE, self.tank_conf + 1.0)
        self.heating_cycles += 1
        return True

    def learn_standby(
        self,
        delta_temp_k: float,
        duration_h: float,
        avg_temp_c: float,
        ambient_c: float | None,
        hour: int,
    ) -> None:
        """Klidový úsek: buď pomalé chladnutí (ztráty), nebo odběr TUV."""
        if duration_h < 0.05 or delta_temp_k >= 0:
            return

        rate_k_h = -delta_temp_k / duration_h
        energy_wh = -delta_temp_k * self.tank_wh_per_k

        if rate_k_h > DRAW_RATE_THRESHOLD_K_H:
            # rychlý pokles je odběr teplé vody, ne ztráta - řeší ho Learner
            return

        # Kvantizace čidla (typicky 0,1 K) by u malých poklesů dominovala nad
        # signálem, proto se ztráty učí jen z dlouhých a dobře měřitelných úseků.
        if ambient_c is None or duration_h < 1.2 or -delta_temp_k < 0.4:
            return

        delta_ambient = avg_temp_c - ambient_c
        if delta_ambient < 8.0:
            return

        loss_w = energy_wh / duration_h / delta_ambient
        if not 0.05 <= loss_w <= 12.0:
            return
        if _is_outlier(self.loss_w_per_k, loss_w, self.loss_conf):
            _LOGGER.debug("Odlehlý vzorek ztrát zahozen: %.2f W/K", loss_w)
            return

        self.loss_w_per_k = _ewma(self.loss_w_per_k, loss_w, self.loss_conf)
        self.loss_conf = min(MAX_CONFIDENCE, self.loss_conf + 1.0)

    def observe_usage_hour(self, hour: int, energy_wh: float) -> None:
        """Uzavřená hodina: kolik tepla si za ni odebrala teplá voda.

        Volá se i pro hodiny s nulovým odběrem - jinak by profil jen rostl.
        """
        hour = hour % 24
        conf = self.usage_conf_by_hour[hour]
        self.usage_wh_by_hour[hour] = max(
            0.0, _ewma(self.usage_wh_by_hour[hour], max(0.0, energy_wh), conf)
        )
        self.usage_conf_by_hour[hour] = min(MAX_CONFIDENCE, conf + 1.0)

    def learn_forecast(self, hour: int, forecast_wh: float, actual_wh: float) -> None:
        """Korekce systematické chyby předpovědi v dané hodině."""
        hour = hour % 24
        if forecast_wh < 50.0:
            return
        ratio = _clamp(actual_wh / forecast_wh, 0.1, 3.0)
        conf = self.forecast_conf_by_hour[hour]
        self.forecast_bias_by_hour[hour] = _clamp(
            _ewma(self.forecast_bias_by_hour[hour], ratio, conf), 0.2, 2.5
        )
        self.forecast_conf_by_hour[hour] = min(MAX_CONFIDENCE, conf + 1.0)

    def learn_baseload(self, hour: int, load_w: float) -> None:
        """Spotřeba domu bez bojleru v dané hodině."""
        hour = hour % 24
        if load_w < 0 or load_w > 30000:
            return
        conf = self.baseload_conf_by_hour[hour]
        self.baseload_w_by_hour[hour] = _ewma(
            self.baseload_w_by_hour[hour], load_w, conf
        )
        self.baseload_conf_by_hour[hour] = min(MAX_CONFIDENCE, conf + 0.2)

    # ------------------------------------------------------------------
    # odvozené hodnoty
    # ------------------------------------------------------------------
    @property
    def forecast_bias(self) -> float:
        """Průměrná korekce předpovědi přes hodiny, kde už něco víme."""
        weighted = 0.0
        total = 0.0
        for bias, conf in zip(self.forecast_bias_by_hour, self.forecast_conf_by_hour):
            if conf > 0:
                weighted += bias * conf
                total += conf
        return weighted / total if total > 0 else 1.0

    @property
    def estimated_liters(self) -> float:
        """Odhad objemu nádrže z naučené kapacity (pro kontrolu zdravého rozumu)."""
        return self.tank_wh_per_k / WH_PER_LITER_K

    @property
    def confidence_pct(self) -> float:
        """Jak moc modelu věřit, 0-100 %."""
        parts = [
            min(1.0, self.tank_conf / 12.0) * 0.4,
            min(1.0, self.heater_conf / 12.0) * 0.25,
            min(1.0, self.loss_conf / 10.0) * 0.15,
            min(1.0, sum(self.forecast_conf_by_hour) / 60.0) * 0.2,
        ]
        return round(sum(parts) * 100.0, 1)

    def energy_to_heat_wh(self, from_c: float, to_c: float) -> float:
        """Kolik Wh elektřiny je potřeba na ohřev z -> na."""
        return max(0.0, (to_c - from_c) * self.tank_wh_per_k)

    def heating_minutes(self, from_c: float, to_c: float) -> float:
        if self.heater_power_w <= 0:
            return 0.0
        return self.energy_to_heat_wh(from_c, to_c) / self.heater_power_w * 60.0

    def expected_usage_wh(self, from_hour: int, to_hour: int) -> float:
        """Očekávaný odběr TUV v hodinovém intervalu (může přetéct přes půlnoc)."""
        total = 0.0
        hour = from_hour % 24
        steps = (to_hour - from_hour) % 24 or 24
        for _ in range(steps):
            total += self.usage_wh_by_hour[hour]
            hour = (hour + 1) % 24
        return total

    def expected_baseload_wh(self, from_hour: int, to_hour: int) -> float:
        total = 0.0
        hour = from_hour % 24
        steps = (to_hour - from_hour) % 24
        for _ in range(steps):
            total += self.baseload_w_by_hour[hour]
            hour = (hour + 1) % 24
        return total

    def standby_loss_w(self, tank_c: float, ambient_c: float | None) -> float:
        if ambient_c is None:
            ambient_c = 18.0
        return max(0.0, self.loss_w_per_k * (tank_c - ambient_c))


def sigmoid_confidence(samples: float, half: float = 8.0) -> float:
    """Pomocná: 0-1 podle počtu vzorků, půlka při `half`."""
    return 1.0 / (1.0 + math.exp(-(samples - half) / (half / 3.0)))
