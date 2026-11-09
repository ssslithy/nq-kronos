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


# ==== vol fix (NQ_VOL_FIX_PLAN.md): clustered misses after shocks ===========================
#
#   python lab/vol_contest.py --fix     # writes lab/results/vol_fix.md
#
# Scored on every origin where all fix candidates have a calibrated forecast (the production
# calibration needs K_MIN matured forecasts; candidates that fit a parameter need TRAIN_MIN
# matured origins first), plus the last-250 view. Extra measures: Christoffersen independence
# test on the 90% misses, miss-after-miss share, stress coverage, calm width, last-60 coverage.

FIX_OUT = ROOT / "lab" / "results" / "vol_fix.md"
DAILY_MIN, DAILY_MAX = 120, 500          # 23h returns in the daily GARCH fit
NEAR = 5                                 # "miss after miss": a previous miss within 5 origins
C_GRID = np.round(np.arange(0, 3.0001, 0.05), 2)


def daily_returns(close: np.ndarray, p: int, n: int = DAILY_MAX) -> np.ndarray:
    """Non-overlapping 23-bar % log returns ending at position p (oldest first). Known at p."""
    idx = np.arange(p, -1, -H)[: n + 1][::-1]
    return 100 * np.diff(np.log(close[idx]))


def best_scale_qlike(rv, s2) -> float:
    c = np.mean(rv / s2)
    return float(np.mean(qlike(rv, c * s2)))


def fit_param(t, s_of, grid):
    """Walk-forward: for each origin pick the grid value whose sigma (s_of(g) -> array) has the
    lowest best-scale QLIKE over the trailing TRAIN matured origins (min TRAIN_MIN)."""
    rv = t["rv_fut"].to_numpy()
    S = {g: s_of(g) for g in grid}
    out, chosen = np.full(len(t), np.nan), np.full(len(t), np.nan)
    for i in range(len(t)):
        tr = trailing(t, i).index.to_numpy()
        tr = tr[np.all([np.isfinite(S[g][tr]) for g in grid], axis=0)] if len(tr) else tr
        if len(tr) < TRAIN_MIN or not all(np.isfinite(S[g][i]) for g in grid):
            continue
        g = min(grid, key=lambda g: best_scale_qlike(rv[tr], S[g][tr] ** 2))
        out[i], chosen[i] = S[g][i], g
    return out, chosen


def rv_nd(t, bars, n):
    close = bars["close"].to_numpy()
    return np.array([np.sqrt(np.mean(daily_returns(close, p, n) ** 2)) if p >= n * H else np.nan
                     for p in t["pos"].to_numpy()])


def cand_floor(t, bars, n):
    sg, rv = t["sigma_raw_pct"].to_numpy(), rv_nd(t, bars, n)
    return fit_param(t, lambda c: np.maximum(sg, c * rv), list(C_GRID))


def daily_garch_sigma(t, bars):
    from arch import arch_model
    import warnings
    close = bars["close"].to_numpy()
    out = np.full(len(t), np.nan)
    for i, p in enumerate(t["pos"].to_numpy()):
        r = daily_returns(close, p)
        if len(r) < DAILY_MIN:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = arch_model(r, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False).fit(
                disp="off", show_warning=False)
        out[i] = float(np.sqrt(res.forecast(horizon=1, reindex=False).variance.values[-1, 0]))
    return out


def cand_two_speed(t, bars):
    sg, sd = np.log(t["sigma_raw_pct"].to_numpy()), np.log(daily_garch_sigma(t, bars))
    return fit_param(t, lambda w: np.exp(w * sg + (1 - w) * sd), list(np.round(np.linspace(0, 1, 21), 2)))


def calibrate_all(t, s) -> pd.DataFrame:
    """Production calibration at every matured origin with a raw sigma: k and measured
    multipliers from the trailing K_DAYS matured, roll-free forecasts (min K_MIN)."""
    rows = calibrate(t, s, [i for i in range(len(t)) if np.isfinite(t["move_pct"].iat[i])])
    return pd.DataFrame([r for r in rows if r is not None]).set_index("i")


def christoffersen(miss: np.ndarray) -> tuple[float, float]:
    """LR test of independence of the miss sequence (Christoffersen 1998); returns (LR, p)."""
    from scipy.special import xlogy
    from scipy.stats import chi2
    a, b = miss[:-1].astype(int), miss[1:].astype(int)
    n00, n01 = np.sum((a == 0) & (b == 0)), np.sum((a == 0) & (b == 1))
    n10, n11 = np.sum((a == 1) & (b == 0)), np.sum((a == 1) & (b == 1))
    p01 = n01 / max(n00 + n01, 1)
    p11 = n11 / max(n10 + n11, 1)
    p = (n01 + n11) / max(n00 + n01 + n10 + n11, 1)
    l0 = xlogy(n00 + n10, 1 - p) + xlogy(n01 + n11, p)
    l1 = xlogy(n00, 1 - p01) + xlogy(n01, p01) + xlogy(n10, 1 - p11) + xlogy(n11, p11)
    lr = float(max(-2 * (l0 - l1), 0.0))
    return lr, float(chi2.sf(lr, 1))


