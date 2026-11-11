"""Timer for the daily vol run (.github/workflows/vol_timer.yml): starts garch-vol on time.

GitHub's scheduler runs this repo's scheduled jobs hours late (Nov 2026: ~6-7 h) and skips many,
so a cron at 13:07 UTC can't start the 1pm run. This job is scheduled through the night instead;
whichever slot GitHub fires first waits on the runner until TARGET (08:07 New York = 1:07pm
London; DST-aware), then dispatches garch_vol.yml (workflow_dispatch from a workflow starts
immediately, unlike a schedule). At BACKUP (08:22 NY) it dispatches again if today's forecast
still isn't published. A job may run 6 h at most, so if the target is further away it sleeps
CHAIN_SLEEP and dispatches the next timer run to carry on.

It does nothing (and exits at once) if today isn't an NQ trading day, today's forecast is
already published, another timer run is already waiting, or it starts after BACKUP + 30 min
(then garch-vol's own late runs and the watchdog publish instead). Standard library + the gh CLI.

    python forecast/vol_timer.py                                  # as the workflow does
    python forecast/vol_timer.py --now "2026-11-11 01:30" --dry-run   # what it would do (New York time)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time as _time
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nq_calendar as cal  # noqa: E402

TARGET, BACKUP = time(8, 7), time(8, 22)          # New York
LATE_LIMIT = timedelta(minutes=30)                # after BACKUP + this, leave it to the late runs
MAX_WAIT = timedelta(hours=5, minutes=20)         # + 15 min to BACKUP stays inside the 355-min job limit
CHAIN_SLEEP = timedelta(hours=5, minutes=20)
MAX_LEAD = timedelta(hours=13)                    # started more than this before the target: leave it to a later slot


def ny_at(d, t) -> datetime:
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=cal.NY)


def forecast_day(d) -> bool:
    return d.weekday() < 5 and not cal.is_holiday(d) and cal.is_tradable_hour(ny_at(d, time(7, 0)).astimezone(timezone.utc))


def published_origin(repo: str) -> str | None:
    """forecast_origin_utc of vol_status.json on the default branch (via the GitHub API)."""
    try:
        out = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                              f"/repos/{repo}/contents/site/data/vol_status.json"],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        return json.loads(out).get("forecast_origin_utc")
    except Exception as e:  # noqa: BLE001
        print(f"could not read vol_status.json: {e}")
        return None


def made_today(repo: str, d) -> bool:
    o = published_origin(repo)
    return bool(o) and datetime.fromisoformat(o.replace("Z", "+00:00")) == ny_at(d, time(7, 0))


def others_waiting(repo: str) -> int:
    """Other vol-timer runs in progress (this one included in the count, so > 1 means another)."""
    try:
        out = subprocess.run(["gh", "run", "list", "--repo", repo, "--workflow", "vol_timer.yml",
                              "--status", "in_progress", "--json", "databaseId"],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        mine = os.environ.get("GITHUB_RUN_ID")
        return sum(1 for r in json.loads(out) if str(r["databaseId"]) != mine)
    except Exception as e:  # noqa: BLE001
        print(f"could not list timer runs: {e}")
        return 0


def dispatch(repo: str, workflow: str, *fields: str) -> None:
    args = ["gh", "workflow", "run", workflow, "--repo", repo, "--ref", os.environ.get("REF", "main")]
    for f in fields:
        args += ["-f", f]
    subprocess.run(args, check=True, timeout=60)
    print(f"dispatched {workflow} {' '.join(fields)} at {datetime.now(timezone.utc):%H:%M:%S} UTC")


def plan(now: datetime) -> tuple[str, datetime | None, object]:
    """("skip", None, reason) | ("chain", wake_utc, day) | ("wait", target_utc, day)."""
    ny = now.astimezone(cal.NY)
    d = ny.date()
    if ny.time() > (datetime.combine(d, BACKUP) + LATE_LIMIT).time():
        d += timedelta(days=1)                       # today's slot has passed: aim at the next day
    while not forecast_day(d):
        d += timedelta(days=1)
    target = ny_at(d, TARGET)
    wait = target - now
    if wait > MAX_LEAD:
        return "skip", None, d
    if wait > MAX_WAIT:
        return "chain", now + CHAIN_SLEEP, d
    return "wait", target, d


def sleep_until(t: datetime, dry: bool) -> None:
    s = (t - datetime.now(timezone.utc)).total_seconds()
    print(f"sleeping {max(s, 0) / 3600:.2f} h until {t.astimezone(cal.NY):%a %H:%M} NY")
    if s > 0 and not dry:
        _time.sleep(s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help="pretend it is this time (New York); implies --dry-run")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, don't wait or dispatch")
    args = ap.parse_args()
    dry = args.dry_run or bool(args.now)
    now = (datetime.fromisoformat(args.now).replace(tzinfo=cal.NY).astimezone(timezone.utc)
           if args.now else datetime.now(timezone.utc))
    repo = os.environ.get("GITHUB_REPOSITORY", "ssslithy/nq-kronos")
    kind, when, d = plan(now)
    print(f"now {now.astimezone(cal.NY):%a %d %b %H:%M} NY; next forecast day {d}; plan: {kind}"
          + (f" until {when.astimezone(cal.NY):%a %H:%M} NY" if when else " (more than 13 h before the target; a later slot will act)"))
    if dry or kind == "skip":
        return 0
    if others_waiting(repo):
        print("another timer run is already waiting: nothing to do"); return 0
    if made_today(repo, d):
        print(f"the forecast for {d} is already published: nothing to do"); return 0
    if kind == "chain":
        sleep_until(when, dry)
        dispatch(repo, "vol_timer.yml")              # the next link carries on to the target
        return 0
    sleep_until(when, dry)
    if made_today(repo, d):
        print("published meanwhile: nothing to do"); return 0
    dispatch(repo, "garch_vol.yml", "force=false", "test_alert=false")
    sleep_until(ny_at(d, BACKUP), dry)
    if not made_today(repo, d):
        print("not published by the backup time: dispatching again")
        dispatch(repo, "garch_vol.yml", "force=false", "test_alert=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
