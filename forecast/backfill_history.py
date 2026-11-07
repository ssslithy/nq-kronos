"""Seed history.jsonl with genuine past Kronos forecasts so the band check has data on day one.

For each of the last N origin bars whose +24h target has already closed, run Kronos on the
512 bars up to and including that origin bar only (no look-ahead), and write a history line
tagged "backfill": true. Lines that already exist for an origin bar are kept as they are.

    python forecast/backfill_history.py --count 32 --samples 30

Fewer samples than the live run keeps this quick; P10/P50/P90 are a little noisier.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch  # noqa: E402
import nq_calendar as cal  # noqa: E402
import run_forecast as rf  # noqa: E402

log = logging.getLogger("backfill")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=32, help="number of past origin bars")
    ap.add_argument("--samples", type=int, default=30)
    ap.add_argument("--device", default=os.environ.get("KRONOS_DEVICE", "cpu"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    bars = fetch.load_bars()
    df = bars.df
    last_bar = df.index[-1]
    # origins whose 24th tradable hour has already closed (target <= last available bar)
    origins = [t for t in df.index[rf.CONTEXT - 1:]
               if cal.next_tradable_hours(t.to_pydatetime(), rf.HORIZON)[-1] <= last_bar]
    origins = origins[-args.count:]
    existing = {r["last_bar_utc"] for r in rf.band_check.read_history(rf.HISTORY)}
    todo = [t for t in origins if rf.iso(t) not in existing]
    print(f"{len(origins)} origin bars {origins[0]:%a %d %b %H:%M} -> {origins[-1]:%a %d %b %H:%M} UTC; "
          f"{len(todo)} to compute, {args.samples} paths each, device {args.device}")

    predictor, _ = rf.load_predictor(args.device)
    new = []
    t0 = time.perf_counter()
    for i, t in enumerate(todo, 1):
        ctx = df.loc[:t].tail(rf.CONTEXT)                  # only data up to the origin bar
        future = cal.next_tradable_hours(t.to_pydatetime(), rf.HORIZON)
        seed = int(t.timestamp() // 3600) % (2**31)
        paths = rf.sample_paths(predictor, ctx, future, args.samples, seed)
        q = rf.quantiles(paths)
        new.append(rf.history_line(rf.iso(pd.Timestamp.now(tz="UTC")), t, ctx["close"].iloc[-1],
                                   q, future, args.samples, backfill=True))
        el = time.perf_counter() - t0
        print(f"  [{i}/{len(todo)}] {t:%a %d %b %H:%M} UTC  P50+24h {q['p50'][-1]:.0f}  "
              f"({el:.0f}s elapsed, ~{el / i * (len(todo) - i):.0f}s left)", flush=True)

    lines = rf.band_check.read_history(rf.HISTORY) + new
    lines.sort(key=lambda r: r["last_bar_utc"])
    tmp = rf.HISTORY.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in lines))
    os.replace(tmp, rf.HISTORY)
    bc = rf.write_band_check(bars, rf.iso(pd.Timestamp.now(tz="UTC")))
    print(f"history now {len(lines)} lines; band check: {bc['matured_24h']} matured +24h, "
          f"summary {bc['h24']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
