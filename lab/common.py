"""Shared helpers for the offline strategy lab (Phase 6). Nothing here touches site/."""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "lab"
CACHE = LAB / "cache"
RESULTS = LAB / "results"
sys.path.insert(0, str(ROOT / "forecast"))
sys.path.insert(0, str(ROOT / "vendor" / "kronos"))

import fetch  # noqa: E402
import nq_calendar as cal  # noqa: E402

BARS = CACHE / "bars.parquet"
ROLLS = CACHE / "rolls.json"
KRONOS = CACHE / "kronos.parquet"
GARCH = CACHE / "garch.parquet"
NAIVE = CACHE / "naive.parquet"

CONTEXT, HORIZON = 512, 24
STEP = 3                       # forecast at every 3rd closed bar
QS = (10, 25, 50, 75, 90)
log = logging.getLogger("lab")


def qcols(q: int) -> list[str]:
    return [f"p{q}_{k}" for k in range(1, HORIZON + 1)]


# Quarterly NQ calendar spread as a fraction of price, from the one roll Yahoo still lets us
# measure exactly (Sep 2026: +294.00 pts at ~29,560 = 0.99%). NQ is always in contango
# (rates > dividend yield), so a roll gap is positive.
CARRY = 294.0 / 29560.0
ROLL_PAD_BEFORE, ROLL_PAD_AFTER = pd.Timedelta(days=1), pd.Timedelta(days=4)


def roll_window(r: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Period around a roll in which Yahoo may switch contracts (trading is blocked here)."""
    return (pd.Timestamp(r["roll_date"], tz="UTC") - ROLL_PAD_BEFORE,
            pd.Timestamp(r["expiry"], tz="UTC") + ROLL_PAD_AFTER)


def lab_roll_adjust(df: pd.DataFrame, days: int) -> tuple[pd.DataFrame, list[dict]]:
    """Back-adjust every quarterly roll in ~2 years of NQ=F.

    Where Yahoo still has the incoming contract, use the site's measured method
    (fetch._find_switch). Otherwise (expired contracts 404 on Yahoo): the switch bar is the
    largest *upward* close-to-close jump between the roll date - 1 day and expiry + 4 days,
    and the gap is the carry estimate CARRY x price rather than the jump itself (the jump
    also contains that hour's real move). On the verifiable Sep 2026 roll this finds the
    exact switch bar and a gap of ~288 vs the true 294 (the raw jump said 459.75).
    The site's fetch.roll_adjust is left untouched.
    """
    df = df.copy()
    rolls: list[dict] = []
    for r in reversed(cal.roll_calendar(df.index[0].date(), df.index[-1].date())):
        lo, hi = roll_window(r)
        if hi < df.index[0] or lo > df.index[-1]:
            continue
        pos, gap, method = None, None, None
        try:
            inc = fetch._normalise(fetch._download(r["incoming"], days, attempts=1))
            for e in rolls:
                inc.loc[inc.index < pd.Timestamp(e["switch_utc"]), fetch.PRICE_COLS] += e["gap"]
            pos = fetch._find_switch(df, inc, lo, hi)
            if pos is not None:
                prev = df.index[pos - 1]
                gap = float(inc.at[prev, "close"] - df.at[prev, "close"])
                df.iloc[pos, df.columns.get_indexer(fetch.COLS)] = inc.loc[df.index[pos], fetch.COLS].values
                method = "contract"
        except fetch.DataError:
            pass
        if pos is None:
            win = df[(df.index >= lo) & (df.index <= hi)]
            if len(win) < 5:
                continue
            jump = win["close"].diff()
            t = jump.idxmax()
            pos = df.index.get_loc(t)
            gap = CARRY * float(df["close"].iloc[pos - 1])
            method = "estimated-carry"
        df.iloc[:pos, df.columns.get_indexer(fetch.PRICE_COLS)] += gap
        rolls.insert(0, {"roll_date": r["roll_date"].isoformat(), "expiry": r["expiry"].isoformat(),
                         "switch_utc": df.index[pos].isoformat(), "outgoing": r["outgoing"],
                         "incoming": r["incoming"], "gap": round(gap, 2), "method": method})
    return df, rolls


def load_bars(refresh: bool = False) -> tuple[pd.DataFrame, list[dict]]:
    """~2 years of cleaned, roll-adjusted hourly bars, cached so every lab script and every
    rerun uses exactly the same data. Same cleaning as the site (off-session bars, in-progress
    bar, spike filter, high/low repair, sanity checks); roll gaps via lab_roll_adjust."""
    CACHE.mkdir(parents=True, exist_ok=True)
    if BARS.exists() and not refresh:
        return pd.read_parquet(BARS), json.loads(ROLLS.read_text())
    days = 729
    df = fetch._normalise(fetch._download(fetch.TICKER, days))
    df = fetch._drop_off_session(df)
    df = fetch._drop_incomplete(df, pd.Timestamp.now(tz="UTC").to_pydatetime())
    df, rolls = lab_roll_adjust(df, days)
    df = fetch.filter_spikes(df)
    df = fetch.repair_high_low(df)
    df["amount"] = df["close"] * df["volume"]
    fetch.sanity_check(df)
    df.to_parquet(BARS)
    ROLLS.write_text(json.dumps(rolls, indent=1))
    return df, rolls


def in_roll_window(times: pd.DatetimeIndex, rolls: list[dict]) -> np.ndarray:
    mask = np.zeros(len(times), dtype=bool)
    for r in rolls:
        lo, hi = roll_window(r)
        mask |= (times >= lo) & (times <= hi)
    return mask


def origins(df: pd.DataFrame, months: float) -> pd.DatetimeIndex:
    """Every STEP-th closed bar in the last `months` months that has a full 512-bar context."""
    start = df.index[-1] - pd.Timedelta(days=round(months * 30.44))
    pos = np.arange(CONTEXT - 1, len(df))
    pos = pos[df.index[pos] >= start]
    pos = pos[(pos - pos[0]) % STEP == 0]
    return df.index[pos]


def realised(df: pd.DataFrame, origin_idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Actual close k bars after each origin (k = 1..HORIZON); NaN past the end of data."""
    pos = df.index.get_indexer(origin_idx)
    close = df["close"].to_numpy()
    out = np.full((len(pos), HORIZON), np.nan)
    for k in range(1, HORIZON + 1):
        ok = pos + k < len(close)
        out[ok, k - 1] = close[pos[ok] + k]
    return pd.DataFrame(out, index=origin_idx, columns=[f"a_{k}" for k in range(1, HORIZON + 1)])


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".tmp.parquet")
    df.to_parquet(tmp)
    tmp.replace(path)
