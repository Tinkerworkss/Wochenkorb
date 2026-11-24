from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from . import purchases as pu
from .const import DOMAIN, PURCHASES_SIGNAL

SERVICE_ADD_PURCHASES = "add_purchases"


class PurchaseHistory:
    """Einkaufsverlauf aus Kassenbons, gespeichert in .storage/wochenkorb_einkaeufe."""

    def __init__(self, hass):
        self.hass = hass
        self._store = Store(hass, 1, "wochenkorb_einkaeufe")
        # Verlauf aus der Zeit, als die Integration technisch noch "marktguru" hieß
        self._old_store = Store(hass, 1, "marktguru_einkaeufe")
        self.rows = []

    async def async_load(self):
        data = await self._store.async_load()
        if data is None:
            data = await self._old_store.async_load()
            if data:
                await self._store.async_save(data)
        self.rows = (data or {}).get("rows", [])

    async def async_add(self, text):
        rows, errors = pu.parse(text)
        if rows:
            self.rows = pu.merge(self.rows, rows)
            await self._store.async_save({"rows": self.rows})
            async_dispatcher_send(self.hass, PURCHASES_SIGNAL)
        return {"gespeichert": len(rows), "bons": len({(r["bon"], r["markt"]) for r in rows}), "fehler": errors[:20]}

async def async_setup(hass: HomeAssistant, config: ConfigType):
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    if "purchases" not in hass.data.setdefault(DOMAIN, {}):
        history = PurchaseHistory(hass)
        await history.async_load()
        hass.data[DOMAIN]["purchases"] = history

        async def add_purchases(call: ServiceCall):
            return await history.async_add(call.data.get("rows", ""))

        hass.services.async_register(
            DOMAIN, SERVICE_ADD_PURCHASES, add_purchases, supports_response=SupportsResponse.OPTIONAL
        )
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    # Geänderte Optionen (Suchbegriffe, PLZ, Händler) sofort übernehmen
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True

async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])
