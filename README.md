# NQ Kronos Forecast

A small self-updating web page for Nasdaq-100 futures (NQ). Once per US trading day, at
1:00pm London (the close of the 07:00–08:00 New York bar), it publishes a calibrated HAR-RV
volatility forecast for the next 23 trading hours (1pm → 1pm London) with five prediction
intervals, the cash-session range, the US release calendar, Treasuries, Fed-decision betting odds
(Polymarket) and tech headlines, plus an
experimental [Kronos](https://github.com/shiyu-coder/Kronos)-small run from the same bar, and checks
how often reality lands inside the ranges.

**Live page:** https://ssslithy.github.io/nq-kronos/

It runs for free on GitHub: one daily Actions workflow fetches data, runs both models on a CPU
runner, commits the results and deploys GitHub Pages: the vol forecast first, then Kronos, so Kronos can
never delay or block it.

> Experimental model output on delayed data. Not investment advice.

## What's on the page

All times are London, 12-hour ("1:00pm"); New York appears only in Details.

- **Header:** forecast time and the 1pm price, next update time, link to the accuracy page.
- **Top card:** "Next 24 hours" (the 1pm → 1pm window, 23 trading hours) and "NYC Cash Session"
  (2:30pm – 9:00pm London): ± points, ± %, and the price range, from the **90% prediction
  interval (PI)**. Beside them: the next 3 US releases (impact bars) and the 2Y / 10Y Treasury note
  futures' 1pm → 1pm move with a sparkline and yields, and Polymarket's odds for the next Fed
  decision (Hike / Hold / Cut, 1pm → 1pm sparkline, change in points; crowd betting odds, not a
  forecast from this site). Below, full width: 3 tech headlines (links only; the day is shown for
  any not from the forecast's day).
- **Chart:** 48 hourly candles, then the forecast fan to the end of the window. Buttons 99 / 95 /
  90 / 85 / 80% (multi-select, 90% on by default) draw stacked bands; at the last step each band
  equals that range exactly. "Cash" draws the cash-session box and cuts the chart at the cash close
  (9:00pm London), so the session fills the forecast area; the box's left edge pairs the cash levels
  with the bands' values at 9:00pm (on phones, one line under the chart). "Levels" (24h, Cash)
  shows or hides the band and cash numbers. "Kronos (experimental)" overlays Kronos-small's P10 / P90 paths when it ran from the
  same bar (its 80% band held only ~46% in the lab); otherwise it is disabled ("not run today").
- **Footer:** the 90% PI's hit rate over the last 250 days, and a Details section (window, model and
  multipliers, recent moves, cash-session notes, Friday weekend-gap note, data sources, and
  "Volatility only, not direction. Not investment advice.").
- **Accuracy page** (`accuracy.html`): 1pm → 1pm and cash-session moves against the 90% ranges for
  the last 250 days, hit-rate tables (80 / 85 / 90 / 95 / 99%, by half, by release day and type; cash
  90% and 80%, touch rates), rolling 60-day hit rates, the live Kronos daily check and the Phase 6
  Kronos lab.
- A banner appears only if the daily run failed or is more than 90 minutes late. If the calendar,
  Treasuries, Fed odds or news fetch fails, that block is simply hidden.

## How it works

| Step | File |
|---|---|
| Download `NQ=F` hourly bars from Yahoo, drop the in-progress bar and stray off-session bars, back-adjust quarterly contract rolls, filter bad ticks, run sanity checks | `forecast/fetch.py` |
| NQ trading calendar (Sun 18:00 – Fri 17:00 New York, 17:00 daily break, US holidays), roll dates, session list for the page | `forecast/nq_calendar.py` |
| Daily vol forecast (HAR-RV), fan, alert, accuracy log | `forecast/run_vol.py`, `garch_vol.py`, `vol_accuracy.py`, `vol_alert.py` |
| Calendar, Treasuries, Fed odds and news for the page (after the alert; fail-soft) | `forecast/page_data.py` |
| Research: volatility contest, vol fix, regime filter | `lab/vol_contest.py` (`--fix`), `lab/regime.py` → `lab/results/` |
| Kronos-small from the same bar (`--origin`, `--horizon 23` in the daily job): 100 paths, `site/data/latest.json`, `history.jsonl`, `band_check.json`, `status.json` | `forecast/run_forecast.py` |
| Score past Kronos forecasts against actual closes | `forecast/band_check.py` |
| Static page (vanilla JS + SVG, no build step) | `site/` |
| Daily job: vol forecast + alert, commit, deploy; then Kronos as its own job (15-min limit), commit, deploy | `.github/workflows/garch_vol.yml` |
| Manual Kronos run from the latest bar; deploy on `site/` code changes | `.github/workflows/forecast.yml` |

If the vol forecast fails, the previous one stays up marked stale and the run fails. If
Kronos fails or runs past 15 minutes, only Kronos is marked stale (badge on the page, error in
`status.json`); the vol forecast, alert and deploy have already gone out.

## Run locally

Requires Python 3.11.

```bash
git clone --recurse-submodules <this repo> && cd nq-kronos
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r forecast/requirements.txt
python forecast/fetch.py                 # last 5 bars + the next forecast hours
python forecast/run_forecast.py          # ~3 min on a laptop CPU; writes site/data/*
python -m http.server -d site            # then open http://localhost:8000
```

On Linux, install the CPU-only PyTorch wheel first to avoid a multi-GB CUDA download:
`pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu`.
On Apple silicon, `--device mps` roughly halves the runtime (results then differ slightly
from the CPU run).

## Changing samples or horizon

- Locally: `python forecast/run_forecast.py --samples 50 --horizon 12`
  (or set `NQ_SAMPLES` / `NQ_HORIZON`).
- In the daily job: 100 samples (`NQ_SAMPLES` in `.github/workflows/garch_vol.yml`). A
  manual Kronos-only run (Actions → forecast → Run workflow) lets you pick the sample count
  and run from the latest bar, even while the market is closed.
- Sampling cost is ~1.5–2 s per path on CPU, because Kronos recomputes its full 512-bar
  context at every step; 100 paths take about 3–5 minutes on a GitHub runner.

## Daily volatility forecast (HAR-RV), 1pm → 1pm London

Every US trading day `.github/workflows/garch_vol.yml` (12:05 and 13:05 UTC) runs
`forecast/run_vol.py`. As of the 07:00–08:00 New York bar close (1pm London; 12pm in the few
weeks when US and UK clocks change on different dates) it forecasts the volatility of the next
**23 trading hours**, to the same bar's close the next trading day, with a calibrated
HAR-RV model (`forecast/garch_vol.py`, `VOL_MODEL`). It writes `site/data/vol_latest.json`,
`vol_history.jsonl` and `vol_status.json`, sends the alert, then writes `calendar.json`,
`treasuries.json`, `fed_odds.json` and `news.json`. Volatility only, not direction.

- **Model (since 9 Nov 2026):** HAR-RV. The log of the next 23 hours' realised variance is
  regressed on the log realised variance of the last 1, 5 and 22 days (sums of squared hourly
  returns), refitted every day on the last 250 days whose outcome was known. It replaced an
  hourly GARCH(1,1), which forgot a shock within about a day: in the Oct–Nov 2026 sell-off it kept
  rating volatility "Calm" and missed 3 of 7 days. On stress days HAR-RV's 90% range held 92.6%
  (GARCH 86.1%) while normal days' ranges got 12.8% narrower (`lab/results/vol_fix.md`).
- σ = k × raw σ, k = RMS(move / raw σ) over the trailing 250 matured forecasts.
- **Five ranges:** 80% = last × exp(±1.2816 σ); 85 / 90 / 95 / 99% = ±mXX σ, where mXX is the
  XXth percentile of |move / σ| over the same trailing 250 (measured multipliers, ≈1.36 / 1.59 /
  2.16 / 2.73). The **90% range is the headline** on the card and in the alert. The 99% rests on
  2–3 observations: rough.
- **Cash session:** σ_cash = σ × the cash hours' share of the window's variance (hour-of-day
  profile). Its 90% range uses its own measured multiplier: the 90th percentile of |cash move /
  σ_cash| over the trailing 250 finished sessions (≈1.6). The range is centred on the 1pm price.
- **Backfill, HAR-RV (3 Nov 2025 – 5 Nov 2026, 250 forecasts, 23-hour window, older / newer half):**
  80% 80.0% / 79.2%, 85% 83.2% / 83.2%, **90% 90.4% / 88.0%**, 95% 93.6% / 96.0%,
  99% 98.4% / 99.2%. Cash 90%: 87.4% / 91.7% (the 191 days with a measured multiplier);
  cash 80%: 80.0% / 85.7%.
- **Research (`lab/results/`):** the redesign's volatility contest (GARCH vs GARCH + VXN, HAR-RV,
  XGBoost, GARCH + regime) kept GARCH, because no challenger was ≥5% narrower with lower QLIKE in
  both halves. The vol fix (`vol_fix.md`) then scored misses that cluster after shocks (stress
  coverage, miss-after-miss, Christoffersen test, calm-day width, QLIKE on 304 origins) and
  HAR-RV was the only candidate to pass.
  A 2-state HMM regime filter failed its stability test (median run 3.5 days), so the page has
  no regime chip.

