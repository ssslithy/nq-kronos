"""Fetch, clean and roll-adjust NQ hourly bars from Yahoo Finance.

Run directly to print the last 5 bars and the next 24 forecast timestamps:
    python forecast/fetch.py

Exits non-zero if a sanity check fails (callers must then keep the last good forecast).
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import nq_calendar as cal  # noqa: E402

log = logging.getLogger("fetch")

TICKER = "NQ=F"
CONTEXT = 512
PRICE_COLS = ["open", "high", "low", "close"]
COLS = PRICE_COLS + ["volume"]


class DataError(RuntimeError):
    """A sanity check failed; the data must not be used."""


@dataclass
class RollEvent:
    roll_date: str          # calendar roll date (2nd Thursday), YYYY-MM-DD
    switch_utc: str         # first bar Yahoo's NQ=F shows the incoming contract
    outgoing: str
    incoming: str
    gap: float              # incoming - outgoing price at the bar before the switch
    method: str             # "contract" (measured) or "estimated" (from the jump)


@dataclass
class Bars:
    df: pd.DataFrame                    # UTC index; open high low close volume amount
    rolls: list[RollEvent] = field(default_factory=list)

    def context(self, n: int = CONTEXT) -> pd.DataFrame:
        return self.df.tail(n)

    def rolls_in_context(self, n: int = CONTEXT) -> list[RollEvent]:
        start = self.df.index[-n] if len(self.df) >= n else self.df.index[0]
        return [r for r in self.rolls if pd.Timestamp(r.switch_utc) > start]

    @property
    def roll_adjusted(self) -> bool:
        return bool(self.rolls_in_context())

    @property
    def roll_date(self) -> str | None:
        r = self.rolls_in_context()
        return r[-1].roll_date if r else None


# ---- download ----------------------------------------------------------------

def _download(symbol: str, days: int, attempts: int = 3) -> pd.DataFrame:
    period = f"{min(days, 729)}d"  # Yahoo caps 1h history at ~730 days
    last_err = None
    for i in range(attempts):
        try:
            df = yf.Ticker(symbol).history(period=period, interval="1h", prepost=True,
                                           auto_adjust=False, actions=False)
            if df is not None and not df.empty:
                return df
            last_err = "empty response"
        except Exception as e:  # network / Yahoo hiccups
            last_err = repr(e)
        time.sleep(2 * (i + 1))
    raise DataError(f"download of {symbol} failed after {attempts} attempts: {last_err}")


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=str.lower)[COLS].copy()
    idx = pd.DatetimeIndex(df.index)
    df.index = (idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC"))
    df.index.name = "t"
    df = df.astype(float)
    # Exact duplicate rows are a harmless Yahoo quirk; conflicting ones fail the checks.
    df = df[~(df.index.duplicated(keep="last") & df.duplicated(keep="last"))]
    return df.sort_index()


def _drop_off_session(df: pd.DataFrame) -> pd.DataFrame:
    """Drop bars outside the weekly Globex schedule (Yahoo emits stray bars in the 17:00 NY
    break). Holiday bars that Yahoo does have are kept: they're real trading."""
    mask = np.array([cal.is_session_hour(t) for t in df.index])
    if (~mask).any():
        log.info("dropped %d off-session bar(s)", int((~mask).sum()))
    return df[mask]