def miss_after_miss(miss: np.ndarray) -> float:
    j = np.flatnonzero(miss)
    if not len(j):
        return np.nan
    return float(np.mean([np.any((j < x) & (j >= x - NEAR)) for x in j]))


def stress_flags(t, garch_cal: pd.DataFrame) -> np.ndarray:
    """Known at the origin: the previous matured origin's |move| > 2x its calibrated GARCH sigma,
    or VXN (last close before the origin's date) in the top quartile of its trailing year."""
    vxn = dor.daily_close("^VXN")
    pos, move = t["pos"].to_numpy(), t["move_pct"].to_numpy()
    out = np.zeros(len(t), dtype=bool)
    for i in range(len(t)):
        prev = [j for j in range(i - 1, max(i - 4, -1), -1) if pos[j] + H <= pos[i]]
        shock = bool(prev) and prev[0] in garch_cal.index and abs(move[prev[0]]) > 2 * garch_cal.at[prev[0], "sigma"]
        yr = vxn[vxn.index < t["day"].iat[i]].tail(252)
        high = len(yr) >= 200 and yr.iloc[-1] >= np.quantile(yr, 0.75)
        out[i] = shock or high
    return out


def fix_measures(c: pd.DataFrame, stress: np.ndarray) -> dict:
    c = c.sort_index()
    half = len(c) // 2
    parts = {"all": c, "older": c.iloc[:half], "newer": c.iloc[half:], "last60": c.iloc[-60:]}
    out = {"n": len(c)}
    for nm, p in parts.items():
        for lv in (80, 90):
            out[f"{nm}_in{lv}"] = 100 * p[f"in{lv}"].mean()
        out[f"{nm}_qlike"] = p["qlike"].mean()
        out[f"{nm}_width"] = p["width90"].mean()
    miss = ~c["in90"].to_numpy(bool)
    out["misses"] = int(miss.sum())
    out["lr"], out["p_ind"] = christoffersen(miss)
    out["mam"] = 100 * miss_after_miss(miss)
    st = stress[c.index.to_numpy()]
    out["n_stress"] = int(st.sum())
    out["stress_in90"] = 100 * c["in90"].to_numpy(bool)[st].mean()
    out["calm_width"] = c["width90"].to_numpy()[~st].mean()
    return out


