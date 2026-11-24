import asyncio
import json
import logging
import os
import re
from datetime import timedelta, datetime, timezone
import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from . import purchases as pu
from homeassistant.util import dt as dt_util
from . import recipes as rc
from .const import (
    DOMAIN,
    CONF_PRODUCTS,
    CONF_ZIP,
    CONF_RETAILERS,
    CONF_RECIPES,
    CONF_SHOPPING_LIST,
    API_URL,
    HEADERS,
    SEARCH_ENTITY,
    RESHUFFLE_ENTITY,
    PURCHASES_SIGNAL,
    CONF_LISTS,
    TAB_ENTITY_PREFIX,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(hours=1)

# Zweite Quelle: meinprospekt.de (deckt u. a. Marktkauf ab, das bei Marktguru fehlt)
MP_URL = "https://www.meinprospekt.de/search"
MP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html",
    "Accept-Language": "de-DE,de;q=0.9",
}
MP_INTERVAL = timedelta(hours=6)
# Rezepte aus Mealie: Rezeptliste täglich, Angebote zu den Hauptzutaten alle sechs Stunden
RECIPE_INTERVAL = timedelta(hours=24)
RECIPE_OFFER_INTERVAL = timedelta(hours=6)
RECIPE_IMAGE_DIR = "wochenkorb_rezepte"
# Zeitraum des Essensplans, der zum Blättern im Wochenplan gelesen wird
PLAN_DAYS_BACK = 182
PLAN_DAYS_AHEAD = 56
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _first_word(name):
    return ((name or "").split() or [""])[0].lower()


def _base_unit(short_name):
    """Einheit, auf die sich der Grundpreis bezieht (g -> kg, ml -> l)."""
    unit = (short_name or "").strip()
    if unit.lower() in ("g", "kg"):
        return "kg"
    if unit.lower() in ("ml", "l"):
        return "l"
    return unit or None


def _parse_base_price(text):
    """Aus '(1 kg = € 5.96)' wird (5.96, 'kg')."""
    match = re.search(r"([\d.,]*\s*[A-Za-zäöüÄÖÜ]+)\.?\s*=\s*(?:€\s*)?(\d+(?:[.,]\d+)?)", text or "")
    if not match:
        return None, None
    unit = re.sub(r"^1\s+", "", match.group(1).strip())
    try:
        return round(float(match.group(2).replace(",", ".")), 2), unit
    except ValueError:
        return None, None


def _first_url(node):
    if isinstance(node, str):
        return node if node.startswith("http") else None
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, list):
        for item in node:
            url = _first_url(item)
            if url:
                return url
    return None


def _local_iso(value):
    if not value:
        return None
    try:
        return dt_util.as_local(datetime.fromisoformat(value.replace("Z", "+00:00"))).isoformat()
    except Exception:
        return value


def _price(value):
    return round(value, 2) if isinstance(value, (int, float)) else value


def _normalize_mp(item, validity=None):
    """meinprospekt-Angebot in das Marktguru-Format bringen."""
    validity = validity or (None, None)
    prices = item.get("prices") or {}
    brand = item.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    base_price, base_unit = _parse_base_price(prices.get("priceByBaseUnit"))
    # Link auf die Prospektseite, auf der das Angebot steht
    parent = item.get("parentContent") or {}
    link = None
    if parent.get("id") and parent.get("type") == "static_brochure":
        link = f"https://www.meinprospekt.de/contentViewer/static/{parent['id']}"
        page = (parent.get("page") or {}).get("number")
        if page:
            link += f"?page={page}"
    return {
        "id": None,
        "price": _price(prices.get("mainPrice")),
        "oldPrice": _price(prices.get("secondaryPrice")),
        "referencePrice": base_price,
        "unit": {"shortName": base_unit} if base_unit else None,
        "description": item.get("description"),
        "brand": {"name": brand} if brand else None,
        "product": {"name": item.get("title")},
        "advertisers": [{"name": item.get("publisherName")}],
        "validityDates": [
            {
                "from": _local_iso(item.get("validFrom") or validity[0]),
                "to": _local_iso(item.get("validUntil") or validity[1]),
            }
        ],
        "_source": "meinprospekt",
        "_link": link,
        "_image_url": _first_url(item.get("offerImages")),
    }

def clean_lists(raw):
    """Freie Listen in eine feste Form bringen: Name, Symbol, Begriffe, Ausschlusswörter."""
    result, names = [], set()
    for item in raw or []:
        name = str((item or {}).get("name") or "").strip()
        if not name or name.lower() in names:
            continue
        names.add(name.lower())
        terms, seen = [], set()
        for term in item.get("terms") or []:
            term = str(term).strip()
            if term and term.lower() not in seen:
                seen.add(term.lower())
                terms.append(term)
        exclude = [str(w).strip().lower() for w in item.get("exclude") or [] if str(w).strip()]
        result.append({"name": name, "icon": item.get("icon") or "", "terms": terms, "exclude": exclude})
    return result


