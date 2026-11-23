# ha-marktguru
AI generated proof of concept of an Home Assistant integration for Marktguru supermarket offer notifications

The Marktguru Home Assistant integration allows you to display current supermarket offers from the Marktguru search directly as sensors in Home Assistant.
For any products and postal codes, sensors with price, retailer, offer period, image, and other details are automatically created and updated hourly.
New offers appear automatically, expired ones are removed — no restart or manual intervention required.

## About this fork

This is a fork of [thorschtn/ha-marktguru](https://github.com/thorschtn/ha-marktguru). All credit for the original integration goes there. The fork adds:

- **Editable settings.** Postal code, search terms and retailer filter can be changed under *Configure* without deleting the integration. The entry reloads itself and sensors of removed search terms are cleaned up.
- **Retailer filter.** Optional comma-separated list (for example `Marktkauf, REWE, EDEKA`); only offers of these retailers become sensors.
- **Second source: meinprospekt.de.** Covers retailers that Marktguru does not list for some regions (for example Marktkauf). It is queried every six hours with the Home Assistant home coordinates. Retailers already delivered by Marktguru are not added twice.
- **More attributes.** `source`, `link` (offer page or brochure page), `grundpreis` and `grundpreis_einheit` (price per kg or litre).
- **Search sensor.** `sensor.marktguru_suche_<zip>` runs a live search in both sources for the text in the helper `input_text.angebote_suche` and returns the hits in its `results` attribute.
- **Optional: recipes from Mealie.** If the Mealie integration is set up, the option `mealie_recipes` adds a sensor with recipes whose main ingredient is on offer and the meal plan of the next days. Without Mealie nothing changes. See section 7.
- **Fixes.** Unloading the entry works, new offers create sensors during the hourly refresh, and the search-term sensor reports the number of terms instead of a string that could exceed 255 characters.

Both sources are unofficial and can change or stop working at any time. Like the original, this is a hobby project without support.

<img width="1240" height="654" alt="image" src="https://github.com/user-attachments/assets/9fb88174-7c4a-4f0a-b2a2-a770a9655403" />


# Disclaimer
This is not a robust software implementation. It's merely an AI-generated proof of concept. I provide no support and don't have the expertise to develop it into a finished implementation.
Anyone who wants to complete the integration is welcome to join!

# Known Issues
- None known for the search term list in this fork (fixed, see above).
- The search helper `input_text.angebote_suche` has to be created by hand (Settings → Devices & Services → Helpers → Text) if you want to use the search sensor.

# Installation & Configuration Guide
## 1. Requirements
Home Assistant (Core, OS, Supervised, or Container)
## 2. Installation
Manual Installation
- Download the repository from GitHub.
- Copy the marktguru folder into your Home Assistant directory:
- <config>/custom_components/marktguru
- Restart Home Assistant.
## 3. Configuration
Add the integration:
Go to
Settings → Devices & Services → Add Integration
Search for “Marktguru” and select it.

Fill in the configuration:

- Postal code:
The ZIP code for which you want to receive offers (e.g., 10115).
- Search terms:
Comma-separated list of products you want to monitor, e.g.:
Havana Rum, Bacardi Spiced, Wernesgruner
Finish:
After saving, the sensors are automatically created.
You can add as many instances of the integration as you like (e.g., for different ZIP codes or product lists).

## 4. Usage
Sensors:
For each found offer, a sensor like
sensor.marktguru_havana_rum_1_10115
is created, with attributes such as price, retailer, offer period, etc.
Automatic updates:
New offers appear automatically, expired ones are removed.
Multiple configurations:
You can monitor the same products for different ZIP codes.
## 5. Example Lovelace Card
marktguru_card.yaml

## 6. Example setup: offers and weekly staples

The folder [`examples/`](examples/) contains the setup this fork was built for. This part works without Mealie: an offers card with tabs and search, and an automation that puts weekly staples on a shopping list. Texts and article names are in German, adjust them to your household.

| File | What it does |
| --- | --- |
| `dashboard_template.yaml` | `button_card_templates` entries. `angebot_zeile` is one offer row (image, name, base price, discount badge, link to the offer, add-to-list button). `rezept_zeile` is only needed for the Mealie recipe card below and can be left out. Goes to the top level of the dashboard's raw configuration. |
| `dashboard_card.yaml` | The card: search field, tabs (fruit, Marktkauf, REWE, EDEKA), scrolling offer lists sorted by discount, live search results. Tab and search text are kept per device. |
| `automation_grundartikel.yaml` | Every Thursday puts staples on the list in a per-article rhythm. If an offer is at least 15 % below the usual base price, the quantity is raised (stock-up) and the article is skipped until the stock is used up. "Fruit of the week" lists up to five fruit offers. Offers it took over disappear from the card until the next run, and a push summary is sent if something new was added. |
| `automation_quellen_ueberwachen.yaml` | Daily notification if marktguru or meinprospekt delivers no offers. |
| `script_abgleich_wert_setzen.yaml` | Script to set usual price, rhythm and stock date per article, e.g. from receipts. |

Requirements:

- Frontend (HACS): [auto-entities](https://github.com/thomasloven/lovelace-auto-entities), [button-card](https://github.com/custom-cards/button-card), [card-mod](https://github.com/thomasloven/lovelace-card-mod)
- Helpers: `input_select.angebote_reiter` (options `Obst`, `Marktkauf`, `REWE`, `EDEKA`), `input_text.angebote_suche`, and, each with the maximum length of 255, `input_text.einkauf_vorrat`, `input_text.einkauf_preise`, `input_text.einkauf_rhythmus`, `input_text.einkauf_uebernommen`
- Per device (optional): `input_select.angebote_reiter_<id>` and `input_text.angebote_suche_<id>`, where `<id>` is the device's browser_mod id without the leading `browser_mod_`. This needs [browser_mod](https://github.com/thomasloven/hass-browser_mod). Reload the integration after creating the helpers, it adds one search sensor per search helper. Devices without their own helpers share the two general ones.
- A to-do list entity for the shopping list (here `todo.einkauf_bring` from the Bring! integration)

Replace before use: `GERAET_1` (browser_mod id of a device), `DEINE_PLZ` (your ZIP code, in lower case as part of the search sensor's entity id), `todo.einkauf_bring`, `notify.mobile_app_DEIN_HANDY`, the retailer names if you use other shops, and `start` plus the `artikel` list in the automation.

## 7. Optional: Mealie (recipes, week plan, shopping list)

Everything in this section is optional. Without the Mealie integration, leave the option `mealie_recipes` off and skip these files; the integration and the setup from section 6 work unchanged.

| File | What it does |
| --- | --- |
| `dashboard_card_rezepte.yaml` | Recipe card: up to five Mealie recipes whose main ingredient is on offer. A tap opens ingredients and steps in a popup, the calendar button asks for the evening and plans the recipe. The button "Andere Vorschläge" shows other recipes; planned ones stay. |
| `dashboard_card_wochenplan.yaml` | The dinners of the next seven days from the Mealie meal plan; a tap on a dish offers to move it to another evening or to remove it. |
| `script_rezept_einplanen.yaml` | Puts a recipe into the Mealie meal plan on the chosen evening and adds its ingredients to the Mealie shopping list, converted to the household size. Called without a date and with a browser_mod id it asks for the evening itself. |
| `script_rezept_ausplanen.yaml` | Removes a recipe from the meal plan again and takes its ingredients off the Mealie shopping list. |
| `script_rezept_verschieben.yaml` | Moves a planned recipe to another day. The shopping list stays as it is. |
| `automation_mealie_einkaufsliste_abgleich.yaml` | Keeps the Mealie shopping list and the real shopping list (here Bring!) in step: merges equal articles, adds up quantities, keeps notes you typed yourself and skips pantry articles. Independent of this integration, but the scripts above rely on it. |

Additional requirements:

- The [Mealie integration](https://www.home-assistant.io/integrations/mealie/) with its shopping list as a to-do entity
- [browser_mod](https://github.com/thomasloven/hass-browser_mod) for the popups
- Helpers: `input_button.rezepte_neu_mischen` (each press shows other suggestions) and `input_number.einkauf_portionen` (household size in portions, steps of 0.5; quantities of a recipe are converted from its servings when it is planned, counted things are rounded up to halves; 0 or no helper keeps the recipe quantities)
- The options `mealie_recipes` and, to mark ingredients that are already on the list, `shopping_list` of this integration

Replace before use: `MEALIE_EINTRAG` (config entry id of the Mealie integration), `MEALIE_LISTE` (to-do entity of the Mealie shopping list), `DEINE_PLZ`, and `todo.mealie_to_bring` / `todo.einkauf_bring` in the sync automation.

### How the recipe suggestions work

With the option `mealie_recipes` the integration reads all recipes from the Mealie integration once a day, looks for main ingredients (meat, fish, cheese, vegetables; spices and staples do not count) and loads offers for the most frequent ones as up to 20 extra search terms. These do not create offer sensors. `sensor.marktguru_rezepte_<zip>` carries the dinners of the next eight days in the attribute `plan` and lists up to five recipes whose main ingredient is on offer, best discount first, meat and fish weighted above cheese and vegetables. The list is mixed: at first only one recipe per main ingredient, at least one vegetarian recipe if one fits, and recipes that were not cooked for a while (Mealie's "last made") get a small bonus. Recipes from the meal plan of the last 14 days are left out, recipes planned for the coming days always stay at the top and are marked as planned. A press on `input_button.rezepte_neu_mischen` puts the current, unplanned suggestions aside and shows the next ones; when all have been shown it starts over. With the option `shopping_list` (a to-do entity, e.g. the Bring! list) main ingredients that are already on the list are marked. The planning script still adds all ingredients to the Mealie shopping list so that quantities can be summed up. Recipe images are copied to `www/marktguru_rezepte` so the card can show them without a Mealie login.
