# CLAUDE.md: NQ forecasts

## Purpose

A free, self-updating page for Nasdaq-100 futures (NQ) at https://ssslithy.github.io/nq-kronos/.
Once per US trading day, at 1pm London, it publishes a **calibrated GARCH volatility forecast for
1pm → 1pm London** (23 trading hours; size of the move, never direction) with five prediction
intervals (90% is the headline), the cash-session range, the US calendar, Treasuries and tech
headlines, a phone alert and an accuracy log. Kronos-small runs from the same bar as an
**experimental** overlay only. Research tool, not investment advice.

## Daily schedule (everything runs once per trading day)

- `.github/workflows/garch_vol.yml`, cron `5 12 * * 1-5` and `5 13 * * 1-5` (UTC). One of the two
  lands in 08:00–09:25 New York in both EST and EDT; `forecast/run_vol.py` decides, the other exits
  "not a forecast window".
- Origin = the **07:00–08:00 NY bar** (closed 08:00 NY). Jobs:
  1. `vol`: `run_vol.py`: GARCH forecast → `site/data/vol_latest.json`, `vol_history.jsonl`,
     `vol_status.json`; fills in earlier forecasts' accuracy; sends the ntfy alert (secret `NTFY_TOPIC`);
     THEN `page_data.py` writes `calendar.json`, `treasuries.json`, `news.json` (fail-soft: a failed
     source writes `"ok": false` and the page hides that block; never fails or delays the forecast);
     commits and pushes those files.
  2. `deploy`: Pages deploy of the GARCH forecast, straight away.
  3. `kronos` (only if a forecast was made): `run_forecast.py --origin <that bar> --horizon 23`, 100 paths, its own
     **15-minute limit**. If it fails or times out, `--mark-stale` flags only Kronos (`latest.json`
     `stale`, `status.json` ok=false); GARCH is never touched. Commits `latest.json`, `history.jsonl`,
     `band_check.json`, `status.json`.
  4. `deploy-kronos`: second Pages deploy (after the GARCH one). All deploys share the
     `pages-deploy` concurrency group with `forecast.yml`.
- Timing: GARCH (alert + deploy) is live within ~5 min of the start; Kronos ~8–10 min after that.
  Even a 20-minute-late start with a Kronos timeout finishes well before 09:30 NY.
- `forecast.yml` has **no schedule**: manual Kronos run from the latest bar, and a deploy on pushes to
  `site/**` (not `site/data/**`). A push that only changes `site/data` does not deploy by itself.
- Accuracy timing: the window ends with the next day's 07:00 NY origin bar, so a forecast's result
  (and its cash-session result, 09:00–16:00 NY) is filled by the next run.

## Key design decisions (don't change without re-running the backfill and saying why)

- **GARCH(1,1), zero mean, Student-t** (`arch`), fitted on the trailing **2,000 hourly log returns**
  (percent) of the roll-adjusted close, refitted every day. **Horizon = 23 bars** (`HORIZON`): the
  origin bar's close (1pm London, 12pm in US/UK clock-change mismatch weeks) to the next trading
  day's origin bar close. The page still says "Next 24 hours"; every time label is built from the
  data, never hard-coded. (Until Nov 2026 it was 24 bars.)
- **Calibration:** raw GARCH σ is too wide, so σ = **k × raw σ**, k = RMS(move / raw σ) over the
  trailing **250 matured, roll-free daily forecasts** (min 60), only forecasts whose 23 bars closed
  before the origin. **No look-ahead anywhere**: backfills must use only data before each origin.
- **Five ranges:** 80% = last × exp(±1.2816 σ); **85 / 90 / 95 / 99% = ±mXX σ**, mXX = the XXth
  percentile of |move / kσ| over the same 250 (measured multipliers ≈1.31 / 1.55 / 1.94 / 3.06).
  **90% is the headline** (card, alert, footer hit rate; `HEADLINE` in garch_vol.py). 99% is rough.
- **Cash session:** σ_cash = window variance × the cash hours' share of the hour-of-day profile
  (trailing 60 days); no k_cash. The page shows the **cash 90% range** = ±cash_m90 σ_cash, centred on
  the 1pm price, cash_m90 = 90th percentile of |cash move / σ_cash| over the trailing 250 finished
  sessions before that date (min 60; `vol_accuracy.assign_k_cash`). `CASH_LEVEL = 80` in
  vol_accuracy.py switches the page back to ±1.28 σ_cash ("Based on the 80% PI"). Measured from the
  9:00am NY bar close (half an hour before the open); labelled 2:30pm–9:00pm London (the real open).
- **Gates (Nov 2026 redesign, NQ_REDESIGN_PLAN.md), all on the 250-forecast backfill, older / newer half:**
  A: 80% 79.2 / 79.2 (needs 75–85), 90% 88.8 / 88.8 (needs 86–94): pass.
  B: cash 90% 92.6 / 86.5 (191 days with a multiplier; needs 86–94): pass, so the cash block shows 90%.
  C: volatility contest (`lab/vol_contest.py`, `lab/results/vol_contest.md`): **GARCH kept**; GARCH+VXN,
  HAR-RV, XGBoost, GARCH+regime all failed (none ≥5% narrower with lower QLIKE in both halves).
  D: regime HMM (`lab/regime.py`, `lab/results/regime.md`): failed stability (median run 3.5 days < 10),
  so **no regime chip** on the page; no direction is shown anywhere.
