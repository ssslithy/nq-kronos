"""Phase 6c: trading rules that use forecast bands. Rules were fixed before looking at results.

All decisions use closed bars only and enter at the NEXT bar's open. A forecast made at
origin bar t is usable from t's close onwards (the latest available forecast is used).
No position is held inside a roll window (forced flat; see common.roll_window).

1. trend      20/100-bar MA crossover, always long or short. Size fixed (BASE MNQ) or
              vol-sized = BASE x (reference width / current +24h P10-P90 width), clipped.
2. stops      Enter only on an MA crossover. Long: stop 1 tick below +24h P10, target
              +24h P75 (short: P90 / P25). Skip if reward/risk < 1 (measured from the
              signal bar's close). Exit on stop, target, opposite crossover or after 24 bars.
3. break      Within 6 bars of a forecast, the first close outside that step's P10-P90:
              fade (enter against the break, target the step's median) or follow (enter
              with the break). Stop 1 band-width from entry, exit after 6 bars.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TICK = 0.25
BASE = 5                 # MNQ contracts for fixed-size rules and the vol-sizing anchor
MAX_MULT = 3             # vol-sized position is clipped to [1, MAX_MULT x BASE]
FAST, SLOW = 20, 100
HOLD_STOPS = 24
BREAK_WINDOW, BREAK_HOLD = 6, 6


@dataclass
class Trade:
    entry_i: int
    entry_px: float
    exit_i: int
    exit_px: float
    side: int            # +1 long, -1 short
    size: int
    reason: str


@dataclass
class Market:
    """Bar arrays for the backtest window [start, end] (positions into the full bar index)."""
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    times: pd.DatetimeIndex
    blocked: np.ndarray  # bar is inside a roll window
    sig: np.ndarray      # +1 / -1 MA state at each bar's close (0 before warm-up)
    start: int
    end: int

    @classmethod
    def build(cls, df: pd.DataFrame, blocked: np.ndarray, start: int) -> "Market":
        c = df["close"]
        fast, slow = c.rolling(FAST).mean(), c.rolling(SLOW).mean()
        sig = np.where(fast > slow, 1, -1)
        sig[: SLOW - 1] = 0
        return cls(df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(),
                   c.to_numpy(), df.index, blocked, sig, start, len(df) - 1)


@dataclass
class Bands:
    """One band source on the common origins: quantile arrays (n_origins, 24)."""
    name: str
    origin_pos: np.ndarray            # bar position of each origin (sorted)
    q: dict                           # {10: arr, 25: arr, 50: arr, 75: arr, 90: arr}

    def latest(self, n_bars: int) -> np.ndarray:
        """For every bar, the index of the latest forecast made at or before it (-1 if none)."""
        return np.searchsorted(self.origin_pos, np.arange(n_bars), side="right") - 1

    def width24(self) -> np.ndarray:
        return self.q[90][:, -1] - self.q[10][:, -1]


# ---- 1. vol-sized trend -------------------------------------------------------------

def trend(m: Market, bands: Bands | None, ref_width: float | None) -> list[Trade]:
    """bands=None -> fixed BASE size; otherwise size = BASE x ref_width / current width."""
    latest = bands.latest(len(m.c)) if bands is not None else None
    w = bands.width24() if bands is not None else None
    trades, side, size, e_i, e_px = [], 0, 0, -1, 0.0
    for i in range(m.start, m.end):
        j = i + 1
        want = int(m.sig[i])
        if m.blocked[j]:
            want = 0
        if want != side:
            if side:
                trades.append(Trade(e_i, e_px, j, m.o[j], side, size, "flip" if want else "roll"))
                side = 0
            if want:
                if bands is None:
                    size = BASE
                else:
                    f = latest[i]
                    if f < 0:
                        continue
                    size = int(np.clip(round(BASE * ref_width / w[f]), 1, MAX_MULT * BASE))
                side, e_i, e_px = want, j, m.o[j]
    if side:
        trades.append(Trade(e_i, e_px, m.end, m.c[m.end], side, size, "end"))
    return trades


# ---- 2. distribution stops / targets ------------------------------------------------

def stops_targets(m: Market, bands: Bands) -> list[Trade]:
    latest = bands.latest(len(m.c))
    trades, pos = [], None   # pos: dict(side, e_i, e_px, stop, target)
    skipped = 0
    for j in range(m.start + 1, m.end + 1):
        # 1) exits during bar j
        if pos is not None:
            s, stop, tgt = pos["side"], pos["stop"], pos["target"]
            px, why = None, None
            if m.blocked[j]:
                px, why = m.o[j], "roll"
            elif pos.get("exit_next"):
                px, why = m.o[j], "flip"
            elif (s > 0 and m.o[j] <= stop) or (s < 0 and m.o[j] >= stop):
                px, why = m.o[j], "stop-gap"
            elif (s > 0 and m.o[j] >= tgt) or (s < 0 and m.o[j] <= tgt):
                px, why = m.o[j], "target-gap"
            elif (s > 0 and m.l[j] <= stop) or (s < 0 and m.h[j] >= stop):
                px, why = stop, "stop"          # stop assumed first if both touched
            elif (s > 0 and m.h[j] >= tgt) or (s < 0 and m.l[j] <= tgt):
                px, why = tgt, "target"
            elif j - pos["e_i"] + 1 >= HOLD_STOPS:
                px, why = m.c[j], "time"
            if px is not None:
                trades.append(Trade(pos["e_i"], pos["e_px"], j, px, s, BASE, why))
                pos = None
        if j >= m.end:
            break
        cross = m.sig[j] != m.sig[j - 1] and m.sig[j - 1] != 0
        # 2) opposite crossover at this close -> exit at next open
        if pos is not None and cross and m.sig[j] != pos["side"]:
            pos["exit_next"] = True
        # 3) entries on a crossover at this close, at next open
        if cross and (pos is None or pos.get("exit_next")) and not m.blocked[j + 1]:
            f = latest[j]
            if f < 0:
                continue
            s, ref = int(m.sig[j]), m.c[j]
            if s > 0:
                stop, tgt = bands.q[10][f, -1] - TICK, bands.q[75][f, -1]
            else:
                stop, tgt = bands.q[90][f, -1] + TICK, bands.q[25][f, -1]
            risk, reward = s * (ref - stop), s * (tgt - ref)
            if risk <= 0 or reward / risk < 1:
                skipped += 1
                continue
            if pos is not None:   # close the old one at the same open first
                trades.append(Trade(pos["e_i"], pos["e_px"], j + 1, m.o[j + 1], pos["side"], BASE, "flip"))
            pos = {"side": s, "e_i": j + 1, "e_px": m.o[j + 1], "stop": stop, "target": tgt}
    if pos is not None:
        trades.append(Trade(pos["e_i"], pos["e_px"], m.end, m.c[m.end], pos["side"], BASE, "end"))
    stops_targets.skipped = skipped
    return trades


# ---- 3. band break: fade vs follow --------------------------------------------------

def band_break(m: Market, bands: Bands, mode: str) -> list[Trade]:
    assert mode in ("fade", "follow")
    events = []   # (signal bar, break direction, band width, median level)
    for f, p in enumerate(bands.origin_pos):
        for k in range(1, BREAK_WINDOW + 1):
            b = p + k
            if b >= m.end:
                break
            lo, hi = bands.q[10][f, k - 1], bands.q[90][f, k - 1]
            if m.c[b] > hi or m.c[b] < lo:
                events.append((b, 1 if m.c[b] > hi else -1, hi - lo, bands.q[50][f, k - 1], p))
                break
    events.sort(key=lambda e: (e[0], -e[4]))            # by bar, newest forecast first
    trades, busy_until = [], -1
    for b, d, width, med, _ in events:
        j = b + 1
        if b <= busy_until or b < m.start or j + BREAK_HOLD - 1 > m.end:
            continue
        if m.blocked[j: j + BREAK_HOLD].any():
            continue
        s = -d if mode == "fade" else d
        e_px = m.o[j]
        stop = e_px - s * width
        tgt = med if mode == "fade" else None
        if tgt is not None and s * (tgt - e_px) <= 0:     # already through the median
            continue
        px, why, x = None, None, None
        for x in range(j, j + BREAK_HOLD):
            if (s > 0 and m.l[x] <= stop) or (s < 0 and m.h[x] >= stop):
                px, why = (m.o[x] if (s > 0 and m.o[x] <= stop) or (s < 0 and m.o[x] >= stop) else stop), "stop"
                break
            if tgt is not None and ((s > 0 and m.h[x] >= tgt) or (s < 0 and m.l[x] <= tgt)):
                px, why = (m.o[x] if (s > 0 and m.o[x] >= tgt) or (s < 0 and m.o[x] <= tgt) else tgt), "target"
                break
        if px is None:
            px, why = m.c[x], "time"
        trades.append(Trade(j, e_px, x, px, s, BASE, why))
        busy_until = x
    return trades
