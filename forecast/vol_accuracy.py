"""Accuracy log for the daily GARCH 24h vol forecast, and the 1-year backfill.

site/data/vol_history.jsonl holds one line per daily forecast (one per origin). Once the
forecast's +24h bar has closed, the line gets:

  move_pct    100 x log(close of the +24h bar / close of the origin bar), signed
  inside80    whether that close was inside the 80% range (|move| <= 1.2816 sigma)
  matured_utc when it was recorded

Both closes come from the same roll-adjusted series, so a roll between the two bars doesn't
count as a move (the same rule as the calibration factor k). The +24h bar is the one starting
at horizon_end_utc; if Yahoo has no bar there, the line gets "missing": true and is skipped.

run_vol.py updates the log on every forecast run, using the bars it already loaded. Those end
with the 07:00 NY bar, so a forecast matures on the second run after it (yesterday's horizon
ends with today's 08:00-09:00 NY bar).

Backfill (run once; re-running keeps live lines and only fills gaps):

    python forecast/vol_accuracy.py --backfill              # last 252 trading days
    python forecast/vol_accuracy.py --backfill --days 30 --out /tmp/x

Each backfilled forecast is made by forecast_vol() on the bars up to its own origin, with
the calibration factor and regime percentile computed only from forecasts whose 24 bars had
closed by then, so nothing after the origin is used except the roll back-adjustment (which
shifts price levels, not returns). Backfilled lines carry "backfill": true; their prices
(last_close, ranges) are on the back-adjusted scale, so only sigma_pct, move_pct and inside80
are comparable with live lines.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import garch_vol as gv  # noqa: E402
import nq_calendar as cal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PRIOR_N = 330          # prior forecasts per origin, as prior_forecasts() keeps
BACKFILL_DAYS = 252
BACKFILL_SIMS = 2000   # the simulation is a diagnostic only, not in the history line


def iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- history file ----------------------------------------------------------------------

def history_line(f: dict) -> dict:
    return {"origin_utc": f["origin_utc"], "horizon_end_utc": f["horizon_end_utc"],
            "last_close": f["last_close"], "sigma_pct": f["sigma_pct"], "sigma_pts": f["sigma_pts"],
            "range80": f["range80"], "range68": f["range68"], "range95": f["range95"],
            "m95": f["multipliers"]["m95"], "m99": f["multipliers"]["m99"],
            "sigma_raw_pct": f["calibration"]["sigma_raw_pct"], "k": f["calibration"]["k"],
            "cash_sigma_pts": f["cash"]["sigma_pts"], "cash_sigma_pct": f["cash"]["sigma_pct"],
            "cash_hours": f["cash"]["hours"], "regime": f["regime"],
            "percentile": f["percentile"], "roll": f["roll_note"] is not None,
            "events": [i["kind"] for i in f["events"]["items"]] if f["events"]["loaded"] else None,
            "event_today": f["events"]["today"],
            "events_today": [i["kind"] for i in f["events"]["items"] if i["today"]] if f["events"]["loaded"] else None}


def read_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]


def write_history(path: Path, rows: list[dict]) -> None:
    """One line per origin, in time order (a later row for the same origin wins)."""
    by = {pd.Timestamp(r["origin_utc"]): r for r in rows}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(by[k], separators=(",", ":")) + "\n" for k in sorted(by)))
    os.replace(tmp, path)


def upsert(rows: list[dict], line: dict) -> tuple[list[dict], bool]:
    """Replace the row for line's origin (or add it). Returns (rows, replaced)."""
    o = pd.Timestamp(line["origin_utc"])
    kept = [r for r in rows if pd.Timestamp(r["origin_utc"]) != o]
    return kept + [line], len(kept) < len(rows)


# ---- accuracy ----------------------------------------------------------------------------

