"""Rezeptvorschläge: Hauptzutaten aus Mealie-Rezepten mit Prospektangeboten abgleichen.

Reine Funktionen ohne Home-Assistant-Abhängigkeit, damit sie einzeln testbar sind.
"""
import re
from datetime import datetime, timezone

# Höchstzahl zusätzlicher Suchbegriffe, die für Rezepte geladen werden
MAX_RECIPE_TERMS = 20
# Höchstens so viele Vorschläge mit derselben Hauptzutat (wird nur gelockert, wenn sonst Plätze frei blieben)
MAX_PER_INGREDIENT = 1
# Bonus in Rabattpunkten für Rezepte, die lange nicht gekocht wurden
FRESH_BONUS = 5

# Hauptzutaten: (Anzeigename, Suchbegriff, Muster Zutat, Ausschluss Zutat, Muster Angebot, Ausschluss Angebot)
# Gewürze, Öle, Grundzutaten und Konserven zählen bewusst nicht dazu.
_FERTIG = r"vegan|vegetar|paniert|cordon|nugget|burger|salat|aufstrich|aufschnitt|suppe|pizza|eintopf|fertig"
_RAW = [
    ("Hähnchenbrust", "Hähnchenbrust", r"hähnchenbrust|hähnchen(innen)?filet|hühnerbrust|hähnchengeschnetzeltes", None,
     r"hähnchen.{0,3}(brust|innenfilet|filet|geschnetzeltes|minutenschnitzel)", _FERTIG + r"|wurst|döner|crispy|gyros"),
    ("Hähnchenschenkel", "Hähnchenschenkel", r"hähnchen.{0,3}(schenkel|keule|unterkeule)", None,
     r"hähnchen.{0,3}(schenkel|keule|unterkeule)", _FERTIG),
    ("Hackfleisch", "Hackfleisch", r"hackfleisch|rinderhack|gehacktes|\bhack\b", r"vegan|vegetar",
     r"hackfleisch|rinderhack|gehacktes", _FERTIG + r"|bällchen|frikadelle|köttbullar|cevapcici"),
    ("Rindfleisch", "Rindersteak", r"rind(er)?.{0,2}(steak|hüft|filet|gulasch|roulade|fleisch|streifen|geschnetzeltes)|rumpsteak|hüftsteak|entrec",
     r"brühe|fond|hack", r"rind(er)?.{0,2}(steak|hüft|filet|gulasch|roulade|braten|geschnetzeltes)|rumpsteak|hüftsteak|entrec",
     _FERTIG + r"|hack|brühe|fond"),
    ("Schweinefleisch", "Schweinefilet", r"schweine.{0,2}(filet|lachs|schnitzel|medaillon|nacken|rücken|geschnetzeltes|gulasch|steak)",
     None, r"schweine.{0,2}(filet|lachs|schnitzel|medaillon|nacken|rücken|geschnetzeltes|gulasch|steak)", _FERTIG),
    ("Pute", "Putenbrust", r"puten.{0,2}(brust|schnitzel|geschnetzeltes|medaillon|filet|steak)", None,
     r"puten.{0,2}(brust|schnitzel|geschnetzeltes|medaillon|filet|steak)", _FERTIG + r"|wurst"),
    ("Lachs", "Lachsfilet", r"(?<!schweine)lachs", r"räucher|geräuchert|schinken",
     r"(?<!schweine)lachs", _FERTIG + r"|räucher|geräuchert|schinken|fleisch|stremel|ersatz"),
    ("Garnelen", "Garnelen", r"garnelen|shrimps|gambas", None, r"garnelen|shrimps|gambas", _FERTIG),
    ("Fischfilet", "Fischfilet", r"kabeljau|seelachs|fischfilet|zander|dorade|forelle|rotbarsch", r"räucher|geräuchert",
     r"kabeljau|seelachs|fischfilet|zander|dorade|forelle|rotbarsch", _FERTIG + r"|stäbchen|schlemmer|räucher|geräuchert"),
    ("Bacon", "Bacon", r"bacon|frühstücksspeck|speckwürfel|schinkenwürfel|katenschinken", None,
     r"bacon|frühstücksspeck|speckwürfel|schinkenwürfel|katenschinken", _FERTIG + r"|chips|geschmack"),
    ("Feta", "Feta", r"\bfeta|hirtenkäse", None, r"\bfeta|hirtenkäse", _FERTIG + r"|creme"),
    ("Mozzarella", "Mozzarella", r"mozzarella", None, r"mozzarella", _FERTIG + r"|sticks"),
    ("Gouda", "Gouda", r"gouda", None, r"gouda", _FERTIG),
    ("Cheddar", "Cheddar", r"cheddar", None, r"cheddar", _FERTIG + r"|sauce|chips"),
    ("Hartkäse", "Parmesan", r"parmesan|hartkäse|grana padano|parmigiano", None,
     r"parmesan|grana padano|parmigiano|italienischer hartkäse", _FERTIG + r"|chips|sauce"),
    ("Kartoffeln", "Kartoffeln", r"(?<!süß)kartoffeln?\b|drillinge", r"stärke|mehl|püreepulver",
     r"speisekartoffel|(?<!süß)kartoffeln\b|drillinge", _FERTIG + r"|chips|püree|kloß|klöße|knödel|puffer|pommes|gratin|rösti|taschen|wedges|kroketten"),
    ("Süßkartoffel", "Süßkartoffel", r"süßkartoffel", None, r"süßkartoffel", _FERTIG + r"|chips|pommes"),
    ("Paprika", "Paprika", r"paprika(schoten?)?\b", r"pulver|edelsüß|geräuchert|rosenscharf|gewürz|paprika,? ?(scharf|mild)|flocken|mark",
     r"paprika", _FERTIG + r"|pulver|chips|gewürz|sauce|wurst|lyoner|frischkäse|hähnchen|edelsüß|gefüllt|creme|mark|eingelegt|geröstet"),
    ("Zucchini", "Zucchini", r"zucchini", None, r"zucchini", _FERTIG),
    ("Champignons", "Champignons", r"champignons?", None, r"champignons?", _FERTIG + r"|dose|glas|creme|sauce"),
    ("Brokkoli", "Brokkoli", r"brokkoli|broccoli", None, r"brokkoli|broccoli", _FERTIG),
    ("Möhren", "Möhren", r"möhren?\b|karotten?\b", r"saft", r"möhren|karotten", _FERTIG + r"|saft|glas|dose|erbsen"),
    ("Tomaten", "Tomaten", r"tomaten?\b", r"mark|dose|passiert|gehackt|stückig|getrocknet|ketchup|sauce|püree|geschält",
     r"tomaten\b|rispentomate|cherrytomate|cocktailtomate", _FERTIG + r"|mark|passiert|ketchup|sauce|dose|gehackt|getrocknet|saft|pesto|hering|fisch|creme|stückig|geschält"),
    ("Spinat", "Spinat", r"spinat", None, r"spinat", _FERTIG + r"|lasagne|ravioli|tortellini|tasche|strudel"),
    ("Blumenkohl", "Blumenkohl", r"blumenkohl", None, r"blumenkohl", _FERTIG),
    ("Kürbis", "Kürbis", r"kürbis", r"kern|öl", r"kürbis", _FERTIG + r"|kern|öl"),
    ("Lauch", "Lauch", r"\b(lauch|porree)\b", None, r"\b(lauch|porree)\b", _FERTIG),
    ("Aubergine", "Aubergine", r"auberginen?", None, r"auberginen?", _FERTIG + r"|creme|gegrillt"),
    ("Gurke", "Salatgurke", r"\b(salat)?gurken?\b", r"gewürz|cornichon|essig", r"salatgurke|\bgurken?\b", _FERTIG + r"|gewürz|cornichon|sticks|glas|relish"),
    ("Avocado", "Avocado", r"avocados?", None, r"avocados?", _FERTIG + r"|creme|öl|guacamole"),
    ("Grüne Bohnen", "Bohnen", r"grüne bohnen|buschbohnen|prinzessbohnen|brechbohnen", None,
     r"grüne bohnen|buschbohnen|prinzessbohnen|brechbohnen", _FERTIG + r"|glas|dose"),
    ("Rosenkohl", "Rosenkohl", r"rosenkohl", None, r"rosenkohl", _FERTIG),
]


