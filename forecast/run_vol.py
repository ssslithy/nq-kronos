"""Daily 08:00 NY GARCH 24h vol forecast: decide whether to run, forecast, write site/data.

Run by .github/workflows/garch_vol.yml at 12:05 and 13:05 UTC on weekdays. One of the two
lands between 08:00 and 09:25 New York whatever the US/UK daylight-saving state; the other
exits quietly. The script runs only when all of these hold, otherwise it exits 0 untouched:

  - today (New York) is an NQ trading day: weekday, not an nq_calendar holiday, and the
    07:00-08:00 NY bar is a tradable hour;
  - New York time is between 08:00 and 09:25;
  - today's forecast hasn't been made yet (vol_latest.json already has today's origin).

Then it loads bars closed by 08:00 NY and needs the 07:00-08:00 NY bar to be the last one
(retrying up to 3 times, 2 minutes apart, if Yahoo is slow).

  success: site/data/vol_latest.json (the forecast), one line appended to
           site/data/vol_history.jsonl (one per origin), site/data/vol_status.json ok.
  failure: vol_status.json records the error and stale=true; vol_latest.json keeps the
           previous forecast, flagged "stale": true; exit 1 so the run fails.

    python forecast/run_vol.py                                   # as the workflow does
    python forecast/run_vol.py --now "2026-11-06 08:10" --out /tmp/x    # a past day, elsewhere
    python forecast/run_vol.py --force                           # ignore the time window
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402
import garch_vol as gv  # noqa: E402
import nq_calendar as cal  # noqa: E402

log = logging.getLogger("run_vol")

ROOT = Path(__file__).resolve().parent.parent
WINDOW_START, WINDOW_END = (8, 0), (9, 25)      # New York
RETRIES, RETRY_WAIT = 3, 120                    # Yahoo lag: attempts, seconds apart


class NotAWindow(Exception):
    """Not a forecast window: exit 0 without changes."""


def iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_atomic(path: Path, obj, **kw) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, **kw) + "\n")
    os.replace(tmp, path)


def origin_for(d: date) -> datetime:
    """Start of the 07:00-08:00 NY bar on day d, in UTC."""
    return datetime(d.year, d.month, d.day, gv.ORIGIN_NY_HOUR, tzinfo=cal.NY).astimezone(cal.UTC)


def is_forecast_day(d: date) -> bool:
    return d.weekday() < 5 and not cal.is_holiday(d) and cal.is_tradable_hour(origin_for(d))


def next_forecast_utc(after: date) -> str:
    d = after + timedelta(days=1)
    while not is_forecast_day(d):
        d += timedelta(days=1)
    t = datetime(d.year, d.month, d.day, *WINDOW_START, tzinfo=cal.NY)
    return iso(t)


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def decide(now: datetime, latest: dict | None, force: bool) -> datetime:
    """The origin (UTC) to forecast from, or raise NotAWindow."""
    ny = now.astimezone(cal.NY)
    d = ny.date()
    if force:
        while not is_forecast_day(d) or (d == ny.date() and (ny.hour, ny.minute) < WINDOW_START):
            d -= timedelta(days=1)
        return origin_for(d)
    if d.weekday() >= 5:
        raise NotAWindow(f"{ny:%A}")
    if not is_forecast_day(d):
        raise NotAWindow(f"{d} is an NQ holiday")
    if not (WINDOW_START <= (ny.hour, ny.minute) <= WINDOW_END):
        raise NotAWindow(f"New York time is {ny:%H:%M}, outside 08:00-09:25")
    o = origin_for(d)
    if latest and latest.get("origin_utc") and pd.Timestamp(latest["origin_utc"]) == pd.Timestamp(o):
        raise NotAWindow(f"today's forecast (origin {iso(o)}) was already made")
    return o


def load_until(origin: datetime, retries: int, wait: float) -> tuple[pd.DataFrame, list[dict]]:
    """Bars closed by origin + 1h whose last bar is the origin bar, retrying while Yahoo lags."""
    close = origin + timedelta(hours=1)
    err = None
    for i in range(1, retries + 1):
        try:
            df, rolls = gv.load_bars(close)
            if df.index[-1] == pd.Timestamp(origin):
                return df, rolls
            err = (f"the 07:00-08:00 NY bar ({iso(origin)}) is not in the data yet; "
                   f"last bar starts {iso(df.index[-1])}")
        except fetch.DataError as e:
            err = str(e)
        log.warning("attempt %d/%d: %s", i, retries, err)
        if i < retries:
            time.sleep(wait)
    raise fetch.DataError(err)


def history_line(f: dict) -> dict:
    return {"origin_utc": f["origin_utc"], "horizon_end_utc": f["horizon_end_utc"],
            "last_close": f["last_close"], "sigma_pct": f["sigma_pct"], "sigma_pts": f["sigma_pts"],
            "range80": f["range80"], "range68": f["range68"],
            "sigma_raw_pct": f["calibration"]["sigma_raw_pct"], "k": f["calibration"]["k"],
            "cash_sigma_pts": f["cash"]["sigma_pts"], "regime": f["regime"],
            "percentile": f["percentile"], "roll": f["roll_note"] is not None,
            "events": [i["kind"] for i in f["events"]["items"]], "event_today": f["events"]["today"]}


def append_history(path: Path, line: dict) -> str:
    """One line per origin: replace an existing line for the same origin, keep time order."""
    rows = []
    if path.exists():
        rows = [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
    replaced = any(r["origin_utc"] == line["origin_utc"] for r in rows)
    rows = [r for r in rows if r["origin_utc"] != line["origin_utc"]] + [line]
    rows.sort(key=lambda r: r["origin_utc"])
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
    os.replace(tmp, path)
    return f"{'replaced' if replaced else 'appended'} ({len(rows)} lines)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help="pretend it is this time (New York unless a zone is given)")
    ap.add_argument("--force", action="store_true",
                    help="ignore the time window and 'already made'; use the latest daily origin")
    ap.add_argument("--out", default=str(ROOT / "site" / "data"), help="output folder")
    ap.add_argument("--retries", type=int, default=RETRIES)
    ap.add_argument("--retry-wait", type=float, default=RETRY_WAIT)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)   # 404s for expired contracts are expected

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    latest_p, hist_p, status_p = out / "vol_latest.json", out / "vol_history.jsonl", out / "vol_status.json"
    now = gv.parse_now(args.now) or datetime.now(cal.UTC)
    latest = read_json(latest_p)

    try:
        origin = decide(now, latest, args.force)
    except NotAWindow as e:
        print(f"not a forecast window: {e}. No changes.")
        return 0

    print(f"forecasting from the bar starting {pd.Timestamp(origin).tz_convert(cal.NY):%a %d %b %Y %H:%M} NY")
    t0 = time.perf_counter()
    try:
        df, _ = load_until(origin, args.retries, args.retry_wait)
        prior = gv.prior_forecasts(df)
        f = gv.forecast_vol(df, prior=prior)
    except Exception as e:  # noqa: BLE001  -- any failure: record it, keep yesterday's forecast
        msg = f"{type(e).__name__}: {e}"
        print(f"FORECAST FAILED: {msg}", file=sys.stderr)
        write_json_atomic(status_p, {"ok": False, "checked_utc": iso(now), "error": msg, "stale": True,
                                     "forecast_origin_utc": latest.get("origin_utc") if latest else None},
                          indent=1)
        if latest:
            latest["stale"] = True
            write_json_atomic(latest_p, latest, indent=1)
        return 1

    today = pd.Timestamp(origin).tz_convert(cal.NY).date()
    f.update({"generated_utc": iso(datetime.now(cal.UTC)) if not args.now else iso(now),
              "forecast_time_utc": iso(origin + timedelta(hours=1)),
              "next_forecast_utc": next_forecast_utc(today),
              "stale": False})
    write_json_atomic(latest_p, f, indent=1)
    hist = append_history(hist_p, history_line(f))
    write_json_atomic(status_p, {"ok": True, "checked_utc": iso(now), "error": None, "stale": False,
                                 "forecast_origin_utc": f["origin_utc"]}, indent=1)
    gv.print_table(f)
    print(f"\nwrote {latest_p} ; history {hist} ; status ok ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
