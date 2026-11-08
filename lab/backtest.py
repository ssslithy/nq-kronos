"""Phase 6d/6e: regenerate lab/results/report.md and charts from cached forecasts.

    python lab/backtest.py            # needs lab/cache/{bars,kronos,garch,naive}.parquet

Band sources: Kronos, Kronos widened (one multiplier on the band around the median, fitted
on the first two-thirds so the in-sample +24h P10-P90 hit rate ~ 80%, then frozen), GARCH,
naive. All sources use the same origins (those Kronos has a cached forecast for).

Costs (MNQ, $2/pt): 1 tick slippage each way + $1.00 commission per side per contract;
slippage doubled for fills in bars starting 22:00-07:00 London.
In-sample / out-of-sample: anything fitted (widening multiplier, vol-sizing reference
width) uses the first two-thirds only; the final third is reported separately.
"""
from __future__ import annotations

import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
import strategies as st  # noqa: E402

POINT_VALUE = 2.0          # MNQ $ per point
COMMISSION = 1.00          # $ per side per contract
HOURS_PER_YEAR = 23 * 252
TARGET_HIT = 0.80
SOURCES = ["Kronos", "Kronos widened", "GARCH", "Naive"]
COLORS = {"Kronos": "#2a9d8f", "Kronos widened": "#e9c46a", "GARCH": "#8d6cab",
          "Naive": "#9aa5ab", "Fixed size": "#e76f51"}


# ---- data --------------------------------------------------------------------------

def load_sources(df: pd.DataFrame):
    k = pd.read_parquet(c.KRONOS).set_index("origin")
    g = pd.read_parquet(c.GARCH).set_index("origin")
    n = pd.read_parquet(c.NAIVE).set_index("origin")
    common = k.index.intersection(g.index).intersection(n.index).sort_values()
    return {"Kronos": k.loc[common], "GARCH": g.loc[common], "Naive": n.loc[common]}, common


def to_bands(name: str, t: pd.DataFrame, df: pd.DataFrame) -> st.Bands:
    pos = df.index.get_indexer(t.index)
    return st.Bands(name, pos, {q: t[c.qcols(q)].to_numpy() for q in c.QS})


def widen(b: st.Bands, mult: float, name: str) -> st.Bands:
    med = b.q[50]
    return st.Bands(name, b.origin_pos, {q: med + mult * (b.q[q] - med) for q in c.QS})


def hits(b: st.Bands, actual: np.ndarray, k: int, rows: np.ndarray) -> np.ndarray:
    a = actual[rows, k - 1]
    ok = ~np.isnan(a)
    return ((b.q[10][rows, k - 1] <= a) & (a <= b.q[90][rows, k - 1]))[ok]


def fit_multiplier(b: st.Bands, actual: np.ndarray, rows: np.ndarray) -> float:
    grid = np.round(np.arange(0.50, 6.001, 0.01), 2)
    rates = [hits(widen(b, m, ""), actual, 24, rows).mean() for m in grid]
    return float(grid[int(np.argmin(np.abs(np.array(rates) - TARGET_HIT)))])


# ---- money -------------------------------------------------------------------------

def fill_cost(times: pd.DatetimeIndex, i: int) -> float:
    hr = times[i].tz_convert(c.cal.LONDON).hour
    ticks = 2 if (hr >= 22 or hr < 7) else 1
    return COMMISSION + ticks * st.TICK * POINT_VALUE


