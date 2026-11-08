"""Calibration study for the daily 07:00 NY GARCH vol forecast (forecast/garch_vol.py).

Every 07:00 NY daily origin in the lab's cached bars with 2,000 prior returns. Forecasts whose
24 bars touch a roll window are left out (as in the Phase 6 lab). z = actual 24-bar log move / sigma.

Variants
  A  lab model: GARCH on all hourly returns (gap returns included)
  B  gap returns (daily 17:00 break, weekends, holidays) dropped from the fit; the horizon's
     gap steps are treated as ordinary GARCH hours
  C  as B, but each gap step in the horizon gets its own empirical variance: mean squared gap
     return of that kind (daily break: last 60; weekend/holiday: last 26) before the origin

    python lab/garch_vol_calibration.py
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
import garch_vol as gv  # noqa: E402

Z80 = 1.2816
N_DAILY_GAPS, N_LONG_GAPS = 60, 26


def prepare():
    df, rolls = c.load_bars()
    lr = gv.log_returns(df["close"])
    dt = df.index.to_series().diff()
    gap = (dt > pd.Timedelta(hours=1)).to_numpy()
    long_gap = (dt > pd.Timedelta(hours=2)).to_numpy()
    o = gv.daily_origins(df)
    o = o[(o >= gv.WINDOW) & (o + gv.HORIZON < len(df))]
    blocked = c.in_roll_window(df.index, rolls)
    touch = np.array([blocked[p + 1:p + gv.HORIZON + 1].any() for p in o])
    return df, lr, gap, long_gap, o, touch


def sigmas(df, lr, gap, long_gap, o) -> pd.DataFrame:
    close = df["close"].to_numpy()
    r_all = lr.to_numpy()
    rows = []
    for p in o:
        move = 100 * np.log(close[p + gv.HORIZON] / close[p])
        # A
        vA = gv.step_variances(gv.fit(r_all[p - gv.WINDOW + 1:p + 1]))
        # B / C: fit on the last WINDOW non-gap returns up to p
        ng = np.flatnonzero(~gap[1:p + 1]) + 1
        resB = gv.fit(r_all[ng[-gv.WINDOW:]])
        vB = gv.step_variances(resB)
        hgap = gap[p + 1:p + gv.HORIZON + 1]
        hlong = long_gap[p + 1:p + gv.HORIZON + 1]
        n_ng = int((~hgap).sum())
        dg = np.flatnonzero(gap[1:p + 1] & ~long_gap[1:p + 1]) + 1
        lg = np.flatnonzero(long_gap[1:p + 1]) + 1
        v_daily = float(np.mean(r_all[dg[-N_DAILY_GAPS:]] ** 2))
        v_long = float(np.mean(r_all[lg[-N_LONG_GAPS:]] ** 2))
        vC = vB[:n_ng].sum() + v_daily * int((hgap & ~hlong).sum()) + v_long * int(hlong.sum())
        rows.append({"pos": p, "origin": df.index[p], "move": move,
                     "sA": np.sqrt(vA.sum()), "sB": np.sqrt(vB.sum()), "sC": np.sqrt(vC),
                     "gap_steps": int(hgap.sum()), "long_steps": int(hlong.sum())})
    return pd.DataFrame(rows)


def rolling_k(z: np.ndarray, pos: np.ndarray, n: int = 250, min_n: int = 60) -> np.ndarray:
    """k_i = RMS(z_j) over the last n forecasts j matured before origin i (pos_j + 24 <= pos_i)."""
    k = np.full(len(z), np.nan)
    for i in range(len(z)):
        m = np.flatnonzero(pos + gv.HORIZON <= pos[i])[-n:]
        if len(m) >= min_n:
            k[i] = np.sqrt(np.mean(z[m] ** 2))
    return k


def stats(z: np.ndarray) -> str:
    z = z[~np.isnan(z)]
    return f"n {len(z):>3}  RMS(z) {np.sqrt(np.mean(z ** 2)):.3f}  inside ±1.28σ {100 * np.mean(np.abs(z) <= Z80):5.1f}%"


def main() -> int:
    t0 = time.perf_counter()
    df, lr, gap, long_gap, o, touch = prepare()
    s = sigmas(df, lr, gap, long_gap, o)
    s["roll"] = touch
    s.to_parquet(c.CACHE / "garch_vol_daily.parquet")
    print(f"{len(s)} daily 07:00 NY origins {s.origin.iloc[0]:%Y-%m-%d} -> {s.origin.iloc[-1]:%Y-%m-%d}; "
          f"{int(touch.sum())} touch a roll window ({time.perf_counter() - t0:.0f}s)")
    for v in "ABC":
        z_all = (s.move / s["s" + v]).to_numpy()
        print(f"  {v} incl. roll windows: {stats(z_all)}")
    ok = s[~s.roll].reset_index(drop=True)
    half = len(ok) // 2
    print(f"\nexcluding roll windows: older half {ok.origin.iloc[0]:%Y-%m-%d} -> {ok.origin.iloc[half - 1]:%Y-%m-%d}, "
          f"newer half {ok.origin.iloc[half]:%Y-%m-%d} -> {ok.origin.iloc[-1]:%Y-%m-%d}")
    for v in "ABC":
        z = (ok.move / ok["s" + v]).to_numpy()
        print(f"  {v}  older: {stats(z[:half])}   newer: {stats(z[half:])}")

    # ---- rolling calibration factor k on variant A (the lab model) -------------------
    sims_q = sim_quantiles(df, ok.pos.to_numpy())
    z = (ok.move / ok.sA).to_numpy()
    pos = ok.pos.to_numpy()
    for n_min, label in ((60, "k over trailing <=250 (min 60)"), (250, "k over trailing 250 (full)")):
        k = rolling_k(z, pos, n=250, min_n=n_min)
        ok[f"k{n_min}"] = k
        print(f"\n{label}: available for {int((~np.isnan(k)).sum())} of {len(k)} origins, "
              f"k range {np.nanmin(k):.3f}-{np.nanmax(k):.3f}, latest {k[-1]:.3f}")
        for name, sl in (("older", slice(0, half)), ("newer", slice(half, None))):
            kk, zz, has = k[sl], z[sl], ~np.isnan(k[sl])
            if not has.any():
                print(f"  {name}: no origin has enough history")
                continue
            q10, q90 = sims_q[sl, 0][has], sims_q[sl, 1][has]
            mv = ok.move.to_numpy()[sl][has]
            raw_q = 100 * np.mean((mv >= q10) & (mv <= q90))
            cal_q = 100 * np.mean((mv >= kk[has] * q10) & (mv <= kk[has] * q90))
            print(f"  {name:5} raw : {stats(zz[has])}   sim P10-P90 {raw_q:5.1f}%")
            print(f"  {name:5} cal : {stats(zz[has] / kk[has])}   sim P10-P90 x k {cal_q:5.1f}%")
    ok.assign(q10=sims_q[:, 0], q90=sims_q[:, 1]).to_parquet(c.CACHE / "garch_vol_daily_ok.parquet")
    return 0


def sim_quantiles(df, pos, sims: int = 2000) -> np.ndarray:
    """Simulated +24h P10/P90 as log moves (%) from the last close, variant A."""
    close = df["close"]
    lr = gv.log_returns(close).to_numpy()
    out = np.empty((len(pos), 2))
    for i, p in enumerate(pos):
        res = gv.fit(lr[p - gv.WINDOW + 1:p + 1])
        end = gv.simulate(res, 1.0, sims, gv.seed_for(df.index[p]))[:, -1]
        out[i] = 100 * np.log(np.percentile(end, [10, 90]))
    return out


if __name__ == "__main__":
    sys.exit(main())
