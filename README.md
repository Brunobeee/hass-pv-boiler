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
- **Umí ostrovní provoz.** V off-gridu pozná, že střídač při plné baterii
  ořezává výrobu, a bojler pustí — jinak by se ta energie zahodila.

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

| Entita | Povinná | Jednotka | K čemu je |
|---|---|---|---|
| Relé bojleru | ano | — | Co se spíná (Shelly, `input_boolean`, …) |
| Teplota v bojleru | ano | °C | Bez ní integrace pro jistotu netopí |
| Výkon FVE | ano | **W / kW** | Aktuální výroba |
| Výkon na přípojce | ne | **W / kW** | **Nejcennější volitelný vstup** — z něj se počítá skutečný přetok |
| SoC baterie | ne | % | Bez ní se baterie nehlídá |
| Výkon baterie | ne | **W / kW** | Umožní „ukrást“ výkon mířící do baterie, když je nad limitem |
| Spotřeba domu | ne | **W / kW** | Náhrada, pokud nemáte měření přípojky. Musí zahrnovat i bojler |
| Příkon bojleru | ne | **W / kW** | Zkrátí učení příkonu spirály ze dnů na minuty |
| Předpověď — zbývá dnes | ne | Wh / kWh | `sensor.energy_production_today_remaining` z Forecast.Solar |
| Teplota u bojleru | ne | °C | Bez ní se nedají naučit tepelné ztráty |
| Levný tarif / HDO | ne | — | Povolí dohřev ze sítě jen v levném pásmu |

**Výkon, ne energie.** U všech čtyř výkonových vstupů chce integrace okamžitý
výkon ve wattech, ne kumulativní součet v kWh. Senzory se v HA jmenují
podobně (*Spotřeba domu* může být obojí), takže je to snadné splést — pokud
vyberete kWh entitu, integrace ji odmítne a napíše do logu, která entita to je
a co se od ní čeká. Senzor bez jednotky se bere jako W, resp. Wh.

**Pozor na znaménko u přípojky.** Většina integrací hlásí odběr kladně a
přetok záporně. Pokud to máte obráceně, zaškrtněte přepínač na konci kroku.

### Ostrovní provoz (off-grid)

Máte-li střídač odpojený od sítě, zapněte v druhém kroku **Ostrovní provoz**.
Bez toho integrace nebude fungovat správně, a to z nečekaného důvodu.

Střídač bez sítě nemá kam vyvést přebytek, takže při plné baterii jednoduše
sníží výrobu. Senzor výkonu FVE pak neukazuje, co panely umí, ale jen to, co
se zrovna spotřebuje. Integrace počítá volný výkon jako *FVE − spotřeba domu*,
takže by v takové chvíli viděla nulu a bojler nezapnula — přesně tehdy, kdy je
energie zdarma nejvíc. Navíc by se z oříznutých hodin naučila, že Forecast.Solar
výrobu přeceňuje, i když by to nebyla pravda.

Se zapnutým off-grid režimem se místo toho stane tohle:

- Baterie nad nastavenou hranicí (výchozí 95 %), která se skoro nenabíjí, se
  bere jako důkaz ořezu — bojler dostane přednost před zahozenou energií.
- Když panely přesto nestačí, SoC klesne a hystereze ohřev ukončí.
- Hodiny s ořezem se vynechají z učení chyby předpovědi.
- Dohřev ze sítě a levný tarif se ignorují, i kdyby byly zaškrtnuté. Když
  není odkud brát, integrace to řekne narovinu ve stavu `blokovano`.

Rozdíl na čtrnáctidenní simulaci ostrovního domu:

| | Off-grid vypnutý | Off-grid zapnutý |
|---|---|---|
| Ohřev celkem | 98,3 kWh | 104,5 kWh |
| Naučená korekce předpovědi | 0,64 (falešně nízká) | 0,87 (skutečnost 0,80) |
| Naučená kapacita nádrže | chyba 3,7 % | chyba 0,2 % |
| Nejčastější stav | čeká na slunce | nahřáto |

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
Test projede i ostrovní scénář s ořezem výroby.

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
jen to, co by viděla v HA, a musí si parametry odvodit sama. Proběhnou dva
scénáře — běžný provoz se sítí a ostrovní provoz, kde střídač při plné baterii
ořezává výrobu. Na konci se naučené porovná se skutečným; když se něco rozejde
o víc než 20 %, skript skončí nenulovým kódem.
