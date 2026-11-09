"""Extra page data collected by the daily vol job AFTER the forecast and the alert (fail-soft).

Each block is written to its own file in site/data. If a source fails, the file is written
with "ok": false and the page hides that block; nothing here can fail or delay the forecast.

  calendar.json    next 3 scheduled US releases at or after the origin (events YAML), London times
  treasuries.json  2Y / 10Y note futures (ZT=F, ZN=F, Yahoo hourly): % move from the close of the
                   previous origin bar to the close of this one (1pm -> 1pm London) and the hourly
                   closes in between (sparkline); yields: 10Y ^TNX (Yahoo), 2Y DGS2 (FRED CSV)
  news.json        up to 12 tech headlines published from the previous cash-session close (4pm
                   New York, previous trading day) to the origin bar close (1pm London), never
                   after it, whenever the job runs. Free RSS (headline, outlet, link, time; never
                   article text): Yahoo Finance per-ticker headlines for ^NDX and the 15 largest
                   Nasdaq-100 names, CNBC Technology / Markets (Market Insider) / Earnings,
                   MarketWatch Top Stories / Breaking, Nasdaq.com Markets / Technology. Filtered,
                   de-duplicated and given a significance (1-3) by forecast/news_rules.yaml.
                   Yahoo's NQ=F feed is stale (last item Oct 2026) and MarketWatch's MarketPulse
                   stopped in 2025, so neither is used. Yahoo per-ticker feeds hold ~20 items
                   (an hour or two on busy days); a re-run for a past time only finds what the
                   feeds still hold.
  fed_odds.json    Polymarket crowd betting odds for the next FOMC decision after the origin (date
                   from the events YAML): Hike / Hold / Cut = summed "Yes" prices of the event's
                   increase / no change / decrease markets, hourly over the same 1pm -> 1pm window
                   as the Treasuries (bid-ask midpoints, normalised to 100%), sampled at the ZT=F bar times (weekend hours skipped like the
                   yields; if ZT=F fails, the NQ trading hours). Free public APIs, no key: Gamma /public-search and /events
                   (find the event by title month + year; the slug has a random suffix), CLOB
                   /prices-history (hourly points).

    python forecast/page_data.py --out /tmp/x          # all three, as of now (needs vol_latest.json there)
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import garch_vol as gv  # noqa: E402
import nq_calendar as cal  # noqa: E402

log = logging.getLogger("page_data")
ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 15
UA = {"User-Agent": "Mozilla/5.0 (compatible; nq-forecasts; +https://ssslithy.github.io/nq-kronos/)"}
NEWS_TICKERS = ["^NDX", "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "NFLX", "COST",
                "PLTR", "AMD", "ASML", "CSCO", "TMUS"]
FEEDS = ([("Yahoo Finance", f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={t.replace('^', '%5E')}&region=US&lang=en-US")
          for t in NEWS_TICKERS] + [
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"),   # Technology
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20409666"),   # Markets (Market Insider)
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135"),   # Earnings
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins"),
    ("Nasdaq", "https://www.nasdaq.com/feed/rssoutbound?category=Markets"),
    ("Nasdaq", "https://www.nasdaq.com/feed/rssoutbound?category=Technology"),
])
# outlet: (timeout s, retries, User-Agent). Nasdaq.com's firewall drops the connection when the
# User-Agent contains a URL (as UA does); the same name without it answers in 2-3 s.
SLOW_FEEDS = {"Nasdaq": (30, 1, {"User-Agent": "Mozilla/5.0 (compatible; nq-forecasts)"})}
NEWS_RULES = Path(__file__).resolve().parent / "news_rules.yaml"
NEWS_MAX = 12
NEWS_PER_OUTLET = 6
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
FRED_DGS2 = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"


def iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def london_time(t) -> str:
    """12-hour London clock, e.g. 1:30pm, 12:42pm, 11:58am."""
    lt = pd.Timestamp(t).tz_convert(cal.LONDON)
    return f"{lt.hour % 12 or 12}:{lt.minute:02d}{'am' if lt.hour < 12 else 'pm'}"


def write(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n")
    os.replace(tmp, path)


def fail(name: str, e: Exception, now) -> dict:
    log.warning("%s: %s: %s", name, type(e).__name__, e)
    return {"ok": False, "generated_utc": iso(now), "error": f"{type(e).__name__}: {e}"[:300]}


# ---- calendar ------------------------------------------------------------------------------

def calendar(f: dict, now) -> dict:
    origin = pd.Timestamp(f["origin_utc"])
    covered_from, covered_until, events = gv.load_events()
    if origin.tz_convert(cal.NY).date() > covered_until:
        raise ValueError(f"event calendar not loaded after {covered_until}")
    o_day = origin.tz_convert(cal.LONDON).date()
    items = []
    for e in events:
        if e["time_utc"] >= origin:
            lt = e["time_utc"].tz_convert(cal.LONDON)
            items.append({"time_utc": iso(e["time_utc"]), "kind": e["kind"], "name": e["name"],
                          "impact": e["impact"], "today": lt.date() == o_day,
                          "date_london": "Today" if lt.date() == o_day else f"{lt:%a} {lt.day} {lt:%b}",
                          "time_london": london_time(e["time_utc"])})
        if len(items) == 3:
            break
    return {"ok": True, "generated_utc": iso(now), "origin_utc": iso(origin), "items": items,
            "source": gv.EVENTS_FILE.name}


# ---- Treasuries ------------------------------------------------------------------------------

def _hourly(ticker: str, start, end) -> pd.Series:
    import yfinance as yf
    d = yf.Ticker(ticker).history(start=(start - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                                  end=(end + pd.Timedelta(days=2)).strftime("%Y-%m-%d"),
                                  interval="1h", auto_adjust=False, timeout=TIMEOUT)
    if d.empty:
        raise ValueError(f"no hourly data for {ticker}")
    s = d["Close"].dropna()
    s.index = s.index.tz_convert("UTC")
    return s


def _close_at(s: pd.Series, bar_start: pd.Timestamp) -> float:
    """Close of the bar starting at bar_start, or of the last bar before it (thin hours)."""
    x = s[s.index <= bar_start]
    if x.empty or bar_start - x.index[-1] > pd.Timedelta(hours=3):
        raise ValueError(f"no bar near {iso(bar_start)}")
    return float(x.iloc[-1])


def _daily_last_before(ticker: str, day) -> float:
    import yfinance as yf
    d = yf.Ticker(ticker).history(period="1mo", interval="1d", auto_adjust=False, timeout=TIMEOUT)
    if d.empty:
        raise ValueError(f"no daily data for {ticker}")
    s = pd.Series(d["Close"].to_numpy(), index=pd.to_datetime(d.index.strftime("%Y-%m-%d"))).dropna()
    s = s[(s.index < pd.Timestamp(day)) & (s.index.weekday < 5)]
    if s.empty:
        raise ValueError(f"no {ticker} close before {day}")
    return float(s.iloc[-1])


def _fred_last_before(day) -> float:
    import requests
    r = requests.get(FRED_DGS2, timeout=TIMEOUT)          # FRED answers the default client fastest
    r.raise_for_status()
    rows = [ln.split(",") for ln in r.text.strip().splitlines()[1:]]
    vals = [(pd.Timestamp(d), float(v)) for d, v in rows if v not in (".", "")]
    vals = [v for d, v in vals if d < pd.Timestamp(day)]
    if not vals:
        raise ValueError("no DGS2 value before the origin")
    return vals[-1]


def treasuries(f: dict, prev_origin: pd.Timestamp, now) -> dict:
    origin = pd.Timestamp(f["origin_utc"])
    day = origin.tz_convert(cal.NY).date()
    rows, errors = [], []
    for label, fut, yld in (("2Y", "ZT=F", "fred"), ("10Y", "ZN=F", "^TNX")):
        row = {"label": label, "ticker": fut}
        try:
            s = _hourly(fut, prev_origin, origin)
            a, b = _close_at(s, prev_origin), _close_at(s, origin)
            w = s[(s.index >= prev_origin) & (s.index <= origin)]
            row.update({"move_pct": round(100 * (b / a - 1), 3), "start_close": a, "end_close": b,
                        "spark": [round(float(v), 6) for v in w.to_numpy()]})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label} move: {type(e).__name__}: {e}")
            row["move_pct"] = None
        try:
            row["yield_pct"] = round(_fred_last_before(day) if yld == "fred" else _daily_last_before(yld, day), 2)
            row["yield_source"] = "FRED DGS2" if yld == "fred" else "Yahoo ^TNX"
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label} yield: {type(e).__name__}: {e}")
            row["yield_pct"] = None
        rows.append(row)
    ok = any(r["move_pct"] is not None for r in rows)
    pl, ol = prev_origin + pd.Timedelta(hours=1), origin + pd.Timedelta(hours=1)
    pl_l, ol_l = pl.tz_convert(cal.LONDON), ol.tz_convert(cal.LONDON)
    short = lambda t: london_time(t).replace(":00", "")      # noqa: E731  "1pm"
    return {"ok": ok, "generated_utc": iso(now), "origin_utc": iso(origin),
            "window_start_utc": iso(pl), "window_end_utc": iso(ol),
            "window_label": f"{pl_l:%a} {short(pl)} – {ol_l:%a} {short(ol)}",
            "rows": rows, "errors": errors or None}


# ---- news ----------------------------------------------------------------------------------

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def load_news_rules(path: Path = NEWS_RULES) -> dict:
    """news_rules.yaml compiled: each list becomes one case-insensitive regex; company aliases and
    event groups are also kept separately for story clustering."""
    import yaml
    raw = yaml.safe_load(path.read_text())

    def pat(x):
        return x[3:] if x.startswith("re:") else rf"\b(?:{x})\b"

    def rx(items):
        return re.compile("|".join(f"(?:{pat(x)})" for x in items), re.I) if items else re.compile(r"(?!x)x")
    top = [a for v in raw["top_names"].values() for a in v]
    R = {k: rx(v) for k, v in (("top", top), ("other", raw["other_names"]), ("sector", raw["sector"]),
                                ("market", raw["market_words"]), ("macro", raw["macro"]),
                                ("earnings", raw["earnings_result"]), ("major", raw["major_event"]),
                                ("rating", raw["rating"]), ("always_1", raw["always_1"]),
                                ("shock", raw["market_shock"]), ("hard", raw["hard_action"]),
                                ("listicle", raw["listicle_shape"]),
                                ("ex_title", raw["exclude_title"]), ("ex_link", raw["exclude_link"]))}
    R["companies"] = ([(name, re.compile(pat(a), re.I)) for name, al in raw["top_names"].items() for a in al] +
                      [(a.lower(), re.compile(pat(a), re.I)) for a in raw["other_names"]])
    R["events"] = {k: rx(v) for k, v in raw["event_words"].items()}
    R["source_rank"] = {s_: i for i, s_ in enumerate(raw["source_rank"])}
    R["blocked_publishers"] = [x.lower() for x in raw["blocked_publishers"]]
    R["blocked_domains"] = [x.lower() for x in raw["blocked_domains"]]
    R["listicle_publishers"] = [x.lower() for x in raw["listicle_publishers"]]
    # one-word aliases (company names, tickers) are not "proper names" for clustering; people's
    # names inside multi-word aliases ("jensen huang") still are
    R["alias_words"] = {a.lower() for al in raw["top_names"].values() for a in al if re.fullmatch(r"[a-z.\-]+", a)} | \
        {a.lower() for a in raw["other_names"] if re.fullmatch(r"[a-z.\-]+", a)}
    return R


def blocked_publisher(publisher: str | None, link: str, title: str, R: dict) -> bool:
    """A syndicated low-quality publisher (by name or link domain), or a listicle from a
    listicle_publishers outlet."""
    from urllib.parse import urlparse
    dom = urlparse(link or "").netloc.lower()
    pub = (publisher or "").lower()
    if any(dom == d or dom.endswith("." + d) for d in R["blocked_domains"]):
        return True
    if pub and any(b in pub for b in R["blocked_publishers"]):
        return True
    listy = any(b in pub or b in dom for b in R["listicle_publishers"])
    return listy and bool(R["listicle"].search(title))


YAHOO_PAGE = re.compile(r"^https?://finance\.yahoo\.com/(?:[a-z-]+/)*(?:articles/|m/)")


def yahoo_publisher(link: str) -> str | None:
    """The publisher Yahoo names on a syndicated article page (e.g. "Simply Wall St.",
    "GuruFocus.com", "Barrons.com", "MT Newswires"); None if it can't be read."""
    import requests
    try:
        r = requests.get(link, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        m = (re.search(r'"provider"\s*:\s*\{[^}]{0,200}?"name"\s*:\s*"([^"]{2,40})"', r.text)
             or re.search(r'"provider(?:Name)?"\s*:\s*"([^"]{2,40})"', r.text))
        return html.unescape(m.group(1)).strip() if m else None
    except Exception:  # noqa: BLE001
        return None


def publisher_label(pub: str | None) -> str | None:
    if not pub:
        return None
    return {"barrons.com": "Barron's", "gurufocus.com": "GuruFocus"}.get(pub.lower(), re.sub(r"\.com$", "", pub).strip())


def news_relevant(title: str, summary: str, link: str, R: dict) -> bool:
    """A Nasdaq-100 company, a macro term, or a sector term with a market word, all in the headline
    (a company only in the summary doesn't count); and no exclusion."""
    if R["ex_title"].search(title) or R["ex_link"].search(link or ""):
        return False
    if R["top"].search(title) or R["other"].search(title):
        return True
    if R["macro"].search(title):
        return True
    return bool(R["sector"].search(title) and R["market"].search(title))


SUBJECT_CHARS = 25                 # "the company is the subject": named within the headline's first 25 characters


def _subject(rx, title: str) -> bool:
    m = rx.search(title)
    return bool(m) and m.start() <= SUBJECT_CHARS


def news_impact(title: str, R: dict) -> int:
    """Significance 1-3 from the headline (see news_rules.yaml)."""
    if R["always_1"].search(title):
        return 1
    top, other = bool(R["top"].search(title)), bool(R["other"].search(title))
    top_subj = _subject(R["top"], title)
    ndx_subj = top_subj or _subject(R["other"], title)
    sector_move = bool(R["sector"].search(title) and R["market"].search(title))
    if (R["macro"].search(title) or (ndx_subj and R["earnings"].search(title))
            or (top_subj and R["major"].search(title))
            or (R["shock"].search(title) and (top or re.search(r"\bnasdaq\b", title, re.I)))):
        return 3
    sector = bool(R["sector"].search(title))
    if (ndx_subj or sector) and (R["hard"].search(title) or R["rating"].search(title) or R["earnings"].search(title)):
        return 2
    return 1


def companies(title: str, R: dict) -> set[str]:
    """Every Nasdaq-100 company named in the headline (canonical names for the top 8)."""
    return {name for name, rx in R["companies"] if rx.search(title)}


def main_company(title: str, R: dict) -> str | None:
    """The first Nasdaq-100 company named in the headline (canonical name for the top 8)."""
    best = None
    for name, rx in R["companies"]:
        m = rx.search(title)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else None


NAME_STOP = {"the", "and", "for", "with", "after", "says", "said", "new", "inc", "corp", "stock", "stocks", "shares", "wall",
             "street", "nasdaq", "dow", "ceo", "cfo", "fed", "u.s", "us", "ai", "chip", "chips", "tech", "monday", "tuesday",
             "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may", "june",
             "july", "august", "september", "october", "november", "december", "china", "europe", "trump", "why", "how",
             "what", "here", "this", "its", "his", "her", "their", "big", "top", "market", "markets", "investors"}


def proper_names(title: str, R: dict) -> set[str]:
    """Capitalised words after the first one (people, products, places), minus company aliases and
    common words: a shared one ties two headlines about the same company to one story."""
    words = re.findall(r"\b[A-Z][a-zA-Z]{2,}(?:'s)?\b", title)[1:]
    long_words = re.findall(r"\b[a-zA-Z]{4,}\b", title)
    if long_words and sum(w[0].isupper() for w in long_words) / len(long_words) > 0.6:
        return set()                                   # Title Case headline: capitals say nothing
    out = set()
    for w in words:
        w = re.sub(r"'s$", "", w).lower()
        if w not in NAME_STOP and w not in R["alias_words"]:
            out.add(w)
    return out


def event_groups(title: str, R: dict) -> set[str]:
    return {k for k, rx in R["events"].items() if rx.search(title)}


def _title_key(t: str) -> list[str]:
    stop = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "as", "is", "at", "with", "after", "by", "its", "from"}
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", t.lower()).split() if w not in stop]


