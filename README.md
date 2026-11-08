# NQ Kronos Forecast

A small self-updating web page: Nasdaq-100 futures (NQ) hourly candles with a
[Kronos](https://github.com/shiyu-coder/Kronos)-small fan of 100 possible paths for the
next 24 trading hours, plus a running check of how often reality lands inside the band.

**Live page:** https://ssslithy.github.io/nq-kronos/

It runs for free on GitHub: an hourly Actions job fetches data, runs the model on a CPU
runner and commits the result; GitHub Pages serves the static page.

> Experimental model output on delayed data. Not investment advice.

## What's on the page

- Last ~2 trading days of hourly candles, a NOW line, 100 sampled paths (green = ends above
  the last close, red = below), the P10–P90 band, the gold median, and right-edge labels.
- Toggles: show/hide paths; 7h or 24h view. Hover/tap the chart for values.
- Side panel: model, context, horizon, samples, last update (London and New York), data
  source, and badges for contract-roll adjustment ("roll gap estimated" when the gap could
  not be measured from the incoming contract).
- "Next 24h" strip: median change, P10–P90 range, share of paths ending up.
- Band check: over the last 30 matured +24h forecasts, the share of actual closes inside
  P10–P90 (≈80% means well calibrated) and the median's error next to a naive "no change"
  forecast. Shows "collecting data, N/30" until enough forecasts have matured.
- Status: "market closed · next update …" outside trading hours; a "stale" warning if no
  new forecast has arrived for 2 hours while the market is open.

## How it works

| Step | File |
|---|---|
| Download `NQ=F` hourly bars from Yahoo, drop the in-progress bar and stray off-session bars, back-adjust quarterly contract rolls, filter bad ticks, run sanity checks | `forecast/fetch.py` |
| NQ trading calendar (Sun 18:00 – Fri 17:00 New York, 17:00 daily break, US holidays), roll dates, session list for the page | `forecast/nq_calendar.py` |
| Load Kronos-small, sample 100 paths × 24 steps, write `site/data/latest.json`, append `history.jsonl`, write `band_check.json` and `status.json` | `forecast/run_forecast.py` |
| Score past forecasts against actual closes | `forecast/band_check.py` |
| Seed history with genuine past forecasts (only data up to each bar) | `forecast/backfill_history.py` |
| Static page (vanilla JS + canvas, no build step) | `site/` |
| Hourly job + Pages deploy | `.github/workflows/forecast.yml` |

If a data check or the model fails, the previous `latest.json` is kept, `status.json`
records the error and the workflow run is marked failed.

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
- In the hourly job: the default is 100 samples (`NQ_SAMPLES` in
  `.github/workflows/forecast.yml`). A manual run (Actions → forecast → Run workflow) lets
  you pick the sample count and force a run while the market is closed.
- Sampling cost is ~1.5–2 s per path on CPU, because Kronos recomputes its full 512-bar
  context at every step; 100 paths take about 3–5 minutes on a GitHub runner.

## Daily 24h volatility forecast (GARCH)

Every US trading day `.github/workflows/garch_vol.yml` (12:05 and 13:05 UTC) runs
`forecast/run_vol.py`, which forecasts the next 24 tradable hours' volatility as of the
08:00 New York bar close with a calibrated GARCH(1,1) (`forecast/garch_vol.py`) and writes
`site/data/vol_latest.json`, `vol_history.jsonl` and `vol_status.json`. Volatility only, not direction.

```bash
python forecast/garch_vol.py --now "2026-11-05 08:10"     # forecast table for a past day (NY time)
```

**Event calendar: update each January.** `forecast/events_2026_2027.yaml` holds the scheduled
FOMC, CPI, payrolls, PCE, GDP (advance) and retail sales dates, copied from the official
calendars cited in the file (Federal Reserve, BLS, BEA, Census). Add the new year's dates
every January (or when an agency revises its calendar); after `covered_until` the page shows
"event calendar not loaded" instead of flagging releases.

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
- Hourly direction on NQ is close to unpredictable for any model. Read the fan's **width**
  (expected range), not the median, as the main signal — and check the band panel to see
  whether that width has been realistic.
- Kronos's published benchmarks come from its authors and are not yet independently
  replicated.
- Backfilled history lines (`"backfill": true`, marked "bf" on the page) were generated
  afterwards with 30 paths; they drop out of the 30-forecast window as live ones arrive.

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
