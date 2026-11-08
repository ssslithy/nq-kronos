"""Phase 6b: baseline forecast bands for the same origins as the Kronos walk-forward.

GARCH(1,1), Student-t errors, zero mean, on hourly log returns (x100), refitted at every
origin on the previous GARCH_WINDOW returns only (no look-ahead). 1,000 simulated 24-step
paths per origin -> the same quantiles as Kronos.

Naive: median = last close ("no change"); band from the trailing 20-trading-day realised
volatility of hourly log returns, scaled by sqrt(h) under a normal assumption.

    python lab/baselines.py --months 6
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

GARCH_WINDOW = 2000          # ~4 months of hourly returns
SIMS = 1000
NAIVE_BARS = 20 * 23         # ~20 trading days of hourly bars


def _row(origin, last, paths_or_q, share_up, extra) -> dict:
    row = {"origin": origin, "last_close": round(last, 2), "share_up": share_up, **extra}
    for q in c.QS:
        v = paths_or_q[q]
        row.update({f"p{q}_{k}": round(float(v[k - 1]), 2) for k in range(1, c.HORIZON + 1)})
    return row


def garch_bands(df: pd.DataFrame, origins: pd.DatetimeIndex) -> pd.DataFrame:
    from arch import arch_model
    lr = 100 * np.log(df["close"]).diff()
    pos_of = {t: i for i, t in enumerate(df.index)}
    rows, t0 = [], time.perf_counter()
    for n, t in enumerate(origins, 1):
        i = pos_of[t]
        r = lr.iloc[max(1, i - GARCH_WINDOW + 1): i + 1].to_numpy()
        last = float(df["close"].iloc[i])
        am = arch_model(r, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = am.fit(disp="off", show_warning=False)
            seed = int(t.timestamp() // 3600) % (2**31)
            fc = res.forecast(horizon=c.HORIZON, method="simulation", simulations=SIMS,
                              reindex=False, random_state=np.random.default_rng(seed))
        sims = fc.simulations.values[-1]                       # (SIMS, HORIZON) returns in %
        paths = last * np.exp(np.cumsum(sims / 100, axis=1))
        q = {p: np.percentile(paths, p, axis=0) for p in c.QS}
        rows.append(_row(t, last, q, float((paths[:, -1] > last).mean()),
                         {"omega": res.params["omega"], "alpha": res.params["alpha[1]"],
                          "beta": res.params["beta[1]"], "nu": res.params["nu"]}))
        if n % 100 == 0:
            print(f"  GARCH {n}/{len(origins)}  {time.perf_counter() - t0:.0f}s", flush=True)
    return pd.DataFrame(rows)


def naive_bands(df: pd.DataFrame, origins: pd.DatetimeIndex) -> pd.DataFrame:
    lr = np.log(df["close"]).diff()
    sig = lr.rolling(NAIVE_BARS).std()                         # uses bars up to and incl. t
    rows = []
    h = np.arange(1, c.HORIZON + 1)
    for t in origins:
        last, s = float(df.at[t, "close"]), float(sig.at[t])
        q = {p: last * np.exp(norm.ppf(p / 100) * s * np.sqrt(h)) for p in c.QS}
        rows.append(_row(t, last, q, 0.5, {"sigma_h": s}))
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=float, default=6)
    args = ap.parse_args()
    df, _ = c.load_bars()
    origins = c.origins(df, args.months)
    print(f"baselines for {len(origins)} origins ({origins[0]:%Y-%m-%d} -> {origins[-1]:%Y-%m-%d})", flush=True)
    t0 = time.perf_counter()
    c.write_parquet_atomic(naive_bands(df, origins), c.NAIVE)
    print(f"  naive done ({time.perf_counter() - t0:.1f}s)", flush=True)
    c.write_parquet_atomic(garch_bands(df, origins), c.GARCH)
    print(f"  GARCH done ({time.perf_counter() - t0:.0f}s) -> {c.GARCH.relative_to(c.ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