def fix_main() -> int:
    logging.basicConfig(level=logging.WARNING)
    t, bars = dor.build()
    t = t.reset_index(drop=True)
    raw = {}
    raw["A. GARCH"] = (cand_garch(t), None)
    raw["B. GARCH + floor 5d"] = cand_floor(t, bars, 5)
    raw["B. GARCH + floor 10d"] = cand_floor(t, bars, 10)
    raw["C. GARCH + VXN"] = (cand_vxn(t), None)
    raw["D. HAR-RV"] = (cand_har(t), None)
    raw["E. Two-speed GARCH"] = cand_two_speed(t, bars)
    cal_ = {k: calibrate_all(t, v[0]) for k, v in raw.items()}
    common = sorted(set.intersection(*[set(c.index) for c in cal_.values()]))
    stress = stress_flags(t, cal_["A. GARCH"])
    res = {k: fix_measures(c.loc[common], stress) for k, c in cal_.items()}
    last = common[-250:]
    res250 = {k: fix_measures(c.loc[last], stress) for k, c in cal_.items()}
    for k, r in res.items():
        print(f"{k:22} n {r['n']} 90% {r['older_in90']:.1f}/{r['newer_in90']:.1f} p_ind {r['p_ind']:.3f} "
              f"mam {r['mam']:.0f}% stress {r['stress_in90']:.1f}% (n {r['n_stress']}) calm {r['calm_width']:.0f} "
              f"qlike {r['all_qlike']:.4f} last60 {r['last60_in90']:.1f}", flush=True)

    g = res["A. GARCH"]
    gates = {}
    for k, r in res.items():
        if k == "A. GARCH":
            continue
        g1 = all(75 <= r[f"{h}_in80"] <= 85 and 86 <= r[f"{h}_in90"] <= 94 for h in ("older", "newer"))
        g2 = r["p_ind"] > 0.05 or r["mam"] <= 2 / 3 * g["mam"]
        g3 = r["stress_in90"] >= 85 and r["stress_in90"] >= g["stress_in90"] + 5
        g4 = r["calm_width"] <= 1.05 * g["calm_width"]
        g5 = all(r[f"{h}_qlike"] <= g[f"{h}_qlike"] for h in ("all", "older", "newer"))
        gates[k] = (g1, g2, g3, g4, g5)
    order = ["B. GARCH + floor 5d", "B. GARCH + floor 10d", "C. GARCH + VXN", "E. Two-speed GARCH", "D. HAR-RV"]
    winners = [k for k in order if all(gates[k])]
    winner = winners[0] if winners else None

    d0, d1 = t["origin_utc"].iat[common[0]], t["origin_utc"].iat[common[-1]]
    yn = lambda b: "yes" if b else "no"  # noqa: E731

    def table(rs, title):
        L = [f"## {title}", "",
             "| model | 80% older / newer | 90% older / newer | 90% last 60 | misses | Christoffersen LR (p) "
             "| miss after miss | stress 90% | calm width (pts) | QLIKE all / older / newer |",
             "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for k, r in rs.items():
            L.append(f"| {k} | {r['older_in80']:.1f} / {r['newer_in80']:.1f} | {r['older_in90']:.1f} / {r['newer_in90']:.1f} "
                     f"| {r['last60_in90']:.1f} | {r['misses']} | {r['lr']:.2f} ({r['p_ind']:.3f}) | {r['mam']:.0f}% "
                     f"| {r['stress_in90']:.1f}% | {r['calm_width']:,.0f} | {r['all_qlike']:.4f} / {r['older_qlike']:.4f} / "
                     f"{r['newer_qlike']:.4f} |")
        return L

    L = ["# Vol fix: clustered misses after shocks", "",
         f"Scored on {len(common)} daily 07:00 NY origins ({d0:%d %b %Y} – {d1:%d %b %Y}): every matured origin where "
         "all candidates have a production-calibrated forecast (k and measured multipliers from the trailing 250 "
         f"matured, roll-free forecasts, min {gv.K_MIN}). Halves = first / last {len(common) // 2} origins. "
         f"Stress origins: {res['A. GARCH']['n_stress']} of {len(common)} (previous origin's |move| > 2x its "
         "calibrated GARCH sigma, or VXN in the top quartile of its trailing 252 closes; both known at the origin).", ""]
    L += table(res, "Full sample")
    L += ["", f"Christoffersen: LR of independence of the 90% misses, chi-squared(1); p < 0.05 means the misses "
          f"cluster. Miss after miss: share of 90% misses with another miss in the previous {NEAR} origins.", ""]
    L += table(res250, f"Last 250 origins ({t['origin_utc'].iat[last[0]]:%d %b %Y} – {d1:%d %b %Y})")
    L += ["", "## Gate (full sample; to replace GARCH all five must hold)", "",
          "| model | 1 coverage both halves | 2 clustering better | 3 stress ≥ 85% and ≥ GARCH + 5 | 4 calm width ≤ +5% "
          "| 5 QLIKE ≤ GARCH all and halves | passes |", "|---|---|---|---|---|---|---|"]
    for k in order:
        v, r = gates[k], res[k]
        L.append(f"| {k} | {yn(v[0])} | {yn(v[1])} (p {r['p_ind']:.3f}, {r['mam']:.0f}% vs {g['mam']:.0f}%) "
                 f"| {yn(v[2])} ({r['stress_in90']:.1f}% vs {g['stress_in90']:.1f}%) "
                 f"| {yn(v[3])} ({100 * (r['calm_width'] / g['calm_width'] - 1):+.1f}%) | {yn(v[4])} | **{yn(all(v))}** |")
    fit_notes = []
    for k in ("B. GARCH + floor 5d", "B. GARCH + floor 10d", "E. Two-speed GARCH"):
        ch = raw[k][1][common]
        fit_notes.append(f"{k}: fitted {'c' if 'floor' in k else 'w (weight on hourly GARCH)'} median "
                         f"{np.nanmedian(ch):.2f}, range {np.nanmin(ch):.2f}–{np.nanmax(ch):.2f}")
    L += ["", "## Verdict", "",
          (f"**{winner} replaces GARCH** ({'the only candidate' if len(winners) == 1 else 'the simplest of the candidates'} "
           "that passes all five)." if winner else
           "**GARCH is kept.** No candidate passed all five tests. The page footer gets a \"Last 20 days\" line "
           "so clusters of misses are visible."), "",
          "## Candidates", "",
          "- A. GARCH: production (hourly GARCH(1,1)-t on 2,000 returns, 23-step analytic sigma).",
          "- B. GARCH + daily floor: sigma = max(sigma_GARCH, c x RV_nd), RV_nd = RMS of the last n non-overlapping "
          "23-bar % returns ending at the origin; c (grid 0–3, step 0.05) minimises best-scale QLIKE over the trailing "
          "250 matured origins (min 100).",
          "- C. GARCH + VXN: log blend with VXN / sqrt(252), weight fitted the same way (see vol_contest.md).",
          "- D. HAR-RV: log RV(next 23h) on log RV of the last 1 / 5 / 22 days, OLS on the trailing 250 (min 100).",
          f"- E. Two-speed GARCH: log sigma = w log sigma_GARCH(hourly) + (1 - w) log sigma_daily, sigma_daily = "
          f"one-step forecast of GARCH(1,1)-t on the last {DAILY_MIN}–{DAILY_MAX} non-overlapping 23-bar returns "
          "ending at the origin; w (grid 0–1, step 0.05) fitted like B.", "",
          *[f"- {n}" for n in fit_notes], "",
          "Run: `python lab/daily_origins.py && python lab/vol_contest.py --fix`."]
    FIX_OUT.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(fix_main() if "--fix" in sys.argv else main())
