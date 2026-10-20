#!/usr/bin/env python3
"""Free public FX news/event surveillance, every OANDA hourly run.

NO language model, paid news API, trade execution, credentials or prediction.
Headlines are source claims, not independently verified macro facts.
A headline/impact tag is a REASSESSMENT risk flag, never a directional trade signal.
"""
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse

OUT = Path("data/news_latest.json")
STATE = Path("data/news_state.json")
CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
SOURCES = {
    "Fed policy": ("USD", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    "Fed speeches": ("USD", "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml"),
    "ECB press": ("EUR", "https://www.ecb.europa.eu/rss/press.html"),
    "BoE news": ("GBP", "https://www.bankofengland.co.uk/rss/news"),
    "BoE speeches": ("GBP", "https://www.bankofengland.co.uk/rss/speeches"),
    "BoJ updates": ("JPY", "https://www.boj.or.jp/en/rss/whatsnew.xml"),
    "SNB monetary policy": ("CHF", "https://www.snb.ch/public/rss/en/mopo"),
    "BoC press": ("CAD", "https://www.bankofcanada.ca/content_type/press-releases/feed/"),
    "RBA press": ("AUD", "https://www.rba.gov.au/rss/rss-cb-media-releases.xml"),
    "BLS inflation": ("USD", "https://www.bls.gov/feed/cpi.rss"),
    "BLS employment": ("USD", "https://www.bls.gov/feed/empsit.rss"),
    # Third-party aggregation supplements primary-source releases, not a validated wire feed.
    "Global markets aggregation": ("GLOBAL",
        "https://news.google.com/rss/search?q=forex%20OR%20%22central%20bank%22%20OR%20%22Treasury%20yields%22%20OR%20%22currency%20markets%22%20when%3A1d&hl=en-GB&gl=GB&ceid=GB%3Aen"),
}
PAIR_CURRENCIES = {"EUR_USD": ("EUR", "USD"), "GBP_USD": ("GBP", "USD"),
                   "USD_JPY": ("USD", "JPY"), "USD_CAD": ("USD", "CAD"),
                   "EUR_GBP": ("EUR", "GBP"), "AUD_USD": ("AUD", "USD"),
                   "USD_CHF": ("USD", "CHF"), "EUR_JPY": ("EUR", "JPY")}
WORDS = {
    "USD": ("federal reserve", "fed ", "fomc", "warsh", "treasury yield", "nonfarm", "us dollar", "u.s. dollar", "us inflation"),
    "EUR": ("ecb", "european central bank", "eurozone", "euro area", " euro ", "germany", "france"),
    "GBP": ("bank of england", "boe", "sterling", "pound", "britain", " uk ", "united kingdom"),
    "JPY": ("bank of japan", "boj", " japanese yen", "japan"),
    "CAD": ("bank of canada", "boc", "loonie", "canadian", "canada"),
    "AUD": ("rba", "reserve bank of australia", "australian", "aussie"),
    "CHF": ("snb", "swiss national bank", "swiss franc", "switzerland"),
}
MATERIAL = ("rate decision", "monetary policy", "interest rate", "inflation", " cpi", "payroll",
            "jobs report", "employment", "unemployment", "central bank", "emergency",
            "sanction", "missile", "attack", "war ", "ceasefire", "tariff", "trade war",
            "yield", "bond market", "intervention", "gdp", "pmi", "opec",
            "oil price", "energy", "bank failure", "resign", "budget",
            "fomc", "economic outlook", "nonfarm", "press conference",
            "geopolitical", "currency", "forex", "risk-off")
CRITICAL = ("rate decision", "monetary policy", "emergency", "attack", "missile",
            "war ", "sanction", "intervention", "inflation", " cpi", "payroll",
            "nonfarm", "fomc", "opec", "ceasefire", "tariff")
CURRENCIES = frozenset(x for group in PAIR_CURRENCIES.values() for x in group)


def timestamp():
    return datetime.now(timezone.utc)


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_date(text):
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        try:
            parsed = parsedate_to_datetime(text)
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
        except (ValueError, TypeError, IndexError):
            return None


def request(url, max_bytes=1250000):
    req = Request(url, headers={"User-Agent": "PersonalResearchRSS/1.0 (+GitHub Actions; read-only)",
                                "Accept": "application/rss+xml, application/atom+xml, application/json, application/xml;q=0.9, */*;q=0.5"}, method="GET")
    with urlopen(req, timeout=10) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError("feed too large")
    return payload


def _text(element, tags):
    for tag in tags:
        node = element.find(tag)
        if node is not None and node.text:
            return node.text.strip()
    return ""


def parse_rss(xml_bytes, source, assigned):
    root = ET.fromstring(xml_bytes)
    atom = "{http://www.w3.org/2005/Atom}"
    rdf = "{http://purl.org/rss/1.0/}"
    entries = (root.findall(".//item") or root.findall(".//" + atom + "entry") or
               root.findall(".//" + rdf + "item"))
    result = []
    for entry in entries[:40]:
        title = _text(entry, ("title", atom + "title", rdf + "title"))
        date = parse_date(_text(entry, ("pubDate", atom + "published", atom + "updated",
                                      "{http://purl.org/dc/elements/1.1/}date", "date")))
        url = _text(entry, ("link", "guid", atom + "id", rdf + "link"))
        if not url:
            link_node = entry.find(atom + "link")
            url = link_node.attrib.get("href", "") if link_node is not None else ""
        if not title or not date:
            continue
        if not urlparse(url).scheme.startswith("http"):
            continue
        result.append({"source": source, "currency_source": assigned, "published_at": iso(date),
                       "title": title[:220], "url": url[:600]})
    return result


def classify(item):
    text = " " + item["title"].lower() + " "
    # Official bank/agency feed identifies the jurisdiction, but non-policy press
    # headlines are not automatically considered market-moving.
    related = set()
    for currency, words in WORDS.items():
        if any(word in text for word in words):
            related.add(currency)
    if item["currency_source"] not in ("GLOBAL", ""):
        related.add(item["currency_source"])
    is_material = any(term in text for term in MATERIAL)
    if not is_material:
        return None
    is_global = item["currency_source"] == "GLOBAL" and any(term in text for term in (
        "geopolitical", "oil price", "war ", "attack", "missile", "risk-off",
        "global markets", "treasury yield", "trade war", "opec", "tariff"))
    if is_global:
        related |= CURRENCIES
    if not related:
        return None
    return dict(item, severity="HIGH" if any(term in text for term in CRITICAL) else "REVIEW",
                currencies=sorted(related), global_risk=is_global)


def calendar_events(payload, now):
    raw = json.loads(payload)
    if not isinstance(raw, list):
        raise ValueError("calendar must be a list")
    events = []
    for item in raw[:400]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("country", "")).strip().upper()
        impact = str(item.get("impact", "")).strip().lower()
        date = parse_date(str(item.get("date", "")))
        if code not in CURRENCIES or not date:
            continue
        if now - timedelta(hours=2) <= date <= now + timedelta(hours=40):
            events.append({"currency": code, "time": iso(date), "title": str(item.get("title", ""))[:150],
                           "impact": impact, "forecast": str(item.get("forecast") or "")[:40],
                           "previous": str(item.get("previous") or "")[:40],
                           "actual": str(item.get("actual") or "")[:40],
                           "source_url": CALENDAR, "verified_by_official_source": False})
    return sorted(events, key=lambda x: x["time"])


