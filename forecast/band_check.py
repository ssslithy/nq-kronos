"""Phase 4 band check: score past forecasts in history.jsonl against what NQ actually did.

For every history line whose +h target bar has closed, look up that bar's actual close and
record whether it landed inside P10-P90 and how far the median (and a naive "no change"
forecast = the last close) was from it. Over the last 30 matured +24h forecasts:
  - band hit rate: share of actual closes inside P10-P90 (~80% = well calibrated)
  - median error: mean |actual - P50|, next to the naive forecast's mean |actual - last close|

Roll handling: forecasts are in the price basis of the day they were made. If a contract
roll happened after a forecast was made, the back-adjusted bars have since shifted by the
roll gap, so the forecast's levels (and its last close) are shifted by the same gap.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

NY = "America/New_York"
DAILY_WINDOW = 60          # daily (07:00 NY origin) live forecasts in the GARCH-vs-Kronos panel

WINDOW = 30
HORIZONS = (1, 6, 12, 23, 24)
TABLE_ROWS = 8


def read_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    by_bar = {r["last_bar_utc"]: r for r in rows}           # last write per bar wins
    return [by_bar[k] for k in sorted(by_bar)]


def evaluate(history: list[dict], df: pd.DataFrame, rolls: list, now_utc: str) -> dict:
    closes = df["close"]
    last_bar = df.index[-1]
    first_bar = df.index[0]

    def gap_since(t0: pd.Timestamp) -> float:
        return sum(r.gap for r in rolls if pd.Timestamp(r.switch_utc) > t0)

    matured: dict[int, list[dict]] = {h: [] for h in HORIZONS}
    pending, missing = 0, 0
    for line in history:
        t0 = pd.Timestamp(line["last_bar_utc"])
        adj = gap_since(t0)
        for h in HORIZONS:
            key = f"h{h}"
            if key not in line:
                continue
            tgt = pd.Timestamp(line[key]["t"])
            if tgt > last_bar:                                 # target bar not closed yet
                pending += h == 24
                continue
            if tgt < first_bar:
                continue                                       # older than the fetched data
            if tgt not in closes.index:
                missing += h == 24                             # Yahoo has no bar there
                continue
            actual = float(closes[tgt])
            p10, p50, p90 = (line[key][k] + adj for k in ("p10", "p50", "p90"))
            last_close = line["last_close"] + adj
            matured[h].append({
                "made_for_bar": line["last_bar_utc"], "target": line[key]["t"],
                "last_close": round(last_close, 2), "p10": round(p10, 2), "p50": round(p50, 2),
                "p90": round(p90, 2), "actual": round(actual, 2),
                "inside": bool(p10 <= actual <= p90),
                "err_median": round(abs(actual - p50), 2),
                "err_naive": round(abs(actual - last_close), 2),
                "roll_adj": round(adj, 2), "backfill": bool(line.get("backfill", False)),
            })

    def summary(rows: list[dict], window: int = WINDOW) -> dict:
        rows = rows[-window:]
        n = len(rows)
        if not n:
            return {"n": 0}
        return {
            "n": n,
            "hit_rate": round(sum(r["inside"] for r in rows) / n, 3),
            "mae_median": round(sum(r["err_median"] for r in rows) / n, 2),
            "mae_naive": round(sum(r["err_naive"] for r in rows) / n, 2),
            "avg_width": round(sum(r["p90"] - r["p10"] for r in rows) / n, 2),
        }

    h24 = matured[24]
    # Daily comparison with the GARCH forecast: only live forecasts made from the 07:00-08:00
    # NY bar (the daily origin), whichever workflow made them. Since Nov 2026 the daily run is
    # 23 steps (1pm -> 1pm London) and scored at +23h; older daily lines only have +24h.
    is_daily = lambda r: (not r["backfill"]                                  # noqa: E731
                          and pd.Timestamp(r["made_for_bar"]).tz_convert(NY).hour == 7)
    d23 = [r for r in matured[23] if is_daily(r)]
    have23 = {r["made_for_bar"] for r in d23}
    daily = sorted(d23 + [r for r in h24 if is_daily(r) and r["made_for_bar"] not in have23],
                   key=lambda r: r["made_for_bar"])
    return {
        "daily": {**summary(daily, DAILY_WINDOW), "window": DAILY_WINDOW, "matured": len(daily)},
        "generated_utc": now_utc,
        "window": WINDOW,
        "matured_24h": len(h24),
        "ready": len(h24) >= WINDOW,
        "pending_24h": pending,
        "missing_24h": missing,
        "backfill_in_window": sum(r["backfill"] for r in h24[-WINDOW:]),
        "h24": summary(h24),
        "by_horizon": {str(h): summary(matured[h]) for h in HORIZONS},
        "recent": h24[-TABLE_ROWS:][::-1],                     # newest first, for hand checks
    }