def _near_duplicate(a: str, b: str) -> bool:
    """Fuzzy title match: same story across feeds (character ratio or word overlap)."""
    from difflib import SequenceMatcher
    ka, kb = _title_key(a), _title_key(b)
    if not ka or not kb:
        return False
    jac = len(set(ka) & set(kb)) / len(set(ka) | set(kb))
    return jac >= 0.6 or SequenceMatcher(None, " ".join(ka), " ".join(kb)).ratio() >= 0.85


def news_window(f: dict, prev_origin) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Previous cash-session close (4pm New York on the previous trading day) -> the origin bar's
    close (1pm London). Fixed by the origin, not by when the job runs."""
    end = pd.Timestamp(f["origin_utc"]) + pd.Timedelta(hours=1)
    if prev_origin is None:
        return end - pd.Timedelta(hours=24), end
    d = pd.Timestamp(prev_origin).tz_convert(cal.NY).date()
    start = pd.Timestamp(datetime(d.year, d.month, d.day, 16), tz=cal.NY).tz_convert("UTC")
    return start, end


def _fetch_feed(outlet_url):
    import feedparser
    import requests
    outlet, url = outlet_url
    timeout, retries, headers = SLOW_FEEDS.get(outlet, (TIMEOUT, 0, UA))
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            break
        except Exception:  # noqa: BLE001
            if attempt == retries:
                raise
    out = []
    for e in feedparser.parse(r.content).entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        link = e.get("link", "")
        title = _clean(e.get("title", ""))
        if not (t and title and link.startswith(("https://", "http://"))):
            continue
        src = e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else None
        out.append({"source": _clean(src) or outlet, "title": title, "link": link,
                    "summary": _clean(e.get("summary", ""))[:400],
                    "_t": datetime(*t[:6], tzinfo=timezone.utc), "_feed": outlet})
    return out


CLUSTER_HOURS = 24


def _split(impacts: list[int]) -> dict:
    """Count and share of 3 / 2 / 1-bar items (target over a typical day: ~20 / 40 / 40 %)."""
    n = len(impacts)
    return {"n": n, **{f"bars{k}": impacts.count(k) for k in (3, 2, 1)},
            **{f"pct{k}": round(100 * impacts.count(k) / n) if n else None for k in (3, 2, 1)}}


def news(f: dict, prev_origin, now, n: int = NEWS_MAX) -> dict:
    """Up to n relevant stories in the news window (news_window), each with a significance 1-3
    (news_rules.yaml): the most significant first, then the newest, at most NEWS_PER_OUTLET per
    outlet; listed newest first. Headlines about the same story within 24 hours (same main company
    and a shared event group or proper name, or near-identical titles) are one story: the
    highest-ranked source's headline at the earliest time. Syndicated low-quality publishers are
    dropped (Yahoo-hosted items: the publisher read from the article page). "split" logs the
    3 / 2 / 1-bar mix of all stories and of the listed ones."""
    from concurrent.futures import ThreadPoolExecutor
    start, end = news_window(f, prev_origin)
    R = load_news_rules()
    items, errors, ok_feeds = [], [], 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [(fu, ex.submit(_fetch_feed, fu)) for fu in FEEDS]
        for (outlet, url), fut in futs:
            try:
                items += fut.result()
                ok_feeds += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{outlet} {url[-40:]}: {type(e).__name__}: {e}"[:200])
    if not ok_feeds:
        raise RuntimeError("; ".join(errors) or "no feeds")
    lo, hi = start.to_pydatetime(), end.to_pydatetime()
    seen_links, cand = set(), []
    stats = {"fetched": len(items), "in_window": 0, "relevant": 0, "blocked_publisher": 0, "clustered": 0}
    rank = lambda it: R["source_rank"].get(it["_feed"], len(R["source_rank"]))  # noqa: E731
    for it in sorted(items, key=lambda x: x["_t"]):                 # oldest first
        if not (lo <= it["_t"] <= hi) or it["link"] in seen_links:
            continue
        stats["in_window"] += 1
        seen_links.add(it["link"])
        if news_relevant(it["title"], it["summary"], it["link"], R):
            cand.append(it)
    # the real publisher of Yahoo-hosted syndicated items (from the article page), in parallel
    yahoo = [it for it in cand if YAHOO_PAGE.match(it["link"])]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for it, pub in zip(yahoo, ex.map(lambda x: yahoo_publisher(x["link"]), yahoo)):
            it["publisher"] = pub
    stories = []
    for it in cand:
        if blocked_publisher(it.get("publisher"), it["link"], it["title"], R):
            stats["blocked_publisher"] += 1
            continue
        stats["relevant"] += 1
        if it.get("publisher"):
            it["source"] = publisher_label(it["publisher"])
        comp, ev, names = main_company(it["title"], R), event_groups(it["title"], R), proper_names(it["title"], R)
        comps = companies(it["title"], R)
        home = None
        for st in stories:                                          # same story within 24 hours?
            if (it["_t"] - st["first"]).total_seconds() > CLUSTER_HOURS * 3600:
                continue
            if (comps & st["companies"] and ev & st["events"]) or \
                    (comp and comp == st["company"] and names & st["names"]) or \
                    any(_near_duplicate(it["title"], m["title"]) for m in st["members"]):
                home = st
                break
        if home is None:
            stories.append({"company": comp, "companies": set(comps), "events": set(ev), "names": set(names),
                            "first": it["_t"], "members": [it]})
        else:
            home["members"].append(it)
            home["events"] |= ev
            home["names"] |= names
            home["companies"] |= comps
            stats["clustered"] += 1
    kept = []
    for st in stories:          # the most significant headline (ties: source rank, earliest), the earliest time
        for m in st["members"]:
            m["impact"] = news_impact(m["title"], R)
        best = min(st["members"], key=lambda m: (-m["impact"], rank(m), m["_t"]))
        kept.append(dict(best, _t=st["first"], cluster=len(st["members"])))
    split = {"stories": _split([it["impact"] for it in kept])}
    # pick the n most significant (then newest), at most NEWS_PER_OUTLET from one outlet so the
    # 16 busy Yahoo feeds can't crowd out CNBC / MarketWatch / Nasdaq; then show newest first
    picked, per = [], {}
    for it in sorted(kept, key=lambda x: (x["impact"], x["_t"]), reverse=True):
        if per.get(it["_feed"], 0) >= NEWS_PER_OUTLET:
            continue
        picked.append(it)
        per[it["_feed"]] = per.get(it["_feed"], 0) + 1
        if len(picked) == n:
            break
    out = []
    for it in sorted(picked, key=lambda x: x["_t"], reverse=True):
        out.append({"source": it["source"], "title": it["title"], "link": it["link"],
                    "published_utc": iso(it["_t"]), "time_london": london_time(it["_t"]),
                    "impact": it["impact"], "cluster": it["cluster"]})
    split["listed"] = _split([it["impact"] for it in out])
    return {"ok": True, "generated_utc": iso(now), "window_start_utc": iso(start), "window_end_utc": iso(end),
            "items": out, "split": split, "stats": stats, "feeds_ok": ok_feeds, "feeds_total": len(FEEDS),
            "errors": errors or None, "rules": NEWS_RULES.name}


# ---- Fed decision odds (Polymarket) ---------------------------------------------------------

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def next_fomc(origin: pd.Timestamp) -> dict:
    """The first FOMC decision in the events YAML after the origin bar's close."""
    _, until, events = gv.load_events()
    for e in events:
        if e["kind"] == "FOMC" and e["time_utc"] > origin + pd.Timedelta(hours=1):
            return e
    raise ValueError(f"no FOMC decision after {origin:%Y-%m-%d} in the events YAML (loaded to {until})")


