DOMAIN = "wochenkorb"
CONF_PRODUCTS = "products"
CONF_ZIP = "zip_code"
CONF_RETAILERS = "retailers"
# Rezeptvorschläge aus Mealie (braucht die Mealie-Integration)
CONF_RECIPES = "mealie_recipes"
# To-do-Liste (Einkaufsliste), gegen die die Rezeptvorschläge geprüft werden, z. B. todo.einkaufsliste
CONF_SHOPPING_LIST = "shopping_list"
API_URL = "https://api.marktguru.de/api/v1/offers/search"
HEADERS = {
    "x-clientkey": "WU/RH+PMGDi+gkZer3WbMelt6zcYHSTytNB7VpTia90=",
    "x-apikey": "8Kk+pmbf7TgJ9nVj2cXeA7P5zBGv8iuutVVMRfOfvNE=",
}
# Text-Helfer, dessen Inhalt der Such-Sensor als Suchbegriff verwendet
SEARCH_ENTITY = "input_text.angebote_suche"
# Knopf-Helfer: jeder Druck zeigt andere Rezeptvorschläge (Eingeplantes bleibt stehen)
RESHUFFLE_ENTITY = "input_button.rezepte_neu_mischen"
# Signal: der Einkaufsverlauf hat sich geändert
PURCHASES_SIGNAL = "wochenkorb_purchases_updated"
# Freie Listen: benannte Gruppen von Suchbegriffen (z. B. "Obst"), jede wird ein eigener Reiter der Angebotskarte
CONF_LISTS = "lists"
# Auswahl-Helfer der Angebotskarte; ihre Optionen (Listen und Händler) hält die Integration aktuell
TAB_ENTITY_PREFIX = "input_select.angebote_reiter"
# Text-Helfer der Rezeptsuche in der Rezeptkarte
RECIPE_SEARCH_ENTITY = "input_text.rezept_suche"
# Rezeptvorschläge: wie viele neue Vorschläge gezeigt werden, wie viele davon ohne Fleisch und Fisch sein müssen
# und ob diese Plätze vegetarisch oder vegan sein sollen
CONF_SUGGESTIONS = "suggestions"
CONF_MEATLESS = "meatless"
CONF_MEATLESS_KIND = "meatless_kind"
DEFAULT_SUGGESTIONS = 5
DEFAULT_MEATLESS = 1
# Rezepte mit diesen Wörtern in Name oder Zutaten werden nie vorgeschlagen
CONF_EXCLUDE = "exclude_ingredients"
# So viele Tage bleibt ein gekochtes Rezept aus den Vorschlägen (0: keine Sperre)
CONF_REPEAT_DAYS = "repeat_days"
DEFAULT_REPEAT_DAYS = 14
# Wie stark Fleisch und Fisch ohne Streichpreis zählen: "off", "normal" oder "strong"
CONF_MEAT_BONUS = "meat_bonus"
MEAT_BONUS_LEVELS = {"off": 0, "normal": 30, "strong": 50}
# Längste Zubereitungszeit in Minuten (0: keine Grenze); Rezepte ohne Zeitangabe bleiben drin
CONF_MAX_TIME = "max_time"
# Schnellauswahl im Fenster für einen eigenen Eintrag ohne Rezept
CONF_QUICK = "quick_entries"
DEFAULT_QUICK = ["Auswärts essen", "Bestellen", "Reste", "Brotzeit"]
