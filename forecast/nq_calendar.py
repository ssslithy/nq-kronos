"""NQ trading calendar: session hours, future tradable timestamps, quarterly roll dates.

Named nq_calendar.py (not calendar.py) so it doesn't shadow Python's stdlib `calendar`
module, which pandas imports when scripts in this folder are run directly.

CME Globex hours for NQ (New York time):
  Sunday 18:00 -> Friday 17:00, with a daily maintenance break 17:00-18:00 Mon-Thu.
Hourly bars are labelled by their start time, so a bar starting at hour H is tradable
when [H, H+1) lies inside the session.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc

QUARTER_MONTHS = {3: "H", 6: "M", 9: "U", 12: "Z"}


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d: date, saturday_to_friday: bool = True) -> date | None:
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset[date]:
    """Trade days with no usable hourly data on Yahoo NQ=F.

    CME closes fully on New Year, Good Friday and Christmas, and runs short sessions on
    the other US holidays and half-days. Checked against two years of Yahoo bars: Yahoo
    has (almost) no bars for any of these trade days, so all are treated as closed. That
    keeps the 24-step forecast off hours Yahoo will never fill, and keeps those hours
    out of the missing-bar count.
    """
    d: set[date | None] = {
        _observed(date(year, 1, 1), saturday_to_friday=False),  # New Year
        _nth_weekday(year, 1, 0, 3),                            # MLK Day
        _nth_weekday(year, 2, 0, 3),                            # Presidents Day
        _easter(year) - timedelta(days=2),                      # Good Friday
        _nth_weekday(year, 6, 0, 1) - timedelta(days=7),        # Memorial Day (last Mon May)
        _observed(date(year, 7, 4)),                            # Independence Day
        _nth_weekday(year, 9, 0, 1),                            # Labor Day
        _nth_weekday(year, 11, 3, 4),                           # Thanksgiving
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),       # day after (half day)
        _observed(date(year, 12, 25)),                          # Christmas
    }
    if year >= 2022:
        d.add(_observed(date(year, 6, 19)))                     # Juneteenth
    if date(year, 7, 3).weekday() < 4:
        d.add(date(year, 7, 3))                                 # July 3 half day (Mon-Thu)
    if date(year, 12, 24).weekday() < 4:
        d.add(date(year, 12, 24))                               # Christmas Eve half day
    return frozenset(x for x in d if x is not None)


def is_holiday(d: date) -> bool:
    return d in holidays(d.year)


def is_session_hour(start: datetime) -> bool:
    """Weekly Globex schedule only (ignores holidays): Sun 18:00 -> Fri 17:00 NY, 17:00 break."""
    ny = start.astimezone(NY)
    wd, hr = ny.weekday(), ny.hour  # Mon=0 .. Sun=6
    if hr == 17:  # daily break (and Friday close)
        return False
    if wd == 5:  # Saturday
        return False
    if wd == 6 and hr < 18:  # Sunday before open
        return False
    if wd == 4 and hr >= 17:  # Friday after close
        return False
    return True


def trade_day(start: datetime) -> date:
    """CME trade date of an hourly bar: 18:00 NY onwards belongs to the next day."""
    return (start.astimezone(NY) + timedelta(hours=6)).date()


def is_tradable_hour(start: datetime) -> bool:
    """Is the hourly bar beginning at `start` expected to trade (and appear on Yahoo)?

    Excludes holiday trade days, and also the 18:00 reopen on the evening of a holiday:
    CME does trade it, but Yahoo almost never has bars for it.
    """
    if not is_session_hour(start) or is_holiday(trade_day(start)):
        return False
    ny = start.astimezone(NY)
    return not (ny.hour >= 18 and is_holiday(ny.date()))


def next_tradable_hours(last_bar: datetime, n: int = 24) -> list[datetime]:
    """The next `n` tradable hourly bar-start times after `last_bar`, returned in UTC.

    Steps in real UTC hours, so DST transitions are handled by zoneinfo.
    """
    t = last_bar.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    out: list[datetime] = []
    while len(out) < n:
        t += timedelta(hours=1)
        if is_tradable_hour(t):
            out.append(t)
    return out


def sessions(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Tradable spans [open, close) in UTC overlapping [start, end): consecutive tradable
    hours merged, so the page can tell 'open' from 'closed' and find the next open."""
    t = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    out: list[list[datetime]] = []
    while t < end:
        if is_tradable_hour(t):
            if out and out[-1][1] == t:
                out[-1][1] = t + timedelta(hours=1)
            else:
                out.append([t, t + timedelta(hours=1)])
        t += timedelta(hours=1)
    return [(a, b) for a, b in out]


def is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return is_tradable_hour(now.replace(minute=0, second=0, microsecond=0))


# ---- Quarterly roll calendar -------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def roll_date(year: int, month: int) -> date:
    """CME roll date: 2nd Thursday of the expiry month (8 days before the 3rd-Friday expiry)."""
    return _nth_weekday(year, month, 3, 2)


def expiry_date(year: int, month: int) -> date:
    return _nth_weekday(year, month, 4, 3)


def contract_symbol(year: int, month: int) -> str:
    """Yahoo ticker for a specific NQ contract, e.g. NQZ26.CME."""
    return f"NQ{QUARTER_MONTHS[month]}{year % 100:02d}.CME"


def roll_calendar(start: date, end: date) -> list[dict]:
    """All quarterly rolls with roll dates in [start - 10d, end + 10d].

    Each entry: roll_date, expiry, outgoing contract, incoming contract (Yahoo symbols).
    """
    rolls = []
    for y in range(start.year - 1, end.year + 2):
        for m in (3, 6, 9, 12):
            rd = roll_date(y, m)
            if start - timedelta(days=10) <= rd <= end + timedelta(days=10):
                ny, nm = (y + 1, 3) if m == 12 else (y, m + 3)
                rolls.append({
                    "roll_date": rd,
                    "expiry": expiry_date(y, m),
                    "outgoing": contract_symbol(y, m),
                    "incoming": contract_symbol(ny, nm),
                })
    return sorted(rolls, key=lambda r: r["roll_date"])


def fmt_times(t: datetime) -> str:
    """'2026-11-06 21:00 UTC | 21:00 London | 16:00 New York' style string."""
    return (f"{t.astimezone(UTC):%a %Y-%m-%d %H:%M} UTC | "
            f"{t.astimezone(LONDON):%a %H:%M %Z} London | "
            f"{t.astimezone(NY):%a %H:%M %Z} New York")