def _get(url: str, params: dict | None = None):
    import requests
    r = requests.get(url, params=params, timeout=TIMEOUT)       # default UA (Cloudflare 403s urllib's)
    r.raise_for_status()
    return r.json()


def find_fed_event(meeting: pd.Timestamp) -> dict:
    """The Polymarket event "Fed decision in <Month>?" for this meeting: title has "fed",
    "decision" and the month; the year is checked on the markets' questions ("... after the
    December 2026 meeting?") or, failing that, the event's end date (within 10 days after)."""
    d = meeting.tz_convert(cal.NY)
    month, year = MONTHS[d.month - 1], d.year
    found = []
    for status in ("active", None):
        q = {"q": f"Fed decision in {month}", "limit_per_type": 20}
        if status:
            q["events_status"] = status
        for ev in (_get(f"{GAMMA}/public-search", q).get("events") or []):
            t = (ev.get("title") or "").lower()
            if not ("fed" in t and "decision" in t and month.lower() in t):
                continue
            qs = " ".join(m.get("question") or "" for m in ev.get("markets") or [])
            end = pd.Timestamp(ev["endDate"]) if ev.get("endDate") else None
            if f"{month} {year}" in qs or (end is not None and pd.Timedelta(0) <= end - meeting <= pd.Timedelta(days=10)):
                found.append(ev)
        if found:
            break
    if not found:
        raise LookupError(f"no Polymarket event 'Fed decision in {month}' for {year}")
    return _get(f"{GAMMA}/events/{found[0]['id']}")


