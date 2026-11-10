"""Watchdog for the daily vol run (.github/workflows/vol_watchdog.yml).

GitHub's scheduler is best effort: on 9 Nov 2026 the only in-window garch-vol slot never
fired and nothing was published. garch_vol.yml now has 7 slots; this is the second line.

Scheduled at 13:45 and 14:45 UTC on weekdays. GitHub often starts these hours late, so it acts
whenever it runs after 09:30 New York on a trading day (the forecast window 08:00-09:25 NY has
closed). If site/data/vol_status.json's forecast origin isn't today's 07:00 NY bar, it prints
late=true for the workflow, which sends an ntfy note saying the forecast is late (if NTFY_TOPIC
is set) and dispatches garch_vol.yml with force=true unless a garch-vol run is already queued
or running. Standard library only (no pip install).

    python forecast/vol_watchdog.py                       # as the workflow does
    python forecast/vol_watchdog.py --now "2026-11-09 09:45" --dry-run   # a past time, New York
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nq_calendar as cal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ACT_FROM, ACT_UNTIL = time(9, 30), time(23, 59)       # New York: any time after the window, same day


def today_origin(d) -> datetime:
    return datetime(d.year, d.month, d.day, 7, tzinfo=cal.NY).astimezone(timezone.utc)


def check(now: datetime, status: dict | None) -> tuple[bool, str]:
    """(late, reason) for the given time and vol_status.json contents."""
    ny = now.astimezone(cal.NY)
    d = ny.date()
    if d.weekday() >= 5 or cal.is_holiday(d) or not cal.is_tradable_hour(today_origin(d)):
        return False, f"{d} is not a forecast day"
    if not (ACT_FROM <= ny.time() < ACT_UNTIL):
        return False, f"New York time {ny:%H:%M} is before 09:30 (the forecast window is still open)"
    want = today_origin(d)
    have = (status or {}).get("forecast_origin_utc")
    if have and datetime.fromisoformat(have.replace("Z", "+00:00")) == want:
        return False, f"ok: today's forecast (origin {want:%Y-%m-%d %H:%M} UTC) is published"
    return True, f"late: vol_status.json origin is {have or 'missing'}, expected {want:%Y-%m-%dT%H:%M}Z"


def notify(text: str, topic: str) -> bool:
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode(), method="POST",
                                 headers={"Title": "NQ forecast late", "Priority": "high", "Tags": "warning"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception as e:  # noqa: BLE001
        print(f"ntfy failed: {type(e).__name__}: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help="pretend it is this time (New York)")
    ap.add_argument("--status", default=str(ROOT / "site" / "data" / "vol_status.json"))
    ap.add_argument("--dry-run", action="store_true", help="don't notify or set outputs")
    args = ap.parse_args()
    now = (datetime.fromisoformat(args.now).replace(tzinfo=cal.NY) if args.now else datetime.now(timezone.utc))
    try:
        status = json.loads(Path(args.status).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        status = None
    late, reason = check(now, status)
    print(reason)
    if late and not args.dry_run:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write("late=true\n")
        topic = os.environ.get("NTFY_TOPIC")
        if topic:
            ny = now.astimezone(cal.NY)
            lon = now.astimezone(cal.LONDON)
            hhmm = f"{lon.hour % 12 or 12}:{lon.minute:02d}{'am' if lon.hour < 12 else 'pm'}"
            ok = notify(f"Today's 1pm London forecast hadn't published by {hhmm} London "
                        f"({ny:%H:%M} New York). Re-running the job now; it should arrive in a few minutes.", topic)
            print(f"ntfy note sent: {ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