```bash
python forecast/garch_vol.py --now "2026-11-05 08:10"     # forecast table for a past day (NY time)
python forecast/run_vol.py --now "2026-11-06 08:10" --force --out /tmp/vol   # full daily job, no alert
python forecast/run_vol.py --now "2026-11-06 08:10" --force --out /tmp/vol --fail=news   # simulate a failed extra
```

**Event calendar: update each January.** `forecast/events_2026_2027.yaml` holds the scheduled
FOMC, CPI, payrolls, PCE, GDP (advance) and retail sales dates, copied from the official
calendars cited in the file (Federal Reserve, BLS, BEA, Census). Add the new year's dates
every January (or when an agency revises its calendar); after `covered_until` the page shows
"event calendar not loaded" instead of flagging releases.

**Accuracy log.** Once a forecast's last bar has closed (normally at the next day's run, since
the window ends at the next origin bar), its line in `vol_history.jsonl` gets the actual move
(`move_pct`) and whether it landed inside each range (`inside80`, `inside85`, `inside90`,
`inside95`, `inside99`). The history is a 1-year backfill (each past day forecast using only the
data before it, `"backfill": true`), redone in Nov 2026 for the 23-hour window:

```bash
python forecast/vol_accuracy.py --backfill --all --out /tmp/vol_a   # try it in a scratch folder first
python forecast/vol_accuracy.py --backfill --all                    # then regenerate site/data
```