class Ingredient:
    def __init__(self, label, query, ing, ing_not, offer, offer_not):
        self.label = label
        self.query = query
        self._ing = re.compile(ing)
        self._ing_not = re.compile(ing_not) if ing_not else None
        self._offer = re.compile(offer)
        self._offer_not = re.compile(offer_not) if offer_not else None

    def in_ingredient(self, text):
        return bool(self._ing.search(text)) and not (self._ing_not and self._ing_not.search(text))

    def in_offer(self, text):
        return bool(self._offer.search(text)) and not (self._offer_not and self._offer_not.search(text))


INGREDIENTS = [Ingredient(*row) for row in _RAW]
# Fleisch und Fisch prägen ein Rezept stärker als Käse, Käse stärker als Gemüse:
# ein Rabatt auf Tomaten soll ein Hähnchengericht nicht vor ein Hackfleischgericht mit ähnlichem Rabatt schieben.
_MEAT = {"Hähnchenbrust", "Hähnchenschenkel", "Hackfleisch", "Rindfleisch", "Schweinefleisch", "Pute", "Lachs", "Garnelen", "Fischfilet", "Bacon"}
_CHEESE = {"Feta", "Mozzarella", "Gouda", "Cheddar", "Hartkäse"}
WEIGHT = {ing.label: 1.0 if ing.label in _MEAT else 0.8 if ing.label in _CHEESE else 0.6 for ing in INGREDIENTS}
BY_LABEL = {ing.label: ing for ing in INGREDIENTS}