def _bucket(m: dict) -> str | None:
    t = f"{m.get('groupItemTitle') or ''} {m.get('question') or ''}".lower()
    if "increase" in t or "hike" in t:
        return "Hike"
    if "decrease" in t or "cut" in t:
        return "Cut"
    if "no change" in t or "pause" in t or "hold" in t:
        return "Hold"
    return None


def window_times(prev_origin: pd.Timestamp, origin: pd.Timestamp) -> pd.DatetimeIndex:
    """The Treasuries sparkline's sample times: closes of the ZT=F hourly bars from the previous
    origin bar to this one (weekend and daily-break hours absent, as in the yields). If ZT=F
    can't be fetched: closes of the NQ trading hours in the same span."""
    try:
        s = _hourly("ZT=F", prev_origin, origin)
        idx = s.index[(s.index >= prev_origin) & (s.index <= origin)]
        if len(idx) >= 2 and idx[0] == prev_origin and idx[-1] == origin:
            return pd.DatetimeIndex(idx + pd.Timedelta(hours=1))
    except Exception as e:  # noqa: BLE001
        log.warning("fed_odds: ZT=F times unavailable (%s); using NQ trading hours", e)
    hrs = pd.date_range(prev_origin, origin, freq="1h")
    return pd.DatetimeIndex([t + pd.Timedelta(hours=1) for t in hrs if cal.is_tradable_hour(t.to_pydatetime())])


