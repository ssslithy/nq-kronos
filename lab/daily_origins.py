"""Shared table of daily 07:00 NY origins for the vol contest (Part C) and the regime study (Part D).

One row per daily origin in the hourly bars that has a production GARCH sigma, with only
information known at the origin plus the realised outcome of the next 23 bars (HORIZON):

  origin_utc, pos, last           origin bar start, position in the bars, its close
  sigma_raw_pct, roll             production GARCH raw sigma (fitted on the 2,000 returns up to
                                  the origin) and whether the horizon touches a roll window
  move_pct, rv_fut                realised: 100 ln(close[p+23] / close[p]) and the sum of the 23
                                  squared hourly % returns after the origin (NaN until matured)
  rv_past                         the same sum for the 23 bars ending at the origin (known then)
  overnight_pct                   100 ln(max high / min low) over the 14 bars 18:00-08:00 NY
  vxn                             latest ^VXN daily close dated before the origin's NY date
  event_any, event_kind           scheduled release inside the horizon (NaN before the YAML's
                                  covered_from date: unknown, not "no event")
  dow                             weekday of the origin (0 = Monday)

    python lab/daily_origins.py          # rebuild lab/cache/daily_origins.parquet
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "forecast"))
import garch_vol as gv  # noqa: E402
import nq_calendar as cal  # noqa: E402

CACHE = ROOT / "lab" / "cache"
TABLE = CACHE / "daily_origins.parquet"
BARS = CACHE / "bars_hourly_23h.parquet"
H = gv.HORIZON
OVERNIGHT_BARS = 14


def daily_close(ticker: str) -> pd.Series:
    """Daily closes indexed by the bar's own date (a NY trading date); weekend-dated rows
    (Yahoo's in-progress session) dropped."""
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    d = yf.Ticker(ticker).history(period="max", interval="1d", auto_adjust=False)
    if d.empty:
        raise RuntimeError(f"no daily data for {ticker}")
    s = pd.Series(d["Close"].to_numpy(), index=pd.to_datetime(d.index.strftime("%Y-%m-%d")))
    return s[s.index.weekday < 5].dropna()


def last_before(s: pd.Series, day) -> float:
    """Latest value dated strictly before `day` (no look-ahead: a bar dated d closes at 17:00 NY d)."""
    x = s[s.index < pd.Timestamp(day)]
    return float(x.iloc[-1]) if len(x) else np.nan


def build(refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    if TABLE.exists() and BARS.exists() and not refresh:
        return pd.read_parquet(TABLE), pd.read_parquet(BARS)
    CACHE.mkdir(parents=True, exist_ok=True)
    bars, _ = gv.load_bars()
    prior = gv.prior_forecasts(bars, n=10 ** 6, model="garch")
    close = bars["close"].to_numpy()
    r = gv.log_returns(bars["close"]).to_numpy()
    r2 = np.nan_to_num(r) ** 2
    hi, lo = bars["high"].to_numpy(), bars["low"].to_numpy()
    vxn = daily_close("^VXN")
    covered_from, _, events = gv.load_events()
    rows = []
    for _, q in prior.iterrows():
        p = int(q["pos"])
        o = bars.index[p]
        day = o.tz_convert(cal.NY).date()
        matured = p + H < len(close)
        end = bars.index[p + H] if matured else None
        if day >= covered_from:
            e_end = (end if end is not None else o + pd.Timedelta(hours=H + 48)) + pd.Timedelta(hours=1)
            ev = [e for e in events if o + pd.Timedelta(hours=1) < e["time_utc"] <= e_end]
            event_any = float(bool(ev))
            kinds = [e["kind"] for e in ev]
            kind = next((k for k in ("FOMC", "CPI", "NFP") if k in kinds), "other" if kinds else "none")
        else:
            event_any, kind = np.nan, "unknown"
        rows.append({
            "origin_utc": o, "pos": p, "day": pd.Timestamp(day), "last": float(close[p]),
            "sigma_raw_pct": float(q["sigma_raw_pct"]), "roll": bool(q["roll"]),
            "move_pct": float(100 * np.log(close[p + H] / close[p])) if matured else np.nan,
            "rv_fut": float(r2[p + 1:p + H + 1].sum()) if matured else np.nan,
            "rv_past": float(r2[p - H + 1:p + 1].sum()),
            "overnight_pct": float(100 * np.log(hi[p - OVERNIGHT_BARS + 1:p + 1].max()
                                                / lo[p - OVERNIGHT_BARS + 1:p + 1].min())),
            "vxn": last_before(vxn, day), "event_any": event_any, "event_kind": kind,
            "dow": int(o.tz_convert(cal.NY).weekday()),
        })
    t = pd.DataFrame(rows)
    t.to_parquet(TABLE)
    bars.to_parquet(BARS)
    return t, bars


def eval_origins(t: pd.DataFrame, n: int = 250) -> pd.DataFrame:
    """The last n matured origins: the same set the production backfill scores."""
    return t[t["move_pct"].notna()].tail(n)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    t, b = build(refresh=True)
    print(f"{len(t)} origins {t['origin_utc'].iloc[0]:%Y-%m-%d} -> {t['origin_utc'].iloc[-1]:%Y-%m-%d}; "
          f"matured {t['move_pct'].notna().sum()}; VXN missing {t['vxn'].isna().sum()}; "
          f"event known {t['event_any'].notna().sum()}")
