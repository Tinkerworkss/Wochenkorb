DOMAIN = "marktguru"
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