MIDPOINT_FRESH = pd.Timedelta(minutes=30)   # use the live bid-ask midpoint only this close to the window end


def round_to_100(shares) -> list[int]:
    """Whole percentages that add up to exactly 100 (largest remainder): floor each share x 100,
    then give the missing points to the largest remainders."""
    x = np.asarray(shares, dtype=float) * 100
    base = np.floor(x).astype(int)
    for k in np.argsort(-(x - base))[: 100 - int(base.sum())]:
        base[k] += 1
    return base.tolist()


def fed_odds(f: dict, prev_origin: pd.Timestamp, now) -> dict:
    """Hike / Hold / Cut for the next FOMC decision over the Treasuries window.

    Each outcome bucket's price is its bid-ask midpoint: the CLOB /midpoint endpoint for the
    window end when the job runs within MIDPOINT_FRESH of it (a later re-run never uses prices
    from after the origin), otherwise Polymarket's price history, which is the displayed price
    = the midpoint (the last trade only when the spread is wider than $0.10). The buckets are
    summed into Hike / Hold / Cut and normalised to add up to 1 at every point; the shown whole
    percentages are rounded to add up to exactly 100, and "pts" = the shown end minus the shown
    start, so they add up to 0."""
    origin = pd.Timestamp(f["origin_utc"])
    meeting = next_fomc(origin)
    ev = find_fed_event(meeting["time_utc"])
    w0, w1 = prev_origin + pd.Timedelta(hours=1), origin + pd.Timedelta(hours=1)
    grid = window_times(prev_origin, origin)
    live = pd.Timestamp(now) - grid[-1] <= MIDPOINT_FRESH
    names = ("Hike", "Hold", "Cut")
    sums = {k: np.zeros(len(grid)) for k in names}
    used = {k: [] for k in sums}
    for m in ev.get("markets") or []:
        b = _bucket(m)
        if b is None:
            continue
        outcomes = json.loads(m.get("outcomes") or "[]")
        tokens = json.loads(m.get("clobTokenIds") or "[]")
        if not tokens or (outcomes and outcomes[0].lower() != "yes"):
            continue
        h = _get(f"{CLOB}/prices-history", {"market": tokens[0], "startTs": int(w0.timestamp()) - 6 * 3600,
                                            "endTs": int(w1.timestamp()), "fidelity": 60}).get("history") or []
        if not h:
            if m.get("closed"):
                continue
            raise ValueError(f"no price history for {m.get('groupItemTitle')}")
        s_ = pd.Series([float(x["p"]) for x in h], index=pd.to_datetime([x["t"] for x in h], unit="s", utc=True)).sort_index()
        v = s_.reindex(s_.index.union(grid)).ffill().reindex(grid)
        if v.isna().all():
            raise ValueError(f"no price before the window for {m.get('groupItemTitle')}")
        v = v.bfill().to_numpy()
        if live:
            mid = _get(f"{CLOB}/midpoint", {"token_id": tokens[0]}).get("mid")
            if mid is None:
                raise ValueError(f"no midpoint for {m.get('groupItemTitle')}")
            v[-1] = float(mid)
        sums[b] += v
        used[b].append(m.get("groupItemTitle") or m.get("question"))
    if not all(used.values()):
        raise ValueError(f"missing outcome buckets: {[k for k, v in used.items() if not v]}")
    raw = np.vstack([sums[k] for k in names])                    # 3 x points, unnormalised
    total = raw.sum(axis=0)
    if np.any(total <= 0):
        raise ValueError("bucket prices sum to zero")
    norm = raw / total
    end_pct, start_pct = round_to_100(norm[:, -1]), round_to_100(norm[:, 0])
    md = meeting["time_utc"].tz_convert(cal.NY)
    rows = [{"label": k, "p": round(float(norm[i, -1]), 4), "pct": end_pct[i],
             "change_pts": end_pct[i] - start_pct[i], "pct_start": start_pct[i],
             "raw_p": round(float(raw[i, -1]), 4), "spark": [round(float(x), 4) for x in norm[i]],
             "markets": used[k]} for i, k in enumerate(names)]
    return {"ok": True, "generated_utc": iso(now), "origin_utc": iso(origin),
            "meeting_utc": iso(meeting["time_utc"]), "meeting_label": f"{MONTHS[md.month - 1][:3]} {md.day}",
            "window_start_utc": iso(grid[0]), "window_end_utc": iso(grid[-1]), "points": len(grid),
            "sampling": "ZT=F hourly bar closes (as the Treasuries)",
            "price": ("bid-ask midpoint: CLOB /midpoint at the window end, price history before it"
                      if live else "price history (Polymarket's midpoint price) throughout: run too late for a live midpoint"),
            "normalised": {"raw_sum_end": round(float(total[-1]), 4), "raw_sum_start": round(float(total[0]), 4),
                           "method": "Hike + Hold + Cut scaled to 1 at every point; shown % rounded to total 100"},
            "event": {"id": ev.get("id"), "title": ev.get("title"), "slug": ev.get("slug"),
                      "url": f"https://polymarket.com/event/{ev.get('slug')}"},
            "rows": rows, "source": "Polymarket (crowd betting odds; Gamma + CLOB public APIs)"}


