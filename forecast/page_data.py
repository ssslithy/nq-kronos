"""Extra page data collected by the daily vol job AFTER the forecast and the alert (fail-soft).

Each block is written to its own file in site/data. If a source fails, the file is written
with "ok": false and the page hides that block; nothing here can fail or delay the forecast.

  calendar.json    next 3 scheduled US releases at or after the origin (events YAML), London times
  treasuries.json  2Y / 10Y note futures (ZT=F, ZN=F, Yahoo hourly): % move from the close of the
                   previous origin bar to the close of this one (1pm -> 1pm London) and the hourly
                   closes in between (sparkline); yields: 10Y ^TNX (Yahoo), 2Y DGS2 (FRED CSV)
  news.json        3 newest tech headlines of the last 24h (headline, outlet, link, time; never
                   article text) from free RSS: Yahoo Finance headlines for NQ=F, ^NDX and the top
                   Nasdaq names, and CNBC Technology. Picked round-robin across the two feeds
                   (newest first within each), so one feed can't fill all three. Yahoo's feed only
                   holds its ~20 newest items (a few hours), CNBC's ~30 (a few days): a re-run for
                   a past time (--now) finds no Yahoo items that old, so it shows CNBC only.
  fed_odds.json    Polymarket crowd betting odds for the next FOMC decision after the origin (date
                   from the events YAML): Hike / Hold / Cut = summed "Yes" prices of the event's
                   increase / no change / decrease markets, hourly over the same 1pm -> 1pm window
                   as the Treasuries. Free public APIs, no key: Gamma /public-search and /events
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
NEWS_TICKERS = ["NQ=F", "^NDX", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "AVGO", "TSLA"]
FEEDS = [
    ("Yahoo Finance", "https://feeds.finance.yahoo.com/rss/2.0/headline?s="
     + ",".join(t.replace("^", "%5E") for t in NEWS_TICKERS) + "&region=US&lang=en-US"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"),
]
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


def news(now, n: int = 3) -> dict:
    import feedparser
    import requests
    items, errors = [], []
    for outlet, url in FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            d = feedparser.parse(r.content)
            for e in d.entries:
                t = e.get("published_parsed") or e.get("updated_parsed")
                link = e.get("link", "")
                title = _clean(e.get("title", ""))
                if not (t and title and link.startswith(("https://", "http://"))):
                    continue
                pub = datetime(*t[:6], tzinfo=timezone.utc)
                src = e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else None
                items.append({"source": _clean(src) or outlet, "title": title, "link": link,
                              "published_utc": iso(pub), "_t": pub, "_feed": outlet})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{outlet}: {type(e).__name__}: {e}")
    if not items and errors:
        raise RuntimeError("; ".join(errors))
    now_ts = pd.Timestamp(now).to_pydatetime()
    lo = now_ts - timedelta(hours=24)
    seen, per = set(), {o: [] for o, _ in FEEDS}
    for it in sorted(items, key=lambda x: x["_t"], reverse=True):
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())
        if key in seen or not (lo <= it["_t"] <= now_ts + timedelta(minutes=5)):
            continue
        seen.add(key)
        per[it["_feed"]].append(it)
    picked, k = [], 0                                   # round-robin: newest of each feed in turn
    while len(picked) < n and any(len(v) > k for v in per.values()):
        picked += [v[k] for v in per.values() if len(v) > k][: n - len(picked)]
        k += 1
    out = []
    for it in sorted(picked, key=lambda x: x["_t"], reverse=True):
        it["time_london"] = london_time(it.pop("_t"))
        it.pop("_feed")
        out.append(it)
    return {"ok": True, "generated_utc": iso(now), "items": out, "errors": errors or None,
            "feeds": [o for o, _ in FEEDS], "per_feed": {o: len(v) for o, v in per.items()}}


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


def fed_odds(f: dict, prev_origin: pd.Timestamp, now) -> dict:
    origin = pd.Timestamp(f["origin_utc"])
    meeting = next_fomc(origin)
    ev = find_fed_event(meeting["time_utc"])
    w0, w1 = prev_origin + pd.Timedelta(hours=1), origin + pd.Timedelta(hours=1)
    grid = pd.date_range(w0, w1, freq="1h")
    sums = {k: np.zeros(len(grid)) for k in ("Hike", "Hold", "Cut")}
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
        s = pd.Series([float(x["p"]) for x in h], index=pd.to_datetime([x["t"] for x in h], unit="s", utc=True)).sort_index()
        v = s.reindex(s.index.union(grid)).ffill().reindex(grid)
        if v.isna().all():
            raise ValueError(f"no price before the window for {m.get('groupItemTitle')}")
        sums[b] += v.bfill().to_numpy()
        used[b].append(m.get("groupItemTitle") or m.get("question"))
    if not all(used.values()):
        raise ValueError(f"missing outcome buckets: {[k for k, v in used.items() if not v]}")
    md = meeting["time_utc"].tz_convert(cal.NY)
    rows = [{"label": k, "p": round(float(v[-1]), 4), "change_pts": round(float(100 * (v[-1] - v[0])), 1),
             "spark": [round(float(x), 4) for x in v], "markets": used[k]} for k, v in sums.items()]
    return {"ok": True, "generated_utc": iso(now), "origin_utc": iso(origin),
            "meeting_utc": iso(meeting["time_utc"]), "meeting_label": f"{MONTHS[md.month - 1][:3]} {md.day}",
            "window_start_utc": iso(w0), "window_end_utc": iso(w1),
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
            "news": lambda: news(now),
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
