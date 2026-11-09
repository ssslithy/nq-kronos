"""Part D: 2-state Gaussian HMM regime filter on daily NQ returns. Decides whether the page
shows a "Regime [Bull | Bear]  xx% state probability" chip.

Method (no look-ahead):
- Daily NQ=F closes from Yahoo (back to 2000), dated by the CME trade date: the bar dated d
  closes at 17:00 NY on d, so an origin at 08:00 NY on day d uses bars dated before d only.
  Each quarterly roll's up-jump (largest daily return in [roll date - 1, expiry + 4]) is
  reduced by the carry (same rule as forecast/garch_vol.py), so rolls don't look like rallies.
- Weekly refit (first origin of each ISO week): GaussianHMM(2 states, hmmlearn) on the last
  ~3 years (756 daily % log returns) before that origin; 5 random starts, best log-likelihood.
- State probability at an origin = the FILTERED probability P(state | returns up to the last
  bar before the origin), from a forward pass with the current week's fit. Never the smoothed
  (forward-backward) probability, which uses later data.
- Labels after each fit: "Bull" = the lower-variance state (reported: how often it was also
  the higher-mean one).

Gate D (all three over the last 250 origins, the same set as the vol backfill):
  1 stable:     median run length in one state >= 10 trading days
  2 meaningful: mean next-23h realised vol in Bear >= 1.3 x Bull, and the 95% block-bootstrap
                interval of that ratio above 1.0
  3 honest:     bucket the stated state's probability into 0.5-0.7, 0.7-0.9, 0.9-1.0; within each
                bucket (n >= 10) the share of the next 20 origins later filtered into the same
                state is within +-10 points of the bucket's mean probability
Also reported: whether the regime predicts the direction of the next 23h (hit rate, rank
correlation, both halves).

    python lab/regime.py          # writes lab/results/regime.md and lab/cache/regime.parquet
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lab"))
sys.path.insert(0, str(ROOT / "forecast"))
import daily_origins as dor  # noqa: E402
import garch_vol as gv  # noqa: E402
import nq_calendar as cal  # noqa: E402

OUT = ROOT / "lab" / "results" / "regime.md"
CACHE = ROOT / "lab" / "cache" / "regime.parquet"
FIT_DAYS = 756
STARTS = 5
N_EVAL = 250
FWD = 20
BUCKETS = [(0.5, 0.7), (0.7, 0.9), (0.9, 1.0001)]
BOOT, BLOCK = 2000, 10


def daily_returns() -> pd.Series:
    c = dor.daily_close("NQ=F")
    r = 100 * np.log(c).diff().dropna()
    for roll in cal.roll_calendar(r.index[0].date(), r.index[-1].date()):
        lo = pd.Timestamp(roll["roll_date"]) - pd.Timedelta(days=1)
        hi = pd.Timestamp(roll["expiry"]) + pd.Timedelta(days=4)
        w = r[(r.index >= lo) & (r.index <= hi)]
        if len(w):
            r.loc[w.idxmax()] -= 100 * gv.CARRY
    return r


def fit_hmm(x: np.ndarray):
    from hmmlearn.hmm import GaussianHMM
    best, best_ll = None, -np.inf
    for seed in range(STARTS):
        m = GaussianHMM(n_components=2, covariance_type="diag", n_iter=300, tol=1e-5, random_state=seed)
        try:
            m.fit(x.reshape(-1, 1))
            ll = m.score(x.reshape(-1, 1))
        except Exception:  # noqa: BLE001
            continue
        if np.isfinite(ll) and ll > best_ll:
            best, best_ll = m, ll
    return best


def filtered(m, x: np.ndarray) -> np.ndarray:
    """Forward pass only: P(state_t | x_1..x_t) for every t (rows sum to 1)."""
    mu = m.means_.ravel()
    sd = np.sqrt(np.array([c.ravel()[0] for c in m.covars_]))
    lik = stats.norm.pdf(x[:, None], mu[None, :], sd[None, :]) + 1e-300
    a = m.startprob_ * lik[0]
    a /= a.sum()
    out = np.empty((len(x), 2))
    out[0] = a
    for t in range(1, len(x)):
        a = (a @ m.transmat_) * lik[t]
        a /= a.sum()
        out[t] = a
    return out


def regime_series(t: pd.DataFrame, r: pd.Series) -> pd.DataFrame:
    """P(Bull) at every origin in t, from weekly refits on data before each origin."""
    rows, model, bull, agree, fits = [], None, None, 0, 0
    week = None
    for _, q in t.iterrows():
        day = q["day"]
        past = r[r.index < day]
        if len(past) < FIT_DAYS:
            rows.append({"origin_utc": q["origin_utc"], "p_bull": np.nan})
            continue
        wk = day.isocalendar()[:2]
        if wk != week or model is None:
            x = past.tail(FIT_DAYS).to_numpy()
            model = fit_hmm(x)
            var = np.array([c.ravel()[0] for c in model.covars_])
            bull = int(np.argmin(var))
            agree += int(np.argmax(model.means_.ravel()) == bull)
            fits += 1
            week, fit_start = wk, past.tail(FIT_DAYS).index[0]
        xs = past[past.index >= fit_start].to_numpy()        # forward pass from the fit window start
        p = filtered(model, xs)[-1, bull]
        mu = model.means_.ravel()
        rows.append({"origin_utc": q["origin_utc"], "p_bull": float(p),
                     "mu_bull": float(mu[bull]), "mu_bear": float(mu[1 - bull])})
    out = pd.DataFrame(rows)
    out.attrs.update(fits=fits, agree=agree)
    return out


def runs(states: np.ndarray) -> list[int]:
    out, n = [], 1
    for a, b in zip(states[:-1], states[1:]):
        if a == b:
            n += 1
        else:
            out.append(n)
            n = 1
    out.append(n)
    return out


def block_boot_ratio(vol: np.ndarray, bear: np.ndarray, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(vol)
    ratios = []
    for _ in range(BOOT):
        starts = rng.integers(0, n - BLOCK + 1, size=n // BLOCK + 1)
        idx = np.concatenate([np.arange(s, s + BLOCK) for s in starts])[:n]
        v, b = vol[idx], bear[idx]
        if b.all() or (~b).all():
            continue
        ratios.append(v[b].mean() / v[~b].mean())
    return float(np.percentile(ratios, 2.5)), float(np.percentile(ratios, 97.5))


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    t, _ = dor.build()
    r = daily_returns()
    reg = regime_series(t, r)
    t = t.merge(reg, on="origin_utc", how="left")
    t.to_parquet(CACHE)
    ev = dor.eval_origins(t, N_EVAL).copy()
    ev = ev[ev["p_bull"].notna()]
    ev["bull"] = ev["p_bull"] >= 0.5
    ev["p_state"] = np.where(ev["bull"], ev["p_bull"], 1 - ev["p_bull"])
    ev["rvol"] = np.sqrt(ev["rv_fut"])
    half = len(ev) // 2
    lines = ["# Part D: regime filter (2-state Gaussian HMM)", "",
             f"Origins: the last {len(ev)} matured daily 07:00 NY origins "
             f"({ev['origin_utc'].iloc[0]:%d %b %Y} – {ev['origin_utc'].iloc[-1]:%d %b %Y}). "
             f"Daily NQ=F returns since {r.index[0]:%Y}, rolls de-jumped; weekly refits on the last {FIT_DAYS} days "
             f"({reg.attrs['fits']} fits, {STARTS} starts each); filtered probabilities only. "
             f"Bull = lower-variance state; it was also the higher-mean state in {reg.attrs['agree']} of {reg.attrs['fits']} fits.", ""]

    # 1 stability
    st = ev["bull"].to_numpy()
    rl = runs(st)
    med = float(np.median(rl))
    g1 = med >= 10
    # 2 meaningful
    bear = ~st
    vol = ev["rvol"].to_numpy()
    if bear.any() and (~bear).any():
        ratio = vol[bear].mean() / vol[~bear].mean()
        lo, hi = block_boot_ratio(vol, bear)
    else:
        ratio, lo, hi = np.nan, np.nan, np.nan
    g2 = bool(np.isfinite(ratio) and ratio >= 1.3 and lo > 1.0)
    # 3 honest probability
    p_state = ev["p_state"].to_numpy()
    rows3, g3_ok, judged = [], True, 0
    for a, b in BUCKETS:
        hits, ps = [], []
        for i in range(len(ev) - FWD):
            if a <= p_state[i] < b:
                hits.append(np.mean(st[i + 1:i + 1 + FWD] == st[i]))
                ps.append(p_state[i])
        n = len(hits)
        if n >= 10:
            judged += 1
            ok = abs(np.mean(hits) - np.mean(ps)) <= 0.10
            g3_ok &= ok
        else:
            ok = None
        rows3.append((a, min(b, 1.0), n, np.mean(ps) if n else np.nan, np.mean(hits) if n else np.nan, ok))
    g3 = bool(g3_ok and judged >= 1)

    lines += ["## Gate D", "", "| test | result | needed | pass |", "|---|---|---|---|",
              f"| 1 stable: median run length | {med:.1f} trading days ({len(rl)} runs; Bull {st.sum()} / Bear {bear.sum()} days) | ≥ 10 | {'yes' if g1 else 'no'} |",
              f"| 2 meaningful: Bear / Bull next-23h realised vol | {ratio:.2f} (95% block bootstrap {lo:.2f}–{hi:.2f}) | ≥ 1.3, interval > 1.0 | {'yes' if g2 else 'no'} |",
              f"| 3 honest probability | {judged} bucket(s) judged | within ±10 pts | {'yes' if g3 else 'no'} |", "",
              "Test 3 detail (stated state's probability vs the share of the next 20 origins later filtered into that state):", "",
              "| bucket | n | mean stated prob | later same state | within ±10 pts |", "|---|--:|--:|--:|---|"]
    for a, b, n, mp, mh, ok in rows3:
        lines.append(f"| {a:.1f}–{b:.1f} | {n} | {100 * mp:.0f}% | {100 * mh:.0f}% | "
                     f"{'—' if ok is None else 'yes' if ok else 'no'} |" if n else f"| {a:.1f}–{b:.1f} | 0 | — | — | — |")

    # direction
    lines += ["", "## Direction (not shown on the page unless clearly significant in both halves)", "",
              "| part | n | hit rate (Bull → up, Bear → down) | binomial p | rank corr P(Bull) vs next-23h move | p |",
              "|---|--:|--:|--:|--:|--:|"]
    sig_both = True
    for name, part in (("all", ev), ("older half", ev.iloc[:half]), ("newer half", ev.iloc[half:])):
        up = part["move_pct"] > 0
        hit = (up == part["bull"]).mean()
        bp = stats.binomtest(int((up == part["bull"]).sum()), len(part), 0.5, alternative="greater").pvalue
        rho, rp = stats.spearmanr(part["p_bull"], part["move_pct"])
        if name != "all":
            sig_both &= bool(bp < 0.05 and rho > 0 and rp < 0.05)
        lines.append(f"| {name} | {len(part)} | {100 * hit:.1f}% | {bp:.3f} | {rho:+.3f} | {rp:.3f} |")
    lines += ["", f"Direction significant in both halves: **{'yes' if sig_both else 'no'}**. "
              + ("" if sig_both else "No direction is shown anywhere.")]

    passed = g1 and g2 and g3
    lines += ["", "## Verdict", "",
              f"**Gate D {'passed' if passed else 'failed'}**: the regime chip is "
              f"{'live' if passed else 'hidden (no placeholder)'}."]
    if not passed:
        why = []
        if not g1:
            why.append(f"states flip too often (median run {med:.1f} days < 10)")
        if not g2:
            why.append(f"the Bear state doesn't carry clearly higher next-day volatility (ratio {ratio:.2f}, interval {lo:.2f}–{hi:.2f})")
        if not g3:
            why.append("the stated probabilities don't match how often the state persists")
        lines.append("Why: " + "; ".join(why) + ".")
    lines += ["", f"Latest origin {ev['origin_utc'].iloc[-1]:%a %d %b %Y}: P(Bull) = {ev['p_bull'].iloc[-1]:.2f}.",
              "", "Run: `python lab/regime.py`."]
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