def relevance(pair, headlines):
    codes = PAIR_CURRENCIES[pair]
    return [item for item in headlines if any(c in item["currencies"] for c in codes)]


def event_impact(pair, events, now, lead=45, lag=45):
    result = []
    for event in events:
        if event["currency"] not in PAIR_CURRENCIES[pair]:
            continue
        if "high" not in event["impact"]:
            continue
        moment = parse_date(event["time"])
        if moment - timedelta(minutes=lead) <= now <= moment + timedelta(minutes=lag):
            result.append(event)
    return result



# Essential pair-specific public announcements plus an independent headline
# aggregation. Other failed feeds must remain openly reported, not suppress all
# monitoring as if no functional source exists.
REQUIRED_FEEDS = {
    "USD": "Fed policy", "EUR": "ECB press", "GBP": "BoE news",
    "JPY": "BoJ updates", "CAD": "BoC press",
    "AUD": "RBA press", "CHF": "SNB monetary policy",
}


def refresh_pair_universe():
    """Load all standard FX symbols from the OANDA account instrument catalog.
    Pepperstone availability cannot be inferred from OANDA account symbols.
    """
    global PAIR_CURRENCIES, CURRENCIES
    catalog_file = load(Path("data/universe.json"), {})
    instruments = catalog_file.get("instruments", {})
    if isinstance(instruments, dict) and instruments:
        pairs = {name: tuple(name.split("_")) for name in instruments
                 if isinstance(name, str) and len(name) == 7 and name[3] == "_"
                 and name[:3].isalpha() and name[4:].isalpha()}
        if pairs:
            PAIR_CURRENCIES = pairs
            CURRENCIES = frozenset(code for codes in pairs.values() for code in codes)
    return PAIR_CURRENCIES