# Fleisch und Fisch in Zutatentexten, auch außerhalb der Hauptzutaten-Tabelle (für "vegetarisch")
_MEAT_RE = re.compile(
    r"hähnchen|huhn|hühner|pute|rind|schwein|hack|speck|bacon|schinken|salami|chorizo|wurst|würst|kasseler|"
    r"\bente\b|lamm|kalb|gulasch|\bmett|köttbullar|frikadelle|nduja|leberkäse|steak|schnitzel|"
    r"lachs|fisch|garnele|shrimp|gambas|thunfisch|sardelle|kabeljau|seelachs|zander|forelle"
)
_NOT_MEAT_RE = re.compile(r"brühe|fond|vegan|vegetar|planted|ersatz|fleischtomate")


def is_vegetarian(ingredient_texts):
    """Kein Fleisch und kein Fisch in den Zutaten (Brühe zählt nicht)."""
    for text in ingredient_texts:
        text = clean(text)
        if _MEAT_RE.search(text) and not _NOT_MEAT_RE.search(text):
            return False
    return True


def clean(text):
    """Kleinschreibung, ohne weiche Trennstriche (stecken in importierten Rezepten)."""
    return (text or "").replace("­", "").lower()


def ingredient_text(item):
    """Lesbarer Text einer Mealie-Zutat."""
    food = (item.get("food") or {}).get("name") if isinstance(item.get("food"), dict) else None
    return item.get("display") or " ".join(str(x) for x in (food, item.get("note")) if x) or ""


def main_ingredients(ingredient_texts):
    """Anzeigenamen der Hauptzutaten, die in den Zutatentexten eines Rezepts vorkommen."""
    found = []
    for text in ingredient_texts:
        text = clean(text)
        for ing in INGREDIENTS:
            if ing.label not in found and ing.in_ingredient(text):
                found.append(ing.label)
    return found


def recipe_terms(recipes, user_queries, limit=MAX_RECIPE_TERMS):
    """Suchbegriffe für die häufigsten Hauptzutaten, die die eigenen Suchbegriffe nicht schon abdecken."""
    user = [clean(q) for q in user_queries]
    counts = {}
    for recipe in recipes.values():
        for label in recipe.get("mains", []):
            counts[label] = counts.get(label, 0) + 1
    terms = []
    for label, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        query = BY_LABEL[label].query
        low = clean(query)
        if any(low == q or low in q or q in low for q in user):
            continue
        terms.append(query)
        if len(terms) >= limit:
            break
    return terms


def _discount(offer):
    price, old = offer.get("price"), offer.get("oldPrice")
    if isinstance(price, (int, float)) and isinstance(old, (int, float)) and old > price > 0:
        return round((old - price) / old * 100)
    return 0


def _valid(offer, now):
    dates = (offer.get("validityDates") or [{}])[0]
    end = dates.get("to")
    if not end:
        return True
    try:
        return datetime.fromisoformat(end.replace("Z", "+00:00")) >= now
    except (ValueError, TypeError):
        return True


def best_offers(offers, now=None):
    """Bestes laufendes Angebot je Hauptzutat: höchster Rabatt, sonst niedrigster Preis."""
    now = now or datetime.now(timezone.utc)
    best = {}
    for offer in offers:
        if not _valid(offer, now):
            continue
        text = clean(
            " ".join(
                str(x)
                for x in ((offer.get("brand") or {}).get("name"), (offer.get("product") or {}).get("name"))
                if x
            )
        )
        price = offer.get("price")
        if not text or not isinstance(price, (int, float)):
            continue
        rank = (-_discount(offer), price)
        for ing in INGREDIENTS:
            if ing.in_offer(text) and (ing.label not in best or rank < best[ing.label][0]):
                best[ing.label] = (rank, offer)
    return {label: (offer, -rank[0]) for label, (rank, offer) in best.items()}