- **Chart fan:** analytic bands (kσ × hour-of-day profile) for all five levels, so the last-step band
  edges equal the card's ranges exactly; the 100 paths are normal draws (kept in the JSON, not drawn).
- **Kronos is experimental:** its lab 80% band held ~46% (target 80%). Off by default; its P10/P90
  paths are drawn only if `latest.json` is from the same bar as the GARCH forecast and not stale.
- **Events:** `forecast/events_2026_2027.yaml` (FOMC 14:00; CPI, NFP, PCE, GDP advance, retail sales
  08:30 NY). `covered_from` 2026-01-01 / `covered_until` 2027-12-31; outside that the flag says
  "not loaded". **Update every January** from the official calendars cited in the file.
- **Backfill results (3 Nov 2025 – 5 Nov 2026, 250 forecasts, 23h window, older / newer half):**
  80% 79.2 / 79.2, 85% 83.2 / 84.0, 90% 88.8 / 88.8, 95% 93.6 / 95.2, 99% 98.4 / 97.6.
- **Times are London only, 12-hour ("1:00pm")**, on the page and in the alert; New York appears only
  in Details. The disclaimer "Volatility only, not direction. Not investment advice." lives in Details.

## Files

| What | Where |
|---|---|
| Model, calibration, fan, events, CLI table | `forecast/garch_vol.py` |
| Daily run (window check, retries, outputs, alert, cash range) | `forecast/run_vol.py` |
| Calendar / Treasuries / news files (fail-soft) | `forecast/page_data.py` |
| Accuracy log, cash-session backtest, backfill | `forecast/vol_accuracy.py` |
| ntfy alert | `forecast/vol_alert.py` |
| Kronos run, band check | `forecast/run_forecast.py`, `forecast/band_check.py` |
| Data download, cleaning, rolls; trading calendar | `forecast/fetch.py`, `forecast/nq_calendar.py` |
| Page (vanilla JS, no build; design in `design/`) | `site/index.html`, `vol.js` (data + everything but the chart), `app.js` (SVG chart), `style.css` (`body.nq` = main page) |
| Accuracy page | `site/accuracy.html`, `site/accuracy.js`, `site/lab/*.png` |
| Phase 6 lab (research) | `lab/`, report in `lab/results/report.md` |
| Vol contest, regime study (lab only; `lab/requirements-lab.txt`) | `lab/daily_origins.py`, `lab/vol_contest.py`, `lab/regime.py` |

## Commands (from the repo root, `.venv` is Python 3.11)

```bash
./.venv/bin/python forecast/garch_vol.py --now "2026-11-05 08:10"            # table for a past day (NY time)
./.venv/bin/python forecast/run_vol.py --now "2026-11-05 08:10" --force --out /tmp/vol  # full daily job, elsewhere, no alert
./.venv/bin/python forecast/run_vol.py --now "2026-11-05 08:10" --force --out /tmp/vol --fail=news  # simulate a failed extra
./.venv/bin/python forecast/vol_accuracy.py --out /tmp/vol                  # hit-rate report: 80-99% ranges and cash
./.venv/bin/python forecast/vol_accuracy.py --backfill --all --out /tmp/vol_a  # redo the 1-year backfill (scratch first)
./.venv/bin/python lab/regime.py && ./.venv/bin/python lab/vol_contest.py   # research gates C and D
./.venv/bin/python forecast/run_forecast.py --origin 2026-11-06T12:00:00Z --horizon 23   # Kronos (writes site/data!)
./.venv/bin/python forecast/vol_alert.py [--send | --new-topic]             # alert text / test send
./.venv/bin/python -m http.server 8765 -d site                              # preview the page
```

GitHub: Actions → garch-vol → Run workflow (tick **force** to forecast outside the window, which also
alerts; tick **test alert** to check the secret). Actions → forecast → Run workflow for Kronos only.

## Rules for future changes

- Wait for the user's OK after each step; show it working (tables, screenshots) first. Don't push
  without being asked (unless a plan explicitly says to, like NQ_REDESIGN_PLAN.md did).
- Never introduce look-ahead. Any change to the model, calibration or ranges: re-run
  `vol_accuracy.py --backfill --all` into a scratch folder first and report 80/95/99 (and cash) hit
  rates on both halves. If the 80% range leaves 75–85% or the 90% range leaves 86–94% on either
  half, stop and tell the user.
- Keep "Volatility only, not direction. Not investment advice." in the page's Details, the accuracy
  page and the README.
- Don't hand-edit `site/data/*`; it's written by the scripts and the bot. Before committing, `git pull
  --rebase`; if the bot has committed data since, take its data files and regenerate rather than merge.
- New page code goes in its own script or a self-contained block; the page must work at 375 px
  wide with no sideways scrolling, London times only. Check with Playwright at 1280 and 375 px
  (`playwright-cli` is in `~/.local/node-v24.19.0-darwin-arm64/bin`).
- Anything new fetched for the page goes in `page_data.py`: after the alert, fail-soft, free sources only.
- Pin versions (`forecast/requirements*.txt`, action SHAs). Keep everything free (Actions, Pages, ntfy).
- Commit messages end with the Co-Authored-By line used in this repo's history.