def coverage_for(pair, feeds, calendar_status):
    currencies = PAIR_CURRENCIES[pair]
    unknown_currencies = [code for code in currencies if code not in REQUIRED_FEEDS]
    required = sorted(set([REQUIRED_FEEDS[code] for code in currencies
                           if code in REQUIRED_FEEDS] +
                          ["Global markets aggregation"]))
    missing_essential = [name for name in required
                         if feeds.get(name, {}).get("status") != "OK"]
    other_failed = sorted(name for name, record in feeds.items()
                          if record.get("status") != "OK" and name not in required)
    status = ("UNAVAILABLE" if calendar_status != "OK" or missing_essential
              or unknown_currencies else "LIMITED" if other_failed else "OK")
    return {"status": status,
            "required_sources": required,
            "missing_required": missing_essential,
            "currencies_without_supported_official_feed": unknown_currencies,
            "other_unavailable": other_failed,
            "note": ("LIMITED means selected official and aggregate sources plus the calendar "
                     "responded, but not every optional source. Never interpret this as "
                     "complete global-news or real-time event coverage.")}


def load(path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except (OSError, ValueError):
        return default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def collect(fetch=request, now=None):
    now = now or timestamp()
    refresh_pair_universe()
    state = load(STATE, {"seen": []})
    seen = set(state.get("seen", []))
    feeds = {}
    all_items = []
    for source, (code, url) in SOURCES.items():
        try:
            items = parse_rss(fetch(url), source, code)
            if not items:
                raise ValueError("feed has no dated usable items")
            feeds[source] = {"status": "OK", "count": len(items), "checked_at": iso(now)}
            all_items += items
        except Exception as exc:
            feeds[source] = {"status": "UNAVAILABLE", "error": type(exc).__name__, "checked_at": iso(now)}
    try:
        events = calendar_events(fetch(CALENDAR), now)
        calendar_status = "OK"
    except Exception:
        events = []
        calendar_status = "UNAVAILABLE"
    by_id = {}
    for raw in all_items:
        item = classify(raw)
        if not item:
            continue
        published = parse_date(item["published_at"])
        if not published or published > now + timedelta(minutes=10) or now - published > timedelta(hours=12):
            continue
        fingerprint = hashlib.sha256((item["source"] + "|" + item["url"] + "|" +
                                      item["published_at"]).encode()).hexdigest()[:18]
        item["id"] = fingerprint
        by_id[fingerprint] = item
    headlines = sorted(by_id.values(), key=lambda x: x["published_at"], reverse=True)[:100]
    new = [item for item in headlines if item["id"] not in seen
           and now - parse_date(item["published_at"]) <= timedelta(minutes=90)]
    status = "OK" if calendar_status == "OK" and all(f["status"] == "OK" for f in feeds.values()) else "PARTIAL"
    out = {
        "status": status, "collected_at": iso(now), "sources": feeds,
        "calendar": {"status": calendar_status, "source": CALENDAR,
                     "note": "Third-party indicative; check official calendars at full ChatGPT reviews",
                     "events": events[:200]},
        "new_material_headlines": new[:35],
        "recent_material_headlines": headlines[:100],
        "fx_pairs_classified": len(PAIR_CURRENCIES),
        "coverage_note": "Headlines are unverified potential market events, never proof of directional impact; "
                         "coverage is not exhaustive or continuous. A successful feed does not mean there is no other breaking news.",
        "by_pair": {
            pair: {
                "recent_relevant_headlines": relevance(pair, headlines)[:12],
                "new_relevant_headlines": relevance(pair, new)[:8],
                "near_high_impact_calendar": event_impact(pair, events, now),
                "calendar_coverage": calendar_status,
                "coverage": coverage_for(pair, feeds, calendar_status)
            } for pair in PAIR_CURRENCIES
        }
    }
    save(OUT, out)
    state["seen"] = list((seen | set(by_id)))[-800:]
    save(STATE, state)
    print("News surveillance", status, "| successful RSS", sum(x["status"] == "OK" for x in feeds.values()),
          "of", len(feeds), "| new material", len(new), "| calendar", calendar_status)
    return out


if __name__ == "__main__":
    try:
        collect()
    except Exception as exc:
        # Do not carry forward old 'OK' news coverage on fatal error.
        save(OUT, {"status": "DATA_UNAVAILABLE", "collected_at": iso(timestamp()),
                   "error": type(exc).__name__, "by_pair": {}})
        print("NEWS COVERAGE UNAVAILABLE:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)
