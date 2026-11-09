"""NQ direction research (NQ_DIRECTION_RESEARCH.md, Parts 2-5). Research only: nothing here
feeds the page, the alert or forecast/.

Everything below was fixed BEFORE any result was looked at (Part 2 of the plan):

Tier 1 models (logit, standardised inputs, L2 penalty; C chosen by walk-forward CV inside the
training window only). Expected signs in brackets; a significant coefficient with the wrong
sign is a failure.

  M1  next 24h: up = 1 if the 1pm -> 1pm London return (origin bar close -> +23 bars) > 0
      past5      past 5-day return (5 x 23 bars to the origin close)                  [-]
      downcash   min(previous cash-session return, 0), 9am -> 4pm NY (bar closes)       [-]
      tom        the window's cash session is trading day -1..+3 of the month          [+]
      fomc_vxn   FOMC decision that day x VXN high (last close >= 75th pct of the
                 trailing 252 closes before that day)                                  [+]
      lrv        log realised variance, mean of the last 22 daily 23-bar RVs            [conditioner]
      Daily proxy (NQ=F since Sep 2000): the same inputs on close-to-close days. The target is
      day t's return, predicted at day t-1's close. downcash uses ^NDX open -> close of t-1.
      On the 7 trading days around each quarterly roll (2nd Thursday -1 .. expiry +1) the
      NQ=F return is replaced by ^NDX's, because Yahoo's contract switch day varies.
  M2  last hour: up = 1 if the 3-4pm NY bar (8-9pm London) closes up
      rod        return from the previous 4pm NY close to 3pm NY                         [+]
      rod_hv     rod x high-vol flag (HAR sigma at that morning's origin in the top tercile
                 of its trailing 252 values)                                           [+]
  M3  cash session from 1pm (control, low prior): up = 1 if the 9am -> 4pm NY return > 0
      overnight  previous 4pm NY close -> 8am NY (origin close)                          [none]
      past5                                                                            [-]
      lrv                                                                              [conditioner]
  Baseline: the training window's base rate.

Tier 2 (Part 3), each added alone to the best Tier 1 model by development BSS. If that isn't
M1, fed_hawk and yld2_chg are also tried on M1 (the Fed-odds hypothesis is about the next 24h);
those tests are inside the Holm set. Long-history Tier 2 variables are tried on the M1 daily
proxy (yld2_chg always; vxn_chg and yld10_chg if M1 is the best model):
      band_pos   1pm price vs yesterday's 90% range: ln(last / last_prev) / (m90 x k sigma),
                 HAR-RV calibrated as in production                                    [-]
      har_garch  ln(HAR sigma / GARCH sigma), both calibrated                           [none]
      vxn_chg    VXN 1-day log change (last two closes before the day)                  [+]
      yld10_chg  10Y yield overnight change: hourly = -ZN=F return prev 4pm NY -> 8am NY;
                 daily = ^TNX change on the previous day                                [-]
      fed_hawk   change over the prior 1pm -> 1pm window in Polymarket hawkishness for
                 the next FOMC meeting, P(hike) - P(cut) summed over buckets             [-]
      yld2_chg   2Y yield change over the same window (long proxy for fed_hawk): hourly =
                 -ZT=F return prev origin -> origin; daily = FRED DGS2 change            [-]
  Kronos and the HMM regime are not re-tested (already coin flips: lab/results/report.md,
  lab/results/regime.md). News and non-FOMC calendar flags are not tested: no history
  (the events YAML starts in 2026).

Protocol (Part 4): holdout = origins from 2026-05-09, scored once (--holdout). Walk-forward on
the rest, refit monthly, predict the next month: hourly expanding window after the first 120
days; daily rolling 5 years (minimum 2). C grid 1e-4..10 by 4-fold forward CV (log loss),
re-chosen at every hourly refit and every 12th daily refit. The holdout uses the model
fitted on all development rows, frozen. Scores: Brier skill score vs the base rate, log loss,
hit rate, calibration, coefficients. Permutation: targets shuffled in 5-day blocks over the
development period, the whole walk-forward rerun 1,000x (Tier 2: the added variable shuffled
instead, testing the BSS gain).

    python lab/direction.py                     # all models, development only
    python lab/direction.py --model M2 --tier 1 # one model
    python lab/direction.py --holdout           # the one final run: scores the holdout, writes
                                                # lab/results/direction.md
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lab"))
sys.path.insert(0, str(ROOT / "forecast"))
import daily_origins as dor  # noqa: E402
import garch_vol as gv  # noqa: E402
import nq_calendar as cal  # noqa: E402

CACHE = ROOT / "lab" / "cache" / "direction"
FOMC_DIR = ROOT / "lab" / "cache" / "fomc"
OUT = ROOT / "lab" / "results" / "direction.md"
OUT_JSON = ROOT / "lab" / "results" / "direction.json"
HOLDOUT = pd.Timestamp("2026-05-09")
H = gv.HORIZON
NY = cal.NY
UA = {"User-Agent": "Mozilla/5.0"}
C_GRID = np.logspace(-4, 1, 11)
N_PERM = 1000
BLOCK = 5
HOURLY_MIN = 120
MIN_SCORED = 150                    # fewer scored development days than this: "insufficient data"
DAILY_WIN, DAILY_MIN = 1260, 504
COST_PTS = 0.25 + 4.5 / 20          # 1 tick slippage + ~$4.50 round-trip fees on NQ ($20/pt)

MODELS = {
    "M1": {"name": "Next 24h (1pm -> 1pm London)", "target": "y1", "ret": "r1",
           "x": ["past5", "downcash", "tom", "fomc_vxn", "lrv"],
           "sign": {"past5": -1, "downcash": -1, "tom": 1, "fomc_vxn": 1}},
    "M2": {"name": "Last hour (3-4pm NY = 8-9pm London)", "target": "y2", "ret": "r2",
           "x": ["rod", "rod_hv"], "sign": {"rod": 1, "rod_hv": 1}},
    "M3": {"name": "Cash session 9am -> 4pm NY (control)", "target": "y3", "ret": "r3",
           "x": ["overnight", "past5", "lrv"], "sign": {"past5": -1}},
}
TIER2 = {"band_pos": -1, "har_garch": 0, "vxn_chg": 1, "yld10_chg": -1, "fed_hawk": -1, "yld2_chg": -1}
DAILY_TIER2 = ("vxn_chg", "yld10_chg", "yld2_chg")      # the ones with a long daily history

log = logging.getLogger("direction")


# ==== data ====================================================================================

def _yf_daily(ticker: str) -> pd.DataFrame:
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    p = CACHE / f"daily_{re.sub(r'[^A-Za-z0-9]', '_', ticker)}.parquet"
    if p.exists():
        return pd.read_parquet(p)
    d = yf.Ticker(ticker).history(period="max", interval="1d", auto_adjust=False)
    d.index = pd.to_datetime(d.index.strftime("%Y-%m-%d"))
    d = d[d.index.weekday < 5][["Open", "High", "Low", "Close"]].dropna()
    CACHE.mkdir(parents=True, exist_ok=True)
    d.to_parquet(p)
    return d


def _yf_hourly(ticker: str) -> pd.Series:
    """Hourly closes indexed by bar start (UTC)."""
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    p = CACHE / f"hourly_{re.sub(r'[^A-Za-z0-9]', '_', ticker)}.parquet"
    if p.exists():
        return pd.read_parquet(p)["close"]
    d = yf.Ticker(ticker).history(period="730d", interval="1h")
    s = pd.Series(d["Close"].to_numpy(), index=d.index.tz_convert("UTC"), name="close").dropna()
    CACHE.mkdir(parents=True, exist_ok=True)
    s.to_frame().to_parquet(p)
    return s


def _fred_dgs2() -> pd.Series:
    import requests
    p = CACHE / "fred_DGS2.parquet"
    if p.exists():
        return pd.read_parquet(p)["v"]
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2", timeout=30)
    r.raise_for_status()
    from io import StringIO
    d = pd.read_csv(StringIO(r.text))
    d.columns = ["date", "v"]
    s = pd.Series(pd.to_numeric(d["v"], errors="coerce").to_numpy(), index=pd.to_datetime(d["date"]), name="v").dropna()
    s.to_frame().to_parquet(p)
    return s


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
MON_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}


def fomc_dates() -> list[date]:
    """Scheduled FOMC announcement days (the meeting's last day), 2000-2027, from the Fed's
    pages (cached in lab/cache/fomc/): fomchistorical<year>.htm to 2020, fomccalendars.htm
    after. Unscheduled meetings, conference calls, notation votes and cancelled meetings are
    excluded. (Statement time: 2:15pm ET until 2012, 2:00pm since 2013; this only needs the day.)"""
    import requests
    FOMC_DIR.mkdir(parents=True, exist_ok=True)
    out = set()
    for y in range(2000, 2021):
        f = FOMC_DIR / f"h{y}.htm"
        if not f.exists():
            f.write_bytes(requests.get(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm",
                                       headers=UA, timeout=30).content)
        for h in re.findall(r"<h[3-5][^>]*>([^<]{3,80})</h[3-5]>", f.read_text(errors="ignore")):
            m = re.match(r"^\s*(\w+) (\d+)(?:-(?:(\w+) )?(\d+))? Meeting - (\d{4})\s*$", h)
            if not m:
                continue                            # unscheduled / cancelled / calls / votes
            mon = m.group(3) or m.group(1)
            day = int(m.group(4) or m.group(2))
            out.add(date(int(m.group(5)), MONTHS.index(mon) + 1, day))
    f = FOMC_DIR / "cal.htm"
    if not f.exists():
        f.write_bytes(requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                                   headers=UA, timeout=30).content)
    s = f.read_text(errors="ignore")
    for blk in re.split(r'<h4><a id="\d+">', s)[1:]:
        yr = int(blk[:4])
        if yr < 2021:
            continue
        mons = re.findall(r"fomc-meeting__month[^>]*><strong>([^<]+)</strong>", blk)
        days = re.findall(r"fomc-meeting__date[^>]*>([^<]+)<", blk)
        for mo, dd in zip(mons, days):
            dd = dd.strip()
            if "(" in dd:
                continue
            last_mon = mo.split("/")[-1].strip()[:3]
            out.add(date(yr, MON_ABBR[last_mon], int(re.sub(r"\D", "", dd.split("-")[-1]))))
    return sorted(out)


def trading_days(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.bdate_range(start, end) if not cal.is_holiday(d.date())]


def tom_set(start: date, end: date) -> set[date]:
    """Trading days -1 .. +3 of each month (last day of the month, first three of the next)."""
    td = pd.Series(trading_days(start - timedelta(days=40), end + timedelta(days=40)))
    ym = td.map(lambda d: (d.year, d.month))
    out = set()
    for _, g in td.groupby(ym.to_numpy()):
        g = list(g)
        out.add(g[-1])
        out.update(g[:3])
    return out


def vxn_high_before(vxn: pd.Series, days) -> np.ndarray:
    """VXN's last close before each day >= the 75th percentile of the trailing 252 closes
    before it (0 when unknown, e.g. before VXN starts in 2001)."""
    v = vxn.sort_index()
    out = np.zeros(len(days))
    for k, d in enumerate(days):
        yr = v[v.index < pd.Timestamp(d)].tail(252)
        if len(yr) >= 200 and yr.iloc[-1] >= np.quantile(yr, 0.75):
            out[k] = 1.0
    return out


def _last_before(s: pd.Series, d, n: int = 1) -> float:
    x = s[s.index < pd.Timestamp(d)]
    return float(x.iloc[-n]) if len(x) >= n else np.nan


def hourly_table() -> pd.DataFrame:
    """One row per daily 07:00 NY origin in the cached hourly bars (lab/cache/bars_hourly_23h.parquet,
    the same roll-adjusted series as production), with the M1-M3 targets and Tier 1/2 inputs."""
    cache_p = CACHE / "hourly_table.parquet"
    if cache_p.exists():
        return pd.read_parquet(cache_p)
    t_vol, bars = dor.build()
    close = bars["close"].to_numpy()
    idx = {ts: k for k, ts in enumerate(bars.index)}
    ny = bars.index.tz_convert(NY)

    def pos_at(d: date, hour: int):
        return idx.get(pd.Timestamp(datetime(d.year, d.month, d.day, hour), tz=NY).tz_convert("UTC"))

    origins = gv.daily_origins(bars)
    origins = origins[origins >= 5 * H]
    har_all, _ = gv.har_sigmas(bars, origins)
    r2 = np.nan_to_num(gv.log_returns(bars["close"]).to_numpy()) ** 2
    cs = np.concatenate([[0.0], np.cumsum(r2)])
    rv1 = cs[origins + 1] - cs[origins - H + 1]
    lrv = np.log(pd.Series(rv1).rolling(22).mean().to_numpy())
    days = [ny[p].date() for p in origins]
    fomc = set(fomc_dates())
    tom = tom_set(days[0], days[-1])
    vxn = _yf_daily("^VXN")["Close"]
    vxn_hi = vxn_high_before(vxn, days)
    zn, zt = _yf_hourly("ZN=F"), _yf_hourly("ZT=F")
    rows = []
    for k, p in enumerate(origins):
        d = days[k]
        prev = days[k - 1] if k else None
        c = lambda q: close[q] if q is not None else np.nan  # noqa: E731
        cash_prev = 100 * np.log(c(pos_at(prev, 15)) / c(pos_at(prev, 8))) if prev else np.nan
        c14, c15, c08 = pos_at(d, 14), pos_at(d, 15), pos_at(d, 8)
        prev15 = c(pos_at(prev, 15)) if prev else np.nan
        r1 = 100 * np.log(close[p + H] / close[p]) if p + H < len(close) else np.nan
        r2_ = 100 * np.log(c(c15) / c(c14)) if c15 is not None and c14 is not None else np.nan
        r3 = 100 * np.log(c(c15) / c(c08)) if c15 is not None and c08 is not None else np.nan
        hv = har_all[max(0, k - 252):k]
        hv = hv[np.isfinite(hv)]
        high_vol = float(np.isfinite(har_all[k]) and len(hv) >= 60 and har_all[k] >= np.quantile(hv, 2 / 3))
        rod = 100 * np.log(c(c14) / prev15) if c14 is not None else np.nan
        o_utc = bars.index[p]

        def fut_close(s, ts):
            v = s.get(ts)
            return float(v) if v is not None else np.nan
        zn_prev = fut_close(zn, pd.Timestamp(datetime(prev.year, prev.month, prev.day, 15), tz=NY).tz_convert("UTC")) if prev else np.nan
        zt_prev = fut_close(zt, bars.index[origins[k - 1]]) if k else np.nan
        rows.append({
            "date": pd.Timestamp(d), "origin_utc": o_utc, "pos": int(p), "last": float(close[p]),
            "y1": float(r1 > 0) if np.isfinite(r1) else np.nan, "r1": r1,
            "y2": float(r2_ > 0) if np.isfinite(r2_) else np.nan, "r2": r2_,
            "y3": float(r3 > 0) if np.isfinite(r3) else np.nan, "r3": r3,
            "past5": 100 * np.log(close[p] / close[p - 5 * H]),
            "downcash": min(cash_prev, 0.0) if np.isfinite(cash_prev) else np.nan,
            "tom": float(d in tom), "fomc": float(d in fomc), "vxn_high": vxn_hi[k],
            "fomc_vxn": float(d in fomc) * vxn_hi[k], "lrv": lrv[k],
            "rod": rod, "high_vol": high_vol, "rod_hv": rod * high_vol if np.isfinite(rod) else np.nan,
            "overnight": 100 * np.log(close[p] / prev15) if np.isfinite(prev15) else np.nan,
            "har_raw": har_all[k],
            "vxn_chg": np.log(_last_before(vxn, d) / _last_before(vxn, d, 2)),
            "yld10_chg": -100 * np.log(fut_close(zn, o_utc) / zn_prev) if np.isfinite(zn_prev) else np.nan,
            "yld2_chg": -100 * np.log(fut_close(zt, o_utc) / zt_prev) if np.isfinite(zt_prev) else np.nan,
        })
    t = pd.DataFrame(rows)
    t = t.merge(_band_and_harg(t_vol), on="origin_utc", how="left")
    t = t.merge(fed_hawk_series(t[["origin_utc"]]), on="origin_utc", how="left")
    CACHE.mkdir(parents=True, exist_ok=True)
    t.to_parquet(cache_p)
    return t


def _band_and_harg(t_vol: pd.DataFrame) -> pd.DataFrame:
    """band_pos and har_garch from walk-forward calibrated HAR-RV and GARCH (production rules)."""
    _, bars = dor.build()
    har = gv.prior_forecasts(bars, n=10 ** 6, model="har")["sigma_raw_pct"].to_numpy()
    t = t_vol.reset_index(drop=True)
    idx = list(range(len(t)))
    out = []
    move, roll, pos = t["move_pct"].to_numpy(), t["roll"].to_numpy(), t["pos"].to_numpy()

    def kcal(s, i):
        prior = np.arange(i)[-330:]
        ok = prior[(pos[prior] + H <= pos[i]) & np.isfinite(move[prior]) & ~roll[prior] & np.isfinite(s[prior])][-gv.K_DAYS:]
        if len(ok) < gv.K_MIN or not np.isfinite(s[i]):
            return np.nan, np.nan
        z = move[ok] / s[ok]
        k = float(np.sqrt(np.mean(z ** 2)))
        return k * s[i], float(np.quantile(np.abs(z) / k, 0.90))
    garch = t["sigma_raw_pct"].to_numpy()
    sig_h, m90_h, sig_g = [], [], []
    for i in idx:
        a, m = kcal(har, i)
        g, _ = kcal(garch, i)
        sig_h.append(a), m90_h.append(m), sig_g.append(g)
    last = t["last"].to_numpy()
    for i in idx:
        bp = (100 * np.log(last[i] / last[i - 1]) / (m90_h[i - 1] * sig_h[i - 1])
              if i and np.isfinite(sig_h[i - 1]) and pos[i] - pos[i - 1] == H else np.nan)
        out.append({"origin_utc": t["origin_utc"].iat[i], "band_pos": bp,
                    "har_garch": np.log(sig_h[i] / sig_g[i]) if np.isfinite(sig_h[i]) and np.isfinite(sig_g[i]) else np.nan})
    return pd.DataFrame(out)


def daily_table() -> pd.DataFrame:
    """Daily proxy for M1 on NQ=F since Sep 2000 (see the module docstring)."""
    p = CACHE / "daily_table.parquet"
    if p.exists():
        return pd.read_parquet(p)
    nq, ndx = _yf_daily("NQ=F"), _yf_daily("^NDX")
    vxn, tnx, dgs2 = _yf_daily("^VXN")["Close"], _yf_daily("^TNX")["Close"], _fred_dgs2()
    d = pd.DataFrame({"nq": np.log(nq["Close"]).diff()}).join(
        pd.DataFrame({"ndx": np.log(ndx["Close"]).diff(), "ndx_oc": np.log(ndx["Close"] / ndx["Open"])}), how="left")
    d = d.dropna(subset=["nq"])
    d = d[d.index < pd.Timestamp.today().normalize()]          # today's bar is still in progress
    roll_days = set()
    for y in range(2000, 2027):
        for m in (3, 6, 9, 12):
            a, b = pd.Timestamp(cal.roll_date(y, m)), pd.Timestamp(cal.expiry_date(y, m))
            k0, k1 = d.index.searchsorted(a), d.index.searchsorted(b)
            roll_days.update(d.index[max(0, k0 - 1):min(len(d), k1 + 2)])
    d["roll_patch"] = d.index.isin(list(roll_days)) & d["ndx"].notna()
    d["r"] = np.where(d["roll_patch"], d["ndx"], d["nq"]) * 100
    days = [x.date() for x in d.index]
    tom = tom_set(days[0], days[-1])
    fomc = set(fomc_dates())
    vxn_hi = vxn_high_before(vxn, days)
    r = d["r"]
    out = pd.DataFrame(index=d.index)
    out["date"] = d.index
    out["y1"] = (r > 0).astype(float)
    out["r1"] = r
    out["price"] = nq["Close"].reindex(d.index).shift(1)
    out["past5"] = r.shift(1).rolling(5).sum()
    out["downcash"] = np.minimum(100 * d["ndx_oc"].shift(1), 0.0)
    out["tom"] = [float(x in tom) for x in days]
    out["fomc"] = [float(x in fomc) for x in days]
    out["vxn_high"] = vxn_hi
    out["fomc_vxn"] = out["fomc"] * out["vxn_high"]
    out["lrv"] = np.log((r.shift(1) ** 2).rolling(22).mean())
    vx = vxn.reindex(d.index)
    out["vxn_chg"] = np.log(vx.shift(1) / vx.shift(2))
    tx = tnx.reindex(d.index)
    out["yld10_chg"] = tx.shift(1) - tx.shift(2)
    g2 = dgs2.reindex(d.index)
    out["yld2_chg"] = g2.shift(1) - g2.shift(2)
    out["roll_patch"] = d["roll_patch"]
    out = out.dropna(subset=["past5", "downcash", "lrv"]).reset_index(drop=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    out.to_parquet(p)
    return out


# ---- Polymarket "Fed decision" history ---------------------------------------------------------

def _pm_get(url, params=None):
    import requests
    for k in range(4):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1 + 2 * k)
    raise RuntimeError(f"Polymarket request failed: {url} {params}")


def fed_events() -> list[dict]:
    """Every Polymarket "Fed decision in <month>?" / "Fed Interest Rates <month> <year>" event
    that maps to a scheduled FOMC meeting (end date within -3..+2 days of the decision day);
    if two events map to one meeting, the higher-volume one."""
    p = CACHE / "polymarket" / "events.json"
    if p.exists():
        return json.loads(p.read_text())
    G = "https://gamma-api.polymarket.com"
    found = {}
    for y in (2022, 2023, 2024):
        for m in MONTHS:
            for slug in (f"fed-interest-rates-{m.lower()}-{y}",):
                r = _pm_get(f"{G}/events", {"slug": slug})
                if r:
                    found[r[0]["id"]] = r[0]
    for m in MONTHS:
        for st in (None, "closed", "active"):
            q = {"q": f"Fed decision in {m}", "limit_per_type": 50, "keep_closed_markets": 1}
            if st:
                q["events_status"] = st
            for e in _pm_get(f"{G}/public-search", q).get("events") or []:
                t = (e.get("title") or "").lower()
                if "fed" in t and "decision" in t and "dissent" not in t and "favored" not in t:
                    found[e["id"]] = e
    meetings = fomc_dates()
    by_meeting = {}
    for eid in found:
        e = _pm_get(f"{G}/events/{eid}")
        if not e.get("endDate"):
            continue
        end = pd.Timestamp(e["endDate"]).date()
        mt = [m for m in meetings if -3 <= (end - m).days <= 2]
        if not mt:
            continue
        mkts = []
        for mk in e.get("markets") or []:
            b = _bucket(mk)
            toks = json.loads(mk.get("clobTokenIds") or "[]")
            outs = json.loads(mk.get("outcomes") or "[]")
            if b and toks and (not outs or outs[0].lower() == "yes"):
                mkts.append({"bucket": b, "token": toks[0], "title": mk.get("groupItemTitle") or mk.get("question"),
                             "start": mk.get("startDate") or e.get("startDate")})
        if not mkts:
            continue
        ev = {"id": eid, "title": e.get("title"), "slug": e.get("slug"), "meeting": mt[0].isoformat(),
              "volume": float(e.get("volume") or 0), "markets": mkts}
        if mt[0] not in by_meeting or ev["volume"] > by_meeting[mt[0]]["volume"]:
            by_meeting[mt[0]] = ev
    evs = sorted(by_meeting.values(), key=lambda e: e["meeting"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(evs, indent=1))
    return evs


def _bucket(m: dict) -> str | None:
    t = f"{m.get('groupItemTitle') or ''} {m.get('question') or ''}".lower()
    if "increase" in t or "hike" in t:
        return "Hike"
    if "decrease" in t or "cut" in t:
        return "Cut"
    if "no change" in t or "pause" in t or "hold" in t:
        return "Hold"
    return None


def _token_history(token: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    p = CACHE / "polymarket" / f"{token[:24]}.json"
    if p.exists():
        h = json.loads(p.read_text())
    else:
        h, a = [], start
        while a < end:
            b = min(a + pd.Timedelta(days=14), end)
            h += _pm_get("https://clob.polymarket.com/prices-history",
                         {"market": token, "startTs": int(a.timestamp()), "endTs": int(b.timestamp()), "fidelity": 60}
                         ).get("history") or []
            a = b
        p.write_text(json.dumps(h))
    if not h:
        return pd.Series(dtype=float)
    s = pd.Series([float(x["p"]) for x in h], index=pd.to_datetime([x["t"] for x in h], unit="s", utc=True))
    return s[~s.index.duplicated()].sort_index()


def fed_hawk_series(origins: pd.DataFrame) -> pd.DataFrame:
    """fed_hawk at each origin: hawkishness H = sum P(hike) - sum P(cut) for the next scheduled
    FOMC meeting after the origin close (decision at 2pm ET; the day after a decision it rolls),
    change from the previous origin close to this one, both on that same meeting's markets.
    Prices are the last hourly point at or before each close (not older than 6 hours)."""
    evs = {e["meeting"]: e for e in fed_events()}
    meetings = fomc_dates()
    o = origins["origin_utc"].sort_values().reset_index(drop=True)
    closes = o + pd.Timedelta(hours=1)
    hawk = {}

    def H_at(meeting: date, ts: pd.Timestamp) -> float:
        e = evs.get(meeting.isoformat())
        if e is None:
            return np.nan
        if meeting not in hawk:
            ser = {}
            dec = pd.Timestamp(datetime(meeting.year, meeting.month, meeting.day, 14), tz=NY).tz_convert("UTC")
            for mk in e["markets"]:
                st = pd.Timestamp(mk["start"]) if mk.get("start") else dec - pd.Timedelta(days=120)
                st = max(st, pd.Timestamp("2024-06-01", tz="UTC"))
                ser[mk["token"]] = (mk["bucket"], _token_history(mk["token"], st, dec))
            hawk[meeting] = ser
        tot = 0.0
        for b, s in hawk[meeting].values():
            x = s[(s.index <= ts) & (s.index > ts - pd.Timedelta(hours=6))]
            if not len(x):
                return np.nan
            tot += float(x.iloc[-1]) * (1 if b == "Hike" else -1 if b == "Cut" else 0)
        return tot
    out = []
    for k in range(len(o)):
        if k == 0:
            out.append(np.nan)
            continue
        dec_after = [m for m in meetings
                     if pd.Timestamp(datetime(m.year, m.month, m.day, 14), tz=NY).tz_convert("UTC") > closes[k]]
        if not dec_after:
            out.append(np.nan)
            continue
        m = dec_after[0]
        out.append(H_at(m, closes[k]) - H_at(m, closes[k - 1]))
    return pd.DataFrame({"origin_utc": o, "fed_hawk": out})


# ==== engine ==================================================================================

def fit_logit(X: np.ndarray, y: np.ndarray, C: float, iters: int = 30) -> np.ndarray:
    """Ridge logit by Newton: minimise C * sum(logloss) + 0.5 * |w|^2 (intercept unpenalised),
    the same objective as sklearn's L2 LogisticRegression. Returns [b0, w...]."""
    n, k = X.shape
    A = np.column_stack([np.ones(n), X])
    beta = np.zeros(k + 1)
    pen = np.r_[0.0, np.full(k, 1.0 / C)]
    for _ in range(iters):
        z = np.clip(A @ beta, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = A.T @ (p - y) + pen * beta
        W = p * (1 - p)
        Hm = (A * W[:, None]).T @ A + np.diag(pen)
        step = np.linalg.solve(Hm, g)
        beta -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def predict(beta, X):
    return 1 / (1 + np.exp(-np.clip(beta[0] + X @ beta[1:], -30, 30)))


def logloss(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def choose_C(X, y, folds: int = 4) -> float:
    n = len(y)
    edges = np.linspace(n // 2, n, folds + 1).astype(int)
    best, bc = np.inf, C_GRID[0]
    for C in C_GRID:
        loss = []
        for a, b in zip(edges[:-1], edges[1:]):
            if b <= a:
                continue
            mu, sd = X[:a].mean(0), X[:a].std(0) + 1e-12
            beta = fit_logit((X[:a] - mu) / sd, y[:a], C)
            loss.append(logloss(y[a:b], predict(beta, (X[a:b] - mu) / sd)))
        v = np.mean(np.concatenate(loss))
        if v < best - 1e-12:
            best, bc = v, C
    return float(bc)


def walk_forward(df: pd.DataFrame, feats: list[str], target: str, daily: bool, y_override=None,
                 x_override: dict | None = None, holdout: bool = False) -> dict:
    """Monthly walk-forward on the development rows (date < HOLDOUT); with holdout=True also the
    frozen final model on the holdout rows. Returns predictions and per-refit coefficients."""
    d = df.dropna(subset=feats + [target]).reset_index(drop=True)
    dev = d[d["date"] < HOLDOUT].reset_index(drop=True)
    X = dev[feats].to_numpy(float)
    if x_override:
        for c, v in x_override.items():
            X[:, feats.index(c)] = v
    y = dev[target].to_numpy(float) if y_override is None else y_override
    months = dev["date"].dt.to_period("M").to_numpy()
    start = DAILY_MIN if daily else HOURLY_MIN
    refits = [i for i in range(start, len(dev)) if i == start or months[i] != months[i - 1]]
    p, base, coefs, Cs = np.full(len(dev), np.nan), np.full(len(dev), np.nan), [], []
    C = None
    for r, i in enumerate(refits):
        j = refits[r + 1] if r + 1 < len(refits) else len(dev)
        lo = max(0, i - DAILY_WIN) if daily else 0
        Xt, yt = X[lo:i], y[lo:i]
        if C is None or not daily or r % 12 == 0:
            C = choose_C(Xt, yt)
        mu, sd = Xt.mean(0), Xt.std(0) + 1e-12
        beta = fit_logit((Xt - mu) / sd, yt, C)
        p[i:j] = predict(beta, (X[i:j] - mu) / sd)
        base[i:j] = yt.mean()
        coefs.append(beta[1:]), Cs.append(C)
    out = {"dev": dev, "p": p, "base": base, "y": y, "coefs": np.array(coefs), "C": Cs, "first": start}
    if holdout:
        ho = d[d["date"] >= HOLDOUT].reset_index(drop=True)
        lo = max(0, len(dev) - DAILY_WIN) if daily else 0
        Xt, yt = X[lo:], y[lo:]
        Cf = choose_C(Xt, yt)
        mu, sd = Xt.mean(0), Xt.std(0) + 1e-12
        beta = fit_logit((Xt - mu) / sd, yt, Cf)
        out.update({"ho": ho, "p_ho": predict(beta, (ho[feats].to_numpy(float) - mu) / sd),
                    "base_ho": np.full(len(ho), yt.mean()), "beta_final": beta, "C_final": Cf})
    else:
        Cf = choose_C(X, y)
        mu, sd = X.mean(0), X.std(0) + 1e-12
        out.update({"beta_final": fit_logit((X - mu) / sd, y, Cf), "C_final": Cf})
    return out


def bss(y, p, base):
    return float(1 - np.mean((p - y) ** 2) / np.mean((base - y) ** 2))


def scores(y, p, base, ret=None, price=None) -> dict:
    ok = np.isfinite(p)
    y, p, base = y[ok], p[ok], base[ok]
    s = {"n": int(len(y)), "bss": bss(y, p, base), "logloss": float(np.mean(logloss(y, p))),
         "logloss_base": float(np.mean(logloss(y, base))), "hit": float(np.mean((p > 0.5) == (y == 1))),
         "up_rate": float(np.mean(y)), "p_mean": float(np.mean(p)), "p_sd": float(np.std(p))}
    if ret is not None:
        r = ret[ok]
        px = price[ok] if price is not None else None
        s["ret_hi"] = float(np.mean(r[p > 0.55])) if np.any(p > 0.55) else None
        s["n_hi"] = int(np.sum(p > 0.55))
        s["ret_lo"] = float(np.mean(r[p < 0.45])) if np.any(p < 0.45) else None
        s["n_lo"] = int(np.sum(p < 0.45))
        if px is not None:
            pnl = np.sign(p - 0.5) * r / 100 * px - COST_PTS
            s["ls_pts_per_trade"] = float(np.mean(pnl))
            s["ls_pts_total"] = float(np.sum(pnl))
            s["ls_gross_pts_per_trade"] = float(np.mean(np.sign(p - 0.5) * r / 100 * px))
            s["long_share"] = float(np.mean(p > 0.5))
            s["always_long_net"] = float(np.mean(r / 100 * px) - COST_PTS)
    return s


def calib_table(y, p, buckets: int) -> list[dict]:
    ok = np.isfinite(p)
    y, p = y[ok], p[ok]
    q = np.quantile(p, np.linspace(0, 1, buckets + 1))
    out = []
    for k in range(buckets):
        m = (p >= q[k]) & ((p <= q[k + 1]) if k == buckets - 1 else (p < q[k + 1]))
        if m.sum():
            out.append({"p": float(p[m].mean()), "obs": float(y[m].mean()), "n": int(m.sum())})
    return out


def _block_perm(n, rng):
    blocks = [np.arange(a, min(a + BLOCK, n)) for a in range(0, n, BLOCK)]
    order = rng.permutation(len(blocks))
    return np.concatenate([blocks[k] for k in order])


_W = {}


def _perm_init(df, feats, target, daily, mode, var):
    _W.update(df=df, feats=feats, target=target, daily=daily, mode=mode, var=var)
    d = df.dropna(subset=feats + [target]).reset_index(drop=True)
    _W["dev"] = d[d["date"] < HOLDOUT].reset_index(drop=True)


def _perm_job(seed):
    w = _W
    idx = _block_perm(len(w["dev"]), np.random.default_rng(seed))
    if w["mode"] == "y":
        r = walk_forward(w["df"], w["feats"], w["target"], w["daily"], y_override=w["dev"][w["target"]].to_numpy(float)[idx])
    else:
        r = walk_forward(w["df"], w["feats"], w["target"], w["daily"], x_override={w["var"]: w["dev"][w["var"]].to_numpy(float)[idx]})
    ok = np.isfinite(r["p"])
    return bss(r["y"][ok], r["p"][ok], r["base"][ok])


def permutation(df, feats, target, daily, mode="y", var=None, n=N_PERM, seed=0):
    """Block-permutation null (5-row blocks over the development period, whole walk-forward
    rerun each time). mode "y": targets shuffled. mode "x": only `var` shuffled (Tier 2: is
    the model with `var` better than with a shuffled `var`?). Returns the null BSS values."""
    with ProcessPoolExecutor(max_workers=8, initializer=_perm_init,
                             initargs=(df, feats, target, daily, mode, var)) as ex:
        return np.array(list(ex.map(_perm_job, range(seed, seed + n), chunksize=8)))


# ==== runs ====================================================================================

def run_model(df, name, feats, target, retcol, daily, signs, holdout, n_perm=N_PERM, price_col="last",
              perm_mode="y", perm_var=None) -> dict:
    t0 = time.perf_counter()
    r = walk_forward(df, feats, target, daily, holdout=holdout)
    dev = r["dev"]
    ok = np.isfinite(r["p"])
    y, p, base = r["y"], r["p"], r["base"]
    ret, price = dev[retcol].to_numpy(float), dev[price_col].to_numpy(float)
    sc = dev.index[ok]
    half = sc[len(sc) // 2]
    res = {"name": name, "features": feats, "target": target, "daily": daily,
           "dev_range": [str(dev["date"].iat[sc[0]].date()), str(dev["date"].iat[sc[-1]].date())],
           "dev": scores(y[ok], p[ok], base[ok], ret[ok], price[ok]),
           "dev_old": scores(y[sc[sc < half]], p[sc[sc < half]], base[sc[sc < half]]),
           "dev_new": scores(y[sc[sc >= half]], p[sc[sc >= half]], base[sc[sc >= half]]),
           "calib_dev": calib_table(y[ok], p[ok], 10 if daily else 3),
           "beta_final": dict(zip(feats, map(float, r["beta_final"][1:]))), "C_final": r["C_final"],
           "C_median": float(np.median(r["C"])),
           "sign_share": {f: float(np.mean(np.sign(r["coefs"][:, k]) == signs[f])) for k, f in enumerate(feats) if signs.get(f)}}
    if daily:
        yrs = dev["date"].dt.year.to_numpy()
        for lab, m in (("era_2000_2012", yrs <= 2012), ("era_2013_2026", yrs >= 2013)):
            mm = ok & m
            res[lab] = scores(y[mm], p[mm], base[mm]) if mm.sum() else None
    else:
        res["_dev_pred"] = pd.DataFrame({"date": dev["date"], "y": y, "p": p, "base": base})
    if holdout:
        ho = r["ho"]
        yh = ho[target].to_numpy(float)
        res["holdout"] = scores(yh, r["p_ho"], r["base_ho"], ho[retcol].to_numpy(float), ho[price_col].to_numpy(float))
        res["holdout_range"] = [str(ho["date"].iat[0].date()), str(ho["date"].iat[-1].date())] if len(ho) else None
        res["calib_ho"] = calib_table(yh, r["p_ho"], 3)
        if not daily:
            res["_ho_pred"] = pd.DataFrame({"date": ho["date"], "y": yh, "p": r["p_ho"], "base": r["base_ho"]})
    if n_perm:
        null = permutation(df, feats, target, daily, mode=perm_mode, var=perm_var, n=n_perm)
        stat = res["dev"]["bss"]
        res["perm_p"] = float((1 + np.sum(null >= stat)) / (1 + len(null)))
        res["perm_null_q95"] = float(np.quantile(null, 0.95))
    res["secs"] = round(time.perf_counter() - t0, 1)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", choices=["M1", "M2", "M3"], nargs="*")
    ap.add_argument("--tier", type=int, choices=[1, 2], nargs="*")
    ap.add_argument("--holdout", action="store_true", help="the one final run: score the holdout and write the report")
    ap.add_argument("--perm", type=int, default=N_PERM)
    ap.add_argument("--rebuild", action="store_true", help="rebuild the cached tables")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.rebuild:
        for f in ("hourly_table.parquet", "daily_table.parquet"):
            (CACHE / f).unlink(missing_ok=True)
    return run_all(args)




# ==== gates, report ===========================================================================

def holm(pvals: dict) -> dict:
    """Holm step-down adjusted p-values."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, out, run = len(items), {}, 0.0
    for k, (name, p) in enumerate(items):
        run = max(run, min(1.0, (m - k) * p))
        out[name] = run
    return out


def gates(r: dict, signs: dict, p_adj: float, extra_sign: str | None = None) -> dict:
    """Part 4 gates for one model result (holdout must have been scored)."""
    ho = r.get("holdout")
    g = {"bss_halves": r["dev_old"]["bss"] > 0 and r["dev_new"]["bss"] > 0,
         "bss_holdout": bool(ho) and ho["bss"] > 0,
         "perm": p_adj < 0.05}
    want = {f: v for f, v in signs.items() if v and f in r["beta_final"]}
    if extra_sign:
        want = {extra_sign: signs[extra_sign]} if signs.get(extra_sign) else {}
    g["signs"] = all(np.sign(r["beta_final"][f]) == v and abs(r["beta_final"][f]) > 1e-6 for f, v in want.items())
    cal = r["calib_dev"] + (r.get("calib_ho") or [])
    g["calibration"] = all(abs(c["p"] - c["obs"]) <= 0.05 for c in cal)
    return g


def verdict(g: dict, r: dict, p_raw: float) -> str:
    if all(g.values()):
        return "real edge"
    if r["dev_old"]["bss"] > 0 and r["dev_new"]["bss"] > 0 and p_raw < 0.10:
        return "maybe"
    return "coin flip"


def _strip(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}


def same_period(r: dict) -> dict:
    """M1-M3 on the HAR-RV vol-fix window (19 Aug 2025 - 5 Nov 2026) and its last 250 origins,
    joining the walk-forward development predictions and the frozen-model holdout predictions."""
    parts = [r["_dev_pred"]] + ([r["_ho_pred"]] if "_ho_pred" in r else [])
    d = pd.concat(parts).dropna(subset=["p"])
    d = d[(d["date"] >= "2025-08-19") & (d["date"] <= "2026-11-05")].sort_values("date")
    out = {}
    for lab, x in (("vol-fix window", d), ("its last 250", d.tail(250))):
        if len(x):
            out[lab] = {"n": len(x), "from": str(x["date"].iat[0].date()), "to": str(x["date"].iat[-1].date()),
                        "bss": bss(x["y"].to_numpy(), x["p"].to_numpy(), x["base"].to_numpy()),
                        "hit": float(np.mean((x["p"] > 0.5) == (x["y"] == 1)))}
    return out


def run_all(args) -> int:
    t_start = time.perf_counter()
    hourly, daily = hourly_table(), daily_table()
    models = args.model or ["M1", "M2", "M3"]
    tiers = args.tier or [1, 2]
    R, T2 = {}, {}
    if 1 in tiers or 2 in tiers:
        for m in models:
            spec = MODELS[m]
            R[m] = run_model(hourly, spec["name"], spec["x"], spec["target"], spec["ret"], False, spec["sign"],
                             args.holdout, n_perm=args.perm)
            _print(m, R[m])
        if "M1" in models:
            spec = MODELS["M1"]
            R["M1_daily"] = run_model(daily, "Next day, daily proxy (NQ=F close-to-close since 2000)", spec["x"], "y1",
                                      "r1", True, spec["sign"], args.holdout, n_perm=args.perm, price_col="price")
            _print("M1_daily", R["M1_daily"])
    hourly_ms = [m for m in ("M1", "M2", "M3") if m in R]
    adj = holm({m: R[m]["perm_p"] for m in hourly_ms}) if args.perm and hourly_ms else {}
    if "M1_daily" in R and args.perm:
        adj["M1_daily"] = R["M1_daily"]["perm_p"]
    if 2 in tiers and len(hourly_ms) == 3:
        best = max(hourly_ms, key=lambda m: R[m]["dev"]["bss"])
        spec = MODELS[best]
        tests = [(best, v, hourly, False) for v in TIER2]
        if best != "M1":       # the Fed-odds idea is about the next 24h: also test it (and its proxy) on M1
            tests += [("M1", v, hourly, False) for v in ("fed_hawk", "yld2_chg")]
        tests += [("M1_daily", v, daily, True) for v in DAILY_TIER2 if best == "M1" or v == "yld2_chg"]
        for base_name, var, df, is_daily in tests:
            sp = MODELS["M1" if base_name == "M1_daily" else base_name]
            dfv = df.dropna(subset=[var])
            usable = dfv.dropna(subset=sp["x"] + [var, sp["target"]])
            n_scored = int((usable["date"] < HOLDOUT).sum()) - (DAILY_MIN if is_daily else HOURLY_MIN)
            if n_scored < MIN_SCORED:
                T2[f"{base_name}+{var}"] = {"insufficient": True, "dev_n": max(n_scored, 0), "perm_p": 1.0,
                                            "first_date": str(usable["date"].iat[0].date()) if len(usable) else None}
                print(f"T2 {base_name}+{var}: insufficient data ({max(n_scored, 0)} scored development days < {MIN_SCORED})")
                continue
            signs = dict(sp["sign"], **{var: TIER2[var]})
            price = "price" if is_daily else "last"
            base_r = run_model(dfv, "base", sp["x"], sp["target"], sp["ret"], is_daily, sp["sign"], args.holdout,
                               n_perm=0, price_col=price)
            aug = run_model(dfv, f"{base_name} + {var}", sp["x"] + [var], sp["target"], sp["ret"], is_daily, signs,
                            args.holdout, n_perm=args.perm, price_col=price, perm_mode="x", perm_var=var)
            aug["base_same_rows"] = {k: base_r[k] for k in ("dev", "dev_old", "dev_new") + (("holdout",) if args.holdout else ())}
            aug["first_date"] = str(dfv["date"].iat[0].date())
            T2[f"{base_name}+{var}"] = aug
            _print(f"T2 {base_name}+{var}", aug, base_r)
        T2["_best"] = best
    adj2 = holm({k: v["perm_p"] for k, v in T2.items() if not k.startswith("_")}) if args.perm and T2 else {}
    print(f"done in {time.perf_counter() - t_start:.0f}s")
    if args.holdout and len(hourly_ms) == 3 and T2:
        write_report(R, T2, adj, adj2, hourly, daily)
    return 0


def _print(name, r, base=None):
    d, o, n = r["dev"], r["dev_old"], r["dev_new"]
    ho = r.get("holdout")
    s = (f"{name:22} dev n {d['n']:4} BSS {d['bss']:+.4f} (halves {o['bss']:+.4f} / {n['bss']:+.4f}) hit {d['hit']:.3f} "
         f"perm p {r.get('perm_p', float('nan')):.3f}  coefs " + " ".join(f"{k} {v:+.3f}" for k, v in r["beta_final"].items()))
    if base:
        s += f"  | gain vs base {d['bss'] - base['dev']['bss']:+.4f}"
    if ho:
        s += f"  | holdout n {ho['n']} BSS {ho['bss']:+.4f} hit {ho['hit']:.3f}"
    print(s, flush=True)


def _f(x, fmt="{:+.4f}"):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else fmt.format(x)


def write_report(R, T2, adj, adj2, hourly, daily) -> None:
    best = T2["_best"]
    V, G = {}, {}
    for m in ("M1", "M2", "M3"):
        g = gates(R[m], MODELS[m]["sign"], adj[m])
        if m == "M1":
            dl = R["M1_daily"]
            g["daily_eras"] = all(dl[e] and dl[e]["bss"] > 0 for e in ("era_2000_2012", "era_2013_2026")) and \
                gates(dl, MODELS["M1"]["sign"], 0.0)["signs"]
        G[m], V[m] = g, verdict(g, R[m], R[m]["perm_p"])
    gd = gates(R["M1_daily"], MODELS["M1"]["sign"], adj["M1_daily"])
    G["M1_daily"], V["M1_daily"] = gd, verdict(gd, R["M1_daily"], R["M1_daily"]["perm_p"])
    for k, r in T2.items():
        if k.startswith("_"):
            continue
        if r.get("insufficient"):
            G[k], V[k] = {}, "insufficient data"
            continue
        var = k.split("+")[1]
        g = gates(r, {var: TIER2[var]}, adj2[k], extra_sign=var)
        b = r["base_same_rows"]
        g["gain"] = all(r[h]["bss"] > b[h]["bss"] for h in ("dev_old", "dev_new", "holdout") if b.get(h) and r.get(h))
        G[k], V[k] = g, verdict(g, r, r["perm_p"])
    fh = hourly.dropna(subset=["fed_hawk"])
    fh_dev = fh[(fh["date"] < HOLDOUT)].dropna(subset=["y1"])
    fed_key = f"{best}+fed_hawk"
    fed_scored = max((T2[k]["dev"]["n"] for k in (fed_key, "M1+fed_hawk") if k in T2 and "dev" in T2[k]), default=0)
    yn = lambda b: "yes" if b else "**no**"  # noqa: E731
    L = ["# NQ direction research (Parts 2–5 of NQ_DIRECTION_RESEARCH.md)", "",
         f"Run {datetime.now():%d %b %Y %H:%M}. Research only: nothing here is on the page or in the alert. "
         f"Holdout ({HOLDOUT:%d %b %Y} onward) scored once, in this run.", ""]
    L += ["## Summary", ""]
    L += _summary(R, T2, V, G, adj, adj2, best, fh, fed_scored)
    L += ["", "## Verdicts", "",
          "| model | verdict | BSS dev (older / newer) | BSS holdout | perm p (Holm) | signs | calibration | other |",
          "|---|---|--:|--:|--:|---|---|---|"]
    for m in ("M1", "M2", "M3", "M1_daily"):
        r, g = R[m], G[m]
        other = f"daily eras {yn(g['daily_eras'])}" if "daily_eras" in g else ""
        L.append(f"| {m}: {r['name']} | **{V[m]}** | {_f(r['dev_old']['bss'])} / {_f(r['dev_new']['bss'])} | "
                 f"{_f(r['holdout']['bss'])} | {r['perm_p']:.3f} ({adj[m]:.3f}) | {yn(g['signs'])} | {yn(g['calibration'])} | {other} |")
    L += ["", "Gate (Part 4): BSS > 0 in both development halves **and** the holdout; permutation p < 0.05 after "
          "Holm across M1–M3; expected signs; calibration within ±5 points per bucket; M1 also needs the daily proxy "
          "to hold in 2000–2012 and 2013–2026. Verdict: **real edge** = all gates; **maybe** = BSS > 0 in both "
          "development halves with unadjusted p < 0.10; otherwise **coin flip**. (M1_daily's p is not in the Holm set; "
          "it is M1's long-history check.)", ""]
    L += ["## Tier 1 detail", ""]
    for m in ("M1", "M2", "M3", "M1_daily"):
        L += _detail(m, R[m], MODELS["M1" if m == "M1_daily" else m]["sign"])
    L += ["## Same days as the HAR-RV vol fix", "",
          "Hourly models on the vol-fix window (304 origins, 19 Aug 2025 – 5 Nov 2026) and its last 250, joining "
          "walk-forward development predictions and frozen-model holdout predictions. Information only.", "",
          "| model | window | n | BSS | hit rate |", "|---|---|--:|--:|--:|"]
    for m in ("M1", "M2", "M3"):
        for lab, x in same_period(R[m]).items():
            L.append(f"| {m} | {lab} ({x['from']} – {x['to']}) | {x['n']} | {_f(x['bss'])} | {x['hit']:.1%} |")
    L += ["", f"## Tier 2 (added one at a time to {best}, the best Tier 1 model by development BSS)", "",
          "fed_hawk and its 2Y-yield proxy are also tried on M1 (the next 24h, which the Fed-odds idea is about), "
          "and yld2_chg on the M1 daily proxy since 2000. All of these are in the Holm set.", "",
          "Each test compares the model with the variable against the same model without it, on the same rows. "
          "p = block permutation of the added variable (1,000×); Holm across all Tier 2 tests. A test with fewer than "
          f"{MIN_SCORED} scored development days is \"insufficient data\" (it still counts in the Holm set, with p = 1).", "",
          "| test | rows from | dev n | BSS dev (older / newer) | gain vs base (dev) | holdout BSS (gain) | coef (expected) | perm p (Holm) | verdict |",
          "|---|---|--:|--:|--:|--:|--:|--:|---|"]
    for k, r in T2.items():
        if k.startswith("_"):
            continue
        var = k.split("+")[1]
        if r.get("insufficient"):
            L.append(f"| {k} | {r['first_date']} | {r['dev_n']} | — | — | — | — | — | **insufficient data** |")
            continue
        b = r["base_same_rows"]
        ex = {1: "+", -1: "−", 0: "none"}[TIER2[var]]
        hg = r["holdout"]["bss"] - b["holdout"]["bss"] if r.get("holdout") and b.get("holdout") else None
        L.append(f"| {k} | {r['first_date']} | {r['dev']['n']} | {_f(r['dev_old']['bss'])} / {_f(r['dev_new']['bss'])} | "
                 f"{_f(r['dev']['bss'] - b['dev']['bss'])} | {_f(r['holdout']['bss'])} ({_f(hg)}) | "
                 f"{r['beta_final'][var]:+.3f} ({ex}) | {r['perm_p']:.3f} ({adj2[k]:.3f}) | **{V[k]}** |")
    L += ["", "### Fed odds (Polymarket)", "",
          f"- Events found: {len(fed_events())} \"Fed decision\" / \"Fed Interest Rates\" events (Sep 2022 – Apr 2027 meetings), "
          "matched to scheduled FOMC decisions by end date; hourly history from the CLOB `/prices-history` endpoint "
          "(14-day requests, fidelity 60).",
          f"- **First date with usable data: {fh['date'].iat[0]:%d %b %Y}** (the hourly NQ sample starts {hourly['date'].iat[0]:%d %b %Y}, "
          "so Polymarket covers essentially all of it).",
          f"- Days with a usable change: {len(fh)} ({len(fh_dev)} before the holdout); scored in the walk-forward: {fed_scored} "
          f"→ {'enough to test (≥ 150)' if fed_scored >= 150 else '**insufficient data** (< 150)'}.",
          "- The 2Y-yield proxy is tested on the daily M1 history (FRED DGS2, 2000+) and on the hourly sample (ZT=F).", ""]
    L += ["## Method notes", "",
          "- Hourly sample: the cached roll-adjusted NQ=F bars production uses (lab/cache/bars_hourly_23h.parquet, "
          f"{hourly['date'].iat[0]:%d %b %Y} – {hourly['date'].iat[-1]:%d %b %Y}), one row per 07:00 NY origin. "
          "Bars are hourly, so \"9am\" is the close of the 8am bar, \"3pm\" the close of the 2pm bar, \"4pm\" the 3pm bar's close.",
          "- Daily proxy: NQ=F close-to-close since Sep 2000 (Yahoo bar dated d closes 17:00 NY d), predicted at the previous "
          "close. Roll days patched with ^NDX (the switch day on Yahoo varies). downcash uses ^NDX open → close "
          "(2000's opens are partly stale; scoring starts after the 2-year minimum anyway).",
          "- FOMC: scheduled decision days 2000–2027 from federalreserve.gov (historical pages to 2020, the calendar page after); "
          "unscheduled meetings, calls and notation votes excluded.",
          "- No look-ahead: every input is known at the forecast time; models are refitted monthly on data before the month; "
          "C is chosen inside the training window; the holdout model is frozen at the end of development.",
          "- Permutation p-values use the whole walk-forward (including the choice of C) on 1,000 block-shuffled series.",
          "- Costs for the long/short check: 1 tick (0.25 pt) + ~$4.50 fees (0.225 pt) per round trip; daily proxy points use "
          "the previous NQ=F close.", "",
          "Run: `python lab/direction.py` (development only) · `python lab/direction.py --holdout` (the one final run)."]
    OUT.write_text("\n".join(L) + "\n")
    OUT_JSON.write_text(json.dumps({"R": {k: _strip(v) for k, v in R.items()},
                                    "T2": {k: _strip(v) for k, v in T2.items() if not k.startswith("_")},
                                    "holm_t1": adj, "holm_t2": adj2, "gates": G, "verdicts": V},
                                   indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print(f"wrote {OUT}")


def _detail(m, r, signs) -> list[str]:
    L = [f"### {m}: {r['name']}", "",
         f"Inputs: {', '.join(r['features'])}. Development {r['dev_range'][0]} – {r['dev_range'][1]}"
         + (f"; holdout {r['holdout_range'][0]} – {r['holdout_range'][1]}" if r.get("holdout_range") else "") + ".", "",
         "| period | n | up rate | BSS | log loss (base) | hit rate | mean P (sd) |", "|---|--:|--:|--:|--:|--:|--:|"]
    rows = [("development", r["dev"]), ("dev older half", r["dev_old"]), ("dev newer half", r["dev_new"])]
    if r.get("era_2000_2012"):
        rows += [("2000–2012 (scored)", r["era_2000_2012"]), ("2013–2026 (scored)", r["era_2013_2026"])]
    rows.append(("holdout", r["holdout"]))
    for lab, s in rows:
        L.append(f"| {lab} | {s['n']} | {s['up_rate']:.1%} | {_f(s['bss'])} | {s['logloss']:.4f} ({s['logloss_base']:.4f}) | "
                 f"{s['hit']:.1%} | {s['p_mean']:.3f} ({s['p_sd']:.3f}) |")
    L += ["", "| input | coefficient (final dev model, standardised) | expected | refits with expected sign |", "|---|--:|---|--:|"]
    for f in r["features"]:
        ex = signs.get(f)
        L.append(f"| {f} | {r['beta_final'][f]:+.4f} | {({1: '+', -1: '−'}).get(ex, 'none')} | "
                 f"{_f(r['sign_share'].get(f), '{:.0%}') if ex else '—'} |")
    L += ["", f"C chosen: final {r['C_final']:.0e}, median over refits {r['C_median']:.0e} (small C = strong shrinkage to the base rate). "
          f"Permutation p = {r['perm_p']:.3f} (null 95th percentile BSS {r['perm_null_q95']:+.4f}).", "",
          "Calibration (development" + (", deciles" if r["daily"] else ", terciles of P") + "):", "",
          "| mean P | observed up | n |", "|--:|--:|--:|"]
    L += [f"| {c['p']:.3f} | {c['obs']:.3f} | {c['n']} |" for c in r["calib_dev"]]
    L += ["", "Calibration (holdout, terciles):", "", "| mean P | observed up | n |", "|--:|--:|--:|"]
    L += [f"| {c['p']:.3f} | {c['obs']:.3f} | {c['n']} |" for c in r["calib_ho"]]
    e, h = r["dev"], r["holdout"]
    L += ["", "Economic sanity (information only): mean next-period return when P > 0.55 vs P < 0.45, and a naive "
          "long/short (long if P > 0.5, short otherwise) after costs, against simply being long every period.", "",
          "| period | mean ret P>0.55 (n) | mean ret P<0.45 (n) | days long | long/short net pts/trade | always long net pts/trade | long/short net total pts |",
          "|---|--:|--:|--:|--:|--:|--:|"]
    for lab, s in (("development", e), ("holdout", h)):
        L.append(f"| {lab} | {_f(s.get('ret_hi'), '{:+.3f}%')} ({s.get('n_hi', 0)}) | {_f(s.get('ret_lo'), '{:+.3f}%')} ({s.get('n_lo', 0)}) | "
                 f"{_f(s.get('long_share'), '{:.0%}')} | {_f(s.get('ls_pts_per_trade'), '{:+.2f}')} | {_f(s.get('always_long_net'), '{:+.2f}')} | "
                 f"{_f(s.get('ls_pts_total'), '{:+,.0f}')} |")
    return L + [""]


def _summary(R, T2, V, G, adj, adj2, best, fh, fed_scored) -> list[str]:
    L = []
    real = [m for m in ("M1", "M2", "M3") if V[m] == "real edge"]
    t2_real = [k for k in T2 if not k.startswith("_") and V[k] == "real edge"]
    if not real and not t2_real:
        L.append("**No direction model passes the gates. Nothing should go on the page.** "
                 "Every model's probabilities stay close to the base rate (NQ goes up a bit more than half the time), "
                 "and none beats that base rate reliably out of sample.")
    else:
        L.append(f"**Passed all gates: {', '.join(real + t2_real)}.** Treat as experimental; see the tables.")
    L.append("")
    for m in ("M1", "M2", "M3"):
        r = R[m]
        L.append(f"- **{m} ({r['name']}): {V[m]}.** Development BSS {_f(r['dev']['bss'])} "
                 f"(older {_f(r['dev_old']['bss'])}, newer {_f(r['dev_new']['bss'])}), holdout {_f(r['holdout']['bss'])}; "
                 f"hit rate {r['dev']['hit']:.1%} vs up rate {r['dev']['up_rate']:.1%}; permutation p {r['perm_p']:.3f} "
                 f"(Holm {adj[m]:.3f}).")
    d = R["M1_daily"]
    L.append(f"- **M1 daily proxy (2000–2026): {V['M1_daily']}.** BSS {_f(d['dev']['bss'])} on {d['dev']['n']:,} days; "
             f"2000–2012 {_f(d['era_2000_2012']['bss'])}, 2013–2026 {_f(d['era_2013_2026']['bss'])}; holdout {_f(d['holdout']['bss'])}; "
             f"permutation p {d['perm_p']:.3f}.")
    n2 = len([k for k in T2 if not k.startswith('_')])
    on = sorted({k.split("+")[0] for k in T2 if not k.startswith("_")}, key=lambda x: (x != best, x))
    L.append(f"- **Tier 2** ({n2} tests, on {', '.join(o.replace('M1_daily', 'the M1 daily proxy') for o in on)}): "
             + ("; ".join(f"{k} {V[k]}" for k in T2 if not k.startswith("_"))) + ".")
    L.append(f"- **Fed odds:** Polymarket history is usable from {fh['date'].iat[0]:%d %b %Y}; {fed_scored} scored "
             f"development days, so it was tested (not \"insufficient data\"). Verdict: on {best} {V.get(best + '+fed_hawk', 'n/a')}"
             + (f", on M1 {V['M1+fed_hawk']}" if "M1+fed_hawk" in V and best != "M1" else "") + ".")
    return L


if __name__ == "__main__":
    sys.exit(main())
