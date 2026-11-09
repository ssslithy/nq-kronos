"""Phone alert for the daily vol forecast via ntfy.sh (free, no account).

After a successful forecast (scheduled, or a manual forced run), run_vol.py POSTs one line
to https://ntfy.sh/<NTFY_TOPIC>, leading with the 90% range and the 1pm -> 1pm London window:

    NQ Fri 1:00pm – Mon 1:00pm London: ±444 pts (±1.64%) · 90%: 26,659–27,546 · Cash 2:30pm–9:00pm ±329 pts · Payrolls (NFP) 1:30pm

NTFY_TOPIC comes from the environment (a GitHub Actions secret in the workflow). If it isn't
set, nothing is sent and nothing is logged. A failed send is logged as a warning and never
fails the forecast. Anyone who knows the topic name can read and post to it, so it should be
long and random (`--new-topic` makes one) and kept as a secret.

    python forecast/vol_alert.py                 # print the alert for site/data/vol_latest.json
    python forecast/vol_alert.py --send          # send it now, as a test (needs NTFY_TOPIC)
    python forecast/vol_alert.py --new-topic     # print a fresh random topic name
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import requests

log = logging.getLogger("vol_alert")

ROOT = Path(__file__).resolve().parent.parent
SERVER = "https://ntfy.sh"
LONDON = ZoneInfo("Europe/London")
TIMEOUT = 15


def london(t) -> str:
    """12-hour London clock: 1:00pm, 11:58am."""
    lt = datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(LONDON)
    return f"{lt.hour % 12 or 12}:{lt.minute:02d}{'am' if lt.hour < 12 else 'pm'}"


def window(f: dict) -> str:
    """'Fri 1:00pm – Mon 1:00pm' from the origin close to the close of the last horizon bar."""
    a = datetime.fromisoformat(f["origin_utc"]) + timedelta(hours=1)
    b = datetime.fromisoformat(f["horizon_end_utc"]) + timedelta(hours=1)
    return f"{a.astimezone(LONDON):%a} {london(a)} – {b.astimezone(LONDON):%a} {london(b)}"


def alert_text(f: dict) -> str:
    """One line: the 90% (headline) move and range for the 1pm -> 1pm London window, the cash
    session range, any scheduled release in the window, and a roll warning."""
    lv = f.get("headline", 90)
    lo, hi = f[f"range{lv}"]
    parts = [f"NQ {window(f)} London: ±{f['headline_pts']:,.0f} pts (±{f['headline_pct']:.2f}%)",
             f"{lv}%: {lo:,.0f}–{hi:,.0f}"]
    c = f.get("cash") or {}
    if c.get("pts") and c.get("start_utc"):
        start = datetime.fromisoformat(c["start_utc"]) + timedelta(minutes=30)   # the 9:30am NY open
        parts.append(f"Cash {london(start)}–{london(c['end_utc'])} ±{c['pts']:,.0f} pts"
                     + ("" if c.get("level") == lv else f" ({c['level']}%)"))
    ev = f.get("events") or {}
    origin_day = datetime.fromisoformat(f["origin_utc"]).astimezone(LONDON).date()
    for i in ev.get("items", []):
        t = datetime.fromisoformat(i["time_utc"])
        same = t.astimezone(LONDON).date() == origin_day
        parts.append(f"{i['name']} {london(t)}" if same else f"{i['name']} {t.astimezone(LONDON):%a} {london(t)}")
    if f.get("roll_note"):
        parts.append("roll in window")
    return " · ".join(parts)


def send(text: str, topic: str | None = None, title: str | None = None) -> bool:
    """POST to ntfy. Returns True if sent; False if no topic is set or the send failed."""
    topic = topic or os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    headers = {"Title": title or "NQ daily volatility", "Tags": "chart_with_upwards_trend"}
    try:
        r = requests.post(f"{SERVER}/{topic}", data=text.encode("utf-8"), headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning("ntfy alert not sent: %s", e)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--latest", default=str(ROOT / "site" / "data" / "vol_latest.json"))
    ap.add_argument("--send", action="store_true", help="send the alert now as a test (needs NTFY_TOPIC)")
    ap.add_argument("--new-topic", action="store_true", help="print a fresh random topic name and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.new_topic:
        print(f"nq-vol-{secrets.token_hex(12)}")
        return 0
    f = json.loads(Path(args.latest).read_text())
    text = alert_text(f)
    print(text)
    if args.send:
        if not os.environ.get("NTFY_TOPIC", "").strip():
            print("NTFY_TOPIC is not set: nothing sent", file=sys.stderr)
            return 1
        origin = datetime.fromisoformat(f["origin_utc"]).strftime("%a %d %b")
        ok = send(f"TEST (forecast of {origin}): {text}", title="NQ daily volatility: test")
        print("sent" if ok else "send failed (see warning above)")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
