"""24h NQ volatility forecast with GARCH(1,1), Student-t (volatility only, not direction).

Model choices are copied from the Phase 6 lab (lab/baselines.py):

- Returns: 100 x hourly log returns of the back-adjusted close, between consecutive bars of
  the cleaned series. Gap returns across the 17:00 NY daily break and the weekend are
  INCLUDED, as in the lab. (Excluding them from the fit, or giving them their own variance,
  was tested in lab/garch_vol_calibration.py and changed nothing: hit rates 93-94% either way.)
- Fit: the trailing 2,000 returns ending at the forecast bar; GARCH(1,1), zero mean,
  Student-t errors, `arch` with rescale=False. Refitted at every forecast.
- Horizon: the next 24 bars (= the next 24 tradable hours).
- Raw sigma: square root of the summed 24 analytic variance forecasts.
- Rolls: additive back-adjustment. Measured from the incoming contract where Yahoo still has
  it; otherwise (expired contracts 404) the switch bar is the largest upward jump in
  [roll date - 1 day, expiry + 4 days] and the gap is CARRY x price. Same as the lab.

Calibration (lab/garch_vol_calibration.py, 429 daily 07:00 NY origins Oct 2024 - Nov 2026):
at this origin the raw sigma is too wide (RMS(move / sigma) ~0.63; +-1.28 sigma held 94%).
The cause is GARCH's variance level (forecast variance ~2.4x the realised sum of squared hourly
returns), not gaps or hourly mean reversion. So:
- k = RMS(move / raw sigma) over the trailing 250 matured daily forecasts (min 60), using only
  forecasts whose 24 bars had closed before this origin and that don't touch a roll window.
- sigma = k x raw sigma. 80% range = last x exp(+-1.2816 sigma); 68% range = last x exp(+-sigma).
  Out of sample this held 84% / 81% (older / newer half) for the 80% range.
  Alternative tested: q80 = 80th percentile of |move / raw sigma| over the same 250, range
  last x exp(+-q80 x raw sigma). It held 81% / 78%: closer on the older half, further on the
  newer (confirmation) half, so +-1.2816 k sigma is kept.
- The simulated GARCH quantiles are kept as a diagnostic only: raw they held 72-79%, and
  scaled by k they fall to 53-57% (fat-tailed paths make the quantiles narrower than 1.28 sigma).
- Cash session (bars 09:00-15:00 NY): variance = 24h variance x the cash hours' share of the
  horizon's variance under the intraday profile (mean squared hourly return by NY hour over
  the trailing 60 trading days). GARCH alone would spread variance evenly (7/24).

    python forecast/garch_vol.py                              # forecast as of now
    python forecast/garch_vol.py --now "2026-11-05 08:10"     # a past time, New York
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import warnings
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import fetch  # noqa: E402
import nq_calendar as cal  # noqa: E402

log = logging.getLogger("garch_vol")

WINDOW = 2000          # hourly returns in each fit
HORIZON = 24           # bars
SIMS = 10_000
DAYS = 729             # history to download (Yahoo's 1h cap); covers the k and regime lookbacks
ORIGIN_NY_HOUR = 7     # the 07:00-08:00 NY bar: the last complete bar before 08:30 NY
CASH_NY_HOURS = range(9, 16)   # bars starting 09:00..15:00 NY = 7 hours 09:00-16:00
REGIME_DAYS = 252
K_DAYS, K_MIN = 250, 60        # calibration factor lookback (matured daily forecasts), minimum
PROFILE_DAYS = 60              # trading days for the intraday variance profile
Z80 = 1.2816                   # normal P90
CARRY = 294.0 / 29560.0        # quarterly calendar spread as a share of price (Sep 2026, measured)
ROLL_PAD_BEFORE, ROLL_PAD_AFTER = pd.Timedelta(days=1), pd.Timedelta(days=4)
EVENTS_FILE = Path(__file__).resolve().parent / "events_2026_2027.yaml"


# ---- data ----------------------------------------------------------------------------

def roll_window(r: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Period around a roll in which Yahoo may switch contracts."""
    return (pd.Timestamp(r["roll_date"], tz="UTC") - ROLL_PAD_BEFORE,
            pd.Timestamp(r["expiry"], tz="UTC") + ROLL_PAD_AFTER)


