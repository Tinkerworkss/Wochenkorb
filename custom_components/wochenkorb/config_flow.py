import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .const import DOMAIN, CONF_PRODUCTS, CONF_ZIP, CONF_RETAILERS, CONF_RECIPES, CONF_SHOPPING_LIST, CONF_LISTS

# Auswahl-Eintrag zum Anlegen einer neuen Liste
NEW_LIST = "__new__"

# Vorschläge für den Händlerfilter; eigene Namen lassen sich dazutippen
KNOWN_RETAILERS = [
    "ALDI Nord", "ALDI SÜD", "EDEKA", "Globus", "Kaufland", "Lidl", "Marktkauf",
    "Netto Marken-Discount", "NORMA", "PENNY", "REWE", "tegut",
]


def parse_products(value):
    """Suchbegriffe aus der Auswahlliste (oder einem kommagetrennten Text) in die gespeicherte Form bringen."""
    items = value.split(",") if isinstance(value, str) else (value or [])
    result, seen = [], set()
    for item in items:
        # Auch in der Liste darf ein Eintrag mehrere kommagetrennte Begriffe enthalten (Einfügen einer alten Liste)
        for part in str(item).split(","):
            query = part.strip()
            if query and query.lower() not in seen:
                seen.add(query.lower())
                result.append({"name": query, "query": query})
    return result


def _product_names(products):
    if isinstance(products, list):
        return [p["query"] if isinstance(p, dict) else str(p) for p in products]
    return [p.strip() for p in (products or "").split(",") if p.strip()]


def _retailer_names(retailers):
    if isinstance(retailers, list):
        return [str(r).strip() for r in retailers if str(r).strip()]
    return [r.strip() for r in (retailers or "").split(",") if r.strip()]


def _schema(zip_code, products, retailers, recipes, shopping_list):
    """Formular: Suchbegriffe und Händler als Listen zum Hinzufügen und Entfernen einzelner Einträge."""
    retailer_options = sorted(set(KNOWN_RETAILERS) | set(retailers), key=str.lower)
    return vol.Schema({
        vol.Required(CONF_ZIP, default=zip_code): str,
        vol.Required(CONF_PRODUCTS, default=products): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=sorted(products, key=str.lower), multiple=True, custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_RETAILERS, default=retailers): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=retailer_options, multiple=True, custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_RECIPES, default=recipes): bool,
        vol.Optional(CONF_SHOPPING_LIST, description={"suggested_value": shopping_list or None}): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="todo")
        ),
    })


def _clean(user_input):
    """Eingaben in die gespeicherte Form bringen: Händler als kommagetrennter Text, Liste als Entitäts-ID oder leer."""
    data = dict(user_input)
    data[CONF_PRODUCTS] = parse_products(user_input.get(CONF_PRODUCTS))
    data[CONF_RETAILERS] = ", ".join(_retailer_names(user_input.get(CONF_RETAILERS)))
    data[CONF_SHOPPING_LIST] = user_input.get(CONF_SHOPPING_LIST) or ""
    return data


class WochenkorbConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wochenkorb."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = _clean(user_input)
            if not (data.get(CONF_ZIP) or "").strip():
                errors["base"] = "zip_required"
            elif not data[CONF_PRODUCTS]:
                errors["base"] = "products_required"
            else:
                return self.async_create_entry(title=f"Wochenkorb ({data[CONF_ZIP]})", data=data)

        current = user_input or {}
        schema = _schema(
            current.get(CONF_ZIP, ""),
            _product_names(current.get(CONF_PRODUCTS)),
            _retailer_names(current.get(CONF_RETAILERS)),
            bool(current.get(CONF_RECIPES, False)),
            current.get(CONF_SHOPPING_LIST, ""),
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return WochenkorbOptionsFlowHandler()


class WochenkorbOptionsFlowHandler(config_entries.OptionsFlow):
    """Einstellungen: allgemeine Angaben und freie Listen."""

    def __init__(self):
        self._list_index = None

    def _current(self):
        # Optionen haben Vorrang vor den Daten der Ersteinrichtung
        return {**self.config_entry.data, **self.config_entry.options}

    def _lists(self):
        return [dict(entry) for entry in self._current().get(CONF_LISTS) or []]

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["general", "lists"])

    async def async_step_general(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = _clean(user_input)
            if not (data.get(CONF_ZIP) or "").strip():
                errors["base"] = "zip_required"
            elif not data[CONF_PRODUCTS]:
                errors["base"] = "products_required"
            else:
                # Die freien Listen werden in einem eigenen Schritt gepflegt und bleiben hier erhalten
                data[CONF_LISTS] = self._lists()
                return self.async_create_entry(title="", data=data)

        current = {**self._current(), **(user_input or {})}
        schema = _schema(
            current.get(CONF_ZIP, ""),
            _product_names(current.get(CONF_PRODUCTS)),
            _retailer_names(current.get(CONF_RETAILERS)),
            bool(current.get(CONF_RECIPES, False)),
            current.get(CONF_SHOPPING_LIST, ""),
        )
        return self.async_show_form(step_id="general", data_schema=schema, errors=errors)

    async def async_step_lists(self, user_input=None):
        """Liste auswählen oder eine neue anlegen."""
        lists = self._lists()
        if user_input is not None:
            choice = user_input["list"]
            self._list_index = None if choice == NEW_LIST else int(choice)
            return await self.async_step_list()
        if not lists:
            self._list_index = None
            return await self.async_step_list()
        options = [selector.SelectOptionDict(value=str(i), label=entry["name"]) for i, entry in enumerate(lists)]
        options.append(selector.SelectOptionDict(value=NEW_LIST, label="＋ Neue Liste"))
        schema = vol.Schema({
            vol.Required("list", default=NEW_LIST): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.LIST)
            ),
        })
        return self.async_show_form(step_id="lists", data_schema=schema)

    async def async_step_list(self, user_input=None):
        """Eine Liste bearbeiten: Name, Symbol, Begriffe, Ausschlusswörter; oder löschen."""
        errors = {}
        lists = self._lists()
        entry = lists[self._list_index] if self._list_index is not None else {}
        if user_input is not None:
            name = (user_input.get("name") or "").strip()
            terms = [t["query"] for t in parse_products(user_input.get("terms"))]
            others = [e["name"].lower() for i, e in enumerate(lists) if i != self._list_index]
            if user_input.get("delete") and self._list_index is not None:
                lists.pop(self._list_index)
            elif not name:
                errors["base"] = "list_name_required"
            elif name.lower() in others:
                errors["base"] = "list_name_exists"
            elif not terms:
                errors["base"] = "list_terms_required"
            else:
                new = {
                    "name": name,
                    "icon": user_input.get("icon") or "",
                    "terms": terms,
                    "exclude": [t["query"].lower() for t in parse_products(user_input.get("exclude"))],
                }
                if self._list_index is None:
                    lists.append(new)
                else:
                    lists[self._list_index] = new
            if not errors:
                data = dict(self.config_entry.options) or {
                    k: v for k, v in self.config_entry.data.items()
                }
                data[CONF_LISTS] = lists
                return self.async_create_entry(title="", data=data)
            entry = {"name": name, "icon": user_input.get("icon"), "terms": terms, "exclude": user_input.get("exclude") or []}

        terms = list(entry.get("terms") or [])
        exclude = list(entry.get("exclude") or [])
        fields = {
            vol.Required("name", default=entry.get("name", "")): str,
            vol.Optional("icon", description={"suggested_value": entry.get("icon") or None}): selector.IconSelector(),
            vol.Required("terms", default=terms): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sorted(terms, key=str.lower), multiple=True, custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional("exclude", default=exclude): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sorted(exclude, key=str.lower), multiple=True, custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
        if self._list_index is not None:
            fields[vol.Optional("delete", default=False)] = bool
        return self.async_show_form(step_id="list", data_schema=vol.Schema(fields), errors=errors)
