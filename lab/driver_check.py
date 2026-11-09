"""Two-week driver check (NQ_PREOPEN.md part 4). Lab only: it never touches the forecast, the
alert or the page.

Every trading-day evening (~22:30 London, .github/workflows/driver_check.yml) it reads the day's
CLOSING market wraps from the same RSS feeds as the page (page_data.FEEDS; market_wrap items
published after 3:45pm New York), extracts the main named drivers with a simple keyword / entity
match (Nasdaq-100 companies from forecast/news_rules.yaml, plus oil, Fed, inflation, jobs,
tariffs, China, yields, dollar, VIX, earnings, AI / chips), and checks whether each one appeared
in that day's 1pm snapshot: the news items, the morning brief, or the "Since US close" cells
(site/data/news.json and since_close.json as committed at 1pm).

One line per day is appended to lab/results/driver_check.jsonl. Once there are 10 trading days,
lab/results/driver_check.md is (re)written: the share of drivers caught by 1pm and the most
common misses. Nothing changes any rule automatically; it's for review.

    python lab/driver_check.py                 # as the workflow does (acts once a day, 22:15-23:59 London)
    python lab/driver_check.py --force         # run now, whatever the time (still once per date)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "forecast"))
import nq_calendar as cal  # noqa: E402
import page_data as P  # noqa: E402

OUT = ROOT / "lab" / "results" / "driver_check.jsonl"
SUMMARY = ROOT / "lab" / "results" / "driver_check.md"
DATA = ROOT / "site" / "data"
MIN_DAYS = 10
CLOSE_FROM = time(15, 45)                # New York: closing wraps from here on
RUN_FROM = time(22, 15)                  # London: the evening slot

THEMES = {
    "oil": r"\b(?:oil|crude|opec\+?|brent|wti)\b",
    "fed": r"\b(?:fed|federal reserve|fomc|powell|rate cuts?|rate hikes?)\b",
    "inflation": r"\b(?:inflation|cpi|pce|ppi)\b",
    "jobs": r"\b(?:jobs report|payrolls|nonfarm|jobless claims|unemployment)\b",
    "tariffs": r"\b(?:tariffs?|trade war|export controls?|sanctions)\b",
    "china": r"\b(?:china|chinese|beijing)\b",
    "yields": r"\b(?:treasury yields?|bond yields?|yields?|10-year)\b",
    "dollar": r"\b(?:dollar|dxy|greenback)\b",
    "vix": r"\b(?:vix|volatility)\b",
    "earnings": r"\b(?:earnings|results|guidance|quarterly)\b",
    "ai_chips": r"\b(?:ai|artificial intelligence|chips?|chipmakers?|semiconductors?|semis)\b",
}
CELL_THEME = {"Oil": "oil", "VIX": "vix", "Dollar": "dollar"}


def drivers(text: str, R: dict) -> set[str]:
    """Named drivers in a text: Nasdaq-100 companies (canonical names) and macro / sector themes."""
    out = set(P.companies(text, R))
    out |= {k for k, rx in THEMES.items() if re.search(rx, text, re.I)}
    return out


def snapshot_drivers(day, R: dict) -> tuple[set[str], bool]:
    """Drivers present in that day's 1pm snapshot (news items, brief, Since-US-close cells)."""
    try:
        news = json.loads((DATA / "news.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return set(), False
    end = news.get("window_end_utc")
    if not end or datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(cal.NY).date() != day:
        return set(), False                                 # no 1pm snapshot for this day
    found = set()
    for it in (news.get("items") or []) + ([news["brief"]] if news.get("brief") else []):
        found |= drivers(it["title"], R)
    try:
        since = json.loads((DATA / "since_close.json").read_text())
        for c in since.get("cells") or []:
            if c.get("value") is not None and c["label"] in CELL_THEME:
                found.add(CELL_THEME[c["label"]])
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return found, True


def closing_wraps(day, R: dict) -> list[dict]:
    lo = datetime.combine(day, CLOSE_FROM, tzinfo=cal.NY)
    hi = datetime.combine(day + timedelta(days=1), time(4, 0), tzinfo=cal.NY)
    items = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in [ex.submit(P._fetch_feed, fu) for fu in P.FEEDS]:
            try:
                items += fut.result()
            except Exception:  # noqa: BLE001  -- fail-soft: a dead feed just contributes nothing
                pass
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["_t"]):
        if it["link"] in seen or not (lo <= it["_t"] <= hi):
            continue
        seen.add(it["link"])
        if P.wrap_kind(it["title"], R) == "market_wrap":
            out.append(it)
    return out


def summarise(rows: list[dict]) -> str:
    days = [r for r in rows if r.get("snapshot_ok") and r.get("drivers")]
    tot = sum(len(r["drivers"]) for r in days)
    hit = sum(sum(r["caught"].values()) for r in days)
    miss = Counter(d for r in days for d, ok in r["caught"].items() if not ok)
    seen = Counter(d for r in days for d in r["drivers"])
    L = ["# Driver check: did the 1pm snapshot catch what moved NQ by the close?", "",
         f"{len(days)} trading days with a 1pm snapshot and closing wraps ({days[0]['date']} – {days[-1]['date']}). "
         "Drivers = companies and themes named in the day's closing market wraps; caught = also named in the "
         "1pm news items, the morning brief, or a Since-US-close cell. For review only: no rule is changed "
         "automatically.", "",
         f"**Caught by 1pm: {hit} of {tot} drivers ({100 * hit / tot:.0f}%).**" if tot else "No drivers found.", "",
         "| driver | named at the close (days) | caught by 1pm | missed |", "|---|--:|--:|--:|"]
    for d, n in seen.most_common():
        L.append(f"| {d} | {n} | {n - miss[d]} | {miss[d]} |")
    L += ["", "Most common misses: " + (", ".join(f"{d} ({n})" for d, n in miss.most_common(5)) or "none") + ".", "",
          "Run: `python lab/driver_check.py` (the evening workflow does it)."]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true", help="run now, whatever the London time")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    lon, ny = now.astimezone(cal.LONDON), now.astimezone(cal.NY)
    day = ny.date()
    if day.weekday() >= 5 or cal.is_holiday(day):
        print(f"{day} is not a trading day"); return 0
    if not args.force and not (RUN_FROM <= lon.time()):
        print(f"London time {lon:%H:%M} is before the evening slot {RUN_FROM:%H:%M}"); return 0
    rows = [json.loads(x) for x in OUT.read_text().splitlines() if x.strip()] if OUT.exists() else []
    if any(r["date"] == day.isoformat() for r in rows):
        print(f"{day} already checked"); return 0
    R = P.load_news_rules()
    wraps = closing_wraps(day, R)
    named = set()
    for w in wraps:
        named |= drivers(f"{w['title']} {w['summary'][:300]}", R)
    snap, ok = snapshot_drivers(day, R)
    row = {"date": day.isoformat(), "checked_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "snapshot_ok": ok,
           "closing_wraps": [w["title"] for w in wraps], "drivers": sorted(named),
           "caught": {d: d in snap for d in sorted(named)}, "snapshot_drivers": sorted(snap)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    rows.append(row)
    print(f"{day}: {len(wraps)} closing wraps, {len(named)} drivers, caught {sum(row['caught'].values())}; snapshot ok {ok}")
    if sum(1 for r in rows if r.get("snapshot_ok")) >= MIN_DAYS:
        SUMMARY.write_text(summarise(rows))
        print(f"wrote {SUMMARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
