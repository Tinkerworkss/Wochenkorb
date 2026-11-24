import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .const import DOMAIN, CONF_PRODUCTS, CONF_ZIP, CONF_RETAILERS, CONF_RECIPES, CONF_SHOPPING_LIST

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


class MarktguruConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Marktguru."""

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
                return self.async_create_entry(title=f"Marktguru ({data[CONF_ZIP]})", data=data)

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
        return MarktguruOptionsFlowHandler()


class MarktguruOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Marktguru."""

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = _clean(user_input)
            if not (data.get(CONF_ZIP) or "").strip():
                errors["base"] = "zip_required"
            elif not data[CONF_PRODUCTS]:
                errors["base"] = "products_required"
            else:
                return self.async_create_entry(title="", data=data)

        # Optionen haben Vorrang vor den Daten der Ersteinrichtung
        current = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        schema = _schema(
            current.get(CONF_ZIP, ""),
            _product_names(current.get(CONF_PRODUCTS)),
            _retailer_names(current.get(CONF_RETAILERS)),
            bool(current.get(CONF_RECIPES, False)),
            current.get(CONF_SHOPPING_LIST, ""),
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
