"""Simulace provozu: ověřuje, že se model sbíhá ke skutečné soustavě.

Spouštění:  python3 tests/simulate.py [dnů]

Simuluje dům s FVE, baterií a bojlerem po minutách. Integrace vidí jen to,
co by viděla v HA (senzory), a musí si sama odvodit parametry nádrže.
Na konci se porovná naučené se skutečným.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "custom_components" / "fve_boiler"

# načteme moduly integrace jako balíček, ale bez __init__.py (ten táhne HA)
_pkg = types.ModuleType("fveb")
_pkg.__path__ = [str(SRC)]
sys.modules["fveb"] = _pkg
for _name in ("const", "model", "planner", "learner"):
    _spec = importlib.util.spec_from_file_location(f"fveb.{_name}", SRC / f"{_name}.py")
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[f"fveb.{_name}"] = _mod
    _spec.loader.exec_module(_mod)

from fveb.learner import Learner  # noqa: E402
from fveb.model import LearnedModel  # noqa: E402
from fveb.planner import Inputs, Settings, decide  # noqa: E402

WH_PER_L_K = 1.163
COLD_WATER_C = 12.0


# ---------------------------------------------------------------------------
# skutečná soustava, kterou má integrace uhodnout
# ---------------------------------------------------------------------------
@dataclass
class Truth:
    tank_liters: float = 160.0
    heater_w: float = 2400.0
    loss_w_per_k: float = 1.2
    ambient_c: float = 18.0
    pv_peak_w: float = 6000.0
    battery_kwh: float = 10.0
    battery_max_w: float = 5000.0
    forecast_optimism: float = 1.25  # předpověď je o čtvrtinu přehnaná

    @property
    def wh_per_k(self) -> float:
        return self.tank_liters * WH_PER_L_K


# odběry teplé vody: hodina -> litrů
DRAWS = {6: 35.0, 7: 25.0, 12: 15.0, 18: 20.0, 20: 60.0, 21: 15.0}

# spotřeba domu podle hodiny [W]
HOUSE = {h: 320.0 for h in range(24)}
HOUSE.update({7: 900.0, 8: 700.0, 12: 1200.0, 17: 800.0, 18: 1800.0, 19: 1400.0, 20: 900.0})


def pv_power(t: datetime, truth: Truth, cloud: float) -> float:
    """Sinusová křivka 5:30-19:30 zeslabená oblačností."""
    hour = t.hour + t.minute / 60.0
    if not 5.5 <= hour <= 19.5:
        return 0.0
    phase = (hour - 5.5) / 14.0
    return truth.pv_peak_w * math.sin(math.pi * phase) * cloud


def day_forecast_wh(day_start: datetime, truth: Truth, cloud: float) -> list[float]:
    """Hodinová předpověď - systematicky optimistická."""
    out = []
    for h in range(24):
        t = day_start.replace(hour=h, minute=30)
        out.append(pv_power(t, truth, cloud) * truth.forecast_optimism)
    return out


class House:
    """Fyzika: nádrž, baterie, tok energie."""

    def __init__(self, truth: Truth, tank_c: float = 45.0, soc: float = 30.0) -> None:
        self.truth = truth
        self.tank_c = tank_c
        self.soc = soc
        self.heater_on = False
        self.grid_import_wh = 0.0
        self.grid_export_wh = 0.0
        self.boiler_wh = 0.0
        self.boiler_grid_wh = 0.0

    def step(self, t: datetime, dt_h: float, cloud: float) -> dict:
        tr = self.truth

        # --- nádrž ---------------------------------------------------------
        heat_in = tr.heater_w * dt_h if self.heater_on else 0.0
        loss = tr.loss_w_per_k * (self.tank_c - tr.ambient_c) * dt_h

        draw_l = DRAWS.get(t.hour, 0.0) if t.minute < 10 else 0.0
        draw_wh = draw_l / 10.0 * WH_PER_L_K * (self.tank_c - COLD_WATER_C) * (1.0 if t.minute < 10 else 0.0)
        # odběr rozložený do prvních 10 minut hodiny
        if t.minute < 10:
            draw_wh = (draw_l / 10.0) * WH_PER_L_K * (self.tank_c - COLD_WATER_C)
        else:
            draw_wh = 0.0

        self.tank_c += (heat_in - loss - draw_wh) / tr.wh_per_k
        self.tank_c = max(COLD_WATER_C, min(95.0, self.tank_c))

        # --- elektrická bilance --------------------------------------------
        pv = pv_power(t, tr, cloud)
        house = HOUSE[t.hour]
        boiler = tr.heater_w if self.heater_on else 0.0
        net = pv - house - boiler

        cap_wh = tr.battery_kwh * 1000.0
        battery_w = 0.0
        if net > 0:
            room_wh = (100.0 - self.soc) / 100.0 * cap_wh
            charge_w = min(net, tr.battery_max_w, room_wh / dt_h if dt_h > 0 else 0)
            charge_w = max(0.0, charge_w)
            self.soc += charge_w * dt_h / cap_wh * 100.0
            battery_w = charge_w
            export = net - charge_w
            grid = -export
            self.grid_export_wh += export * dt_h
        else:
            avail_wh = max(0.0, (self.soc - 10.0) / 100.0 * cap_wh)
            discharge_w = min(-net, tr.battery_max_w, avail_wh / dt_h if dt_h > 0 else 0)
            discharge_w = max(0.0, discharge_w)
            self.soc -= discharge_w * dt_h / cap_wh * 100.0
            battery_w = -discharge_w
            grid = -net - discharge_w
            self.grid_import_wh += grid * dt_h

        self.soc = max(0.0, min(100.0, self.soc))

        if self.heater_on:
            self.boiler_wh += boiler * dt_h
            if grid > 0:
                self.boiler_grid_wh += min(grid, boiler) * dt_h

        return {
            "pv": pv,
            "house_total": house + boiler,
            "battery_w": battery_w,
            "grid": grid,
            "boiler": boiler,
        }


def offgrid_pv_seen(pv_potential: float, house: "House", truth: Truth, load_w: float,
                    dt_h: float) -> float:
    """Co ukáže senzor FVE v ostrovním provozu.

    Střídač bez sítě nemá kam vyvést přebytek, takže výrobu ořízne na to, co
    zrovna spotřebuje dům a co se vejde do baterie.
    """
    room_wh = (100.0 - house.soc) / 100.0 * truth.battery_kwh * 1000.0
    absorbable = load_w + min(truth.battery_max_w, room_wh / dt_h if dt_h > 0 else 0.0)
    return min(pv_potential, max(0.0, absorbable))


def run(days: int = 7, verbose: bool = True) -> tuple[LearnedModel, dict]:
    truth = Truth()
    house = House(truth)
    model = LearnedModel()
    learner = Learner(model)

    settings = Settings(
        target_temp=60.0,
        min_temp=42.0,
        max_temp=75.0,
        hysteresis=3.0,
        min_soc=70.0,
        reserve_soc=40.0,
        start_threshold=0.85,
        min_run_minutes=6,
        min_off_minutes=5,
        deadline_hour=20,
        allow_grid_backup=True,
        battery_capacity_kwh=truth.battery_kwh,
    )

    start = datetime(2026, 6, 1, 0, 0)
    dt_h = 1.0 / 60.0
    last_change: datetime | None = None
    clouds = [1.0, 0.95, 0.30, 0.25, 1.0, 0.7, 1.0, 0.4, 0.9, 1.0]

    stats = {"below_min_minutes": 0, "switches": 0, "min_temp_seen": 99.0}
    daily = []

    for day in range(days):
        cloud = clouds[day % len(clouds)]
        day_start = start + timedelta(days=day)
        forecast = day_forecast_wh(day_start, truth, cloud)
        day_states: dict[str, int] = {}

        for minute in range(24 * 60):
            t = day_start + timedelta(minutes=minute)
            phys = house.step(t, dt_h, cloud)

            remaining_wh = sum(forecast[t.hour + 1 :]) + forecast[t.hour] * (
                1.0 - t.minute / 60.0
            )

            inp = Inputs(
                now=t,
                heater_on=house.heater_on,
                tank_temp=round(house.tank_c, 1),  # čidlo s rozlišením 0,1 K
                ambient_temp=truth.ambient_c,
                pv_power_w=phys["pv"],
                battery_soc=round(house.soc, 1),
                battery_power_w=phys["battery_w"],
                grid_power_w=phys["grid"],
                house_load_w=phys["house_total"],
                boiler_power_w=phys["boiler"] if phys["boiler"] > 0 else 0.0,
                forecast_remaining_wh=remaining_wh,
                cheap_tariff=t.hour in (0, 1, 2, 3, 4, 5),
            )

            decision = decide(inp, model, settings, last_change=last_change)
            learner.update(
                inp,
                heating_from_surplus=decision.state in ("ohrev_z_prebytku", "antilegionella"),
            )

            if decision.heat != house.heater_on:
                house.heater_on = decision.heat
                last_change = t
                stats["switches"] += 1

            day_states[decision.state] = day_states.get(decision.state, 0) + 1
            if house.tank_c < settings.min_temp:
                stats["below_min_minutes"] += 1
            stats["min_temp_seen"] = min(stats["min_temp_seen"], house.tank_c)

        daily.append((day, cloud, dict(day_states)))
        if verbose:
            top = sorted(day_states.items(), key=lambda kv: -kv[1])[:3]
            print(
                f"den {day + 1} (oblačnost {cloud:.2f}): "
                f"nádrž {house.tank_c:4.1f} °C, SoC {house.soc:5.1f} %, "
                f"kapacita {model.tank_wh_per_k:5.1f} Wh/K, "
                f"spirála {model.heater_power_w:5.0f} W, "
                f"ztráty {model.loss_w_per_k:.2f} W/K, "
                f"bias {model.forecast_bias:.2f}, "
                f"důvěra {model.confidence_pct:4.1f} % | "
                + ", ".join(f"{k}:{v}" for k, v in top)
            )

    stats["daily"] = daily
    stats["truth"] = truth
    stats["house"] = house
    return model, stats


def run_offgrid(days: int = 14) -> dict:
    """Ostrovní provoz: síť neexistuje a při plné baterii se výroba ořezává."""
    truth = Truth()
    house = House(truth, tank_c=45.0, soc=85.0)
    model = LearnedModel()
    learner = Learner(model)
    settings = Settings(
        battery_capacity_kwh=truth.battery_kwh,
        allow_grid_backup=False,
        offgrid=True,
        curtail_soc=95.0,
    )

    start = datetime(2026, 6, 1)
    dt_h = 1.0 / 60.0
    last_change: datetime | None = None
    clouds = [1.0, 0.95, 0.30, 0.25, 1.0, 0.7, 1.0, 0.4, 0.9, 1.0, 0.85, 0.5, 1.0, 0.95]
    curtailed_min = 0
    wasted_wh = 0.0
    states: dict[str, int] = {}

    for day in range(days):
        cloud = clouds[day % len(clouds)]
        day_start = start + timedelta(days=day)
        forecast = day_forecast_wh(day_start, truth, cloud)

        for minute in range(24 * 60):
            t = day_start + timedelta(minutes=minute)
            pv_potential = pv_power(t, truth, cloud)
            load_w = HOUSE[t.hour] + (truth.heater_w if house.heater_on else 0.0)
            pv_seen = offgrid_pv_seen(pv_potential, house, truth, load_w, dt_h)
            if pv_seen < pv_potential - 50:
                curtailed_min += 1
                wasted_wh += (pv_potential - pv_seen) * dt_h

            phys = house.step(t, dt_h, cloud)
            remaining_wh = sum(forecast[t.hour + 1 :]) + forecast[t.hour] * (
                1.0 - t.minute / 60.0
            )
            inp = Inputs(
                now=t,
                heater_on=house.heater_on,
                tank_temp=round(house.tank_c, 1),
                ambient_temp=truth.ambient_c,
                pv_power_w=pv_seen,
                battery_soc=round(house.soc, 1),
                battery_power_w=phys["battery_w"],
                house_load_w=load_w,
                boiler_power_w=phys["boiler"] if phys["boiler"] > 0 else 0.0,
                forecast_remaining_wh=remaining_wh,
            )
            decision = decide(inp, model, settings, last_change=last_change)
            learner.update(inp, pv_curtailed=decision.pv_curtailed)
            if decision.heat != house.heater_on:
                house.heater_on = decision.heat
                last_change = t
            states[decision.state] = states.get(decision.state, 0) + 1

    return {
        "model": model,
        "house": house,
        "curtailed_min": curtailed_min,
        "wasted_wh": wasted_wh,
        "states": states,
        "truth": truth,
        "days": days,
    }


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    model, stats = run(days)
    truth: Truth = stats["truth"]
    house: House = stats["house"]

    print("\n--- naučeno vs. skutečnost ---")
    rows = [
        ("kapacita nádrže [Wh/K]", model.tank_wh_per_k, truth.wh_per_k),
        ("objem nádrže [l]", model.estimated_liters, truth.tank_liters),
        ("příkon spirály [W]", model.heater_power_w, truth.heater_w),
        ("ztráty [W/K]", model.loss_w_per_k, truth.loss_w_per_k),
        ("korekce předpovědi", model.forecast_bias, 1.0 / truth.forecast_optimism),
    ]
    ok = True
    for name, learned, real in rows:
        err = abs(learned - real) / real * 100.0
        flag = "OK " if err < 20 else "!! "
        ok &= err < 20
        print(f"{flag}{name:26s} {learned:8.2f}   skutečnost {real:8.2f}   chyba {err:5.1f} %")

    total = house.boiler_wh
    solar = total - house.boiler_grid_wh
    print(f"\nohřev celkem {total / 1000:.1f} kWh, z toho ze sítě "
          f"{house.boiler_grid_wh / 1000:.1f} kWh ({house.boiler_grid_wh / max(total, 1) * 100:.0f} %)")
    print(f"podíl slunce {solar / max(total, 1) * 100:.0f} %")
    print(f"zapnutí bojleru: {stats['switches'] // 2}  (~{stats['switches'] / 2 / days:.1f}/den)")
    print(f"nejnižší teplota: {stats['min_temp_seen']:.1f} °C, "
          f"minut pod minimem: {stats['below_min_minutes']}")
    print(f"důvěra modelu: {model.confidence_pct:.1f} %")

    print("\nprofil odběru TUV [Wh/h]:")
    print("  " + " ".join(f"{h:02d}:{v:4.0f}" for h, v in enumerate(model.usage_wh_by_hour) if v > 20))

    # --- ostrovní provoz ---------------------------------------------------
    off = run_offgrid(days)
    om: LearnedModel = off["model"]
    oh: House = off["house"]
    otruth: Truth = off["truth"]
    print(f"\n--- ostrovní provoz ({days} dní) ---")
    print(f"minut s ořezanou výrobou: {off['curtailed_min']}, "
          f"zahozeno {off['wasted_wh'] / 1000:.0f} kWh")
    print(f"ohřev {oh.boiler_wh / 1000:.1f} kWh, nádrž na konci {oh.tank_c:.1f} °C")

    cap_err = abs(om.tank_wh_per_k - otruth.wh_per_k) / otruth.wh_per_k * 100.0
    bias_err = abs(om.forecast_bias - 1.0 / otruth.forecast_optimism) * otruth.forecast_optimism * 100.0
    for name, err in (("kapacita nádrže", cap_err), ("korekce předpovědi", bias_err)):
        flag = "OK " if err < 20 else "!! "
        ok &= err < 20
        print(f"{flag}{name:26s} chyba {err:5.1f} %  "
              f"(ořezané hodiny se do učení předpovědi nesmí započítat)")

    top = sorted(off["states"].items(), key=lambda kv: -kv[1])[:4]
    print("stavy: " + ", ".join(f"{k}:{v}" for k, v in top))

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
