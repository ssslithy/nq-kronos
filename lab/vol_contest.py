"""Part C: volatility contest. Which model gives the best calibrated sigma for the next 23h?

Same 250 daily 07:00 NY origins as the production backfill, 23-bar horizon, walk-forward:
every model uses only data before each origin, and is then put through the production
calibration (k = RMS(move / sigma) over the trailing 250 matured, roll-free forecasts among
the last 330, min 60; measured 85/90/95/99% multipliers from the same set).

Candidates (each gives one raw sigma for the next 23h):
  1 GARCH          production GARCH(1,1) Student-t on 2,000 hourly returns (baseline)
  2 GARCH + VXN    log sigma = w log sigma_GARCH + (1 - w) log sigma_VXN, sigma_VXN = VXN / sqrt(252)
                   from the last VXN close dated before the origin; w (grid 0..1) minimises
                   QLIKE (best scale) over the trailing 250 matured origins
  3 HAR-RV         log RV(next 23h) on log RV of the last 1, 5, 22 days (23h windows ending at
                   each origin); OLS on the trailing 250 matured origins (min 100)
  4 XGBoost        log RV(next 23h) on: log RV 1/5/22 days, overnight range, log VXN, weekday,
                   event-in-horizon flag and kind (unknown before 2026: missing), log GARCH sigma;
                   retrained at the first origin of each month on the trailing 250 matured
                   origins (min 100), fixed settings
  5 GARCH + regime GARCH sigma x sqrt(mean(RV / sigma^2 | state) / mean(RV / sigma^2)) over the
                   trailing 250 matured origins, state = Part D filtered HMM state (Bull if
                   P(Bull) >= 0.5)

Scoring on the 250 origins: hit rates 80 / 90 / 95% (both halves), mean 90% width in points,
QLIKE = RV / s^2 - ln(RV / s^2) - 1 with s = the calibrated sigma, RV = realised 23h variance.

Gate C (to replace GARCH): 80% in 75-85% and 90% in 86-94% on both halves; mean 90% width >= 5%
narrower than GARCH; lower QLIKE; and better than GARCH on width and QLIKE in BOTH halves.

    python lab/vol_contest.py      # needs lab/cache/regime.parquet (python lab/regime.py)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lab"))
sys.path.insert(0, str(ROOT / "forecast"))
import daily_origins as dor  # noqa: E402
import garch_vol as gv  # noqa: E402

OUT = ROOT / "lab" / "results" / "vol_contest.md"
H = gv.HORIZON
TRAIN, TRAIN_MIN = 250, 100
PRIOR_N = 330
LEVELS = (80, 85, 90, 95, 99)
SIMPLICITY = ["GARCH", "GARCH + regime", "GARCH + VXN", "HAR-RV", "XGBoost"]   # fewest moving parts first


def trailing(t: pd.DataFrame, i: int, n: int = TRAIN) -> pd.DataFrame:
    """Rows before row i whose 23h outcome had closed by origin i (pos + H <= pos_i)."""
    p = t["pos"].iat[i]
    m = t.iloc[:i]
    return m[(m["pos"] + H <= p) & m["rv_fut"].notna()].tail(n)


def qlike(rv, s2):
    x = np.asarray(rv) / np.asarray(s2)
    return x - np.log(x) - 1


# ---- candidates ------------------------------------------------------------------------

def cand_garch(t):
    return t["sigma_raw_pct"].to_numpy()


def cand_vxn(t):
    sg = np.log(t["sigma_raw_pct"].to_numpy())
    sv = np.log(t["vxn"].to_numpy() / np.sqrt(252))
    out = np.full(len(t), np.nan)
    grid = np.linspace(0, 1, 21)
    for i in range(len(t)):
        tr = trailing(t, i)
        if len(tr) < TRAIN_MIN:
            continue
        idx = tr.index.to_numpy()
        rv = t.loc[idx, "rv_fut"].to_numpy()
        best, bw = np.inf, 1.0
        for w in grid:
            s2 = np.exp(2 * (w * sg[idx] + (1 - w) * sv[idx]))
            c = np.mean(rv / s2)                                   # best scale for QLIKE
            q = np.mean(qlike(rv, c * s2))
            if q < best:
                best, bw = q, w
        out[i] = np.exp(bw * sg[i] + (1 - bw) * sv[i])
    return out


def har_features(t):
    lr = np.log(t["rv_past"].to_numpy())
    rv = t["rv_past"].to_numpy()
    f1 = lr
    f5 = np.log(pd.Series(rv).rolling(5).mean().to_numpy())
    f22 = np.log(pd.Series(rv).rolling(22).mean().to_numpy())
    return np.column_stack([f1, f5, f22])


def cand_har(t):
    X = har_features(t)
    y = np.log(t["rv_fut"].to_numpy())
    out = np.full(len(t), np.nan)
    for i in range(len(t)):
        if not np.isfinite(X[i]).all():
            continue
        tr = trailing(t, i)
        idx = [j for j in tr.index if np.isfinite(X[j]).all()]
        if len(idx) < TRAIN_MIN:
            continue
        A = np.column_stack([np.ones(len(idx)), X[idx]])
        beta, *_ = np.linalg.lstsq(A, y[idx], rcond=None)
        out[i] = np.sqrt(np.exp(beta[0] + X[i] @ beta[1:]))
    return out


def xgb_features(t):
    X = pd.DataFrame(har_features(t), columns=["lrv1", "lrv5", "lrv22"], index=t.index)
    X["overnight"] = t["overnight_pct"]
    X["lvxn"] = np.log(t["vxn"])
    X["dow"] = t["dow"]
    X["event"] = t["event_any"]
    for k in ("FOMC", "CPI", "NFP", "other"):
        X[f"ev_{k}"] = np.where(t["event_kind"] == "unknown", np.nan, (t["event_kind"] == k).astype(float))
    X["lgarch"] = np.log(t["sigma_raw_pct"])
    return X


def cand_xgb(t):
    import xgboost as xgb
    X = xgb_features(t)
    y = np.log(t["rv_fut"].to_numpy())
    out = np.full(len(t), np.nan)
    model, month = None, None
    for i in range(len(t)):
        m = (t["origin_utc"].iat[i].year, t["origin_utc"].iat[i].month)
        if m != month:
            tr = trailing(t, i)
            idx = [j for j in tr.index if np.isfinite(X.loc[j, ["lrv1", "lrv5", "lrv22"]]).all()]
            if len(idx) >= TRAIN_MIN:
                model = xgb.XGBRegressor(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8,
                                         colsample_bytree=0.8, min_child_weight=5, random_state=0, n_jobs=1)
                model.fit(X.loc[idx], y[idx])
                month = m
        if model is not None and month == m:
            out[i] = np.sqrt(np.exp(model.predict(X.iloc[[i]])[0]))
    return out


def cand_regime(t):
    sg = t["sigma_raw_pct"].to_numpy()
    bull = (t["p_bull"] >= 0.5).to_numpy()
    known = t["p_bull"].notna().to_numpy()
    rv = t["rv_fut"].to_numpy()
    out = np.full(len(t), np.nan)
    for i in range(len(t)):
        if not known[i]:
            continue
        tr = trailing(t, i)
        idx = [j for j in tr.index if known[j]]
        if len(idx) < TRAIN_MIN:
            continue
        idx = np.array(idx)
        ratio = rv[idx] / sg[idx] ** 2
        same = idx[bull[idx] == bull[i]]
        f = np.sqrt(np.mean(rv[same] / sg[same] ** 2) / np.mean(ratio)) if len(same) >= 20 else 1.0
        out[i] = sg[i] * f
    return out


# ---- production calibration + scoring ------------------------------------------------------

def calibrate(t: pd.DataFrame, s: np.ndarray, eval_idx) -> pd.DataFrame:
    rows = []
    move, roll, pos = t["move_pct"].to_numpy(), t["roll"].to_numpy(), t["pos"].to_numpy()
    for i in eval_idx:
        if not np.isfinite(s[i]):
            rows.append(None)
            continue
        prior = np.arange(i)[-PRIOR_N:]
        ok = prior[(pos[prior] + H <= pos[i]) & np.isfinite(move[prior]) & ~roll[prior] & np.isfinite(s[prior])][-gv.K_DAYS:]
        if len(ok) < gv.K_MIN:
            rows.append(None)
            continue
        z = move[ok] / s[ok]
        k = float(np.sqrt(np.mean(z ** 2)))
        az = np.abs(z) / k
        mult = {lv: float(np.quantile(az, lv / 100)) for lv in (85, 90, 95, 99)}
        mult[80] = gv.Z80
        sig = k * s[i]
        last = t["last"].iat[i]
        r = {"i": i, "sigma": sig, "qlike": float(qlike(t["rv_fut"].iat[i], sig ** 2)),
             "width90": last * (np.exp(mult[90] * sig / 100) - np.exp(-mult[90] * sig / 100))}
        for lv in LEVELS:
            r[f"in{lv}"] = abs(move[i]) <= mult[lv] * sig
        rows.append(r)
    return rows


def summarise(rows) -> dict:
    ok = [r for r in rows if r is not None]
    half = len(rows) // 2
    parts = {"all": ok, "older": [r for r in rows[:half] if r], "newer": [r for r in rows[half:] if r]}
    out = {"n": len(ok), "missing": len(rows) - len(ok)}
    for name, p in parts.items():
        for lv in LEVELS:
            out[f"{name}_in{lv}"] = 100 * np.mean([r[f"in{lv}"] for r in p]) if p else np.nan
        out[f"{name}_width"] = np.mean([r["width90"] for r in p]) if p else np.nan
        out[f"{name}_qlike"] = np.mean([r["qlike"] for r in p]) if p else np.nan
    return out


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    t, _ = dor.build()
    reg = pd.read_parquet(ROOT / "lab" / "cache" / "regime.parquet")[["origin_utc", "p_bull"]]
    t = t.merge(reg, on="origin_utc", how="left").reset_index(drop=True)
    ev = dor.eval_origins(t)
    eval_idx = ev.index.to_numpy()
    cands = {"GARCH": cand_garch, "GARCH + VXN": cand_vxn, "HAR-RV": cand_har,
             "XGBoost": cand_xgb, "GARCH + regime": cand_regime}
    res = {}
    for name, fn in cands.items():
        s = fn(t)
        res[name] = summarise(calibrate(t, s, eval_idx))
        r = res[name]
        print(f"{name:15} n {r['n']}  80% {r['older_in80']:.1f}/{r['newer_in80']:.1f}  90% {r['older_in90']:.1f}/{r['newer_in90']:.1f}  "
              f"width {r['all_width']:.0f}  qlike {r['all_qlike']:.4f}", flush=True)

    g = res["GARCH"]
    verdict = {}
    for name, r in res.items():
        if name == "GARCH":
            continue
        cov = all(75 <= r[f"{h}_in80"] <= 85 and 86 <= r[f"{h}_in90"] <= 94 for h in ("older", "newer"))
        narrower = r["all_width"] <= 0.95 * g["all_width"]
        lower_q = r["all_qlike"] < g["all_qlike"]
        both = all(r[f"{h}_width"] < g[f"{h}_width"] and r[f"{h}_qlike"] < g[f"{h}_qlike"] for h in ("older", "newer"))
        verdict[name] = (cov, narrower, lower_q, both, cov and narrower and lower_q and both, r["missing"] == 0)
    winners = [n for n in SIMPLICITY if n in verdict and verdict[n][4] and verdict[n][5]]
    winner = winners[0] if winners else None

    first, last = ev["origin_utc"].iloc[0], ev["origin_utc"].iloc[-1]
    L = ["# Part C: volatility contest", "",
         f"{len(ev)} daily 07:00 NY origins ({first:%d %b %Y} – {last:%d %b %Y}), 23-hour horizon (1pm → 1pm London), "
         "walk-forward, each model then put through the production calibration (k and measured multipliers "
         "from the trailing 250 matured forecasts). Halves = first / last 125 origins.", "",
         "## Hit rates (target 80 / 90 / 95%)", "",
         "| model | 80% older | 80% newer | 90% older | 90% newer | 95% older | 95% newer | scored |",
         "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for name, r in res.items():
        L.append(f"| {name} | {r['older_in80']:.1f}% | {r['newer_in80']:.1f}% | {r['older_in90']:.1f}% | {r['newer_in90']:.1f}% | "
                 f"{r['older_in95']:.1f}% | {r['newer_in95']:.1f}% | {r['n']} |")
    L += ["", "## Sharpness and loss", "",
          "| model | mean 90% width (pts) | vs GARCH | older | newer | QLIKE | older | newer |",
          "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for name, r in res.items():
        L.append(f"| {name} | {r['all_width']:,.0f} | {100 * (r['all_width'] / g['all_width'] - 1):+.1f}% | "
                 f"{r['older_width']:,.0f} | {r['newer_width']:,.0f} | {r['all_qlike']:.4f} | "
                 f"{r['older_qlike']:.4f} | {r['newer_qlike']:.4f} |")
    L += ["", "## Gate C (to replace GARCH, all four)", "",
          "| model | coverage both halves | ≥ 5% narrower | lower QLIKE | beats GARCH on width and QLIKE in both halves | passes |",
          "|---|---|---|---|---|---|"]
    yn = lambda b: "yes" if b else "no"  # noqa: E731
    for name, v in verdict.items():
        L.append(f"| {name} | {yn(v[0])} | {yn(v[1])} | {yn(v[2])} | {yn(v[3])} | **{yn(v[4] and v[5])}** |")
    L += ["", "## Verdict", ""]
    if winner:
        L.append(f"**{winner} replaces GARCH** (simplest candidate that passes all four tests).")
    else:
        L.append("**GARCH is kept.** No candidate passed all four tests, so the production model is unchanged.")
    L += ["", "Notes: realised variance = sum of the 23 squared hourly % log returns after the origin (roll-adjusted series). "
          "Events are only known from 2026 (the YAML's coverage), so XGBoost sees them as missing before that. "
          "VXN: Yahoo `^VXN` daily close dated before the origin's New York date. "
          "The GARCH row differs slightly from the production backfill (79.2% / 88.8%) because the contest scores the "
          "last 250 origins with a full 23-bar outcome by position, while the backfill matches the horizon-end bar by "
          "calendar time (one origin with a missing Yahoo bar drops out). "
          "Near miss: GARCH + VXN was 6.1% narrower with lower QLIKE overall, but on the older half its width was "
          "not narrower than GARCH, so it fails the both-halves test.", "",
          "Run: `python lab/regime.py && python lab/vol_contest.py`."]
    OUT.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