def with_list_terms(products, lists):
    """Suchbegriffe um die Begriffe der Listen ergänzen; was in einer Liste steht, wird immer gesucht."""
    result = list(products)
    known = {p["query"].lower() for p in result}
    for entry in lists:
        for term in entry["terms"]:
            if term.lower() not in known:
                known.add(term.lower())
                result.append({"name": term, "query": term})
    return result


async def _sync_tab_helpers(hass, lists, retailer_names):
    """Die Reiter-Helfer der Angebotskarte kennen immer alle Listen und Händler als Optionen."""
    wanted = [entry["name"] for entry in lists] + [r for r in retailer_names if r]
    if not wanted:
        return
    for state in hass.states.async_all("input_select"):
        if not state.entity_id.startswith(TAB_ENTITY_PREFIX):
            continue
        current = list(state.attributes.get("options") or [])
        # Ohne Händlerfilter bleiben die bisherigen Optionen hinter den Listen stehen
        options = wanted if retailer_names else wanted + [o for o in current if o not in wanted]
        if options != current:
            await hass.services.async_call(
                "input_select", "set_options", {"entity_id": state.entity_id, "options": options}, blocking=False
            )


async def async_setup_entry(hass, entry, async_add_entities):
    # Optionen (nachträglich geändert) haben Vorrang vor den Daten der Ersteinrichtung
    conf = {**entry.data, **entry.options}
    zip_code = conf[CONF_ZIP]
    lists = clean_lists(conf.get(CONF_LISTS))
    products = with_list_terms(conf[CONF_PRODUCTS], lists)
    retailer_names = [r.strip() for r in (conf.get(CONF_RETAILERS) or "").split(",") if r.strip()]
    retailers = [
        r.strip().lower()
        for r in (conf.get(CONF_RETAILERS) or "").split(",")
        if r.strip()
    ]

    recipes_enabled = bool(conf.get(CONF_RECIPES))
    shopping_list = (conf.get(CONF_SHOPPING_LIST) or "").strip() or None
    coordinator = WochenkorbDataCoordinator(
        hass, zip_code, products, retailers, recipes_enabled, shopping_list
    )
    coordinator.lists = lists
    # Suchbegriff (klein) -> Liste, zu der er gehört
    coordinator.list_of = {term.lower(): entry for entry in lists for term in entry["terms"]}
    await coordinator.async_config_entry_first_refresh()
    await _sync_tab_helpers(hass, lists, retailer_names)

    current_sensors = {}

    def get_sensor_id(query, idx):
        return f"wochenkorb_{zip_code}_{query.replace(' ', '_').lower()}_{idx}"

    # Initiales Setup: Angebotssensoren
    sensors = []
    for product in products:
        name = product["name"]
        query = product["query"]
        offers = coordinator.data.get(query, [])
        for idx, offer in enumerate(offers):
            sensor_id = get_sensor_id(query, idx)
            if sensor_id not in current_sensors:
                sensor = WochenkorbSensor(coordinator, name, query, idx, zip_code)
                sensors.append(sensor)
                current_sensors[sensor_id] = sensor

    # Queries-Sensor als CoordinatorEntity (wird immer behalten)
    queries_sensor_id = f"wochenkorb_queries_{zip_code}"
    queries_sensor = WochenkorbQueriesSensor(coordinator, products, zip_code)
    sensors.append(queries_sensor)
    current_sensors[queries_sensor_id] = queries_sensor

    registry = er.async_get(hass)

    # Such-Sensoren: je Text-Helfer einer. SEARCH_ENTITY gilt für alle Geräte gemeinsam,
    # Helfer mit angehängter Kennung (input_text.angebote_suche_<kennung>) für ein einzelnes Gerät.
    fixed_sensor_ids = set()
    search_entities = [SEARCH_ENTITY] + sorted(
        reg_entry.entity_id
        for reg_entry in registry.entities.values()
        if reg_entry.entity_id.startswith(SEARCH_ENTITY + "_")
    )
    for search_entity in search_entities:
        suffix = search_entity[len(SEARCH_ENTITY) + 1:]
        search_sensor = WochenkorbSearchSensor(coordinator, zip_code, search_entity, suffix)
        sensors.append(search_sensor)
        current_sensors[search_sensor.unique_id] = search_sensor
        fixed_sensor_ids.add(search_sensor.unique_id)

    # Rezept-Sensor: Vorschläge aus Mealie, deren Hauptzutat im Angebot ist
    if recipes_enabled:
        recipes_sensor = WochenkorbRecipesSensor(coordinator, zip_code)
        sensors.append(recipes_sensor)
        current_sensors[recipes_sensor.unique_id] = recipes_sensor
        fixed_sensor_ids.add(recipes_sensor.unique_id)

    # Einkaufsverlauf: ein Sensor für die ganze Integration, auch bei mehreren Einträgen
    domain_data = hass.data.get(DOMAIN, {})
    if domain_data.get("purchases") and domain_data.setdefault("purchases_entry", entry.entry_id) == entry.entry_id:
        history_sensor = PurchaseHistorySensor(domain_data["purchases"])
        sensors.append(history_sensor)
        current_sensors[history_sensor.unique_id] = history_sensor
        fixed_sensor_ids.add(history_sensor.unique_id)

    async_add_entities(sensors, True)

    # Verwaiste Sensoren entfernen (z. B. von gelöschten Suchbegriffen)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id not in current_sensors:
            registry.async_remove(reg_entry.entity_id)

    @callback
    def async_new_data():
        new_entities = []
        new_sensor_ids = set()

        # Angebote aus API
        for product in coordinator.products:
            name = product["name"]
            query = product["query"]
            offers = coordinator.data.get(query, [])
            for idx, offer in enumerate(offers):
                sensor_id = get_sensor_id(query, idx)
                new_sensor_ids.add(sensor_id)
                if sensor_id not in current_sensors:
                    sensor = WochenkorbSensor(coordinator, name, query, idx, zip_code)
                    new_entities.append(sensor)
                    current_sensors[sensor_id] = sensor

        # Queries-Sensor immer behalten/ggf. neu anlegen
        queries_sensor_id = f"wochenkorb_queries_{zip_code}"
        new_sensor_ids.add(queries_sensor_id)
        new_sensor_ids.update(fixed_sensor_ids)
        if queries_sensor_id not in current_sensors:
            queries_sensor = WochenkorbQueriesSensor(coordinator, coordinator.products, zip_code)
            new_entities.append(queries_sensor)
            current_sensors[queries_sensor_id] = queries_sensor

        # Entferne Sensoren, die nicht mehr gebraucht werden
        sensors_to_remove = []
        for sensor_id in list(current_sensors.keys()):
            if sensor_id not in new_sensor_ids:
                sensors_to_remove.append(sensor_id)

        if new_entities:
            async_add_entities(new_entities)

        if sensors_to_remove:
            for sensor_id in sensors_to_remove:
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, sensor_id)
                if entity_id:
                    registry.async_remove(entity_id)
                current_sensors.pop(sensor_id, None)

    entry.async_on_unload(coordinator.async_add_listener(async_new_data))