def _drop_incomplete(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Drop bars whose hour hasn't finished yet (the in-progress bar)."""
    done = df.index + pd.Timedelta(hours=1) <= pd.Timestamp(now)
    if (~done).any():
        log.info("dropped in-progress bar(s): %s", list(df.index[~done]))
    return df[done]


# ---- roll adjustment ---------------------------------------------------------

MATCH_TOL = 0.5  # points: NQ=F "equals" the incoming contract within this


def _find_switch(cont: pd.DataFrame, inc: pd.DataFrame, lo, hi):
    """Index position of the first bar where NQ=F equals the incoming contract (and stays so)."""
    both = pd.concat({"close": cont["close"], "close_inc": inc["close"]}, axis=1, join="inner")
    both = both[(both.index >= lo) & (both.index <= hi)]
    if both.empty:
        return None
    same = (both["close"] - both["close_inc"]).abs() <= MATCH_TOL
    for i in range(len(same)):
        if same.iloc[i:i + 6].all():
            t = same.index[i]
            pos = cont.index.get_loc(t)
            if pos == 0 or cont.index[pos - 1] not in inc.index:
                return None  # data starts after the switch: nothing to back-adjust
            prev = cont.index[pos - 1]
            if abs(cont.at[prev, "close"] - inc.at[prev, "close"]) <= MATCH_TOL:
                return None  # never differed in this window: no switch visible
            return pos
    return None


def _estimate_switch(df: pd.DataFrame, lo, hi):
    """Fallback: the largest close-to-close jump near the roll, if it stands out (> 8x typical)."""
    win = df[(df.index >= lo) & (df.index <= hi)]
    if len(win) <= 10:
        return None, None
    jump = win["close"].diff()
    t = jump.abs().idxmax()
    if abs(jump[t]) > 8 * jump.abs().median():
        return df.index.get_loc(t), float(jump[t])
    return None, None


def roll_adjust(df: pd.DataFrame, days: int) -> tuple[pd.DataFrame, list[RollEvent]]:
    """Back-adjust (additively) all bars before each Yahoo contract switch.

    The gap is measured from the incoming contract's own Yahoo series where available;
    the switch bar itself (often a mix of both contracts) is replaced by the incoming
    contract's bar. If the incoming series is unavailable, or a calendar roll lies inside
    the 512-bar context but no switch is found in it, the gap is estimated from the largest
    bar-to-bar jump near the roll date (method="estimated") and a WARNING is logged.
    No roll is ever skipped without a log line.
    """
    df = df.copy()
    events: list[RollEvent] = []
    ctx_start = df.index[-CONTEXT] if len(df) >= CONTEXT else df.index[0]
    first, last = df.index[0].date(), df.index[-1].date()
    for r in reversed(cal.roll_calendar(first, last)):  # newest first: gaps accumulate backwards
        lo = pd.Timestamp(r["roll_date"] - timedelta(days=4), tz="UTC")
        hi = pd.Timestamp(r["expiry"] + timedelta(days=4), tz="UTC")
        name = f"{r['outgoing']}->{r['incoming']} (calendar {r['roll_date']})"
        if hi < df.index[0] or lo > df.index[-1]:
            log.info("roll %s: outside fetched data, nothing to do", name)
            continue
        roll_ts = pd.Timestamp(r["roll_date"], tz=cal.NY)
        in_ctx = ctx_start <= roll_ts <= df.index[-1]
        pos, gap, method, why = None, None, None, ""
        try:
            inc = _normalise(_download(r["incoming"], days, attempts=2))
            # Apply already-found later rolls to the incoming series so levels line up.
            for e in events:
                inc.loc[inc.index < pd.Timestamp(e.switch_utc), PRICE_COLS] += e.gap
            pos = _find_switch(df, inc, lo, hi)
            if pos is not None:
                prev = df.index[pos - 1]
                gap = float(inc.at[prev, "close"] - df.at[prev, "close"])
                df.iloc[pos, df.columns.get_indexer(COLS)] = inc.loc[df.index[pos], COLS].values
                method = "contract"
            else:
                why = f"no switch to {r['incoming']} detected in Yahoo data"
        except DataError as e:
            inc = None
            why = f"incoming contract {r['incoming']} unavailable ({e})"
        if pos is None and (inc is None or in_ctx):
            pos, gap = _estimate_switch(df, lo, hi)
            if pos is not None:
                method = "estimated"
        level = logging.WARNING if in_ctx else logging.INFO
        if pos is None:
            log.log(level, "roll %s: %s; estimate found no clear jump -> NO adjustment applied%s",
                    name, why or "no switch detected",
                    " (roll is inside the 512-bar context)" if in_ctx else "")
            continue
        if method == "estimated":
            log.log(level, "roll %s: %s; using ESTIMATED gap %+.2f at %s%s", name, why, gap,
                    df.index[pos], " (inside the 512-bar context)" if in_ctx else "")
        df.iloc[:pos, df.columns.get_indexer(PRICE_COLS)] += gap
        events.insert(0, RollEvent(
            roll_date=r["roll_date"].isoformat(), switch_utc=df.index[pos].isoformat(),
            outgoing=r["outgoing"], incoming=r["incoming"], gap=round(gap, 2), method=method))
    return df, events


# ---- checks & pipeline -------------------------------------------------------

MAX_MISSING = 10      # missing tradable hours allowed in the context before failing
MAX_STALE_HOURS = 3   # tradable hours allowed to pass without a new closed bar


SPIKE_MULT = 6  # x median bar range


def filter_spikes(df: pd.DataFrame, mult: float = SPIKE_MULT) -> pd.DataFrame:
    """Remove bad ticks: an open/high/low more than `mult` x the median bar range away from
    BOTH the previous close and the bar's own close. Open -> previous close; high/low ->
    max/min of the bar's remaining prices. A genuine gap (e.g. the Sunday reopen) is far
    from the previous close but near its own close, so it is left alone."""
    df = df.copy()
    limit = mult * float((df["high"] - df["low"]).median())
    prev_close = df["close"].shift(1)

    def far(col):
        return ((df[col] - prev_close).abs() > limit) & ((df[col] - df["close"]).abs() > limit)

    hits = []
    for t in df.index[far("open")]:
        hits.append(f"{t:%Y-%m-%d %H:%M} UTC open {df.at[t, 'open']:.2f}->{prev_close[t]:.2f}")
        df.at[t, "open"] = prev_close[t]
    for col, pick in (("high", max), ("low", min)):
        for t in df.index[far(col)]:
            new = pick(df.at[t, c] for c in PRICE_COLS if c != col)
            hits.append(f"{t:%Y-%m-%d %H:%M} UTC {col} {df.at[t, col]:.2f}->{new:.2f}")
            df.at[t, col] = new
    if hits:
        log.warning("spike filter (> %.0f pts = %gx median bar range) fixed %d value(s): %s",
                    limit, mult, len(hits), "; ".join(hits))
    return df


def repair_high_low(df: pd.DataFrame) -> pd.DataFrame:
    """Force high = max(OHLC) and low = min(OHLC); log how many bars needed it."""
    hi, lo = df[PRICE_COLS].max(axis=1), df[PRICE_COLS].min(axis=1)
    bad = (df["high"] < hi) | (df["low"] > lo)
    if bad.any():
        log.warning("repaired high/low on %d bar(s) inconsistent with open/close", int(bad.sum()))
        df = df.copy()
        df["high"], df["low"] = hi, lo
    return df


def check_stale(df: pd.DataFrame, now: datetime) -> None:
    """If the market is open, fail when > MAX_STALE_HOURS tradable hours have fully elapsed
    since the last closed bar. Counted in trading hours, so the Sunday reopen (last bar on
    Friday) isn't mistaken for stale data."""
    if not cal.is_market_open(now):
        return
    last = df.index[-1]
    t, elapsed = last, 0
    while t + pd.Timedelta(hours=2) <= pd.Timestamp(now):  # next bar after t has closed
        t += pd.Timedelta(hours=1)
        if cal.is_tradable_hour(t):
            elapsed += 1
    if elapsed > MAX_STALE_HOURS:
        raise DataError(f"stale data: market is open but last closed bar is {last:%Y-%m-%d %H:%M} "
                        f"UTC, {elapsed} tradable hours ago (> {MAX_STALE_HOURS})")


def missing_hours(df: pd.DataFrame, n: int = CONTEXT) -> list[pd.Timestamp]:
    """Tradable hours (per nq_calendar) with no bar, within the last `n` bars."""
    ctx = df.tail(n)
    have = set(ctx.index)
    expected = pd.date_range(ctx.index[0], ctx.index[-1], freq="h")
    return [t for t in expected if cal.is_tradable_hour(t) and t not in have]


def check_missing(df: pd.DataFrame, n: int = CONTEXT) -> None:
    miss = missing_hours(df, n)
    if len(miss) > MAX_MISSING:
        raise DataError(f"{len(miss)} tradable hours missing in the last {n} bars "
                        f"(> {MAX_MISSING}); first: {miss[0]:%Y-%m-%d %H:%M} UTC")
    if miss:
        log.warning("%d tradable hour(s) missing in the last %d bars: %s", len(miss), n,
                    ", ".join(f"{t:%m-%d %H:%M}" for t in miss[:10]))


def sanity_check(df: pd.DataFrame, min_bars: int = CONTEXT) -> None:
    problems = []
    if df.index.has_duplicates:
        problems.append(f"{int(df.index.duplicated().sum())} duplicate timestamp(s)")
    p = df[PRICE_COLS]
    if p.isna().any().any() or df["volume"].isna().any():
        problems.append("NaN values")
    if (p <= 0).any().any():
        problems.append("zero/negative prices")
    if len(df) < min_bars:
        problems.append(f"only {len(df)} bars (< {min_bars})")
    if problems:
        raise DataError("; ".join(problems))


def load_bars(days: int = 60, now: datetime | None = None, min_bars: int = CONTEXT) -> Bars:
    now = now or datetime.now(cal.UTC)
    df = _normalise(_download(TICKER, days))
    df = _drop_off_session(df)
    df = _drop_incomplete(df, now)
    if df.empty:
        raise DataError("no closed bars")
    check_stale(df, now)
    df, rolls = roll_adjust(df, days)
    df = filter_spikes(df)
    df = repair_high_low(df)
    df["amount"] = df["close"] * df["volume"]  # Kronos expects an `amount` column
    sanity_check(df, min_bars)
    check_missing(df, min(CONTEXT, len(df)))
    return Bars(df=df, rolls=rolls)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        bars = load_bars()
    except DataError as e:
        print(f"DATA CHECK FAILED: {e}", file=sys.stderr)
        return 1
    df = bars.df
    print(f"\n{TICKER}: {len(df)} clean hourly bars  {df.index[0]:%Y-%m-%d %H:%M} -> "
          f"{df.index[-1]:%Y-%m-%d %H:%M} UTC   (context = last {CONTEXT})")
    for r in bars.rolls:
        print(f"roll: {r.outgoing} -> {r.incoming}, calendar {r.roll_date}, Yahoo switched "
              f"{r.switch_utc[:16]} UTC, gap {r.gap:+.2f} pts ({r.method})")
    print(f"roll_adjusted (inside 512-bar context): {bars.roll_adjusted}  roll_date: {bars.roll_date}")

    print("\nLast 5 closed bars (bar start times):")
    for t, row in df.tail(5).iterrows():
        print(f"  {cal.fmt_times(t)}  O {row.open:9.2f} H {row.high:9.2f} "
              f"L {row.low:9.2f} C {row.close:9.2f} V {int(row.volume):>7}")

    print("\nNext 24 tradable hours to forecast:")
    for i, t in enumerate(cal.next_tradable_hours(df.index[-1].to_pydatetime(), 24), 1):
        print(f"  +{i:<2} {cal.fmt_times(t)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
