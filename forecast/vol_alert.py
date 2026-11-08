"""Phone alert for the daily GARCH 24h vol forecast via ntfy.sh (free, no account).

After a successful forecast (scheduled, or a manual forced run), run_vol.py POSTs one line
to https://ntfy.sh/<NTFY_TOPIC>:

    NQ 24h vol: ±276 pts (±1.02%) · 80%: 26,747–27,454 · 95%: 26,535–27,674 · Calm (19th pct) · NFP 08:30 NY

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
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger("vol_alert")

ROOT = Path(__file__).resolve().parent.parent
SERVER = "https://ntfy.sh"
TIMEOUT = 15


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def alert_text(f: dict) -> str:
    """One line: sigma, 80% range, regime, and any scheduled release in the 24h horizon."""
    lo, hi = f["range80"]
    parts = [f"NQ 24h vol: ±{f['sigma_pts']:,.0f} pts (±{f['sigma_pct']:.2f}%)",
             f"80%: {lo:,.0f}–{hi:,.0f}"]
    if f.get("range95"):
        parts.append(f"95%: {f['range95'][0]:,.0f}–{f['range95'][1]:,.0f}")
    if f.get("regime"):
        parts.append(f"{f['regime']} ({_ordinal(round(f['percentile']))} pct)")
    ev = f.get("events") or {}
    for i in ev.get("items", []):
        # "NFP 08:30 NY" today; "CPI Tue 08:30 NY" later in the horizon
        day, hhmm = i["ny"][:3], i["ny"][-5:]
        parts.append(f"{i['kind']} {hhmm} NY" if i["today"] else f"{i['kind']} {day} {hhmm} NY")
    if f.get("roll_note"):
        parts.append("roll in horizon")
    return " · ".join(parts)


def send(text: str, topic: str | None = None, title: str | None = None) -> bool:
    """POST to ntfy. Returns True if sent; False if no topic is set or the send failed."""
    topic = topic or os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    headers = {"Title": title or "NQ 24h volatility", "Tags": "chart_with_upwards_trend"}
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
        ok = send(f"TEST (forecast of {origin}): {text}", title="NQ 24h volatility: test")
        print("sent" if ok else "send failed (see warning above)")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