def roll_adjust_carry(df: pd.DataFrame, days: int) -> tuple[pd.DataFrame, list[dict]]:
    """Back-adjust every quarterly roll in the data (shared with lab/common.py).

    Incoming contract on Yahoo -> measured switch and gap (fetch._find_switch). If it is on
    Yahoo but no switch is visible, nothing is adjusted (the switch hasn't happened yet).
    Incoming contract gone (expired) -> switch = largest upward close-to-close jump in the
    roll window, gap = CARRY x price (the raw jump also contains that hour's real move).
    """
    df = df.copy()
    rolls: list[dict] = []
    for r in reversed(cal.roll_calendar(df.index[0].date(), df.index[-1].date())):
        lo, hi = roll_window(r)
        if hi < df.index[0] or lo > df.index[-1]:
            continue
        pos, gap, method, have_inc = None, None, None, False
        try:
            inc = fetch._normalise(fetch._download(r["incoming"], days, attempts=1))
            have_inc = True
            for e in rolls:
                inc.loc[inc.index < pd.Timestamp(e["switch_utc"]), fetch.PRICE_COLS] += e["gap"]
            pos = fetch._find_switch(df, inc, lo, hi)
            if pos is not None:
                prev = df.index[pos - 1]
                gap = float(inc.at[prev, "close"] - df.at[prev, "close"])
                df.iloc[pos, df.columns.get_indexer(fetch.COLS)] = inc.loc[df.index[pos], fetch.COLS].values
                method = "contract"
        except fetch.DataError:
            pass
        if pos is None and have_inc:
            log.info("roll %s->%s: incoming contract on Yahoo but no switch visible; not adjusted",
                     r["outgoing"], r["incoming"])
            continue
        if pos is None:
            win = df[(df.index >= lo) & (df.index <= hi)]
            if len(win) < 5:
                continue
            t = win["close"].diff().idxmax()
            pos = df.index.get_loc(t)
            gap = CARRY * float(df["close"].iloc[pos - 1])
            method = "estimated-carry"
        df.iloc[:pos, df.columns.get_indexer(fetch.PRICE_COLS)] += gap
        rolls.insert(0, {"roll_date": r["roll_date"].isoformat(), "expiry": r["expiry"].isoformat(),
                         "switch_utc": df.index[pos].isoformat(), "outgoing": r["outgoing"],
                         "incoming": r["incoming"], "gap": round(gap, 2), "method": method})
    return df, rolls


def load_bars(now: datetime | None = None, days: int = DAYS) -> tuple[pd.DataFrame, list[dict]]:
    """Cleaned, roll-adjusted hourly bars, cut at `now` (only bars closed by then).

    As in the lab, the back-adjustment is computed on the full download and the series is
    then cut; adjustments only shift price levels, so returns up to `now` are unaffected
    (apart from a roll's own switch bar, which is repaired from the incoming contract).
    """
    real_now = datetime.now(cal.UTC)
    now = now or real_now
    df = fetch._normalise(fetch._download(fetch.TICKER, days))
    df = fetch._drop_off_session(df)
    df = fetch._drop_incomplete(df, real_now)
    df, rolls = roll_adjust_carry(df, days)
    df = df[df.index + pd.Timedelta(hours=1) <= pd.Timestamp(now)]
    if df.empty:
        raise fetch.DataError("no closed bars before the forecast time")
    fetch.check_stale(df, now)
    df = fetch.filter_spikes(df)
    df = fetch.repair_high_low(df)
    fetch.sanity_check(df, min_bars=WINDOW + 1)
    fetch.check_missing(df)
    return df, rolls


# ---- model ---------------------------------------------------------------------------

def log_returns(close: pd.Series) -> pd.Series:
    return 100 * np.log(close).diff()