def _offer_summary(offer):
    """Kompakte Darstellung eines Angebots für die Suche."""
    offer_id = offer.get("id")
    dates = (offer.get("validityDates") or [{}])[0]
    return {
        "name": (offer.get("product") or {}).get("name"),
        "brand": (offer.get("brand") or {}).get("name"),
        "retailer": ((offer.get("advertisers") or [{}])[0]).get("name"),
        "price": offer.get("price"),
        "old_price": offer.get("oldPrice"),
        "grundpreis": offer.get("referencePrice"),
        "grundpreis_einheit": _base_unit((offer.get("unit") or {}).get("shortName")),
        "description": offer.get("description"),
        "valid_from": dates.get("from"),
        "valid_to": dates.get("to"),
        "image": offer.get("_image_url")
        or (f"https://mg2de.b-cdn.net/api/v1/offers/{offer_id}/images/default/0/small.webp" if offer_id else None),
        "link": offer.get("_link")
        or (f"https://www.marktguru.de/offers/{offer_id}" if offer_id else None),
        "source": offer.get("_source", "marktguru"),
    }


class WochenkorbDataCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, zip_code, products, retailers=None, recipes_enabled=False, shopping_list=None):
        super().__init__(
            hass,
            _LOGGER,
            name=f"Wochenkorb Coordinator {zip_code}",
            update_interval=SCAN_INTERVAL,
        )
        self.zip_code = zip_code
        self.products = products
        self.lists = []
        self.list_of = {}
        self.retailers = retailers or []
        self._mp_cache = {}
        self._mp_time = None
        # Rezeptvorschläge (optional, braucht die Mealie-Integration)
        self.recipes_enabled = recipes_enabled
        self.shopping_list = shopping_list
        self.suggestions = []
        self.recipe_terms = []
        self._recipes = {}
        self._recipes_time = None
        self._recipe_offers = []
        self._recipe_offers_time = None
        self._recipe_task = None
        self._planned = {}
        self._cooked = set()
        self.plan = []
        # Essensplan zum Blättern: Datum -> Abendessen, ein halbes Jahr zurück und acht Wochen voraus
        self.calendar = {}
        # Rezept -> letzter Tag, an dem es im Essensplan stand
        self._last_cooked = {}
        # Rezepte, die nach "andere Vorschläge" gerade nicht gezeigt werden
        self._skipped = set()

    async def _fetch_meinprospekt(self, session, query):
        params = {
            "query": query,
            "lat": self.hass.config.latitude,
            "lng": self.hass.config.longitude,
        }
        async with session.get(
            MP_URL, headers=MP_HEADERS, params=params, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            resp.raise_for_status()
            html = await resp.text()
        match = _NEXT_DATA_RE.search(html)
        if not match:
            return []
        data = json.loads(match.group(1))
        page = ((data.get("props") or {}).get("pageProps") or {}).get("pageInformation") or {}
        contents = (page.get("searchResults") or {}).get("contents") or {}
        offers = contents.get("offers") or []
        # Die Gültigkeit steht am Prospekt, nicht am einzelnen Angebot
        validity = {}
        for brochure in contents.get("brochures") or []:
            content = (brochure or {}).get("content") or {}
            if content.get("id"):
                validity[content["id"]] = (content.get("validFrom"), content.get("validUntil"))
        return [
            _normalize_mp(item, validity.get((item.get("parentContent") or {}).get("id")))
            for item in offers
            if isinstance(item, dict)
        ]

    async def _update_meinprospekt(self, session):
        now = datetime.now(timezone.utc)
        if self._mp_time is not None and now - self._mp_time < MP_INTERVAL:
            return
        limit = asyncio.Semaphore(4)

        async def fetch(query):
            async with limit:
                try:
                    return query, await self._fetch_meinprospekt(session, query), True
                except Exception as e:
                    _LOGGER.warning(f"meinprospekt error for {query}: {e}")
                    return query, self._mp_cache.get(query, []), False

        fetched = await asyncio.gather(*(fetch(product["query"]) for product in self.products))
        self._mp_cache = {query: offers for query, offers, _ in fetched}
        if any(ok for _, _, ok in fetched):
            self._mp_time = now

    async def _fetch_marktguru(self, session, query):
        params = {"as": "web", "limit": 24, "offset": 0, "q": query, "zipCode": self.zip_code}
        async with session.get(
            API_URL, headers=HEADERS, params=params, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [offer for offer in data.get("results", []) if self._retailer_ok(offer)]

    def _retailer_ok(self, offer):
        if not self.retailers:
            return True
        names = [(adv.get("name") or "").lower() for adv in (offer.get("advertisers") or [])]
        return any(r in name for r in self.retailers for name in names)

    async def async_search(self, query):
        """Freie Suche in beiden Quellen, mit demselben Händlerfilter."""
        async with aiohttp.ClientSession() as session:
            try:
                offers = await self._fetch_marktguru(session, query)
            except Exception as e:
                _LOGGER.warning(f"Marktguru search error for {query}: {e}")
                offers = []
            try:
                extra = await self._fetch_meinprospekt(session, query)
            except Exception as e:
                _LOGGER.warning(f"meinprospekt search error for {query}: {e}")
                extra = []
        covered = {
            _first_word(adv.get("name")) for o in offers for adv in (o.get("advertisers") or [])
        }
        seen = set()
        for offer in extra:
            retailer = offer["advertisers"][0].get("name")
            if _first_word(retailer) in covered or not self._retailer_ok(offer):
                continue
            key = (retailer, offer["product"].get("name"), offer.get("price"))
            if key not in seen:
                seen.add(key)
                offers.append(offer)
        return [_offer_summary(o) for o in offers[:40]]

    async def _async_update_data(self):
        results = {}
        async with aiohttp.ClientSession() as session:
            for product in self.products:
                query = product["query"]
                try:
                    results[query] = await self._fetch_marktguru(session, query)
                except Exception as e:
                    _LOGGER.error(f"Marktguru API error for {query}: {e}")
                    results[query] = []

            await self._update_meinprospekt(session)

        # Händler, die Marktguru schon liefert, nicht doppelt aus meinprospekt übernehmen
        covered = {
            _first_word(adv.get("name"))
            for offers in results.values()
            for offer in offers
            for adv in (offer.get("advertisers") or [])
        }
        for product in self.products:
            query = product["query"]
            seen = set()
            for offer in self._mp_cache.get(query, []):
                retailer = offer["advertisers"][0].get("name")
                if _first_word(retailer) in covered or not self._retailer_ok(offer):
                    continue
                key = (retailer, offer["product"].get("name"), offer.get("price"))
                if key in seen:
                    continue
                seen.add(key)
                results.setdefault(query, []).append(offer)

        if self.recipes_enabled and (self._recipe_task is None or self._recipe_task.done()):
            # Im Hintergrund, damit die Angebots-Sensoren nicht auf die Rezepte warten
            self._recipe_task = self.hass.async_create_background_task(
                self._update_recipes(results, covered), f"wochenkorb_recipes_{self.zip_code}"
            )
        return results

    # ----- Rezeptvorschläge aus Mealie -----

    def _mealie_entry(self):
        for entry in self.hass.config_entries.async_entries("mealie"):
            if entry.state.value == "loaded":
                return entry
        return None

    async def _mealie(self, entry, service, **data):
        response = await self.hass.services.async_call(
            "mealie",
            service,
            {"config_entry_id": entry.entry_id, **data},
            blocking=True,
            return_response=True,
        )
        return response or {}

    async def _load_recipes(self, entry):
        listing = await self._mealie(entry, "get_recipes", result_limit=1000)
        items = (listing.get("recipes") or {}).get("items") or []
        limit = asyncio.Semaphore(4)

        async def load(item):
            slug = item.get("slug")
            known = self._recipes.get(slug)
            async with limit:
                try:
                    recipe = (await self._mealie(entry, "get_recipe", recipe_id=slug)).get("recipe") or {}
                except Exception as e:
                    _LOGGER.debug(f"Mealie recipe {slug} not loaded: {e}")
                    return slug, known
            texts = [rc.ingredient_text(i) for i in recipe.get("ingredients") or []]
            texts = [t for t in texts if t]
            steps = [
                (step.get("text") or "").strip()
                for step in recipe.get("instructions") or []
                if (step.get("text") or "").strip()
            ]
            return slug, {
                "name": recipe.get("name") or item.get("name"),
                "recipe_id": recipe.get("recipe_id") or item.get("recipe_id"),
                "has_image": bool(recipe.get("image")),
                "time": recipe.get("total_time"),
                "servings": recipe.get("recipe_servings") or 0,
                "last_made": recipe.get("last_made"),
                "last_made_mealie": recipe.get("last_made"),
                "vegetarian": rc.is_vegetarian(texts),
                "ingredients": texts,
                "steps": steps,
                "mains": rc.main_ingredients(texts),
            }

        loaded = await asyncio.gather(*(load(item) for item in items if item.get("slug")))
        return {slug: recipe for slug, recipe in loaded if recipe}

    async def _load_planned(self, entry):
        """Essensplan lesen: kommende Einträge (Slug -> Datum) und die der letzten 14 Tage.

        Gelesen wird ein halbes Jahr zurück und acht Wochen voraus, damit sich im Wochenplan blättern lässt
        und bekannt ist, wann ein Rezept zuletzt auf dem Plan stand.
        """
        today = dt_util.now().date()
        plan = await self._mealie(
            entry,
            "get_mealplan",
            start_date=(today - timedelta(days=PLAN_DAYS_BACK)).isoformat(),
            end_date=(today + timedelta(days=PLAN_DAYS_AHEAD)).isoformat(),
        )
        planned, cooked = {}, set()
        calendar, last_cooked = {}, {}
        recent = (today - timedelta(days=14)).isoformat()
        # Abendessen der nächsten acht Tage für Wochenplan und Tagesauswahl
        dinners = {(today + timedelta(days=offset)).isoformat(): [] for offset in range(8)}
        for item in plan.get("mealplan") or []:
            slug = (item.get("recipe") or {}).get("slug")
            day = str(item.get("mealplan_date") or "")
            if item.get("entry_type") == "dinner":
                name = (item.get("recipe") or {}).get("name") or item.get("title") or "Notiz"
                dinner = {"name": name, "slug": slug, "id": item.get("mealplan_id")}
                calendar.setdefault(day, []).append(dinner)
                if day in dinners:
                    dinners[day].append(dinner)
            if not slug:
                continue
            if day >= today.isoformat():
                planned[slug] = min(day, planned.get(slug, day))
            else:
                last_cooked[slug] = max(day, last_cooked.get(slug, day))
                if day >= recent:
                    cooked.add(slug)
        self.plan = [{"date": day, "dinner": entries} for day, entries in dinners.items()]
        self.calendar = dict(sorted(calendar.items()))
        self._last_cooked = last_cooked
        return planned, cooked

    async def _load_shopping_list(self):
        """Offene Einträge der Einkaufsliste als Texte (Name und Notiz)."""
        if not self.shopping_list or self.hass.states.get(self.shopping_list) is None:
            return []
        response = await self.hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": self.shopping_list, "status": ["needs_action"]},
            blocking=True,
            return_response=True,
        )
        items = ((response or {}).get(self.shopping_list) or {}).get("items") or []
        return [rc.clean(f"{item.get('summary') or ''} {item.get('description') or ''}") for item in items]

    async def _recipe_image(self, entry, recipe):
        """Rezeptbild lokal ablegen, damit die Karte es ohne Mealie-Anmeldung zeigen kann."""
        recipe_id = recipe.get("recipe_id")
        if not recipe_id or not recipe.get("has_image"):
            return None
        folder = self.hass.config.path("www", RECIPE_IMAGE_DIR)
        path = os.path.join(folder, f"{recipe_id}.webp")
        url = f"/local/{RECIPE_IMAGE_DIR}/{recipe_id}.webp"
        if await self.hass.async_add_executor_job(os.path.exists, path):
            return url
        host = (entry.data.get("host") or "").rstrip("/")
        if not host:
            return None
        if not host.startswith("http"):
            host = f"http://{host}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{host}/api/media/recipes/{recipe_id}/images/min-original.webp",
                    timeout=aiohttp.ClientTimeout(total=15),
                    ssl=bool(entry.data.get("verify_ssl", True)),
                ) as resp:
                    resp.raise_for_status()
                    content = await resp.read()

            def save():
                os.makedirs(folder, exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(content)

            await self.hass.async_add_executor_job(save)
            return url
        except Exception as e:
            _LOGGER.debug(f"Mealie image for {recipe.get('name')} not loaded: {e}")
            return None

    async def _update_recipe_offers(self, covered):
        """Angebote für die Hauptzutaten laden, getrennt von den eigenen Suchbegriffen."""
        self.recipe_terms = rc.recipe_terms(self._recipes, [p["query"] for p in self.products])
        offers = []
        limit = asyncio.Semaphore(4)
        ok = False
        async with aiohttp.ClientSession() as session:
            for term in self.recipe_terms:
                try:
                    offers.extend(await self._fetch_marktguru(session, term))
                    ok = True
                except Exception as e:
                    _LOGGER.warning(f"Marktguru API error for recipe term {term}: {e}")

            async def fetch(term):
                async with limit:
                    try:
                        return await self._fetch_meinprospekt(session, term)
                    except Exception as e:
                        _LOGGER.warning(f"meinprospekt error for recipe term {term}: {e}")
                        return None

            covered = covered | {
                _first_word(adv.get("name")) for o in offers for adv in (o.get("advertisers") or [])
            }
            for extra in await asyncio.gather(*(fetch(term) for term in self.recipe_terms)):
                if extra is None:
                    continue
                ok = True
                for offer in extra:
                    retailer = offer["advertisers"][0].get("name")
                    if _first_word(retailer) not in covered and self._retailer_ok(offer):
                        offers.append(offer)
        if ok or not self.recipe_terms:
            self._recipe_offers = offers
            self._recipe_offers_time = datetime.now(timezone.utc)

    async def _update_recipes(self, results, covered):
        try:
            entry = self._mealie_entry()
            if entry is None or not self.hass.services.has_service("mealie", "get_recipes"):
                return
            now = datetime.now(timezone.utc)
            if not self._recipes or self._recipes_time is None or now - self._recipes_time > RECIPE_INTERVAL:
                self._recipes = await self._load_recipes(entry)
                self._recipes_time = now
                self._recipe_offers_time = None
            if self._recipe_offers_time is None or now - self._recipe_offers_time > RECIPE_OFFER_INTERVAL:
                await self._update_recipe_offers(covered)
            await self.async_refresh_suggestions(results)
        except Exception as e:
            _LOGGER.warning(f"Recipe suggestions not updated: {e}")

    async def async_refresh_suggestions(self, results=None):
        """Essensplan neu lesen und Vorschläge neu berechnen (ohne die Angebote neu zu laden)."""
        entry = self._mealie_entry()
        if entry is None:
            return
        try:
            self._planned, self._cooked = await self._load_planned(entry)
        except Exception as e:
            _LOGGER.debug(f"Mealie meal plan not loaded: {e}")
        try:
            listed = await self._load_shopping_list()
        except Exception as e:
            _LOGGER.debug(f"Shopping list not loaded: {e}")
            listed = []
        results = results if results is not None else (self.data or {})
        offers = [offer for group in results.values() for offer in group] + self._recipe_offers
        suggestions = []
        # Kürzlich Gekochtes fällt weg; Eingeplantes steht immer oben und wird als eingeplant markiert
        pinned = sorted(self._planned, key=lambda slug: self._planned[slug])
        # "Lange nicht gekocht" richtet sich nach dem Essensplan; Mealies eigenes Datum zählt, wenn es neuer ist
        for slug, recipe in self._recipes.items():
            dates = [str(d)[:10] for d in (recipe.get("last_made_mealie"), self._last_cooked.get(slug)) if d]
            recipe["last_made"] = max(dates) if dates else None
        rows = rc.suggest(self._recipes, offers, self._cooked, pinned=pinned, skipped=self._skipped)
        if self._skipped and len(rows) < 5:
            # Alle Kandidaten wurden einmal gezeigt: von vorn beginnen
            self._skipped = set()
            rows = rc.suggest(self._recipes, offers, self._cooked, pinned=pinned)
        for slug, label, offer, discount, others in rows:
            recipe = self._recipes[slug]
            last = self._last_cooked.get(slug)
            on_list = [
                main
                for main in recipe["mains"]
                if any(rc.BY_LABEL[main].in_ingredient(text) for text in listed)
            ]
            suggestions.append(
                {
                    "name": recipe["name"],
                    "slug": slug,
                    "recipe_id": recipe.get("recipe_id"),
                    "image": await self._recipe_image(entry, recipe),
                    "time": recipe.get("time"),
                    "servings": recipe.get("servings") or 0,
                    "vegetarian": bool(recipe.get("vegetarian")),
                    "ingredient": label,
                    "discount": discount,
                    "offer": _offer_summary(offer),
                    "also_on_offer": others,
                    "planned": self._planned.get(slug),
                    # Letzter Tag, an dem das Rezept im Essensplan stand (leer: im letzten halben Jahr nicht)
                    "last_cooked": last,
                    "on_list": on_list,
                    "ingredients": recipe["ingredients"],
                    # Zutaten, die noch zu kaufen sind: ohne Hauptzutaten, die schon auf der Liste stehen
                    "shopping": [
                        text
                        for text in recipe["ingredients"]
                        if not any(rc.BY_LABEL[main].in_ingredient(rc.clean(text)) for main in on_list)
                    ],
                    "steps": recipe["steps"],
                }
            )
        self.suggestions = suggestions
        self.async_update_listeners()

    async def async_reshuffle(self):
        """Andere Vorschläge zeigen: die aktuellen, nicht eingeplanten zurückstellen."""
        self._skipped |= {s["slug"] for s in self.suggestions if not s.get("planned")}
        await self.async_refresh_suggestions()

class WochenkorbSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, query, idx, zip_code):
        super().__init__(coordinator)
        self._name = f"Wochenkorb {name} {idx+1} ({zip_code})"
        self._query = query
        self._idx = idx
        self._zip_code = zip_code

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return f"wochenkorb_{self._zip_code}_{self._query.replace(' ', '_').lower()}_{self._idx}"

    @property
    def state(self):
        offers = self.coordinator.data.get(self._query, [])
        if len(offers) > self._idx:
            return offers[self._idx].get("price")
        return None

    @property
    def extra_state_attributes(self):
        offers = self.coordinator.data.get(self._query, [])
        if len(offers) > self._idx:
            offer = offers[self._idx]
            price = offer.get("price")
            old_price = offer.get("oldPrice")
            reference_price = offer.get("referencePrice")
            vergleichspreis = (
                f"{old_price}€" if old_price else (f"{reference_price}€" if reference_price else "-.-")
            )
            angebotspreis = f"{price}€" if price else None

            # Von- und Bis-Datum (ISO aus API)
            valid_from = offer.get("validityDates", [{}])[0].get("from") if offer.get("validityDates") else None
            valid_to = offer.get("validityDates", [{}])[0].get("to") if offer.get("validityDates") else None

            # Datum menschenlesbar (ohne Jahr)
            von_datum = "-"
            if valid_from:
                try:
                    dt_from = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
                    von_datum = dt_from.strftime("%d.%m.")
                except Exception:
                    von_datum = valid_from

            bis_datum = "-"
            if valid_to:
                try:
                    dt_to = datetime.fromisoformat(valid_to.replace("Z", "+00:00"))
                    bis_datum = dt_to.strftime("%d.%m.")
                except Exception:
                    bis_datum = valid_to

            # Restlaufzeit in Tagen
            restlaufzeit_tage = "-"
            if valid_to:
                try:
                    dt = datetime.fromisoformat(valid_to.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    diff = (dt - now).days
                    restlaufzeit_tage = diff if diff >= 0 else 0
                except Exception:
                    restlaufzeit_tage = "-"

            # Produktbild-URL extrahieren
            produkt_bild_url = None
            produkt = offer.get("product", {})
            if produkt and "images" in produkt and isinstance(produkt["images"], list) and len(produkt["images"]) > 0:
                produkt_bild_url = produkt["images"][0].get("url")
            elif "images" in offer and isinstance(offer["images"], dict) and "url" in offer["images"]:
                produkt_bild_url = offer["images"]["url"]

            # Ergänzt: results-id und image-url
            result_id = offer.get("id")
            result_image_url = offer.get("_image_url")
            if result_id:
                result_image_url = f"https://mg2de.b-cdn.net/api/v1/offers/{result_id}/images/default/0/small.webp"

            return {
                "price": angebotspreis,
                "old_price": old_price,
                "reference_price": reference_price,
                "vergleichspreis": vergleichspreis,
                "description": offer.get("description"),
                "brand_name": offer.get("brand", {}).get("name") if offer.get("brand") else None,
                "product_name": offer.get("product", {}).get("name") if offer.get("product") else None,
                "retailer_name": offer.get("advertisers", [{}])[0].get("name") if offer.get("advertisers") else None,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "von_datum": von_datum,
                "bis_datum": bis_datum,
                "restlaufzeit_tage": restlaufzeit_tage,
                "produkt_bild_url": produkt_bild_url,
                "result_id": result_id,
                "result_image_url": result_image_url,
                "zip_code": self._zip_code,
                # Suchbegriff und freie Liste, zu der er gehört (leer: Angebot steht beim Händler)
                "suchbegriff": self._query,
                "liste": (self.coordinator.list_of.get(self._query.lower()) or {}).get("name", ""),
                "liste_ohne": "|".join(
                    re.escape(w) for w in (self.coordinator.list_of.get(self._query.lower()) or {}).get("exclude", [])
                ),
                "source": offer.get("_source", "marktguru"),
                "link": offer.get("_link")
                or (f"https://www.marktguru.de/offers/{result_id}" if result_id else None),
                "grundpreis": reference_price,
                "grundpreis_einheit": _base_unit((offer.get("unit") or {}).get("shortName")),
            }
        return {}

class WochenkorbQueriesSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, products, zip_code):
        super().__init__(coordinator)
        self._zip_code = zip_code

    @property
    def name(self):
        return f"Wochenkorb Suchbegriffe ({self._zip_code})"

    @property
    def unique_id(self):
        return f"wochenkorb_queries_{self._zip_code}"

    @property
    def state(self):
        # Anzahl der Suchbegriffe; die Liste selbst steht im Attribut "queries"
        # (als Zustand wäre sie bei vielen Begriffen länger als die erlaubten 255 Zeichen)
        return len(self.coordinator.products)

    @property
    def extra_state_attributes(self):
        # Auch als Liste verfügbar
        return {
            "queries": [product["query"] for product in self.coordinator.products],
            # Freie Listen in der Reihenfolge der Einstellungen: je Liste ein Reiter der Angebotskarte
            "listen": [
                {"name": entry["name"], "icon": entry["icon"], "begriffe": entry["terms"], "ohne": entry["exclude"]}
                for entry in self.coordinator.lists
            ],
            "zip_code": self._zip_code,
        }