def evaluate(trades: list[st.Trade], m: st.Market):
    """Per-trade net $ and an hourly mark-to-market P&L series (costs at fill bars)."""
    pnl = np.zeros(len(m.c))
    rows = []
    for t in trades:
        mult = t.side * t.size * POINT_VALUE
        cost = t.size * (fill_cost(m.times, t.entry_i) + fill_cost(m.times, t.exit_i))
        if t.exit_i == t.entry_i:
            pnl[t.entry_i] += mult * (t.exit_px - t.entry_px)
        else:
            pnl[t.entry_i] += mult * (m.c[t.entry_i] - t.entry_px)
            if t.exit_i - t.entry_i > 1:
                seg = slice(t.entry_i + 1, t.exit_i)
                pnl[seg] += mult * (m.c[seg] - m.c[t.entry_i: t.exit_i - 1])
            pnl[t.exit_i] += mult * (t.exit_px - m.c[t.exit_i - 1])
        pnl[t.entry_i] -= t.size * fill_cost(m.times, t.entry_i)
        pnl[t.exit_i] -= t.size * fill_cost(m.times, t.exit_i)
        gross_pts = t.side * (t.exit_px - t.entry_px)
        rows.append({"entry_i": t.entry_i, "pts": gross_pts,
                     "net": mult * (t.exit_px - t.entry_px) - cost, "size": t.size})
    return pd.DataFrame(rows), pnl


def metrics(tr: pd.DataFrame, pnl: np.ndarray, lo: int, hi: int) -> dict:
    """Stats for trades entered in bars [lo, hi) and hourly P&L over the same bars."""
    t = tr[(tr["entry_i"] >= lo) & (tr["entry_i"] < hi)] if len(tr) else tr
    h = pnl[lo:hi]
    eq = np.cumsum(h)
    dd = float((np.maximum.accumulate(np.concatenate([[0], eq])) - np.concatenate([[0], eq])).max())
    sd = h.std()
    return {
        "trades": len(t),
        "win": float((t["net"] > 0).mean()) if len(t) else np.nan,
        "avg_pts": float(t["pts"].mean()) if len(t) else np.nan,
        "avg_usd": float(t["net"].mean()) if len(t) else np.nan,
        "total": float(h.sum()),
        "sharpe": float(h.mean() / sd * np.sqrt(HOURS_PER_YEAR)) if sd > 0 else np.nan,
        "maxdd": dd,
        "avg_size": float(t["size"].mean()) if len(t) else np.nan,
    }


# ---- report helpers ----------------------------------------------------------------

def f_money(v):
    return "–" if v != v else (f"−${abs(v):,.0f}" if v < 0 else f"${v:,.0f}")


def strat_table(results: dict, segs: dict, show_size=False) -> str:
    head = "| Band source | Period | Trades | Win rate | Avg trade (pts) | Avg trade ($ net) | Total net $ | Sharpe | Max drawdown |"
    sep = "|---|---|--:|--:|--:|--:|--:|--:|--:|"
    if show_size:
        head += " Avg size |"
        sep += "--:|"
    lines = [head, sep]
    for name, (tr, pnl) in results.items():
        for seg, (lo, hi) in segs.items():
            x = metrics(tr, pnl, lo, hi)
            row = (f"| {name} | {seg} | {x['trades']} | {x['win']:.0%} | {x['avg_pts']:+.1f} | "
                   f"{f_money(x['avg_usd'])} | {f_money(x['total'])} | {x['sharpe']:+.2f} | {f_money(x['maxdd'])} |")
            if x["trades"] == 0:
                row = f"| {name} | {seg} | 0 | – | – | – | {f_money(x['total'])} | – | {f_money(x['maxdd'])} |"
            if show_size:
                row += f" {x['avg_size']:.1f} |"
            lines.append(row)
    return "\n".join(lines)


def equity_chart(path, title, series: dict, m: st.Market, split_i: int):
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=120)
    x = m.times[m.start:]
    for name, pnl in series.items():
        base = name.split(" (")[0]
        ax.plot(x, np.cumsum(pnl[m.start:]), lw=1.4, label=name,
                color=COLORS.get(base, None), ls="--" if "follow" in name else "-")
    ax.axvline(m.times[split_i], color="#555", lw=1, ls=":")
    ax.text(m.times[split_i], ax.get_ylim()[1], "  out-of-sample →", va="top", fontsize=8, color="#555")
    ax.axhline(0, color="#999", lw=0.6)
    ax.set_title(title, fontsize=11)
    ax.set_ylabel("cumulative net $ (MNQ)")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, ncol=3, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---- main --------------------------------------------------------------------------