def fit(returns: np.ndarray):
    """GARCH(1,1), zero mean, Student-t, on percent log returns (lab settings)."""
    from arch import arch_model
    am = arch_model(returns, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return am.fit(disp="off", show_warning=False)


def seed_for(t: pd.Timestamp) -> int:
    return int(t.timestamp() // 3600) % (2**31)


def simulate(res, last: float, sims: int, seed: int) -> np.ndarray:
    """(sims, HORIZON) price paths from the fitted model.

    Shocks come from a seeded standardised Student-t, so a forecast is reproducible. (The lab
    passed random_state=Generator, which arch ignores for method="simulation": its paths were
    unseeded. Same model and distribution; only the Monte Carlo draw differs.)
    """
    from arch.univariate import StudentsT
    shocks = StudentsT(seed=np.random.default_rng(seed)).simulate([res.params["nu"]])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fc = res.forecast(horizon=HORIZON, method="simulation", simulations=sims,
                          reindex=False, rng=shocks)
    r = fc.simulations.values[-1]
    return last * np.exp(np.cumsum(r / 100, axis=1))


def step_variances(res) -> np.ndarray:
    """Analytic per-step variance forecasts (percent^2), steps 1..HORIZON."""
    return res.forecast(horizon=HORIZON, reindex=False).variance.values[-1]


def sigma_only(returns: np.ndarray) -> float:
    """Raw analytic 24h sigma (%) from the last WINDOW returns; cheap (no simulation)."""
    return float(np.sqrt(step_variances(fit(returns[-WINDOW:])).sum()))


def roll_windows_for(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp, dict]]:
    return [(*roll_window(r), r) for r in cal.roll_calendar(start.date(), end.date())]


def touches_roll(times, windows) -> bool:
    return any(lo <= pd.Timestamp(t) <= hi for t in times for lo, hi, _ in windows)


def roll_note(future: list[datetime]) -> str | None:
    for lo, hi, r in roll_windows_for(pd.Timestamp(future[0]), pd.Timestamp(future[-1])):
        if any(lo <= pd.Timestamp(t) <= hi for t in future):
            return (f"roll this week: {r['outgoing']} -> {r['incoming']} "
                    f"(roll date {r['roll_date']:%a %d %b}, expiry {r['expiry']:%a %d %b})")
    return None


# ---- prior daily forecasts: calibration factor, regime, realised ----------------------

def daily_origins(df: pd.DataFrame) -> np.ndarray:
    """Positions of the 07:00 NY bar on every NQ trading weekday present in the data."""
    ny = df.index.tz_convert(cal.NY)
    ok = (ny.hour == ORIGIN_NY_HOUR) & (ny.minute == 0) & (ny.weekday < 5)
    pos = np.flatnonzero(ok)
    return np.array([p for p in pos if cal.is_tradable_hour(df.index[p].to_pydatetime())], dtype=int)


def prior_forecasts(bars: pd.DataFrame, n: int = 330) -> pd.DataFrame:
    """Raw sigma and realised 24-bar move for up to `n` daily 07:00 NY origins before the last
    bar, each fitted only on the WINDOW returns up to its own origin. `move` is NaN until the
    forecast's 24 bars have closed (by the last bar of `bars`). 330 leaves K_DAYS matured
    forecasts after the ~18% that touch a roll window are excluded from k."""
    close = bars["close"].to_numpy()
    r = log_returns(bars["close"]).to_numpy()
    pos = daily_origins(bars)
    pos = pos[(pos >= WINDOW) & (pos < len(bars) - 1)][-n:]
    windows = roll_windows_for(bars.index[0], bars.index[-1] + pd.Timedelta(days=3))
    rows = []
    for p in pos:
        matured = p + HORIZON < len(close)
        rows.append({
            "origin_utc": bars.index[p], "pos": int(p),
            "sigma_raw_pct": sigma_only(r[p - WINDOW + 1:p + 1]),
            "move_pct": float(100 * np.log(close[p + HORIZON] / close[p])) if matured else np.nan,
            "roll": touches_roll(bars.index[p + 1:p + HORIZON + 1], windows),
        })
    return pd.DataFrame(rows)


def calibration_k(prior: pd.DataFrame) -> tuple[float, int]:
    """RMS(move / raw sigma) over the trailing K_DAYS matured, roll-free daily forecasts."""
    m = prior[prior["move_pct"].notna() & ~prior["roll"]].tail(K_DAYS)
    if len(m) < K_MIN:
        raise ValueError(f"only {len(m)} matured daily forecasts for the calibration factor (< {K_MIN})")
    z = m["move_pct"] / m["sigma_raw_pct"]
    return float(np.sqrt(np.mean(z ** 2))), int(len(m))


def regime_of(pctile: float) -> str:
    if pctile > 90:
        return "High"
    if pctile > 75:
        return "Elevated"
    if pctile < 25:
        return "Calm"
    return "Normal"


def intraday_profile(bars: pd.DataFrame, days: int = PROFILE_DAYS) -> dict[int, float]:
    """Share of mean squared hourly return by NY bar-start hour over the trailing `days`
    trading days (data up to the last bar only). Shares sum to 1 over the hours present."""
    r = log_returns(bars["close"]).iloc[1:]
    td = pd.Series([cal.trade_day(t.to_pydatetime()) for t in r.index], index=r.index)
    keep = td.isin(sorted(td.unique())[-days:])
    r = r[keep]
    m = (r ** 2).groupby(r.index.tz_convert(cal.NY).hour).mean()
    return {int(h): float(v) for h, v in (m / m.sum()).items()}


# ---- scheduled events (forecast/events_2026_2027.yaml) ----------------------------------

EVENT_NOTE = "Scheduled release today: realised move often exceeds the GARCH range."
EVENT_NOTE_HORIZON = "Scheduled release inside the 24h horizon: realised move often exceeds the GARCH range."


@lru_cache(maxsize=1)
def load_events(path: str = str(EVENTS_FILE)) -> tuple[date, list[dict]]:
    """(covered_until, events sorted by time); each event has kind, name, release, time_utc, source."""
    import yaml
    doc = yaml.safe_load(Path(path).read_text())
    out = []
    for kind, t in doc["types"].items():
        for d in t["dates"]:
            day, hhmm = (str(d).split() + [t["time"]])[:2]
            ny = pd.Timestamp(f"{day} {hhmm}").tz_localize(cal.NY)
            out.append({"kind": kind, "name": t["name"], "release": t["release"],
                        "time_utc": ny.tz_convert("UTC"), "source": t["sources"][0]})
    return pd.Timestamp(doc["covered_until"]).date(), sorted(out, key=lambda e: e["time_utc"])


def events_between(start: pd.Timestamp, end: pd.Timestamp, today: date) -> dict:
    """Scheduled releases in (start, end] and the flag text for the page and the alert."""
    covered_until, events = load_events()
    if end.tz_convert(cal.NY).date() > covered_until:
        return {"loaded": False, "items": [], "today": False,
                "note": f"event calendar not loaded after {covered_until} (update {EVENTS_FILE.name})"}
    items = []
    for e in events:
        if start < e["time_utc"] <= end:
            ny = e["time_utc"].tz_convert(cal.NY)
            items.append({"kind": e["kind"], "name": e["name"], "release": e["release"],
                          "time_utc": e["time_utc"].isoformat(), "ny": f"{ny:%a %d %b %H:%M}",
                          "today": ny.date() == today, "source": e["source"]})
    is_today = any(i["today"] for i in items)
    note = EVENT_NOTE if is_today else EVENT_NOTE_HORIZON if items else None
    return {"loaded": True, "items": items, "today": is_today, "note": note}


# ---- forecast ------------------------------------------------------------------------

def forecast_vol(bars: pd.DataFrame, sims: int = SIMS, seed: int | None = None,
                 prior: pd.DataFrame | None = None) -> dict:
    """Pure: calibrated 24h volatility forecast as of the last bar in `bars`.

    `prior` (from prior_forecasts) may be passed in to avoid recomputing it; it must come
    from the same bars. Needs > WINDOW bars plus enough history for K_MIN matured forecasts.
    """
    if len(bars) <= WINDOW:
        raise ValueError(f"need more than {WINDOW} bars, got {len(bars)}")
    origin = bars.index[-1]
    last = float(bars["close"].iloc[-1])
    r = log_returns(bars["close"]).iloc[-WINDOW:].to_numpy()
    res = fit(r)
    var = step_variances(res)
    raw = float(np.sqrt(var.sum()))
    prior = prior_forecasts(bars) if prior is None else prior
    k, k_n = calibration_k(prior)
    sigma = k * raw

    future = cal.next_tradable_hours(origin.to_pydatetime(), HORIZON)
    hours = [t.astimezone(cal.NY).hour for t in future]
    first_day = future[0].astimezone(cal.NY).date()
    cash_steps = [i for i, t in enumerate(future)
                  if hours[i] in CASH_NY_HOURS and t.astimezone(cal.NY).date() == first_day]
    profile = intraday_profile(bars)
    w = np.array([profile.get(h, 0.0) for h in hours])
    cash_share = float(w[cash_steps].sum() / w.sum()) if cash_steps else None
    cash_sigma = sigma * np.sqrt(cash_share) if cash_share else None

    seed = seed_for(origin) if seed is None else seed
    end = simulate(res, last, sims, seed)[:, -1]
    sim_sigma = float(np.std(100 * np.log(end / last)))

    s_prev = prior["sigma_raw_pct"].tail(REGIME_DAYS).to_numpy()
    pctile = float(100 * (s_prev < raw).mean()) if len(s_prev) else None
    matured = prior[prior["move_pct"].notna()]
    def rms(n):
        x = matured["move_pct"].tail(n).to_numpy()
        return round(float(np.sqrt(np.mean(x ** 2))), 4) if len(x) == n else None

    p = res.params
    px = lambda s: round(last * np.exp(s / 100), 2)  # noqa: E731
    pts = lambda s: round(last * s / 100, 1)  # noqa: E731
    return {
        "origin_utc": origin.isoformat(),
        "horizon_end_utc": future[-1].isoformat(),
        "last_close": round(last, 2),
        "sigma_pct": round(sigma, 4),
        "sigma_pts": pts(sigma),
        "range80": [px(-Z80 * sigma), px(Z80 * sigma)],
        "range68": [px(-sigma), px(sigma)],
        "calibration": {"k": round(k, 4), "n": k_n, "sigma_raw_pct": round(raw, 4),
                        "method": f"k = RMS(move / raw sigma) over the last {k_n} matured daily forecasts"},
        "cash": {"hours": len(cash_steps),
                 "start_utc": future[cash_steps[0]].isoformat() if cash_steps else None,
                 "variance_share": round(cash_share, 4) if cash_share else None,
                 "sigma_pct": round(cash_sigma, 4) if cash_sigma else None,
                 "sigma_pts": pts(cash_sigma) if cash_sigma else None},
        "profile": {str(h): round(v, 4) for h, v in sorted(profile.items())},
        "regime": regime_of(pctile) if pctile is not None else None,
        "percentile": round(pctile, 1) if pctile is not None else None,
        "regime_days": int(len(s_prev)),
        "realised_rms_5d_pct": rms(5), "realised_rms_20d_pct": rms(20),
        "roll_note": roll_note(future),
        "events": events_between(origin + pd.Timedelta(hours=1), pd.Timestamp(future[-1]) + pd.Timedelta(hours=1),
                                 origin.tz_convert(cal.NY).date()),
        "model": {"spec": "GARCH(1,1), zero mean, Student-t", "n_returns": len(r),
                  "omega": float(p["omega"]), "alpha": float(p["alpha[1]"]),
                  "beta": float(p["beta[1]"]), "nu": float(p["nu"]),
                  "persistence": float(p["alpha[1]"] + p["beta[1]"]),
                  "loglik": float(res.loglikelihood)},
        "sim_raw": {"sims": sims, "seed": seed, "p10": round(float(np.percentile(end, 10)), 2),
                    "p90": round(float(np.percentile(end, 90)), 2),
                    "sigma_pct": round(sim_sigma, 4), "vs_analytic": round(sim_sigma / raw, 4)},
    }


# ---- CLI -----------------------------------------------------------------------------

def parse_now(s: str | None) -> datetime | None:
    if not s:
        return None
    t = pd.Timestamp(s)
    t = t.tz_localize(cal.NY) if t.tz is None else t
    return t.tz_convert("UTC").to_pydatetime()


def print_table(f: dict) -> None:
    o = pd.Timestamp(f["origin_utc"])
    end = pd.Timestamp(f["horizon_end_utc"])
    last = f["last_close"]
    close_t = o + pd.Timedelta(hours=1)
    print("\nNQ 24h volatility forecast (GARCH, calibrated) — volatility only, not direction")
    print(f"  as of bar         {o.tz_convert(cal.NY):%a %d %b %Y %H:%M}-{close_t.tz_convert(cal.NY):%H:%M} NY "
          f"({close_t.tz_convert(cal.LONDON):%H:%M} London close)   last close {last:,.2f}")
    print(f"  horizon           next 24 tradable hours, to the bar starting {end.tz_convert(cal.NY):%a %d %b %H:%M} NY")
    cal_ = f["calibration"]
    rows = [
        ("Expected 24h move (σ)", f"±{f['sigma_pts']:,.0f} pts  (±{f['sigma_pct']:.2f}%)"),
        ("80% range (±1.28σ)", f"{f['range80'][0]:,.0f} – {f['range80'][1]:,.0f}  "
                               f"({f['range80'][0] - last:+,.0f} / {f['range80'][1] - last:+,.0f} pts)"),
        ("68% range (±1σ)", f"{f['range68'][0]:,.0f} – {f['range68'][1]:,.0f}"),
    ]
    c = f["cash"]
    if c["sigma_pts"]:
        st = pd.Timestamp(c["start_utc"]).tz_convert(cal.NY)
        rows.append(("Cash session σ", f"±{c['sigma_pts']:,.0f} pts (±{c['sigma_pct']:.2f}%) over {c['hours']} h "
                                       f"from {st:%H:%M} NY = {100 * c['variance_share']:.0f}% of the 24h variance"))
    else:
        rows.append(("Cash session σ", "n/a (no 09:00–16:00 NY hours in the horizon)"))
    if f["percentile"] is not None:
        rows.append(("Regime", f"{f['regime']}  ({f['percentile']:.0f}th percentile of the last "
                               f"{f['regime_days']} daily forecasts, raw σ)"))
    r5, r20 = f["realised_rms_5d_pct"], f["realised_rms_20d_pct"]
    if r5 and r20:
        rows.append(("vs realised", f"forecast {f['sigma_pct']:.2f}% vs realised RMS 24h move "
                                    f"{r5:.2f}% (5d), {r20:.2f}% (20d) → ×{f['sigma_pct'] / r5:.2f}, ×{f['sigma_pct'] / r20:.2f}"))
    ev = f["events"]
    if ev["items"]:
        rows.append(("Event flag", "; ".join(f"{i['name']} {i['ny']} NY" + (" (today)" if i["today"] else "")
                                             for i in ev["items"])))
        rows.append(("", ev["note"]))
    else:
        rows.append(("Event flag", ev["note"] or "no scheduled release in the 24h horizon"))
    rows.append(("Roll", f["roll_note"] or "no roll in the horizon"))
    rows.append(("Calibration", f"raw GARCH σ {cal_['sigma_raw_pct']:.2f}% × k {cal_['k']:.3f} "
                                f"(RMS move/σ, last {cal_['n']} matured daily forecasts)"))
    m = f["model"]
    rows.append(("Model", f"{m['spec']}, last {m['n_returns']:,} hourly returns"))
    rows.append(("  parameters", f"ω {m['omega']:.4f}  α {m['alpha']:.3f}  β {m['beta']:.3f}  "
                                 f"ν {m['nu']:.2f}  persistence α+β {m['persistence']:.3f}"))
    s = f["sim_raw"]
    rows.append(("  diagnostic", f"raw simulated P10–P90 {s['p10']:,.0f} – {s['p90']:,.0f} from {s['sims']:,} paths; "
                                 f"simulated σ = {100 * s['vs_analytic']:.0f}% of raw analytic"))
    w = max(len(k) for k, _ in rows)
    print("  " + "-" * 96)
    for k, v in rows:
        print(f"  {k:<{w}}  {v}")
    prof = f["profile"]
    order = [18, 19, 20, 21, 22, 23] + list(range(0, 17))
    print(f"\n  Intraday variance profile (share of a day's squared hourly returns, last {PROFILE_DAYS} trading days; bar start, NY)")
    top = max(prof.values())
    for h in order:
        v = prof.get(str(h), 0.0)
        tag = "  cash" if h in CASH_NY_HOURS else ""
        print(f"    {h:02d}:00  {100 * v:5.1f}%  {'█' * round(40 * v / top)}{tag}")
    cash = sum(prof.get(str(h), 0.0) for h in CASH_NY_HOURS)
    print(f"    09:00–16:00 NY: {100 * cash:.1f}% of a day (an even spread would be {100 * 7 / 23:.0f}%)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help='forecast time, New York unless a zone is given, e.g. "2026-11-05 08:10"')
    ap.add_argument("--sims", type=int, default=SIMS)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    now = parse_now(args.now)
    t0 = time.perf_counter()
    try:
        df, rolls = load_bars(now)
    except fetch.DataError as e:
        print(f"DATA CHECK FAILED: {e}", file=sys.stderr)
        return 1
    t1 = time.perf_counter()
    o = df.index[-1]
    if o.tz_convert(cal.NY).hour != ORIGIN_NY_HOUR:
        print(f"note: last closed bar starts {o.tz_convert(cal.NY):%a %H:%M} NY, not the 07:00 NY bar; "
              f"forecasting from it anyway")
    prior = prior_forecasts(df)
    t2 = time.perf_counter()
    f = forecast_vol(df, sims=args.sims, prior=prior)
    t3 = time.perf_counter()
    print(f"{len(df):,} clean bars {df.index[0]:%Y-%m-%d} -> {o:%Y-%m-%d %H:%M} UTC; rolls: " +
          ", ".join(f"{r['roll_date']} {r['gap']:+.0f} ({r['method']})" for r in rolls))
    print_table(f)
    print(f"\n  timing: data {t1 - t0:.1f}s, {len(prior)} prior daily forecasts {t2 - t1:.1f}s, forecast {t3 - t2:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