def update_accuracy(rows: list[dict], bars: pd.DataFrame, now: datetime | None = None) -> int:
    """Fill move_pct / inside80 on rows whose +24h bar has closed in `bars`. Returns the count."""
    if bars.empty:
        return 0
    close, last = bars["close"], bars.index[-1]
    stamp = iso(now or datetime.now(timezone.utc))
    n = 0
    for r in rows:
        if r.get("missing"):
            continue
        if "move_pct" not in r or r["move_pct"] is None:
            o, e = pd.Timestamp(r["origin_utc"]), pd.Timestamp(r["horizon_end_utc"])
            if e > last:
                continue                                # +24h bar not closed yet
            if o not in close.index or e not in close.index:
                r["missing"] = True                     # Yahoo has no bar there: skip for good
                continue
            r["move_pct"] = round(100 * float(np.log(close[e] / close[o])), 4)
            r["matured_utc"] = stamp
            n += 1
        a = abs(r["move_pct"])
        r["inside80"] = bool(a <= gv.Z80 * r["sigma_pct"])
        if r.get("m95") is not None:                    # lines from before the 95% band lack these
            r["inside95"] = bool(a <= r["m95"] * r["sigma_pct"])
            r["inside99"] = bool(a <= r["m99"] * r["sigma_pct"])
    n += update_cash(rows, bars)
    return n


# ---- cash session (09:00-16:00 NY on the forecast day) ------------------------------------

CASH_START_NY, CASH_END_NY = 8, 15        # close of the 08:00-09:00 bar -> close of the 15:00-16:00 bar
K_CASH_DAYS, K_CASH_MIN = 250, 60


def _ny_bar(day, hour: int) -> pd.Timestamp:
    return pd.Timestamp(datetime(day.year, day.month, day.day, hour), tz=cal.NY).tz_convert("UTC")


def cash_outcome(r: dict, bars: pd.DataFrame) -> dict | None:
    """Cash-session result for one forecast, or None while the session hasn't finished.
    Cash move = 100 ln(close of the 15:00-16:00 NY bar / close of the 08:00-09:00 NY bar) on
    the forecast's own New York date; high/low from the 09:00-15:00 NY bars."""
    day = pd.Timestamp(r["origin_utc"]).tz_convert(cal.NY).date()
    t0, t1 = _ny_bar(day, CASH_START_NY), _ny_bar(day, CASH_END_NY)
    if t1 > bars.index[-1]:
        return None
    sess = bars.loc[(bars.index > t0) & (bars.index <= t1)]
    if t0 not in bars.index or t1 not in bars.index or len(sess) != CASH_END_NY - CASH_START_NY:
        return {"cash_missing": True}                       # early close or missing Yahoo bars
    base = float(bars.at[t0, "close"])
    return {"cash_move_pct": round(100 * float(np.log(bars.at[t1, "close"] / base)), 4),
            "cash_high_pct": round(100 * float(np.log(sess["high"].max() / base)), 4),
            "cash_low_pct": round(100 * float(np.log(sess["low"].min() / base)), 4)}


def assign_k_cash(rows: list[dict]) -> None:
    """k_cash per row = RMS(cash move / raw cash sigma) over the trailing K_CASH_DAYS sessions
    completed before that row's origin (earlier NY dates only), min K_CASH_MIN; else None."""
    done = []                                               # (date, z) of completed sessions, in order
    rows_sorted = sorted(rows, key=lambda r: pd.Timestamp(r["origin_utc"]))
    for r in rows_sorted:
        day = pd.Timestamp(r["origin_utc"]).tz_convert(cal.NY).date()
        z = [v for d, v in done if d < day][-K_CASH_DAYS:]
        r["k_cash"] = round(float(np.sqrt(np.mean(np.square(z)))), 4) if len(z) >= K_CASH_MIN else None
        if r.get("cash_move_pct") is not None and r.get("cash_sigma_pct"):
            done.append((day, r["cash_move_pct"] / r["cash_sigma_pct"]))


def cash_flags(r: dict) -> None:
    """inside / touch for the raw cash sigma and, where k_cash exists, the calibrated one."""
    if r.get("cash_move_pct") is None or not r.get("cash_sigma_pct"):
        return
    for suffix, s in (("", r["cash_sigma_pct"]),
                      ("_cal", r["cash_sigma_pct"] * r["k_cash"] if r.get("k_cash") else None)):
        if s is None:
            r.pop(f"cash_inside80{suffix}", None); r.pop(f"cash_touch{suffix}", None)
            continue
        lim = gv.Z80 * s
        r[f"cash_inside80{suffix}"] = bool(abs(r["cash_move_pct"]) <= lim)
        r[f"cash_touch{suffix}"] = bool(r["cash_high_pct"] > lim or r["cash_low_pct"] < -lim)


