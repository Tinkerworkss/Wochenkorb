"""Einkaufsverlauf aus Kassenbons: Zeilen einlesen, zusammenführen, auswerten.

Reine Logik ohne Home Assistant, damit sie sich einzeln testen lässt.
"""
import re
from datetime import date, datetime
from statistics import median

# Eine Zeile je Bonposition, Felder mit Semikolon getrennt
FIELDS = ("bon", "markt", "bontext", "artikel", "gruppe", "anzahl", "menge", "einheit", "preis", "grundartikel")
UNITS = {"l": "l", "liter": "l", "kg": "kg", "stk": "stk", "stück": "stk", "st": "stk", "": ""}
# Grenzen, in denen ein gelernter Grundpreis glaubhaft ist (Euro je Liter oder Kilogramm)
PLAUSIBLE = {
    "Milch": (0.60, 2.50),
    "Joghurt": (1, 6),
    "Butter": (3, 14),
    "Softdrinks": (0.40, 2.50),
    "Wasser Black Forest": (0.20, 1.50),
}
_BON_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2})[:.](\d{2}))?$")


def _number(text, default=None):
    text = (text or "").strip().replace("€", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def parse(text):
    """Text in Zeilen wandeln. Liefert (Zeilen, Fehlermeldungen)."""
    rows, errors = [], []
    for number, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.lower().startswith("bon;"):
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 9:
            errors.append(f"Zeile {number}: zu wenige Felder")
            continue
        parts += [""] * (len(FIELDS) - len(parts))
        raw = dict(zip(FIELDS, parts))
        match = _BON_RE.match(raw["bon"])
        price = _number(raw["preis"])
        if not match or price is None or not raw["markt"] or not (raw["artikel"] or raw["bontext"]):
            errors.append(f"Zeile {number}: Datum, Markt, Artikel oder Preis fehlt")
            continue
        try:
            date.fromisoformat(match.group(1))
        except ValueError:
            errors.append(f"Zeile {number}: Datum ungültig")
            continue
        time = f"{int(match.group(2)):02d}:{match.group(3)}" if match.group(2) else "00:00"
        unit = UNITS.get(raw["einheit"].lower())
        rows.append(
            {
                "bon": f"{match.group(1)} {time}",
                "markt": raw["markt"][:40],
                "bontext": raw["bontext"][:80],
                "artikel": (raw["artikel"] or raw["bontext"])[:80],
                "gruppe": raw["gruppe"][:40] or "Sonstiges",
                "anzahl": _number(raw["anzahl"], 1) or 1,
                "menge": (_number(raw["menge"], 0) or 0) if unit else 0,
                "einheit": unit or "",
                "preis": round(price, 2),
                "grundartikel": raw["grundartikel"][:40],
            }
        )
    return rows, errors


def merge(existing, new):
    """Neue Zeilen übernehmen. Ein Bon (Zeitpunkt und Markt) wird immer als Ganzes ersetzt."""
    receipts = {(r["bon"], r["markt"].lower()) for r in new}
    kept = [r for r in existing if (r["bon"], r["markt"].lower()) not in receipts]
    return sorted(kept + list(new), key=lambda r: (r["bon"], r["markt"]))


def _norm(text):
    text = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _stem(word):
    """Mehrzahl-Endungen ab, damit Clementinen und Clementine zusammenfinden."""
    return re.sub(r"(en|e|n|s)$", "", word) if len(word) > 4 else word


def _matches(row, term):
    """Gehört die Bonposition zum Suchbegriff?

    Ein Wort des Artikelnamens muss der Begriff sein oder auf ihn enden: „Markenbutter“ gehört zu „Butter“,
    „Buttercroissant“ nicht. Begriffe aus mehreren Wörtern müssen als Ganzes im Namen stehen.
    """
    name = _norm(row.get("artikel"))
    if " " in term:
        return term in name
    stem = _stem(term)
    return any(word == term or _stem(word).endswith(stem) for word in name.split())


def usual_price(rows, term, unit):
    """Üblicher Grundpreis (Euro je kg oder l) für einen Suchbegriff aus den eigenen Kassenbons.

    Median der letzten fünf Käufe mit bekannter Menge. Trägt eine Bonposition den Begriff als Grundartikel,
    zählen nur solche Positionen (so bleibt Erdnussbutter bei „Butter“ außen vor).
    Liefert (Preis, Zahl der Käufe) oder None.
    """
    term = _norm(term)
    if len(term) < 3 or unit not in ("kg", "l"):
        return None
    priced = [r for r in rows if r.get("einheit") == unit and (r.get("menge") or 0) > 0 and (r.get("preis") or 0) > 0]
    staple = [r for r in priced if _norm(r.get("grundartikel")) == term]
    hits = staple or [r for r in priced if _matches(r, term)]
    if not hits:
        return None
    last = [r["preis"] / r["menge"] for r in sorted(hits, key=lambda r: r["bon"])[-5:]]
    return round(median(last), 2), len(hits)


def compare(rows, term, unit, base_price):
    """Angebot gegen den eigenen Bonpreis: {"preis", "kaeufe", "rabatt"} oder None.

    rabatt ist die Ersparnis in Prozent (negativ: das Angebot ist teurer als sonst). Liegen die Preise um mehr
    als das Drei- bis Vierfache auseinander, passen Angebot und Bonartikel nicht zusammen; dann gibt es keinen Vergleich.
    """
    if not base_price or base_price <= 0:
        return None
    usual = usual_price(rows, term, unit)
    if not usual or not 1 / 3 <= base_price / usual[0] <= 4:
        return None
    return {"preis": usual[0], "kaeufe": usual[1], "rabatt": round((1 - base_price / usual[0]) * 100)}


def _weeks(days):
    """Median des Abstands zwischen Kauftagen in ganzen Wochen (1 bis 8), ab drei Kauftagen."""
    if len(days) < 3:
        return None
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    return max(1, min(8, round(median(gaps) / 7)))


def summary(rows, today=None):
    """Auswertung für den Sensor."""
    today = today or date.today()
    receipts, months, staples, articles = set(), {}, {}, {}
    for r in rows:
        day = date.fromisoformat(r["bon"][:10])
        receipts.add((r["bon"], r["markt"].lower()))
        month = months.setdefault(r["bon"][:7], {"monat": r["bon"][:7], "summe": 0, "bons": set(), "maerkte": {}, "gruppen": {}})
        month["summe"] += r["preis"]
        month["bons"].add((r["bon"], r["markt"].lower()))
        month["maerkte"][r["markt"]] = month["maerkte"].get(r["markt"], 0) + r["preis"]
        month["gruppen"][r["gruppe"]] = month["gruppen"].get(r["gruppe"], 0) + r["preis"]
        if r["grundartikel"]:
            staple = staples.setdefault(r["grundartikel"], {"days": set(), "prices": [], "pieces": []})
            staple["days"].add(day)
            if r["menge"] > 0 and r["preis"] > 0:
                if r["einheit"] in ("l", "kg"):
                    staple["prices"].append((r["bon"], r["preis"] / r["menge"], r["einheit"]))
                elif r["einheit"] == "stk":
                    staple["pieces"].append((r["bon"], r["preis"] / r["menge"]))
        article = articles.setdefault(
            r["artikel"].lower(), {"artikel": r["artikel"], "gruppe": r["gruppe"], "days": set(), "prices": [], "staple": False}
        )
        article["days"].add(day)
        article["staple"] = article["staple"] or bool(r["grundartikel"])
        if r["preis"] > 0:
            article["prices"].append((r["bon"], r["markt"], round(r["preis"] / max(r["anzahl"], 1), 2)))

    month_list = []
    for key in sorted(months, reverse=True)[:13]:
        m = months[key]
        month_list.append(
            {
                "monat": key,
                "summe": round(m["summe"], 2),
                "bons": len(m["bons"]),
                "maerkte": {k: round(v, 2) for k, v in sorted(m["maerkte"].items(), key=lambda x: -x[1])},
                "gruppen": {k: round(v, 2) for k, v in sorted(m["gruppen"].items(), key=lambda x: -x[1])},
            }
        )

    staple_out = {}
    for name, s in staples.items():
        days = sorted(s["days"])
        entry = {"kaeufe": len(days), "letzter": days[-1].isoformat()}
        weeks = _weeks(days)
        if weeks:
            entry["wochen"] = weeks
        prices = sorted(s["prices"])[-5:]
        if len(prices) >= 2:
            price = round(median(p for _, p, _ in prices), 2)
            low, high = PLAUSIBLE.get(name, (0.05, 100))
            if low <= price <= high:
                entry["preis"] = price
                entry["einheit"] = prices[-1][2]
        # Stückware (Eier): Preis je Stück als eigenes Feld. „preis“ bleibt der Grundpreis je kg oder l,
        # weil die Grundartikel-Automation ihn mit dem Grundpreis der Angebote vergleicht.
        # Nur wenn der Grundartikel ausschließlich in Stück gekauft wird: Bei gemischten Einheiten
        # (Obst mal gewogen, mal einzeln) wäre ein Stückpreis eine Zahl ohne Aussage.
        pieces = [p for _, p in sorted(s["pieces"])[-5:]]
        if not s["prices"] and len(pieces) >= 2:
            entry["stueckpreis"] = round(median(pieces), 2)
        staple_out[name] = entry

    article_out, suggestions = [], []
    for a in articles.values():
        days = sorted(a["days"])
        prices = sorted(a["prices"])
        entry = {"artikel": a["artikel"], "gruppe": a["gruppe"], "kaeufe": len(days), "letzter": days[-1].isoformat()}
        weeks = _weeks(days)
        if weeks:
            entry["wochen"] = weeks
        if prices:
            values = [p for _, _, p in prices]
            entry.update(
                zuletzt=values[-1],
                ueblich=round(median(values[-8:]), 2),
                guenstig=min(values),
                verlauf=[[bon[:10], markt, p] for bon, markt, p in prices[-6:]],
            )
        if a["gruppe"].lower() != "pfand":
            article_out.append(entry)
        recent = [d for d in days if (today - d).days <= 180]
        if not a["staple"] and a["gruppe"].lower() != "pfand" and len(recent) >= 4 and (today - days[-1]).days <= 60:
            recent_weeks = _weeks(recent)
            if recent_weeks and recent_weeks <= 4:
                suggestions.append({"artikel": a["artikel"], "kaeufe": len(recent), "wochen": recent_weeks, "ueblich": entry.get("ueblich")})
    article_out.sort(key=lambda e: (-e["kaeufe"], e["artikel"].lower()))
    suggestions.sort(key=lambda e: (-e["kaeufe"], e["artikel"].lower()))
    return {
        "bons": len(receipts),
        "letzter_bon": max((r["bon"] for r in rows), default=None),
        "monate": month_list,
        "grundartikel": staple_out,
        "vorschlaege": suggestions[:12],
        "artikel": article_out[:80],
    }
