# NQ Kronos Forecast

A small self-updating web page for Nasdaq-100 futures (NQ). Once per US trading day,
from the 07:00–08:00 New York bar, it publishes a calibrated GARCH 24h volatility forecast
and its forecast fan, plus an experimental [Kronos](https://github.com/shiyu-coder/Kronos)-small
fan from the same bar, and checks how often reality lands inside each model's band.

**Live page:** https://ssslithy.github.io/nq-kronos/

It runs for free on GitHub: one daily Actions job fetches data, runs both models on a CPU
runner, commits the result and deploys GitHub Pages.

> Experimental model output on delayed data. Not investment advice.

## What's on the page

- **24h volatility card (GARCH):** expected 24h move, 80% and 95% ranges, a rough 99% stress
  figure, cash-session σ, regime, scheduled-release flag, and a 60-day sparkline with the
  80% hit rate.
- **Chart (default: GARCH fan):** the last 2 trading days of hourly candles up to the
  07:00–08:00 NY bar, then 100 simulated paths, the shaded 80% and 95% bands, the median
  and right-edge labels at +24h. The bands equal the card's ranges at +24h; in between, the
  variance follows the hour-of-day profile. The **Kronos (experimental)** toggle swaps in
  Kronos-small's own 100 paths and P10–P90 band from the same bar; in the lab its 80% band
  held only ~46% of the time.
- Toggles: show/hide paths; 7h or 24h view. Hover/tap the chart for values.
- Status pill: "daily · next update <date> 08:00 NY (<London time>)"; "stale" with a
  banner if the daily run failed or is more than 90 minutes late.
- **Band check:** live daily forecasts only, GARCH next to Kronos: the share of +24h
  closes inside each 80% band (shows "collecting" until 20 have matured), then the Kronos
  detail table.

## How it works

| Step | File |
|---|---|
| Download `NQ=F` hourly bars from Yahoo, drop the in-progress bar and stray off-session bars, back-adjust quarterly contract rolls, filter bad ticks, run sanity checks | `forecast/fetch.py` |
| NQ trading calendar (Sun 18:00 – Fri 17:00 New York, 17:00 daily break, US holidays), roll dates, session list for the page | `forecast/nq_calendar.py` |
| Daily GARCH forecast, fan, alert, accuracy log | `forecast/run_vol.py`, `garch_vol.py`, `vol_accuracy.py`, `vol_alert.py` |
| Kronos-small from the same bar (`--origin`): 100 paths × 24 steps, `site/data/latest.json`, `history.jsonl`, `band_check.json`, `status.json` | `forecast/run_forecast.py` |
| Score past Kronos forecasts against actual closes | `forecast/band_check.py` |
| Static page (vanilla JS + canvas, no build step) | `site/` |
| Daily job (GARCH, then Kronos, one commit, one deploy) | `.github/workflows/garch_vol.yml` |
| Manual Kronos run from the latest bar; deploy on `site/` code changes | `.github/workflows/forecast.yml` |

If the GARCH forecast fails, the previous one stays up marked stale and the run fails. If
only Kronos fails, the GARCH forecast is still published and `status.json` records the
Kronos error.

## Run locally

Requires Python 3.11.

```bash
git clone --recurse-submodules <this repo> && cd nq-kronos
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r forecast/requirements.txt
python forecast/fetch.py                 # last 5 bars + next 24 forecast hours
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

## Daily 24h volatility forecast (GARCH)

Every US trading day `.github/workflows/garch_vol.yml` (12:05 and 13:05 UTC) runs
`forecast/run_vol.py`, which forecasts the next 24 tradable hours' volatility as of the
08:00 New York bar close with a calibrated GARCH(1,1) (`forecast/garch_vol.py`) and writes
`site/data/vol_latest.json`, `vol_history.jsonl` and `vol_status.json`. Volatility only, not direction.

- 80% range: last × exp(±1.2816 kσ). 95% range: ±m95 kσ, where m95 is the 95th percentile
  of |24h move / kσ| over the trailing 250 matured forecasts (≈2.05). The 99% stress figure
  uses the 99th percentile the same way (≈3.1). It is shown as text only, because it rests on
  2–3 observations.
- Backfilled hit rates, 3 Nov 2025 – 5 Nov 2026 (250 forecasts): 80% range 80.8%
  (older/newer half 81.6% / 80.0%); 95% range 96.0% (95.2% / 96.8%); 99% stress 98.4%
  (99.2% / 97.6%).

```bash
python forecast/garch_vol.py --now "2026-11-05 08:10"     # forecast table for a past day (NY time)
```

**Event calendar: update each January.** `forecast/events_2026_2027.yaml` holds the scheduled
FOMC, CPI, payrolls, PCE, GDP (advance) and retail sales dates, copied from the official
calendars cited in the file (Federal Reserve, BLS, BEA, Census). Add the new year's dates
every January (or when an agency revises its calendar); after `covered_until` the page shows
"event calendar not loaded" instead of flagging releases.

**Accuracy log.** Once a forecast's +24h bar has closed, its line in `vol_history.jsonl` gets
the actual 24h move (`move_pct`) and whether it landed inside the 80% range (`inside80`). This is
recorded by the next forecast run after that bar closes, so normally two runs after the forecast.
The page's sparkline and "Last 60 forecasts" line read these fields. The history started with a
1-year backfill: each past day was forecast using only the data available before it
(`"backfill": true`). To redo or extend it:

```bash
python forecast/vol_accuracy.py --backfill     # last 252 trading days; live lines are kept
```

**Phone alert (optional).** After each forecast, `forecast/vol_alert.py` posts one line to
[ntfy.sh](https://ntfy.sh), for example
`NQ 24h vol: ±276 pts (±1.02%) · 80%: 26,747–27,454 · Calm (19th pct) · NFP 08:30 NY`.
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
  (expected range), not the median, as the main signal — and check the band panel to see
  whether that width has been realistic.
- Kronos's published benchmarks come from its authors and are not yet independently
  replicated.
- Backfilled history lines (`"backfill": true`, marked "bf" on the page) were generated
  afterwards with 30 paths; they drop out of the 30-forecast window as live ones arrive.
- **GARCH volatility forecast: volatility only, not direction.** It says how far NQ is likely
  to move in the next 24 tradable hours, not which way. Not investment advice.
- GARCH assumes tomorrow looks like the recent past: it reacts to shocks only after they
  happen, and it knows nothing about scheduled releases (FOMC, CPI, payrolls…). The event
  flag marks those days; realised moves on them often exceed the range.
- The GARCH forecast uses the same delayed, unofficial Yahoo data as above, so a bad or late
  bar affects it too. The 80% range held 80.8% of the time over the 1-year backfill
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