**Cash-session backtest.** For each forecast, the cash move is 100·ln(close of the 15:00–16:00 NY
bar / close of the 08:00–09:00 NY bar) on the forecast day, i.e. measured from the 9:00am New York
bar, half an hour before the 9:30am open (2:00–9:00pm London). Lines get `cash_move_pct`,
`cash_inside80`, `cash_inside90` (against the measured `cash_m90`), touch flags and a monitoring
`k_cash`. The page labels the session with the real open, 2:30pm – 9:00pm London.

**Phone alert (optional).** After each forecast, `forecast/vol_alert.py` posts one line to
[ntfy.sh](https://ntfy.sh), leading with the 90% range and the window in London time, for example
`NQ Fri 1:00pm – Mon 1:00pm London: ±448 pts (±1.65%) · 90%: 26,654–27,550 · Cash 2:30pm–9:00pm ±346 pts · Payrolls (NFP) 1:30pm`.
To turn it on, install the ntfy app and subscribe to a long random topic name
(`python forecast/vol_alert.py --new-topic` makes one). Then store that name as the repository
secret `NTFY_TOPIC` (Settings → Secrets and variables → Actions). Anyone who knows the name can
read the topic, so keep it secret. Without the secret nothing is sent. Manual forced runs alert too;
`--now` runs never do. To check the setup, run garch-vol manually with "Send a TEST phone alert" ticked.

## Data notes and known limits

- Yahoo data is delayed and unofficial; it can have gaps, bad ticks or revisions. Bars
  whose open/high/low sit more than 6× the median bar range away from both the previous
  close and their own close are treated as bad ticks and clipped (closes are never changed).
  This can also clip genuine extreme wicks.
- Yahoo's continuous `NQ=F` jumps at each quarterly roll. The gap is measured from the
  incoming contract's own Yahoo series (e.g. `NQZ26.CME`) and earlier bars are
  back-adjusted; if that series is unavailable the gap is estimated and flagged.
- Yahoo has no bars for US holiday sessions, so the calendar treats them as closed.
  Early-close sessions are approximate.
- Direction on NQ is close to unpredictable for any model. Read the fan's **width**
  (expected range), not a median, as the main signal, and check the accuracy page to see
  whether that width has been realistic.
- Treasuries: the % move is of the 2Y / 10Y note futures (`ZT=F`, `ZN=F`, Yahoo hourly), not of
  yields; yields are the latest daily values before the forecast (FRED `DGS2`, Yahoo `^TNX`).
  Headlines are a 1pm snapshot of free RSS feeds (Yahoo Finance, CNBC): titles and links only,
  mixed across both feeds. Fed odds are Polymarket's public prices (crowd betting odds), not a
  forecast from this site.
- Kronos's published benchmarks come from its authors and are not yet independently
  replicated.
- Backfilled history lines (`"backfill": true`, marked "bf" on the page) were generated
  afterwards with 30 paths; they drop out of the 30-forecast window as live ones arrive.
- **Volatility forecast: volatility only, not direction.** It says how far NQ is likely
  to move from 1pm to 1pm London (23 trading hours), not which way. Not investment advice.
- On Fridays the window runs over the weekend; weekend gap risk isn't fully in Friday's range.
- The model assumes tomorrow looks like the recent past: it reacts to shocks only after they
  happen, and it knows nothing about scheduled releases (FOMC, CPI, payrolls…). The event
  flag marks those days; realised moves on them often exceed the range.
- The forecast uses the same delayed, unofficial Yahoo data as above, so a bad or late
  bar affects it too. The 90% range held 89.2% of the time over the 1-year backfill
  (3 Nov 2025 – 5 Nov 2026). That is past behaviour, not a guarantee.

## Credits

- **Kronos** — Yu Shi et al., *Kronos: A Foundation Model for the Language of Financial
  Markets* (AAAI 2026). Code: <https://github.com/shiyu-coder/Kronos> (MIT licence),
  included as a git submodule in `vendor/kronos` pinned to commit `67b630e`. Weights:
  [NeoQuasar/Kronos-small](https://huggingface.co/NeoQuasar/Kronos-small) and
  [NeoQuasar/Kronos-Tokenizer-base](https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base)
  (MIT), pinned to specific revisions in `forecast/run_forecast.py`.
- **Data** — Yahoo Finance via [yfinance](https://github.com/ranaroussi/yfinance).

## Disclaimer

Experimental model output on delayed data. Not investment advice. Nothing here is a
recommendation to trade; any strategy ideas belong in a demo account first.
