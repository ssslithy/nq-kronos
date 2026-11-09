"""Extra page data collected by the daily vol job AFTER the forecast and the alert (fail-soft).

Each block is written to its own file in site/data. If a source fails, the file is written
with "ok": false and the page hides that block; nothing here can fail or delay the forecast.

  calendar.json    next 3 scheduled US releases at or after the origin (events YAML), London times
  treasuries.json  2Y / 10Y note futures (ZT=F, ZN=F, Yahoo hourly): % move from the close of the
                   previous origin bar to the close of this one (1pm -> 1pm London) and the hourly
                   closes in between (sparkline); yields: 10Y ^TNX (Yahoo), 2Y DGS2 (FRED CSV)
  news.json        3 newest tech headlines of the last 24h (headline, outlet, link, time; never
                   article text) from free RSS: Yahoo Finance headlines for NQ=F, ^NDX and the top
                   Nasdaq names, and CNBC Technology

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
                              "published_utc": iso(pub), "_t": pub})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{outlet}: {type(e).__name__}: {e}")
    if not items and errors:
        raise RuntimeError("; ".join(errors))
    now_ts = pd.Timestamp(now).to_pydatetime()
    lo = now_ts - timedelta(hours=24)
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["_t"], reverse=True):
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())
        if key in seen or not (lo <= it["_t"] <= now_ts + timedelta(minutes=5)):
            continue
        seen.add(key)
        it["time_london"] = london_time(it.pop("_t"))
        out.append(it)
        if len(out) == n:
            break
    return {"ok": True, "generated_utc": iso(now), "items": out, "errors": errors or None,
            "feeds": [o for o, _ in FEEDS]}


# ---- all ------------------------------------------------------------------------------------

def previous_origin(bars: pd.DataFrame) -> pd.Timestamp | None:
    pos = gv.daily_origins(bars)
    return bars.index[pos[-2]] if len(pos) >= 2 else None


def collect(f: dict, out: Path, now, prev_origin=None, skip: set[str] | None = None) -> dict:
    """Write calendar.json, treasuries.json and news.json; never raises. Returns {name: ok}."""
    skip = skip or set()
    res = {}
    jobs = {"calendar": lambda: calendar(f, now),
            "treasuries": lambda: treasuries(f, pd.Timestamp(prev_origin), now),
            "news": lambda: news(now)}
    for name, job in jobs.items():
        try:
            if name in skip:
                raise RuntimeError("simulated failure (--fail)")
            if name == "treasuries" and prev_origin is None:
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