def update_cash(rows: list[dict], bars: pd.DataFrame) -> int:
    """Fill cash outcomes for finished sessions, then k_cash and the flags on every row."""
    n = 0
    for r in rows:
        if r.get("cash_move_pct") is not None or r.get("cash_missing") or r.get("cash_hours") != 7:
            continue
        out = cash_outcome(r, bars)
        if out:
            r.update(out)
            n += "cash_move_pct" in out
    assign_k_cash(rows)
    for r in rows:
        cash_flags(r)
    return n


def hit_rate(rows: list[dict], key: str = "inside80") -> tuple[float | None, int]:
    m = [r[key] for r in rows if isinstance(r.get(key), bool)]
    return (100 * sum(m) / len(m) if m else None), len(m)


# ---- backfill ----------------------------------------------------------------------------

def backfill(bars: pd.DataFrame, days: int = BACKFILL_DAYS, skip: set | None = None,
             sims: int = BACKFILL_SIMS, progress: bool = True) -> tuple[list[dict], list[str]]:
    """History lines for the last `days` daily origins in `bars`, each forecast as of its own
    origin (see the module docstring). Origins in `skip` (timestamps) are not recomputed."""
    skip = skip or set()
    t0 = time.perf_counter()
    prior_all = gv.prior_forecasts(bars, n=10 ** 6)            # each sigma uses data to its origin
    if progress:
        print(f"  {len(prior_all)} daily raw sigmas in {time.perf_counter() - t0:.0f}s", flush=True)
    pos = gv.daily_origins(bars)
    pos = pos[pos >= gv.WINDOW + 1][-days:]
    out, skipped = [], []
    for j, p in enumerate(pos):
        o = bars.index[p]
        if o in skip:
            continue
        prior = prior_all[prior_all["pos"] < p].tail(PRIOR_N).copy()
        prior.loc[prior["pos"] + gv.HORIZON > p, "move_pct"] = np.nan    # not matured by then
        try:
            f = gv.forecast_vol(bars.iloc[:p + 1], sims=sims, prior=prior)
        except ValueError as e:                                         # too few matured for k
            skipped.append(f"{o:%Y-%m-%d}: {e}")
            continue
        line = history_line(f)
        line["backfill"] = True
        out.append(line)
        if progress and (j + 1) % 25 == 0:
            print(f"  {j + 1}/{len(pos)} origins ({time.perf_counter() - t0:.0f}s)", flush=True)
    return out, skipped


def report(rows: list[dict]) -> None:
    m = [r for r in rows if isinstance(r.get("inside80"), bool)]
    if not m:
        print("no matured forecasts")
        return
    half = len(m) // 2
    print(f"  hit rates over {len(m)} matured forecasts ({m[0]['origin_utc'][:10]} to {m[-1]['origin_utc'][:10]}):")
    print(f"    {'':12} {'80% range':>10} {'95% range':>10} {'99% stress':>11}")
    for name, part in (("all", m), ("older half", m[:half]), ("newer half", m[half:]), ("last 60", m[-60:])):
        cells = []
        for key in ("inside80", "inside95", "inside99"):
            h, n = hit_rate(part, key)
            cells.append(f"{h:9.1f}%" if h is not None else f"{'—':>10}")
        print(f"    {name:12} {cells[0]:>10} {cells[1]:>10} {cells[2]:>11}   (n {len(part)})")
    for name, sel in (("no roll in horizon", [r for r in m if not r.get("roll")]),
                      ("release in horizon", [r for r in m if r.get("events")]),
                      ("release on the day", [r for r in m if r.get("event_today")])):
        if sel:
            print(f"    {name}: {hit_rate(sel)[0]:.1f}% of {len(sel)}")
    z = np.array([r["move_pct"] / r["sigma_pct"] for r in m])
    print(f"  RMS(move / sigma) {np.sqrt(np.mean(z ** 2)):.3f} (1.000 = sigma right on average); "
          f"missing +24h bar: {sum(1 for r in rows if r.get('missing'))}")


