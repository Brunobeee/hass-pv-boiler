"""Konstanty integrace FVE Bojler."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "fve_boiler"
PLATFORMS: Final = ["switch", "sensor", "binary_sensor", "number"]

STORAGE_VERSION: Final = 1
STORAGE_KEY_TMPL: Final = "fve_boiler.{entry_id}.model"

# --- konfigurace: entity ---
CONF_HEATER_SWITCH: Final = "heater_switch"
CONF_TEMP_SENSOR: Final = "temp_sensor"
CONF_PV_POWER: Final = "pv_power"
CONF_BATTERY_SOC: Final = "battery_soc"
CONF_BATTERY_POWER: Final = "battery_power"
CONF_GRID_POWER: Final = "grid_power"
CONF_HOUSE_LOAD: Final = "house_load"
CONF_BOILER_POWER: Final = "boiler_power"
CONF_FORECAST_REMAINING: Final = "forecast_remaining"
CONF_FORECAST_TODAY: Final = "forecast_today"
CONF_AMBIENT_TEMP: Final = "ambient_temp"
CONF_CHEAP_TARIFF: Final = "cheap_tariff"

ENTITY_KEYS: Final = (
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

# --- konfigurace: parametry ---
CONF_GRID_EXPORT_POSITIVE: Final = "grid_export_positive"
CONF_OFFGRID: Final = "offgrid"
CONF_CURTAIL_SOC: Final = "curtail_soc"
CONF_BATTERY_CAPACITY: Final = "battery_capacity_kwh"
CONF_TARGET_TEMP: Final = "target_temp"
CONF_MIN_TEMP: Final = "min_temp"
CONF_MAX_TEMP: Final = "max_temp"
CONF_TEMP_HYSTERESIS: Final = "temp_hysteresis"
CONF_MIN_SOC: Final = "min_soc"
CONF_RESERVE_SOC: Final = "reserve_soc"
CONF_START_THRESHOLD: Final = "start_threshold"
CONF_MIN_RUN_MIN: Final = "min_run_minutes"
CONF_MIN_OFF_MIN: Final = "min_off_minutes"
CONF_DEADLINE_HOUR: Final = "deadline_hour"
CONF_ALLOW_GRID: Final = "allow_grid_backup"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LEGIONELLA_DAY: Final = "legionella_day"
CONF_LEGIONELLA_TEMP: Final = "legionella_temp"

DEFAULTS: Final[dict] = {
    CONF_GRID_EXPORT_POSITIVE: False,
    CONF_OFFGRID: False,
    CONF_CURTAIL_SOC: 95.0,
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_TARGET_TEMP: 60.0,
    CONF_MIN_TEMP: 42.0,
    CONF_MAX_TEMP: 75.0,
    CONF_TEMP_HYSTERESIS: 3.0,
    CONF_MIN_SOC: 70.0,
    CONF_RESERVE_SOC: 40.0,
    CONF_START_THRESHOLD: 0.85,
    CONF_MIN_RUN_MIN: 10,
    CONF_MIN_OFF_MIN: 8,
    CONF_DEADLINE_HOUR: 20,
    CONF_ALLOW_GRID: True,
    CONF_SCAN_INTERVAL: 30,
    CONF_LEGIONELLA_DAY: 7,
    CONF_LEGIONELLA_TEMP: 65.0,
}

# --- stavy rozhodovacího automatu ---
STATE_OFF: Final = "vypnuto"
STATE_IDLE: Final = "necinnost"
STATE_HEAT_SURPLUS: Final = "ohrev_z_prebytku"
STATE_HEAT_BATTERY: Final = "ohrev_z_baterie"
STATE_HEAT_FORCED: Final = "nouzovy_ohrev"
STATE_HEAT_LEGIONELLA: Final = "antilegionella"
STATE_HEAT_BOOST: Final = "rucni_boost"
STATE_HEAT_CURTAILED: Final = "ohrev_z_oriznute_vyroby"
STATE_WAIT_SUN: Final = "ceka_na_slunce"
STATE_SATISFIED: Final = "nahrato"
STATE_BLOCKED: Final = "blokovano"
STATE_UNAVAILABLE: Final = "chybi_data"

# --- výchozí hodnoty modelu ---
DEFAULT_HEATER_POWER_W: Final = 2000.0
DEFAULT_TANK_WH_PER_K: Final = 140.0   # ~120 l nádrž
DEFAULT_LOSS_W_PER_K: Final = 0.9
DEFAULT_TANK_LITERS: Final = 120.0

# fyzika: 1 litr vody = 1.163 Wh/K
WH_PER_LITER_K: Final = 1.163

# učení
EWMA_MIN_ALPHA: Final = 0.03
EWMA_START_ALPHA: Final = 0.35
MAX_CONFIDENCE: Final = 40.0
# pokles teploty rychlejší než tohle (K/h) bereme jako odběr TUV, ne jako ztrátu
DRAW_RATE_THRESHOLD_K_H: Final = 2.5

SERVICE_RESET_LEARNING: Final = "reset_learning"
SERVICE_BOOST: Final = "boost"
SERVICE_SET_MODEL: Final = "set_model"

ATTR_DURATION: Final = "duration"
ATTR_TARGET_TEMP: Final = "target_temp"