def main() -> int:
    t0 = time.perf_counter()
    c.RESULTS.mkdir(parents=True, exist_ok=True)
    df, rolls = c.load_bars()
    raw, common = load_sources(df)
    if len(common) < 30:
        print(f"only {len(common)} common forecasts cached; run build_forecasts.py and baselines.py first")
        return 1
    actual = c.realised(df, common).to_numpy()
    n = len(common)
    split_t = common[int(n * 2 / 3)]
    split_i = df.index.get_loc(split_t)
    origin_pos = df.index.get_indexer(common)

    # forecasts whose 24 bars touch a roll window are left out of calibration
    in_roll = np.array([c.in_roll_window(df.index[p: p + c.HORIZON + 1], rolls).any() for p in origin_pos])
    target_pos = origin_pos + c.HORIZON
    is_rows = np.where((target_pos < split_i) & ~in_roll)[0]          # target known before the split
    oos_rows = np.where((origin_pos >= split_i) & ~in_roll)[0]
    all_rows = np.where(~in_roll)[0]

    bands = {name: to_bands(name, raw[name], df) for name in ("Kronos", "GARCH", "Naive")}
    mult = fit_multiplier(bands["Kronos"], actual, is_rows)
    bands["Kronos widened"] = widen(bands["Kronos"], mult, "Kronos widened")
    bands = {s: bands[s] for s in SOURCES}

    # ---- calibration
    def calib(rows):
        out = []
        for s, b in bands.items():
            r = {"source": s}
            for k in (1, 6, 24):
                hh = hits(b, actual, k, rows)
                r[f"+{k}h"] = hh.mean() if len(hh) else np.nan
            r["width24"] = float(np.mean(b.q[90][rows, -1] - b.q[10][rows, -1]))
            r["n"] = len(rows)
            out.append(r)
        return pd.DataFrame(out)

    cal_tables = {"All": calib(all_rows), "In-sample (first 2/3)": calib(is_rows),
                  "Out-of-sample (final 1/3)": calib(oos_rows)}

    # ---- strategies
    blocked = c.in_roll_window(df.index, rolls)
    start = int(origin_pos[0])
    m = st.Market.build(df, blocked, start)
    segs = {"All": (start, m.end + 1), "In-sample": (start, split_i), "Out-of-sample": (split_i, m.end + 1)}

    def ref_width(b):  # vol-sizing anchor: median IS +24h width (fitted in-sample only)
        return float(np.median(b.width24()[origin_pos < split_i]))

    s1 = {"Fixed size": evaluate(st.trend(m, None, None), m)}
    for s, b in bands.items():
        s1[s] = evaluate(st.trend(m, b, ref_width(b)), m)
    s2, skipped = {}, {}
    for s, b in bands.items():
        s2[s] = evaluate(st.stops_targets(m, b), m)
        skipped[s] = st.stops_targets.skipped
    s3f = {s: evaluate(st.band_break(m, b, "fade"), m) for s, b in bands.items()}
    s3w = {s: evaluate(st.band_break(m, b, "follow"), m) for s, b in bands.items()}

    # ---- charts
    equity_chart(c.RESULTS / "equity_1_vol_sized_trend.png", "1. Vol-sized trend (MA 20/100), net of costs",
                 {k: v[1] for k, v in s1.items()}, m, split_i)
    equity_chart(c.RESULTS / "equity_2_stops_targets.png", "2. Distribution stops/targets, net of costs",
                 {k: v[1] for k, v in s2.items()}, m, split_i)
    equity_chart(c.RESULTS / "equity_3_band_break.png", "3. Band break: fade (solid) vs follow (dashed), net of costs",
                 {**{f"{k} (fade)": v[1] for k, v in s3f.items()},
                  **{f"{k} (follow)": v[1] for k, v in s3w.items()}}, m, split_i)

    # ---- verdict inputs
    oos = cal_tables["Out-of-sample (final 1/3)"].set_index("source")
    gap = (oos["+24h"] - TARGET_HIT).abs()
    best_cal = gap.idxmin()

    def oos_stat(res, s):
        return metrics(res[s][0], res[s][1], *segs["Out-of-sample"])

    strat_sets = {"1. vol-sized trend": s1, "2. stops/targets": s2,
                  "3a. band-break fade": s3f, "3b. band-break follow": s3w}
    beats = []
    for label, res in strat_sets.items():
        g = oos_stat(res, "GARCH")
        for s in ("Kronos", "Kronos widened"):
            k = oos_stat(res, s)
            if k["total"] > g["total"] and k["total"] > 0:
                beats.append(f"{label}: {s} {f_money(k['total'])} vs GARCH {f_money(g['total'])} out-of-sample")

    # ---- report
    first, last = df.index[start], df.index[-1]
    n_k_all = len(pd.read_parquet(c.KRONOS))
    lines = []
    A = lines.append
    A("# NQ Kronos strategy lab: report\n")
    A(f"_Generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC by `lab/backtest.py` from cached forecasts. "
      f"Research only — not investment advice._\n")
    A("## Setup\n")
    A(f"- **Period:** {first:%Y-%m-%d %H:%M} → {last:%Y-%m-%d %H:%M} UTC; {n} forecast origins "
      f"(every {c.STEP}rd closed bar) common to all band sources ({n_k_all} Kronos forecasts cached).")
    A(f"- **Split:** in-sample = origins before {split_t:%Y-%m-%d %H:%M} UTC (first two-thirds); "
      f"out-of-sample = the final third. Fitted on in-sample only: the Kronos widening multiplier "
      f"(**×{mult:.2f}**) and each source's vol-sizing reference width.")
    A("- **Kronos:** Kronos-small, 512-bar context, 24 steps, 30 sampled paths, T=1.0, top_p=0.9, "
      "only bars up to each origin. **GARCH:** GARCH(1,1), Student-t, zero mean, refitted at every origin "
      f"on the previous {2000} hourly returns, 1,000 simulated paths. **Naive:** no-change median, band "
      "from trailing 20-day realised volatility (normal, √h scaling).")
    A("- **Kronos widened:** each quantile moved away from the median by the fitted multiplier "
      "(P10′ = P50 + m·(P10 − P50), same for the others), at every horizon.")
    A("- **Costs:** MNQ ($2/pt); 1 tick slippage each way + $1.00 commission per side per contract; "
      "slippage doubled for fills in bars starting 22:00–07:00 London. Entries at the next bar's open.")
    A(f"- **Rolls:** prices are back-adjusted; {sum(r['method'] != 'contract' for r in rolls)} of {len(rolls)} roll gaps "
      "are estimates (Yahoo drops expired contracts). No position is held inside a roll window "
      "(roll date − 1 day to expiry + 4 days), and forecasts whose 24 hours touch one are left out of "
      f"calibration ({int(in_roll.sum())} of {n}).")
    A(f"- **Sizes:** fixed rules trade {st.BASE} MNQ; vol-sized trend trades {st.BASE} × (reference width ÷ "
      f"current +24h P10–P90 width), rounded, clipped to 1–{st.MAX_MULT * st.BASE}.\n")

    A("## Calibration: share of actual closes inside P10–P90 (target ≈ 80%)\n")
    for seg, tb in cal_tables.items():
        A(f"**{seg}** — {int(tb['n'].iloc[0])} forecasts\n")
        A("| Band source | +1h | +6h | +24h | Avg +24h width (pts) |")
        A("|---|--:|--:|--:|--:|")
        for _, r in tb.iterrows():
            A(f"| {r['source']} | {r['+1h']:.0%} | {r['+6h']:.0%} | {r['+24h']:.0%} | {r['width24']:,.0f} |")
        A("")

    A("## Strategy 1: vol-sized trend (MA 20/100, always in the market)\n")
    A("Same entries and exits for every row; only the position size differs. "
      "Kronos and Kronos widened size identically by construction (a constant multiplier cancels out "
      "against the reference width).\n")
    A(strat_table(s1, segs, show_size=True))
    A("\n![equity](equity_1_vol_sized_trend.png)\n")
    A("## Strategy 2: distribution stops and targets\n")
    A("Enter on an MA crossover; stop just beyond +24h P10 (P90 for shorts), target +24h P75 (P25); "
      "skip if reward ÷ risk < 1; exit on stop, target, opposite crossover or after 24 bars. "
      "Crossovers skipped for reward/risk < 1: "
      + ", ".join(f"{s} {skipped[s]}" for s in bands) + ". "
      "Note: for a band roughly symmetric around the last close, P75 is ~0.67σ away and P10 ~1.28σ, "
      "so reward ÷ risk ≈ 0.5 and most crossovers are skipped; the rule only trades when a forecast's "
      "distribution leans strongly in the trade's direction. The rule was fixed in advance and is "
      "reported as specified.\n")
    A(strat_table(s2, segs))
    A("\n![equity](equity_2_stops_targets.png)\n")
    A("## Strategy 3: band break, fade vs follow\n")
    A("Within 6 hours of a forecast, the first close outside that hour's P10–P90 triggers a trade at the "
      "next open: **fade** (against the break, target the median) or **follow** (with the break). "
      "Stop one band-width from entry; exit after 6 hours.\n")
    A("**3a. Fade**\n")
    A(strat_table(s3f, segs))
    A("\n**3b. Follow**\n")
    A(strat_table(s3w, segs))
    A("\n![equity](equity_3_band_break.png)\n")

    A("## Verdict\n")
    k_oos, g_oos, w_oos = oos.loc["Kronos", "+24h"], oos.loc["GARCH", "+24h"], oos.loc["Kronos widened", "+24h"]
    cal_line = (f"Out of sample, the +24h band held the actual close {k_oos:.0%} of the time for raw Kronos, "
                f"{w_oos:.0%} for Kronos widened (×{mult:.2f}, fitted in-sample), {g_oos:.0%} for GARCH and "
                f"{oos.loc['Naive', '+24h']:.0%} for naive (target ≈ 80%). Closest to 80%: **{best_cal}**.")
    if gap["Kronos"] < gap["GARCH"]:
        cal_line += " **Raw Kronos is better calibrated than GARCH.**"
    else:
        cal_line += " **No — raw Kronos is not better calibrated than GARCH;** its band is "
        cal_line += "too narrow." if k_oos < TARGET_HIT else "too wide."
    if gap["Kronos widened"] < gap["GARCH"]:
        cal_line += " Widening fixes enough of that to beat GARCH out of sample."
    else:
        cal_line += " Even the widened Kronos band does not beat GARCH out of sample."
    A(cal_line + "\n")
    if beats:
        A("Out of sample, after costs, a Kronos band source made more money than the same rule on GARCH bands in: "
          + "; ".join(beats) + ". Treat this with caution: one period, a few hundred trades at most, "
          "and no correction for having tried several rules and four band sources.\n")
    else:
        A("**No — out of sample, after costs, neither Kronos nor Kronos widened beat the same rule on GARCH bands "
          "(with a positive result) in any of the strategies.**\n")
    A("Caveats: a single ~6-month period dominated by one market regime; Kronos uses 30 paths per forecast "
      "(quantiles are noisy); roll gaps before Sep 2026 are estimated; Yahoo data is delayed and unofficial. "
      "Anything that looks good here should go to a demo account before real money.\n")

    (c.RESULTS / "report.md").write_text("\n".join(lines))
    print(f"report: {c.RESULTS / 'report.md'}  ({n} origins, split {split_t:%Y-%m-%d %H:%M}, "
          f"widening ×{mult:.2f})  in {time.perf_counter() - t0:.1f}s")
    print(cal_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
