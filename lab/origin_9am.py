"""Origin study (NQ_9AM_ORIGIN.md): daily origin A = the 07:00-08:00 New York bar (forecast at
08:00 NY) vs B = the 08:00-09:00 bar (forecast at 09:00 NY, after the 08:30 releases).

Both are scored by the production backfill (forecast/vol_accuracy.backfill: HAR-RV, rolling k,
measured multipliers, trailing 250, no look-ahead) on the same hourly bars; only
garch_vol.ORIGIN_NY_HOUR differs. Horizon 23 tradable hours either way. The cash session
(09:00-16:00 NY) is measured from the 09:00 NY price for both, which under B is the origin price.

Gate B (switch to 09:00 only if all hold), on the last 250 common days, older / newer half:
  24h 80% range in 75-85% and 90% in 86-94%; cash 90% in 86-94%; QLIKE no worse than A.
Release days (08:30 NY: CPI, payrolls, PCE, GDP, retail sales; calendar from 2026) are reported
separately. Writes lab/results/origin_9am.md and .json.

    python lab/origin_9am.py            # downloads the bars (falls back to lab/cache)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "forecast"))
import garch_vol as gv  # noqa: E402
import nq_calendar as cal  # noqa: E402
import vol_accuracy as va  # noqa: E402

OUT = ROOT / "lab" / "results" / "origin_9am.md"
CACHE = ROOT / "lab" / "cache" / "bars_origin_9am.parquet"
LAST = 250


def load() -> pd.DataFrame:
    try:
        bars, _ = gv.load_bars()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        bars.to_parquet(CACHE)
    except Exception as e:  # noqa: BLE001
        print(f"download failed ({e}); using {CACHE}")
        bars = pd.read_parquet(CACHE)
    return bars


def score(bars: pd.DataFrame, hour: int) -> pd.DataFrame:
    gv.ORIGIN_NY_HOUR = hour
    rows, skipped = va.backfill(bars, days=10 ** 6, sims=200)
    va.update_accuracy(rows, bars)
    r2 = np.nan_to_num(gv.log_returns(bars["close"]).to_numpy()) ** 2
    cs = np.concatenate([[0.0], np.cumsum(r2)])
    out = []
    for r in rows:
        if not isinstance(r.get("inside80"), bool):
            continue
        p = bars.index.get_loc(pd.Timestamp(r["origin_utc"]))
        rv = cs[p + gv.HORIZON + 1] - cs[p + 1]
        x = rv / r["sigma_pct"] ** 2
        d = {"day": pd.Timestamp(r["origin_utc"]).tz_convert(cal.NY).date(),
             "sigma": r["sigma_pct"], "move": r["move_pct"], "qlike": x - np.log(x) - 1,
             "w90": 2 * r["m90"] * r["sigma_pct"], "in80": r["inside80"], "in90": r["inside90"],
             "cash_in90": r.get("cash_inside90"), "cash_move": r.get("cash_move_pct"),
             "cash_w90": 2 * r["cash_m90"] * r["cash_sigma_pct"] if r.get("cash_m90") and r.get("cash_sigma_pct") else np.nan}
        out.append(d)
    return pd.DataFrame(out).set_index("day")


def pct(s) -> float:
    s = pd.Series(s).dropna()
    return float(100 * s.astype(float).mean()) if len(s) else float("nan")


def summary(t: pd.DataFrame) -> dict:
    return {"n": len(t), "in80": pct(t["in80"]), "in90": pct(t["in90"]), "w90": float(t["w90"].mean()),
            "qlike": float(t["qlike"].mean()), "cash_n": int(t["cash_in90"].notna().sum()),
            "cash_in90": pct(t["cash_in90"]), "cash_w90": float(t.loc[t["cash_in90"].notna(), "cash_w90"].mean())}


def main() -> int:
    bars = load()
    live = gv.ORIGIN_NY_HOUR
    A, B = score(bars, 7), score(bars, 8)
    gv.ORIGIN_NY_HOUR = live
    days = sorted(set(A.index) & set(B.index))
    A, B = A.loc[days], B.loc[days]
    _, _, events = gv.load_events()
    rel = {}
    for e in events:
        ny = e["time_utc"].tz_convert(cal.NY)
        if (ny.hour, ny.minute) == (8, 30):
            rel.setdefault(ny.date(), []).append(e["kind"])
    rel_days = [d for d in days if d in rel]
    # B's forecast whose window (09:00 the day before -> 09:00) holds the 08:30 release
    prev = {d: days[i - 1] for i, d in enumerate(days) if i}
    rel_prev = [prev[d] for d in rel_days if d in prev]
    cov_from = min(rel) if rel else None
    plain = [d for d in days if cov_from and d >= cov_from and d not in rel]

    last = days[-LAST:]
    half = len(last) // 2
    parts = {"all": days, "last 250": last, "older half": last[:half], "newer half": last[half:]}
    res = {name: {"A": summary(A.loc[d]), "B": summary(B.loc[d])} for name, d in parts.items()}
    res["release days"] = {"A": summary(A.loc[rel_days]), "B": summary(B.loc[rel_days])}
    res["other days (2026)"] = {"A": summary(A.loc[plain]), "B": summary(B.loc[plain])}
    res["release next morning (B, day before)"] = {"A": summary(A.loc[rel_days]), "B": summary(B.loc[rel_prev])}

    o, n = res["older half"]["B"], res["newer half"]["B"]
    checks = {
        "24h 80% in 75-85% (both halves)": all(75 <= x["in80"] <= 85 for x in (o, n)),
        "24h 90% in 86-94% (both halves)": all(86 <= x["in90"] <= 94 for x in (o, n)),
        "cash 90% in 86-94% (both halves)": all(86 <= x["cash_in90"] <= 94 for x in (o, n)),
        "QLIKE no worse than A (last 250)": res["last 250"]["B"]["qlike"] <= res["last 250"]["A"]["qlike"],
    }
    passed = all(checks.values())

    def row(name):
        a, b = res[name]["A"], res[name]["B"]
        return (f"| {name} | {a['n']} | {a['in80']:.1f} / {b['in80']:.1f} | {a['in90']:.1f} / {b['in90']:.1f} | "
                f"{a['w90']:.2f} / {b['w90']:.2f} | {a['qlike']:.3f} / {b['qlike']:.3f} | "
                f"{a['cash_in90']:.1f} / {b['cash_in90']:.1f} ({b['cash_n']}) | {a['cash_w90']:.2f} / {b['cash_w90']:.2f} |")

    L = ["# Origin study: 08:00 vs 09:00 New York", "",
         f"A = forecast at 08:00 NY (the 07:00-08:00 bar, current); B = forecast at 09:00 NY (the 08:00-09:00 bar). "
         f"Same bars ({bars.index[0]:%d %b %Y} – {bars.index[-1]:%d %b %Y}), same production backfill (HAR-RV, rolling k, "
         f"measured multipliers, trailing 250, no look-ahead), {len(days)} days scored for both "
         f"({days[0]} – {days[-1]}). Every cell is A / B. Widths are the full 90% range as % of price. "
         "The cash session is 09:00-16:00 NY measured from the 09:00 price for both (under B that is the origin price).", "",
         "| days | n | 24h 80% hit | 24h 90% hit | 24h 90% width % | QLIKE | cash 90% hit (n) | cash 90% width % |",
         "|---|--:|--:|--:|--:|--:|--:|--:|"]
    L += [row(k) for k in ("all", "last 250", "older half", "newer half")]
    L += ["", "## Release days (08:30 NY: CPI, payrolls, PCE, GDP, retail sales; calendar from 2026)", "",
          f"{len(rel_days)} release days, {len(plain)} other days since {cov_from}. On a release day A forecasts before "
          "the number and B after it. B's 24-hour window then holds the *next* morning's 08:30 release in its last "
          "half hour, so the last row scores B's forecast made the day before a release (A's column there is the "
          "release-day forecast again, for comparison).", "",
          "| days | n | 24h 80% hit | 24h 90% hit | 24h 90% width % | QLIKE | cash 90% hit (n) | cash 90% width % |",
          "|---|--:|--:|--:|--:|--:|--:|--:|"]
    L += [row(k) for k in ("release days", "other days (2026)", "release next morning (B, day before)")]
    L += ["", "## Gate B", ""] + [f"- {'pass' if v else 'FAIL'}: {k}" for k, v in checks.items()]
    L += ["", f"**Decision: {'switch to the 09:00 New York origin' if passed else 'keep 08:00; nothing else changed'}.**", "",
          "Run: `python lab/origin_9am.py`."]
    OUT.write_text("\n".join(L) + "\n")
    OUT.with_suffix(".json").write_text(json.dumps({"checks": checks, "passed": passed, "results": res}, indent=1, default=str))
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