class WochenkorbSearchSensor(SensorEntity):
    """Sucht nach dem Text im Helfer SEARCH_ENTITY und stellt die Treffer als Attribut bereit."""

    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"results"})

    def __init__(self, coordinator, zip_code, search_entity=SEARCH_ENTITY, suffix=""):
        self._coordinator = coordinator
        self._zip_code = zip_code
        self._search_entity = search_entity
        self._suffix = suffix
        self._query = ""
        self._done_query = ""
        self._results = []

    @property
    def name(self):
        return f"Wochenkorb Suche ({self._zip_code})" + (f" {self._suffix}" if self._suffix else "")

    @property
    def unique_id(self):
        return f"wochenkorb_search_{self._zip_code}" + (f"_{self._suffix}" if self._suffix else "")

    @property
    def icon(self):
        return "mdi:magnify"

    @property
    def state(self):
        return len(self._results)

    @property
    def extra_state_attributes(self):
        return {
            "query": self._done_query,
            "results": self._results,
            "zip_code": self._zip_code,
            "search_entity": self._search_entity,
        }

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._search_entity], self._handle_change)
        )
        current = self.hass.states.get(self._search_entity)
        if current is not None:
            self.hass.async_create_task(self._search(current.state))

    @callback
    def _handle_change(self, event):
        new_state = event.data.get("new_state")
        self.hass.async_create_task(self._search(new_state.state if new_state else ""))

    async def _search(self, text):
        query = (text or "").strip()
        if query in ("unknown", "unavailable"):
            query = ""
        self._query = query
        results = await self._coordinator.async_search(query) if query else []
        if self._query != query:
            return  # inzwischen wurde etwas anderes gesucht
        self._results = results
        self._done_query = query
        self.async_write_ha_state()


