"""Ověří, že se schémata config flow dají postavit a projdou validací.

Home Assistant validuje konfiguraci každého selektoru voluptuous schématem.
Jediná hodnota navíc (nebo None tam, kde se čeká řetězec) shodí celý krok
formuláře a uživatel uvidí jen "Unknown error occurred" — v prohlížeči POST
na /api/config_entries/flow končí 400, důvod je vidět až v logu HA.

Tenhle test proto podstrčí za `homeassistant` minimální náhradu, která
selektory validuje stejnými pravidly jako HA, a zkusí postavit oba kroky
průvodce i s předvyplněnými hodnotami.

Spouštění:  python3 tests/test_config_flow.py
"""

from __future__ import annotations

import importlib.util
import sys
import types
from enum import Enum
from pathlib import Path

import voluptuous as vol

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "custom_components" / "fve_boiler"


# --- validační schémata převzatá z homeassistant/helpers/selector.py -------
NUMBER_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional("min"): vol.Coerce(float),
        vol.Optional("max"): vol.Coerce(float),
        vol.Optional("step"): vol.Any(
            "any", vol.All(vol.Coerce(float), vol.Range(min=1e-3))
        ),
        vol.Optional("unit_of_measurement"): str,
        vol.Optional("mode"): vol.All(vol.Coerce(str), vol.In(("box", "slider"))),
    }
)

ENTITY_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional("integration"): str,
        vol.Optional("domain"): vol.All(vol.Any(str, [str])),
        vol.Optional("device_class"): vol.All(vol.Any(str, [str])),
        vol.Optional("multiple", default=False): bool,
        vol.Optional("include_entities"): [str],
        vol.Optional("exclude_entities"): [str],
    }
)

BOOLEAN_CONFIG_SCHEMA = vol.Schema({})


class _NumberSelectorMode(str, Enum):
    BOX = "box"
    SLIDER = "slider"


def _install_homeassistant_stub() -> list[tuple[str, dict]]:
    """Podstrčí minimální `homeassistant`, ať se config_flow dá importovat."""
    seen: list[tuple[str, dict]] = []

    selector_mod = types.ModuleType("homeassistant.helpers.selector")

    def _make(kind: str, schema: vol.Schema):
        class _Selector:
            def __init__(self, config=None):
                cfg = dict(config or {})
                # enum -> hodnota, stejně jako to dělá HA při serializaci
                for key, value in list(cfg.items()):
                    if isinstance(value, Enum):
                        cfg[key] = value.value
                try:
                    schema(cfg)
                except vol.Invalid as err:
                    raise AssertionError(
                        f"{kind} dostal neplatnou konfiguraci {cfg!r}: {err}"
                    ) from err
                seen.append((kind, cfg))
                self.config = cfg

            def __call__(self, data):
                # HA selektory jsou zároveň validátory hodnoty
                return data

        return _Selector

    selector_mod.NumberSelector = _make("NumberSelector", NUMBER_CONFIG_SCHEMA)
    selector_mod.EntitySelector = _make("EntitySelector", ENTITY_CONFIG_SCHEMA)
    selector_mod.BooleanSelector = _make("BooleanSelector", BOOLEAN_CONFIG_SCHEMA)
    selector_mod.NumberSelectorConfig = dict
    selector_mod.EntitySelectorConfig = dict
    selector_mod.NumberSelectorMode = _NumberSelectorMode

    ha = types.ModuleType("homeassistant")
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.selector = selector_mod
    ha.helpers = helpers

    config_entries = types.ModuleType("homeassistant.config_entries")

    class _Flow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_show_menu(self, **kwargs):
            return {"type": "menu", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

    config_entries.ConfigFlow = _Flow
    config_entries.OptionsFlow = _Flow
    config_entries.ConfigEntry = object
    config_entries.ConfigFlowResult = dict

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda fn: fn

    for name, mod in (
        ("homeassistant", ha),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.selector", selector_mod),
        ("homeassistant.config_entries", config_entries),
        ("homeassistant.core", core),
    ):
        sys.modules[name] = mod

    return seen


def _load_config_flow():
    pkg = types.ModuleType("fveb")
    pkg.__path__ = [str(SRC)]
    sys.modules["fveb"] = pkg
    for name in ("const", "config_flow"):
        spec = importlib.util.spec_from_file_location(f"fveb.{name}", SRC / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"fveb.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["fveb.config_flow"], sys.modules["fveb.const"]


def main() -> int:
    seen = _install_homeassistant_stub()
    cf, const = _load_config_flow()

    failures: list[str] = []

    # 1. oba kroky prázdné (první instalace)
    cf.entities_schema({})
    cf.parameters_schema({})

    # 2. oba kroky předvyplněné (options flow po změně nastavení)
    filled_entities = {
        const.CONF_HEATER_SWITCH: "switch.bojler",
        const.CONF_TEMP_SENSOR: "sensor.teplota",
        const.CONF_PV_POWER: "sensor.fve",
        const.CONF_BATTERY_SOC: "sensor.soc",
        const.CONF_GRID_EXPORT_POSITIVE: True,
    }
    entities = cf.entities_schema(filled_entities)
    cf.parameters_schema(dict(const.DEFAULTS))

    print(f"postaveno {len(seen)} selektorů, všechny prošly validací HA")

    # 3. žádný selektor nesmí nést None
    for kind, cfg in seen:
        for key, value in cfg.items():
            if value is None:
                failures.append(f"{kind} má {key}=None")

    # 4. volitelné entity musí jít odebrat -> suggested_value, ne default
    for key, marker in entities.schema.items():
        if not isinstance(marker, vol.Optional):
            continue
        if marker.default is not vol.UNDEFINED:
            failures.append(
                f"volitelné pole {marker.schema} má default -> nepůjde odebrat"
            )

    # 5. předvyplnění se musí opravdu propsat do formuláře
    soc_marker = next(
        m for m in entities.schema if str(m) == const.CONF_BATTERY_SOC
    )
    suggested = (soc_marker.description or {}).get("suggested_value")
    if suggested != "sensor.soc":
        failures.append(f"předvyplnění SoC se ztratilo: {suggested!r}")

    # 6. validace vstupů druhého kroku
    bad = dict(const.DEFAULTS)
    bad[const.CONF_MIN_TEMP] = 70.0  # vyšší než cíl
    if not cf._validate(bad):
        failures.append("validace nechytila minimální teplotu nad cílovou")
    if cf._validate(dict(const.DEFAULTS)):
        failures.append("validace hlásí chybu u výchozích hodnot")

    if failures:
        print("\nCHYBY:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("config flow OK: oba kroky se postaví, nic není None, "
          "volitelné entity jdou odebrat, validace funguje")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
