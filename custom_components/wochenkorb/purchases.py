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
            staple = staples.setdefault(r["grundartikel"], {"days": set(), "prices": []})
            staple["days"].add(day)
            if r["menge"] > 0 and r["einheit"] in ("l", "kg") and r["preis"] > 0:
                staple["prices"].append((r["bon"], r["preis"] / r["menge"]))
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
        last = [p for _, p in sorted(s["prices"])[-5:]]
        if len(last) >= 2:
            price = round(median(last), 2)
            low, high = PLAUSIBLE.get(name, (0.05, 100))
            if low <= price <= high:
                entry["preis"] = price
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