class WochenkorbRecipesSensor(CoordinatorEntity, SensorEntity):
    """Rezepte aus Mealie, deren Hauptzutat gerade im Angebot ist."""

    _unrecorded_attributes = frozenset({"results", "plan", "kalender"})

    def __init__(self, coordinator, zip_code):
        super().__init__(coordinator)
        self._zip_code = zip_code

    @property
    def name(self):
        return f"Wochenkorb Rezepte ({self._zip_code})"

    @property
    def unique_id(self):
        return f"wochenkorb_recipes_{self._zip_code}"

    @property
    def icon(self):
        return "mdi:chef-hat"

    @property
    def state(self):
        return len(self.coordinator.suggestions)

    @property
    def extra_state_attributes(self):
        return {
            "results": self.coordinator.suggestions,
            "plan": self.coordinator.plan,
            "kalender": self.coordinator.calendar,
            "search_terms": self.coordinator.recipe_terms,
            "zip_code": self._zip_code,
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Knopf "andere Vorschläge": ein input_button, dessen Zustand sich bei jedem Druck ändert
        self.async_on_remove(
            async_track_state_change_event(self.hass, [RESHUFFLE_ENTITY], self._handle_reshuffle)
        )
        # Ändert sich die Einkaufsliste, die Markierung "schon auf der Liste" nachziehen
        if self.coordinator.shopping_list:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.coordinator.shopping_list], self._handle_list_change
                )
            )

    @callback
    def _handle_reshuffle(self, event):
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or new.state in ("unknown", "unavailable") or old.state == new.state:
            return
        self.hass.async_create_task(self.coordinator.async_reshuffle())

    @callback
    def _handle_list_change(self, event):
        self.hass.async_create_task(self.coordinator.async_refresh_suggestions())

    async def async_update(self):
        # homeassistant.update_entity: nur Essensplan, Einkaufsliste und Vorschläge, keine neuen Angebotsabfragen
        await self.coordinator.async_refresh_suggestions()


class PurchaseHistorySensor(SensorEntity):
    """Auswertung des Einkaufsverlaufs: Ausgaben je Monat, Grundartikel, Preisverlauf, Vorschläge."""

    _attr_should_poll = False
    _attr_icon = "mdi:receipt-text-clock"
    _attr_name = "Einkauf Verlauf"
    _attr_unique_id = "wochenkorb_einkauf_verlauf"
    _attr_native_unit_of_measurement = "Bons"
    _unrecorded_attributes = frozenset({"monate", "grundartikel", "vorschlaege", "artikel"})

    def __init__(self, history):
        self._history = history
        self._summary = pu.summary(history.rows)

    @property
    def native_value(self):
        return self._summary["bons"]

    @property
    def extra_state_attributes(self):
        return {k: v for k, v in self._summary.items() if k != "bons"}

    @callback
    def _refresh(self):
        self._summary = pu.summary(self._history.rows)
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, PURCHASES_SIGNAL, self._refresh))
