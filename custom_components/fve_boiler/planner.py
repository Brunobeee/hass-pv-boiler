"""Rozhodovací logika: kdy pustit bojler.

Čistá funkce `decide()` bez vazby na Home Assistant, aby se dala testovat
samostatně. Bere naměřené vstupy + naučený model + nastavení a vrací
rozhodnutí i s vysvětlením, proč tak dopadlo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .const import (
    STATE_BLOCKED,
    STATE_HEAT_CURTAILED,
    STATE_HEAT_BATTERY,
    STATE_HEAT_BOOST,
    STATE_HEAT_FORCED,
    STATE_HEAT_LEGIONELLA,
    STATE_HEAT_SURPLUS,
    STATE_IDLE,
    STATE_OFF,
    STATE_SATISFIED,
    STATE_UNAVAILABLE,
    STATE_WAIT_SUN,
)
from .model import LearnedModel

# o kolik procent SoC smí spadnout, než se běžící ohřev přeruší
SOC_HYSTERESIS = 6.0
# v off-gridu reagujeme na pokles SoC ostřeji - není kam sáhnout pro dokrytí
SOC_HYSTERESIS_OFFGRID = 2.0
# nabíjecí výkon, pod kterým plnou baterii považujeme za "už nic nepobere"
CHARGE_IDLE_W = 300.0
# o kolik % SoC klesne práh ořezu, když už bojler běží (proti kmitání)
CURTAIL_HYSTERESIS = 8.0


@dataclass
class Inputs:
    """Aktuální snímek soustavy."""

    now: datetime
    heater_on: bool
    tank_temp: float | None = None
    ambient_temp: float | None = None
    pv_power_w: float | None = None
    battery_soc: float | None = None
    battery_power_w: float | None = None  # + nabíjení, - vybíjení
    grid_power_w: float | None = None  # + odběr ze sítě, - dodávka
    house_load_w: float | None = None  # celková spotřeba domu vč. bojleru
    boiler_power_w: float | None = None
    forecast_remaining_wh: float | None = None
    forecast_today_wh: float | None = None
    cheap_tariff: bool = False


@dataclass
class Settings:
    """Uživatelské nastavení (mix z options flow a number entit)."""

    enabled: bool = True
    target_temp: float = 60.0
    min_temp: float = 42.0
    max_temp: float = 75.0
    hysteresis: float = 3.0
    min_soc: float = 70.0
    reserve_soc: float = 40.0
    start_threshold: float = 0.85
    min_run_minutes: int = 6
    min_off_minutes: int = 5
    deadline_hour: int = 20
    allow_grid_backup: bool = True
    battery_capacity_kwh: float = 10.0
    legionella_days: int = 7
    legionella_temp: float = 65.0
    # ostrovní provoz: síť neexistuje, přebytek se při plné baterii zahazuje
    offgrid: bool = False
    curtail_soc: float = 95.0


@dataclass
class Decision:
    """Výsledek rozhodování."""

    heat: bool = False
    state: str = STATE_IDLE
    reason: str = ""
    active_target: float = 0.0
    need_wh: float = 0.0
    free_power_w: float = 0.0
    free_power_source: str = "n/a"
    expected_surplus_wh: float = 0.0
    deficit_wh: float = 0.0
    heating_minutes_needed: float = 0.0
    latest_start: datetime | None = None
    blocked_by_timer: bool = False
    pv_curtailed: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def is_pv_curtailed(inp: Inputs, settings: Settings) -> bool:
    """Ořezává střídač výrobu, protože ji není kam dát?

    V ostrovním provozu se přebytek nemá kam vyvést, takže při plné baterii
    střídač jednoduše sníží výrobu. Senzor výkonu FVE pak ukazuje jen aktuální
    spotřebu, ne to, co panely umí - volný výkon z něj spočítat nejde a bez
    téhle detekce by bojler zůstal stát právě když je energie zdarma nejvíc.
    """
    if not settings.offgrid or inp.battery_soc is None:
        return False

    threshold = settings.curtail_soc - (CURTAIL_HYSTERESIS if inp.heater_on else 0.0)
    if inp.battery_soc < threshold:
        return False

    # plná baterie, která se skoro nenabíjí, znamená nevyužitý potenciál panelů
    if inp.battery_power_w is None:
        return True
    return inp.battery_power_w < CHARGE_IDLE_W


def compute_free_power(inp: Inputs, model: LearnedModel, settings: Settings) -> tuple[float, str]:
    """Kolik W je právě teď k dispozici pro bojler, aniž se sáhne do sítě.

    Preferované pořadí zdrojů podle spolehlivosti:
      1. export do sítě + nabíjecí výkon baterie + co už bere bojler
         (nad min_soc smíme "ukrást" i výkon mířící do baterie)
      2. jen export + bojler (pod min_soc má baterie přednost)
      3. FVE - spotřeba domu + bojler
      4. FVE - naučený baseload
    """
    boiler_now_w = 0.0
    if inp.heater_on:
        boiler_now_w = (
            inp.boiler_power_w
            if inp.boiler_power_w is not None and inp.boiler_power_w > 50
            else model.heater_power_w
        )

    if is_pv_curtailed(inp, settings):
        # Kolik panely doopravdy umí, se změřit nedá - jistotu máme jen v tom,
        # že se energie zahazuje. Vrátíme příkon spirály, ať se bojler rozjede;
        # když panely nestačí, SoC klesne a hystereze ohřev zase ukončí.
        return max(model.heater_power_w, boiler_now_w), "orez-vyroby"

    soc = inp.battery_soc
    battery_has_room = soc is None or soc >= settings.min_soc

    if inp.grid_power_w is not None and not settings.offgrid:
        export_w = max(0.0, -inp.grid_power_w)
        if battery_has_room and inp.battery_power_w is not None:
            charge_w = max(0.0, inp.battery_power_w)
            return export_w + charge_w + boiler_now_w, "export+baterie"
        return export_w + boiler_now_w, "export"

    if inp.pv_power_w is not None and inp.house_load_w is not None:
        return max(0.0, inp.pv_power_w - inp.house_load_w) + boiler_now_w, "fve-dum"

    if inp.pv_power_w is not None:
        baseload = model.baseload_w_by_hour[inp.now.hour]
        if baseload <= 0:
            baseload = 400.0
        return max(0.0, inp.pv_power_w - baseload), "fve-odhad"

    return 0.0, "n/a"


def expected_surplus_wh(inp: Inputs, model: LearnedModel, settings: Settings) -> float:
    """Kolik Wh přebytku ještě dnes reálně čekáme, po odečtení domu a baterie."""
    if inp.forecast_remaining_wh is None:
        return 0.0

    hour = inp.now.hour
    end_hour = max(hour + 1, settings.deadline_hour)

    # předpověď zkorigovaná naučenou chybou Forecast.Solar
    raw = max(0.0, inp.forecast_remaining_wh) * model.forecast_bias

    # dům si vezme svoje
    baseload_wh = model.expected_baseload_wh(hour, end_hour % 24)
    if baseload_wh <= 0:
        baseload_wh = 400.0 * max(1, end_hour - hour)

    # baterie chce dojet na min_soc
    battery_need_wh = 0.0
    if inp.battery_soc is not None and inp.battery_soc < settings.min_soc:
        battery_need_wh = (
            (settings.min_soc - inp.battery_soc) / 100.0 * settings.battery_capacity_kwh * 1000.0
        )

    return max(0.0, raw - baseload_wh - battery_need_wh)


def decide(
    inp: Inputs,
    model: LearnedModel,
    settings: Settings,
    *,
    last_change: datetime | None = None,
    boost_until: datetime | None = None,
    boost_target: float | None = None,
    legionella_due: bool = False,
) -> Decision:
    """Hlavní rozhodovací automat."""
    d = Decision()
    d.pv_curtailed = is_pv_curtailed(inp, settings)
    d.free_power_w, d.free_power_source = compute_free_power(inp, model, settings)
    d.expected_surplus_wh = expected_surplus_wh(inp, model, settings)

    # --- 0. dostupnost dat -------------------------------------------------
    if inp.tank_temp is None:
        d.state = STATE_UNAVAILABLE
        d.reason = "Chybí údaj o teplotě v bojleru, pro jistotu netopím."
        return d

    # --- 1. tvrdé bezpečnostní meze ---------------------------------------
    if inp.tank_temp >= settings.max_temp:
        d.state = STATE_BLOCKED
        d.active_target = settings.max_temp
        d.reason = f"Teplota {inp.tank_temp:.1f} °C dosáhla bezpečnostního maxima."
        return d

    if not settings.enabled:
        d.state = STATE_OFF
        d.reason = "Automatika je vypnutá."
        return d

    # --- 2. ruční boost ----------------------------------------------------
    if boost_until is not None and inp.now < boost_until:
        target = boost_target if boost_target is not None else settings.target_temp
        d.active_target = min(target, settings.max_temp)
        if inp.tank_temp < d.active_target:
            d.heat = True
            d.state = STATE_HEAT_BOOST
            d.reason = (
                f"Ruční boost na {d.active_target:.0f} °C do "
                f"{boost_until.strftime('%H:%M')}."
            )
            d.need_wh = model.energy_to_heat_wh(inp.tank_temp, d.active_target)
            return d

    # --- 3. antilegionella -------------------------------------------------
    active_target = settings.target_temp
    legionella_mode = False
    if legionella_due:
        active_target = max(settings.legionella_temp, settings.target_temp)
        legionella_mode = True
    active_target = min(active_target, settings.max_temp)
    d.active_target = active_target

    d.need_wh = model.energy_to_heat_wh(inp.tank_temp, active_target)
    d.heating_minutes_needed = model.heating_minutes(inp.tank_temp, active_target)
    d.deficit_wh = max(0.0, d.need_wh - d.expected_surplus_wh)

    # --- 4. hystereze cílové teploty --------------------------------------
    if inp.heater_on and inp.tank_temp >= active_target:
        d.state = STATE_SATISFIED
        d.reason = f"Nahřáto na {inp.tank_temp:.1f} °C, končím."
        return d

    if not inp.heater_on and inp.tank_temp >= active_target - settings.hysteresis:
        d.state = STATE_SATISFIED
        d.reason = (
            f"Teplota {inp.tank_temp:.1f} °C je v pásmu cíle "
            f"{active_target:.0f} °C (hystereze {settings.hysteresis:.0f} K)."
        )
        return d

    # --- 5. anti-cyklování -------------------------------------------------
    if last_change is not None:
        elapsed_min = (inp.now - last_change).total_seconds() / 60.0
        if inp.heater_on and elapsed_min < settings.min_run_minutes:
            d.heat = True
            d.blocked_by_timer = True
            d.state = STATE_HEAT_SURPLUS if d.free_power_w > 0 else STATE_HEAT_FORCED
            d.reason = (
                f"Minimální doba běhu ({settings.min_run_minutes} min) ještě neuplynula."
            )
            return d
        if not inp.heater_on and elapsed_min < settings.min_off_minutes:
            d.blocked_by_timer = True
            d.state = STATE_IDLE
            d.reason = (
                f"Minimální pauza ({settings.min_off_minutes} min) ještě neuplynula."
            )
            return d

    # --- 6. topení z aktuálního přebytku ----------------------------------
    # pod komfortním minimem má teplá voda přednost před dobíjením baterie
    below_min = inp.tank_temp < settings.min_temp
    soc = inp.battery_soc
    heater_w = model.heater_power_w
    start_w = heater_w * settings.start_threshold
    # jednou rozjeté topení nevypínáme kvůli mráčku - krátkodobý propad pokryje baterie
    stop_w = heater_w * max(0.25, settings.start_threshold - 0.45)
    threshold = stop_w if inp.heater_on else start_w

    battery_full = soc is not None and soc >= 97.0
    if battery_full:
        # plná baterie už přebytek nepobere - ber i menší výkon, ať to nejde do sítě
        threshold *= 0.6

    # hystereze i na SoC: baterie se běžícím bojlerem nabíjí pomaleji a bez
    # tohohle by systém kmital kolem prahu s periodou pár minut
    soc_slack = SOC_HYSTERESIS_OFFGRID if settings.offgrid else SOC_HYSTERESIS
    soc_floor = settings.min_soc - (soc_slack if inp.heater_on else 0.0)
    soc_ok = soc is None or soc >= soc_floor
    if below_min and soc is not None and soc > settings.reserve_soc:
        soc_ok = True
    if d.pv_curtailed:
        # baterie je plná, přednost už dostala; energie by se jinak zahodila
        soc_ok = True

    if d.free_power_w >= threshold and soc_ok:
        d.heat = True
        if legionella_mode:
            d.state = STATE_HEAT_LEGIONELLA
        elif d.pv_curtailed:
            d.state = STATE_HEAT_CURTAILED
        else:
            d.state = STATE_HEAT_SURPLUS

        if d.pv_curtailed:
            d.reason = (
                f"Baterie {soc:.0f} % je plná a střídač ořezává výrobu - "
                f"energie by přišla vniveč, topím."
            )
        else:
            d.reason = (
                f"Přebytek {d.free_power_w:.0f} W ≥ práh {threshold:.0f} W "
                f"(zdroj: {d.free_power_source})."
            )
            if soc is not None:
                d.reason += f" Baterie {soc:.0f} %."
        return d

    if not soc_ok and d.free_power_w >= threshold:
        d.state = STATE_IDLE
        d.reason = (
            f"Přebytek by byl ({d.free_power_w:.0f} W), ale baterie {soc:.0f} % "
            f"je pod hranicí {soc_floor:.0f} % - nechávám ji nabít."
        )
        return d

    # --- 7. plánování: stihne to slunce? ----------------------------------
    deadline = inp.now.replace(
        hour=min(23, settings.deadline_hour), minute=0, second=0, microsecond=0
    )
    if deadline <= inp.now:
        deadline += timedelta(days=1)

    # dohřev ze sítě míří jen na komfortní minimum, ne na plný cíl
    backup_target = min(settings.min_temp + settings.hysteresis, settings.max_temp)
    backup_minutes = model.heating_minutes(inp.tank_temp, backup_target)
    latest_start = deadline - timedelta(minutes=backup_minutes + 20)
    d.latest_start = latest_start

    # 7a. slunce to podle předpovědi zvládne -> počkáme na levnou energii
    #     (ale ne když je voda pod komfortním minimem - to se řeší hned)
    if not below_min and d.expected_surplus_wh >= d.need_wh * 1.1 and inp.now < latest_start:
        d.state = STATE_WAIT_SUN
        d.reason = (
            f"Čekám na přebytek. Potřebuji {d.need_wh:.0f} Wh, "
            f"předpověď slibuje ještě {d.expected_surplus_wh:.0f} Wh."
        )
        return d

    # 7b. nestíháme -> nouzový dohřev
    if below_min or inp.now >= latest_start:
        can_use_battery = soc is not None and soc > settings.reserve_soc
        if can_use_battery:
            d.heat = True
            d.state = STATE_HEAT_BATTERY
            d.reason = (
                f"Slunce nestačí (chybí {d.deficit_wh:.0f} Wh), dohřívám na "
                f"{backup_target:.0f} °C z baterie ({soc:.0f} %)."
            )
            return d

        if settings.offgrid:
            # ostrovní provoz: mimo baterii není odkud brát
            d.state = STATE_BLOCKED
            d.reason = (
                f"Voda je studená ({inp.tank_temp:.1f} °C), ale baterie "
                f"{soc:.0f} % je na rezervě {settings.reserve_soc:.0f} % - "
                f"v ostrovním provozu není odkud dohřát, čekám na slunce."
                if soc is not None
                else "Chybí údaj o baterii, v ostrovním provozu radši netopím."
            )
            return d

        if inp.cheap_tariff:
            d.heat = True
            d.state = STATE_HEAT_FORCED
            d.reason = f"Dohřev na {backup_target:.0f} °C v levném tarifu."
            return d

        if settings.allow_grid_backup and below_min:
            d.heat = True
            d.state = STATE_HEAT_FORCED
            d.reason = (
                f"Teplota {inp.tank_temp:.1f} °C je pod minimem "
                f"{settings.min_temp:.0f} °C, dohřívám ze sítě."
            )
            return d

        d.state = STATE_BLOCKED
        d.reason = (
            "Bylo by potřeba topit, ale dohřev ze sítě je zakázaný a baterie "
            "je pod rezervou."
        )
        return d

    # --- 8. nic se neděje --------------------------------------------------
    d.state = STATE_IDLE
    d.reason = (
        f"Přebytek {d.free_power_w:.0f} W nestačí na {threshold:.0f} W, "
        f"do nejzazšího startu ({latest_start.strftime('%H:%M')}) je čas."
    )
    return d
