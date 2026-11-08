"""Phase 6a: walk-forward Kronos forecasts for the strategy lab.

At every 3rd closed bar in the last N months, forecast the next 24 hours with Kronos-small
using ONLY the 512 bars up to that bar (30 sampled paths). Saves p10/p25/p50/p75/p90 at
+1..+24h and the share of paths ending above the last close to lab/cache/kronos.parquet,
writing after every batch. Reruns skip origins already in the cache.

    python lab/build_forecasts.py --time 10          # time 10 forecasts, print an estimate
    caffeinate -i python lab/build_forecasts.py --months 6
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
import run_forecast as rf  # noqa: E402  (forecast/ is on sys.path via common)

SAMPLES = 30


def default_device() -> str:
    import torch
    return "mps" if torch.backends.mps.is_available() else "cpu"


def forecast_batch(predictor, df: pd.DataFrame, batch_origins, samples: int, seed: int) -> list[dict]:
    """Several origins in one Kronos call: each origin's context repeated `samples` times,
    sample_count=1, so every row is an independent path (nothing averaged)."""
    import torch
    torch.manual_seed(seed)
    np.random.seed(seed)
    df_list, xts, yts, meta = [], [], [], []
    for t in batch_origins:
        ctx = df.loc[:t].tail(c.CONTEXT)
        fut = c.cal.next_tradable_hours(t.to_pydatetime(), c.HORIZON)
        x_df = ctx[["open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)
        x_ts = pd.Series(ctx.index.tz_convert(c.cal.NY))
        y_ts = pd.Series(pd.DatetimeIndex(fut).tz_convert(c.cal.NY))
        df_list += [x_df] * samples
        xts += [x_ts] * samples
        yts += [y_ts] * samples
        meta.append((t, float(ctx["close"].iloc[-1])))
    out = predictor.predict_batch(df_list=df_list, x_timestamp_list=xts, y_timestamp_list=yts,
                                  pred_len=c.HORIZON, T=rf.T, top_p=rf.TOP_P, sample_count=1,
                                  verbose=False)
    closes = np.vstack([o["close"].to_numpy() for o in out]).reshape(len(batch_origins), samples, c.HORIZON)
    rows = []
    for (t, last), paths in zip(meta, closes):
        row = {"origin": t, "last_close": round(last, 2), "samples": samples, "seed": seed,
               "share_up": float((paths[:, -1] > last).mean())}
        for q in c.QS:
            v = np.percentile(paths, q, axis=0)
            row.update({f"p{q}_{k}": round(float(v[k - 1]), 2) for k in range(1, c.HORIZON + 1)})
        rows.append(row)
    return rows


def load_cache() -> pd.DataFrame:
    if c.KRONOS.exists():
        return pd.read_parquet(c.KRONOS)
    return pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=float, default=6)
    ap.add_argument("--samples", type=int, default=SAMPLES)
    ap.add_argument("--batch", type=int, default=4, help="origins per Kronos call")
    ap.add_argument("--device", default=None)
    ap.add_argument("--time", type=int, default=0, help="only time this many forecasts, then estimate")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    device = args.device or default_device()

    df, _ = c.load_bars()
    origins = c.origins(df, args.months)
    cache = load_cache()
    done = set(pd.DatetimeIndex(cache["origin"])) if len(cache) else set()
    todo = [t for t in origins if t not in done]
    print(f"{args.months:g}-month walk-forward: {len(origins)} origins "
          f"({origins[0]:%Y-%m-%d %H:%M} -> {origins[-1]:%Y-%m-%d %H:%M} UTC), "
          f"{len(origins) - len(todo)} cached, {len(todo)} to run; {args.samples} paths, "
          f"batch {args.batch}, device {device}", flush=True)
    if args.time:
        todo = todo[:args.time]
    if not todo:
        return 0

    predictor, _ = rf.load_predictor(device)
    rows, t0, n_done = [], time.perf_counter(), 0
    for i in range(0, len(todo), args.batch):
        chunk = todo[i:i + args.batch]
        seed = int(chunk[0].timestamp() // 3600) % (2**31)
        rows += forecast_batch(predictor, df, chunk, args.samples, seed)
        n_done += len(chunk)
        # save as we go
        new = pd.DataFrame(rows)
        merged = pd.concat([cache, new], ignore_index=True) if len(cache) else new
        merged = merged.drop_duplicates("origin", keep="last").sort_values("origin").reset_index(drop=True)
        c.write_parquet_atomic(merged, c.KRONOS)
        el = time.perf_counter() - t0
        per = el / n_done
        print(f"  {n_done}/{len(todo)}  last origin {chunk[-1]:%Y-%m-%d %H:%M}  "
              f"{per:.1f}s/forecast  elapsed {el / 60:.1f} min  ~{per * (len(todo) - n_done) / 60:.0f} min left",
              flush=True)

    per = (time.perf_counter() - t0) / n_done
    if args.time:
        full = len([t for t in origins if t not in done])
        print(f"\nTIMING: {per:.1f} s per forecast on {device} (batch {args.batch}). "
              f"Remaining {args.months:g}-month origins: {full - n_done} -> estimated "
              f"{per * (full - n_done) / 3600:.1f} h. 2-year run would be ~"
              f"{per * len(c.origins(df, 24)) / 3600:.1f} h in total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