# ---- all ------------------------------------------------------------------------------------

def previous_origin(bars: pd.DataFrame) -> pd.Timestamp | None:
    pos = gv.daily_origins(bars)
    return bars.index[pos[-2]] if len(pos) >= 2 else None


def collect(f: dict, out: Path, now, prev_origin=None, skip: set[str] | None = None) -> dict:
    """Write calendar.json, treasuries.json, news.json and fed_odds.json; never raises. Returns {name: ok}."""
    skip = skip or set()
    res = {}
    jobs = {"calendar": lambda: calendar(f, now),
            "treasuries": lambda: treasuries(f, pd.Timestamp(prev_origin), now),
            "news": lambda: news(f, prev_origin, now),
            "fed_odds": lambda: fed_odds(f, pd.Timestamp(prev_origin), now)}
    for name, job in jobs.items():
        try:
            if name in skip:
                raise RuntimeError("simulated failure (--fail)")
            if name in ("treasuries", "fed_odds") and prev_origin is None:
                raise ValueError("no previous origin in the bars")
            obj = job()
        except Exception as e:  # noqa: BLE001
            obj = fail(name, e, now)
        try:
            write(out / f"{name}.json", obj)
        except Exception as e:  # noqa: BLE001
            log.warning("could not write %s.json: %s", name, e)
        res[name] = bool(obj.get("ok"))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "site" / "data"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    out = Path(args.out)
    f = json.loads((out / "vol_latest.json").read_text())
    o = pd.Timestamp(f["origin_utc"])
    d = o.tz_convert(cal.NY).date() - timedelta(days=1)
    while d.weekday() >= 5 or cal.is_holiday(d):
        d -= timedelta(days=1)
    prev = pd.Timestamp(datetime(d.year, d.month, d.day, gv.ORIGIN_NY_HOUR), tz=cal.NY).tz_convert("UTC")
    print(collect(f, out, datetime.now(timezone.utc), prev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
