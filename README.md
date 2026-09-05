# FVE Bojler

Custom integrace pro Home Assistant, která nahřívá bojler z fotovoltaických
přebytků. Nemá natvrdo nastavené konstanty — parametry vaší soustavy si
změří sama za provozu a průběžně je zpřesňuje.

## Co to umí

- **Hlídá baterii.** Dokud není nabitá na nastavenou úroveň, má přednost před
  bojlerem. Výjimka: když voda spadne pod komfortní minimum, dostane přednost
  teplá voda.
- **Kouká na předpověď.** Když Forecast.Solar slibuje dost energie na zbytek
  dne, integrace počká na levnou energii místo aby topila hned. Když předpověď
  nestačí, začne dohřívat včas, aby to do večera stihla.
- **Učí se.** Z provozu si odvodí příkon topné spirály, tepelnou kapacitu
  nádrže, její ztráty, denní profil odběru teplé vody a systematickou chybu
  předpovědi.
- **Nedovolí studenou vodu.** Pod komfortním minimem dohřeje z baterie, v
  levném tarifu nebo ze sítě — podle toho, co je povolené a dostupné.
- **Antilegionella.** Jednou za nastavený počet dní vytáhne nádrž na 65 °C.

## Instalace

1. Zkopírujte adresář `custom_components/fve_boiler` do konfigurace HA:

```bash
cp -r custom_components/fve_boiler /config/custom_components/
```

2. Restartujte Home Assistant.
3. *Nastavení → Zařízení a služby → Přidat integraci → **FVE Bojler***.

Přes HACS: *Vlastní repozitáře → tento repozitář → typ Integration*.

## Nastavení

Průvodce má dva kroky. V prvním vyberete entity — všechny z rozbalovacích
seznamů, nic se nepíše ručně.

| Entita | Povinná | K čemu je |
|---|---|---|
| Relé bojleru | ano | Co se spíná (Shelly, `input_boolean`, …) |
| Teplota v bojleru | ano | Bez ní integrace pro jistotu netopí |
| Výkon FVE | ano | Aktuální výroba |
| Výkon na přípojce | ne | **Nejcennější volitelný vstup** — z něj se počítá skutečný přetok |
| SoC baterie | ne | Bez ní se baterie nehlídá |
| Výkon baterie | ne | Umožní „ukrást“ výkon mířící do baterie, když je nad limitem |
| Spotřeba domu | ne | Náhrada, pokud nemáte měření přípojky |
| Příkon bojleru | ne | Zkrátí učení příkonu spirály ze dnů na minuty |
| Předpověď — zbývá dnes | ne | `sensor.energy_production_today_remaining` z Forecast.Solar |
| Teplota u bojleru | ne | Bez ní se nedají naučit tepelné ztráty |
| Levný tarif / HDO | ne | Povolí dohřev ze sítě jen v levném pásmu |

**Pozor na znaménko u přípojky.** Většina integrací hlásí odběr kladně a
přetok záporně. Pokud to máte obráceně, zaškrtněte přepínač na konci kroku.

Druhý krok jsou provozní parametry. Ty důležité jdou pak měnit posuvníky
přímo z dashboardu, bez otevírání nastavení.

### Doporučený start

Hned po instalaci zavolejte službu `fve_boiler.set_model` a zadejte, co o
bojleru víte — učení pak nezačíná od odhadu:

```yaml
action: fve_boiler.set_model
data:
  heater_power_w: 2000   # příkon spirály ze štítku
  tank_liters: 120       # objem nádrže
```

Zvlášť užitečné, když nemáte měření spotřeby ani příkonu bojleru — v takové
konfiguraci si integrace příkon spirály sama odvodit nedokáže.

## Jak se rozhoduje

Každých 30 sekund (nastavitelné) projde tenhle řetěz:

1. **Bezpečnost.** Nad maximální teplotou nebo bez údaje z čidla se netopí.
2. **Boost.** Ruční požadavek má přednost před vším ostatním.
3. **Hystereze.** V pásmu kolem cílové teploty se nic neděje.
4. **Anti-cyklování.** Minimální doba běhu a pauzy chrání relé i stykač.
5. **Přebytek teď.** Volný výkon = přetok + výkon do baterie + co už bere
   bojler. Nad prahem (výchozí 85 % příkonu spirály) se topí. Když je baterie
   pod limitem, počítá se jen přetok — nabíjení má přednost.
6. **Plánování.** Když přebytek zrovna není: porovná se potřeba energie
   (z naučené kapacity nádrže) s tím, co ještě dnes čeká podle zkorigované
   předpovědi. Vyjde-li to, čeká se na slunce. Nevyjde-li, spočítá se nejzazší
   start, aby byla voda do nastavené hodiny aspoň na minimu.
7. **Dohřev.** Nejdřív z baterie (nad rezervou), pak levný tarif, pak síť.

Sensor **Stav** má v atributu `duvod` větu, proč se integrace zrovna takhle
rozhodla — tam se koukněte, když vám chování nesedí.

## Co se učí a jak dlouho to trvá

| Parametr | Zdroj | Kdy je použitelný |
|---|---|---|
| Příkon spirály | Wattmetr, nebo skok spotřeby po sepnutí | Hodiny |
| Kapacita nádrže [Wh/K] | Dokončené topné cykly | 2–3 dny |
| Tepelné ztráty [W/K] | Dlouhé klidové úseky bez odběru | Týden |
| Profil odběru TUV | Rychlé poklesy teploty po hodinách dne | Týden |
| Korekce předpovědi | Předpověď vs. skutečná výroba po hodinách | 1–2 týdny |

Senzor **Naučenost modelu** ukazuje, jak daleko je. Cykly, do kterých někdo
zasáhl odběrem teplé vody, i hrubě odlehlé vzorky se zahazují — jeden
divný den model nerozhodí.

Ověřeno simulací (`tests/simulate.py`): po dvou týdnech na simulovaném domě
sedí kapacita nádrže na 2,5 %, příkon spirály přesně, ztráty na 7 % a korekce
předpovědi na 7 %. Ze sítě se dobralo 7 % energie na ohřev, zbytek ze slunce.

## Služby

| Služba | K čemu |
|---|---|
| `fve_boiler.boost` | Nahřát hned, bez ohledu na přebytek (`duration`, `target_temp`) |
| `fve_boiler.set_model` | Ručně předvyplnit parametry modelu |
| `fve_boiler.reset_learning` | Zahodit vše naučené a začít znovu |

## Dashboard

V `dashboard.yaml` je hotová karta — zkopírujte její obsah do ručně přidané
karty na dashboardu.

## Testy

```bash
python3 tests/simulate.py 14
```

Simuluje dům s FVE, baterií a bojlerem po minutách. Integrace v simulaci vidí
jen to, co by viděla v HA, a musí si parametry odvodit sama. Na konci porovná
naučené se skutečným a skončí nenulovým kódem, když se něco rozejde o víc než
20 %.