def offer_for(recipe, best):
    """Bestes Angebot zu den Hauptzutaten eines Rezepts: (Zutat, Angebot, Rabatt, weitere Zutaten) oder None."""
    hits = [label for label in recipe.get("mains", []) if label in best]
    if not hits:
        return None
    hits.sort(key=lambda label: (-best[label][1] * WEIGHT[label], -WEIGHT[label], best[label][0].get("price") or 0))
    top = hits[0]
    return top, best[top][0], best[top][1], hits[1:]


def search(recipes, text, limit=10):
    """Rezepte, in deren Name oder Zutaten alle Suchwörter vorkommen; Treffer im Namen zuerst."""
    words = [w for w in clean(text or "").split() if w]
    if not words:
        return []
    found = []
    for slug, recipe in recipes.items():
        name = clean(recipe.get("name") or "")
        ingredients = " ".join(clean(i) for i in recipe.get("ingredients") or [])
        if all(w in name or w in ingredients for w in words):
            found.append((-sum(w in name for w in words), name, slug))
    return [slug for _, _, slug in sorted(found)[:limit]]


def _freshness(recipe, now):
    """Bonus für lange nicht Gekochtes; ohne Datum gilt ein Rezept als lange her."""
    made = recipe.get("last_made")
    if not made:
        return FRESH_BONUS
    try:
        days = (now - datetime.fromisoformat(str(made).replace("Z", "+00:00")).replace(tzinfo=now.tzinfo)).days
    except (ValueError, TypeError):
        return FRESH_BONUS
    return FRESH_BONUS if days > 60 else (FRESH_BONUS * 0.6 if days > 30 else 0)


def suggest(recipes, offers, blocked_slugs=(), limit=5, now=None, pinned=(), skipped=()):
    """Rezepte, deren Hauptzutat im Angebot ist.

    - pinned: Rezepte, die immer zuerst stehen, solange eine Hauptzutat im Angebot ist (schon Eingeplantes)
    - skipped: Rezepte, die gerade nicht gezeigt werden sollen ("andere Vorschläge")
    - von jeder Hauptzutat zunächst nur ein Rezept, mindestens ein vegetarisches, lange nicht Gekochtes bevorzugt

    Liefert Tupel (slug, Anzeigename der Zutat, Angebot, Rabatt in Prozent, weitere Zutaten im Angebot).
    """
    now = now or datetime.now(timezone.utc)
    best = best_offers(offers, now)
    blocked, skipped = set(blocked_slugs), set(skipped)
    rows = {}
    for slug, recipe in recipes.items():
        hits = [label for label in recipe.get("mains", []) if label in best]
        if not hits:
            continue
        # Reihenfolge: gewichteter Rabatt, bei Gleichstand die prägendere Zutat
        hits.sort(key=lambda label: (-best[label][1] * WEIGHT[label], -WEIGHT[label], best[label][0].get("price") or 0))
        top = hits[0]
        score = best[top][1] * WEIGHT[top] + _freshness(recipe, now)
        rows[slug] = ((-score, -WEIGHT[top], -len(hits), recipe.get("name") or slug), top, hits[1:])

    def row(slug):
        _, top, others = rows[slug]
        return (slug, top, best[top][0], best[top][1], others)

    result = [slug for slug in pinned if slug in rows][:limit]
    free = limit - len(result)
    candidates = sorted(
        (slug for slug in rows if slug not in blocked and slug not in skipped and slug not in result),
        key=lambda slug: rows[slug][0],
    )
    picked, per_ingredient = [], {}
    for cap in (MAX_PER_INGREDIENT, MAX_PER_INGREDIENT + 1, limit):
        for slug in candidates:
            if len(picked) >= free:
                break
            top = rows[slug][1]
            if slug in picked or per_ingredient.get(top, 0) >= cap:
                continue
            per_ingredient[top] = per_ingredient.get(top, 0) + 1
            picked.append(slug)
    # Mindestens ein vegetarischer Vorschlag, wenn es einen passenden gibt
    if picked and not any(recipes[slug].get("vegetarian") for slug in result + picked):
        veggie = next((slug for slug in candidates if recipes[slug].get("vegetarian")), None)
        if veggie:
            picked[-1] = veggie
    return [row(slug) for slug in result + picked]