def cash_table(rows: list[dict]) -> list[dict]:
    """Rows of the cash-session results table (raw and, where k_cash exists, calibrated)."""
    m = [r for r in rows if r.get("cash_move_pct") is not None and r.get("cash_sigma_pct")]
    half = len(m) // 2
    groups = [("all", m), ("older half", m[:half]), ("newer half", m[half:]), ("last 60", m[-60:]),
              ("release that day", [r for r in m if r.get("events_today")]),
              ("no release that day", [r for r in m if r.get("events_today") == []])]
    out = []
    for name, g in groups:
        row = {"group": name, "n": len(g)}
        for suffix, k in (("", lambda r: 1.0), ("_cal", lambda r: r.get("k_cash"))):
            gg = [r for r in g if k(r)]
            if not gg:
                continue
            z = np.array([r["cash_move_pct"] / (r["cash_sigma_pct"] * k(r)) for r in gg])
            row[f"inside{suffix}"] = hit_rate(gg, f"cash_inside80{suffix}")[0]
            row[f"rms{suffix}"] = float(np.sqrt(np.mean(z ** 2)))
            row[f"touch{suffix}"] = hit_rate(gg, f"cash_touch{suffix}")[0]
            row[f"n{suffix}"] = len(gg)
        out.append(row)
    return out


def cash_report(rows: list[dict]) -> None:
    t = cash_table(rows)
    if not t or not t[0]["n"]:
        print("  no finished cash sessions")
        return
    m = [r for r in rows if r.get("cash_move_pct") is not None]
    print(f"  cash session 09:00-16:00 NY, {t[0]['n']} sessions ({m[0]['origin_utc'][:10]} to {m[-1]['origin_utc'][:10]}); "
          f"missing/early close: {sum(1 for r in rows if r.get('cash_missing'))}")
    print(f"    {'':28} {'raw cash σ':^26}   {'calibrated (× k_cash)':^33}")
    print(f"    {'':28} {'inside 80%':>10} {'RMS z':>7} {'touch':>7}   {'inside 80%':>10} {'RMS z':>7} {'touch':>7} {'n':>5}")
    for r in t:
        raw = f"{r['inside']:9.1f}% {r['rms']:7.3f} {r['touch']:6.1f}%" if "inside" in r else f"{'—':>26}"
        cal_ = (f"{r['inside_cal']:9.1f}% {r['rms_cal']:7.3f} {r['touch_cal']:6.1f}% {r['n_cal']:5d}"
                if "inside_cal" in r else f"{'—':>33}")
        label = f"{r['group']} (n {r['n']})"
        print(f"    {label:28} {raw}   {cal_}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backfill", action="store_true", help="forecast the last --days daily origins")
    ap.add_argument("--days", type=int, default=BACKFILL_DAYS)
    ap.add_argument("--all", action="store_true",
                    help="with --backfill: recompute live lines too (they keep backfill=false)")
    ap.add_argument("--now", help="use bars up to this time (New York unless a zone is given)")
    ap.add_argument("--out", default=str(ROOT / "site" / "data"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    path = Path(args.out) / "vol_history.jsonl"
    rows = read_history(path)
    bars, _ = gv.load_bars(gv.parse_now(args.now))
    print(f"{len(bars):,} bars {bars.index[0]:%Y-%m-%d} -> {bars.index[-1]:%Y-%m-%d %H:%M} UTC; "
          f"{len(rows)} lines in {path.name}")
    if args.backfill:
        live = {pd.Timestamp(r["origin_utc"]) for r in rows if not r.get("backfill")}
        new, skipped = backfill(bars, args.days, skip=set() if args.all else live)
        for line in new:
            if pd.Timestamp(line["origin_utc"]) in live:
                line["backfill"] = False                # recomputed, but it was published live
            rows, _ = upsert(rows, line)
        print(f"  backfilled {len(new)} forecasts, kept {len(live)} live line(s)"
              + (f", skipped {len(skipped)} (first: {skipped[0]})" if skipped else ""))
    n = update_accuracy(rows, bars)
    rows.sort(key=lambda r: pd.Timestamp(r["origin_utc"]))
    write_history(path, rows)
    print(f"  {n} newly matured; wrote {len(rows)} lines to {path}")
    report(rows)
    cash_report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
