# CLAUDE.md: NQ forecasts

## Purpose

A free, self-updating page for Nasdaq-100 futures (NQ) at https://ssslithy.github.io/nq-kronos/.
Once per US trading day it publishes a **calibrated GARCH 24h volatility forecast** (size of
the move, never direction) with its forecast fan, a phone alert, and an accuracy log. Kronos-small
runs from the same bar as an **experimental** comparison only. Research tool, not investment advice.

## Daily schedule (everything runs once per trading day)

- `.github/workflows/garch_vol.yml`, cron `5 12 * * 1-5` and `5 13 * * 1-5` (UTC). One of the two
  lands in 08:00–09:25 New York in both EST and EDT; `forecast/run_vol.py` decides, the other exits
  "not a forecast window".
- Origin = the **07:00–08:00 NY bar** (closed 08:00 NY). Steps in the one job:
  1. `run_vol.py`: GARCH forecast → `site/data/vol_latest.json`, `vol_history.jsonl`, `vol_status.json`;
     fills in the accuracy of earlier forecasts; ntfy alert (secret `NTFY_TOPIC`).
  2. `run_forecast.py --origin <that bar>`: Kronos-small, 100 paths → `latest.json`, `history.jsonl`,
     `band_check.json`, `status.json`. `continue-on-error`: never blocks the GARCH commit.
  3. One data commit, one Pages deploy (concurrency group `pages-deploy`, shared with `forecast.yml`).
- `forecast.yml` has **no schedule**: manual Kronos run from the latest bar, and a deploy on pushes to
  `site/**` (not `site/data/**`). A push that only changes `site/data` does not deploy by itself.
- Accuracy timing: the run only has bars up to the 07:00 NY bar, so a forecast's 24h result is filled
  two runs later and its cash-session result (09:00–16:00 NY) on the next run.

## Key design decisions (don't change without re-running the backfill and saying why)

- **GARCH(1,1), zero mean, Student-t** (`arch`), fitted on the trailing **2,000 hourly log returns**
  (percent) of the roll-adjusted close, refitted every day. Horizon = next 24 tradable hours.
- **Calibration:** raw GARCH σ is too wide, so σ = **k × raw σ**, k = RMS(move / raw σ) over the
  trailing **250 matured, roll-free daily forecasts** (min 60), only forecasts whose 24 bars closed
  before the origin. **No look-ahead anywhere**: backfills must use only data before each origin.
- **Ranges:** 80% = last × exp(±1.2816 σ); 68% = ±1σ; **95% = ±m95 σ**, m95 = 95th percentile of
  |move / kσ| over the same 250 (≈2.05); **99% stress = ±m99 σ** (≈3.1), shown as text only and
  labelled "rough: few observations". The q80 alternative was tested and rejected (step 1).
- **Cash session σ:** 24h variance × the cash hours' share of the hour-of-day variance profile
  (trailing 60 days). Kept **raw** (no k_cash): backfill held 82.4% / 83.3% (older / newer half).
  `k_cash` is still computed per row (rolling, no look-ahead) for monitoring.
- **Chart fan:** analytic bands (kσ × hour-of-day profile), so +24h bands equal the card's 80%/95%
  ranges exactly; the 100 paths are normal draws for illustration.
- **Kronos is experimental:** its lab 80% band held ~46% (target 80%). Off by default on the chart.
- **Events:** `forecast/events_2026_2027.yaml` (FOMC 14:00; CPI, NFP, PCE, GDP advance, retail sales
  08:30 NY). `covered_from` 2026-01-01 / `covered_until` 2027-12-31; outside that the flag says
  "not loaded". **Update every January** from the official calendars cited in the file.
- **Backfill results (3 Nov 2025 – 5 Nov 2026, 250 forecasts):** 80% range 81.6% / 80.0%,
  95% range 95.2% / 96.8%, 99% stress 99.2% / 97.6% (older / newer half).
- Times on the page are shown in both New York and London; the alert and event times are New York.

## Files

| What | Where |
|---|---|
| Model, calibration, fan, events, CLI table | `forecast/garch_vol.py` |
| Daily run (window check, retries, outputs, alert) | `forecast/run_vol.py` |
| Accuracy log, cash-session backtest, backfill | `forecast/vol_accuracy.py` |
| ntfy alert | `forecast/vol_alert.py` |
| Kronos run, band check | `forecast/run_forecast.py`, `forecast/band_check.py` |
| Data download, cleaning, rolls; trading calendar | `forecast/fetch.py`, `forecast/nq_calendar.py` |
| Page (vanilla JS, no build) | `site/index.html`, `app.js` (chart), `vol.js` (card), `style.css` |
| Accuracy page | `site/accuracy.html`, `site/accuracy.js`, `site/lab/*.png` |
| Phase 6 lab (research) | `lab/`, report in `lab/results/report.md` |

## Commands (from the repo root, `.venv` is Python 3.11)

```bash
./.venv/bin/python forecast/garch_vol.py --now "2026-11-05 08:10"            # table for a past day (NY time)
./.venv/bin/python forecast/run_vol.py --now "2026-11-05 08:10" --out /tmp/vol  # full daily job, elsewhere, no alert
./.venv/bin/python forecast/vol_accuracy.py --out /tmp/vol                  # 24h and cash hit-rate report
./.venv/bin/python forecast/vol_accuracy.py --backfill [--all]              # redo the 1-year backfill
./.venv/bin/python forecast/run_forecast.py --origin 2026-11-06T12:00:00Z   # Kronos from a given bar
./.venv/bin/python forecast/vol_alert.py [--send | --new-topic]             # alert text / test send
./.venv/bin/python -m http.server 8765 -d site                              # preview the page
```

GitHub: Actions → garch-vol → Run workflow (tick **force** to forecast outside the window, which also
alerts; tick **test alert** to check the secret). Actions → forecast → Run workflow for Kronos only.

## Rules for future changes

- Wait for the user's OK after each step; show it working (tables, screenshots) first. Don't push
  without being asked.
- Never introduce look-ahead. Any change to the model, calibration or ranges: re-run
  `vol_accuracy.py --backfill --all` into a scratch folder first and report 80/95/99 (and cash) hit
  rates on both halves. If the 80% range leaves ~75–85% on either half, stop and tell the user.
- Keep "volatility only, not direction" and "not investment advice" on the page, card and README.
- Don't hand-edit `site/data/*`; it's written by the scripts and the bot. Before committing, `git pull
  --rebase`; if the bot has committed data since, take its data files and regenerate rather than merge.
- New page code goes in its own script or a self-contained block; the page must work at 375 px
  wide with no sideways scrolling, in both New York and London times.
- Pin versions (`forecast/requirements*.txt`, action SHAs). Keep everything free (Actions, Pages, ntfy).
- Commit messages end with the Co-Authored-By line used in this repo's history.
