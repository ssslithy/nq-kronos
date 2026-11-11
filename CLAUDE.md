# CLAUDE.md: NQ forecasts

## Purpose

A free, self-updating page for Nasdaq-100 futures (NQ) at https://ssslithy.github.io/nq-kronos/.
Once per US trading day, at 1pm London, it publishes a **calibrated HAR-RV volatility forecast for
1pm → 1pm London** (23 trading hours; size of the move, never direction) with five prediction
intervals (90% is the headline), the cash-session range, the US calendar, Treasuries, Polymarket
Fed-decision odds and tech headlines, a phone alert and an accuracy log. Kronos-small runs from the same bar as an
**experimental** overlay only. Research tool, not investment advice.

## Daily schedule (everything runs once per trading day)

- **Triggers.** GitHub's scheduler drops or delays most scheduled runs in this repo (Nov 2026: the vol
  job's schedule never fired on 9 or 10 Nov; the old hourly Kronos cron ran 3 times in ~36 h; the
  driver check ran 6 h late). So the 1pm run has three lines:
  1. **`.github/workflows/vol_timer.yml` (primary, `forecast/vol_timer.py`):** scheduled through the night
     (`7 0-6 * * 1-5` UTC); whichever slot GitHub fires first (hours late is fine) waits on the runner
     until 08:07 New York (DST-aware), dispatches garch-vol (dispatches from a workflow start
     immediately), and again at 08:22 if today's forecast isn't published. Too far ahead (> 5h20m) it
     sleeps 5h20m and dispatches the next timer run; > 13 h ahead it leaves it to a later slot; extra
     slots exit at once. (An external cron-job.org timer was tried on 10 Nov: its test calls never
     created a run, so it isn't used.)
  2. `.github/workflows/garch_vol.yml`'s own cron (`7,27,47 12`, `7,27,47 13`, `7 14` UTC, weekdays).
  3. `.github/workflows/vol_watchdog.yml` (`forecast/vol_watchdog.py`, standard library only), cron
     `45 13` / `45 14` UTC: acts whenever it runs after 09:30 New York on a trading day. If
     `vol_status.json`'s origin isn't today's, an ntfy "forecast late" note and a `force=true` dispatch
     (skipped if a garch-vol run is already queued or running).
  `forecast/run_vol.py` decides: before 08:00 NY, weekends, holidays and "already made" exit quietly;
  08:00–09:25 forecasts on time; **any later run that day still forecasts once (late)**, using data only
  up to the origin bar, with the alert titled "NQ daily volatility (late)".
- `.github/workflows/driver_check.yml` (`lab/driver_check.py`, lab only), cron `30 21 * * 1-5` and
  `30 22 * * 1-5` (acts once per trading day: after 17:00 NY for that day, or before 08:00 NY for the
  previous one, so late starts still count): the drivers named in the day's closing market
  wraps vs the 1pm snapshot (news items, brief, Since-US-close cells) → `lab/results/driver_check.jsonl`,
  and `driver_check.md` (share caught, most common misses) once there are 10 days. Never changes a rule
  and never touches the forecast, the alert or site/.
- Origin = the **07:00–08:00 NY bar** (closed 08:00 NY). Jobs:
  1. `vol`: `run_vol.py`: vol forecast → `site/data/vol_latest.json`, `vol_history.jsonl`,
     `vol_status.json`; fills in earlier forecasts' accuracy; sends the ntfy alert (secret `NTFY_TOPIC`);
     THEN `page_data.py` writes `calendar.json`, `treasuries.json`, `fed_odds.json`, `news.json` (fail-soft: a failed
     source writes `"ok": false` and the page hides that block; never fails or delays the forecast);
     commits and pushes those files.
  2. `deploy`: Pages deploy of the vol forecast, straight away.
  3. `kronos` (only if a forecast was made): `run_forecast.py --origin <that bar> --horizon 23`, 100 paths, its own
     **15-minute limit**. If it fails or times out, `--mark-stale` flags only Kronos (`latest.json`
     `stale`, `status.json` ok=false); the vol forecast is never touched. Commits `latest.json`, `history.jsonl`,
     `band_check.json`, `status.json`.
  4. `deploy-kronos`: second Pages deploy (after the vol one). All deploys share the
     `pages-deploy` concurrency group with `forecast.yml`.
- Timing: the vol forecast (alert + deploy) is live within ~5 min of the start; Kronos ~8–10 min after that.
  Even a 20-minute-late start with a Kronos timeout finishes well before 09:30 NY.
- `forecast.yml` has **no schedule**: manual Kronos run from the latest bar, and a deploy on pushes to
  `site/**` (not `site/data/**`). A push that only changes `site/data` does not deploy by itself.
- Accuracy timing: the window ends with the next day's 07:00 NY origin bar, so a forecast's result
  (and its cash-session result, 09:00–16:00 NY) is filled by the next run.

## Key design decisions (don't change without re-running the backfill and saying why)

- **Model: HAR-RV since 9 Nov 2026** (`VOL_MODEL = "har"` in garch_vol.py; `"garch"` switches back).
  log RV(next 23 bars) = b0 + b1 log RV1 + b5 log RV5 + b22 log RV22, RV1 = sum of the 23 squared hourly
  % returns ending at the origin, RV5 / RV22 = mean RV1 over the last 5 / 22 daily origins; OLS on the
  trailing **250 origins matured by the origin** (min 100), refitted every day; raw σ = sqrt(exp(fit)).
  Why (NQ_VOL_FIX_PLAN.md, `lab/results/vol_fix.md`): hourly GARCH (persistence ~0.86 per hour) forgot a
  shock within a day, so misses clustered in the Oct–Nov 2026 sell-off (rated "Calm" throughout). On all
  304 scorable origins (19 Aug 2025 – 5 Nov 2026) HAR-RV vs GARCH: 90% 89.5 / 89.5 vs 90.8 / 90.1 (halves);
  stress-origin 90% coverage **92.6% vs 86.1%**; miss-after-miss 38% vs 52% (Christoffersen p 0.12);
  calm-day 90% width **−12.8%**; QLIKE 0.331 vs 0.441 (lower in both halves). The only one of 5
  candidates (daily floor 5d / 10d, GARCH+VXN, HAR-RV, two-speed GARCH) to pass all five gates.
  The GARCH(1,1)-t on the trailing 2,000 hourly returns is still fitted daily as a diagnostic (CLI table,
  `model.garch_*` in vol_latest.json). **Horizon = 23 bars** (`HORIZON`): the
  origin bar's close (1pm London, 12pm in US/UK clock-change mismatch weeks) to the next trading
  day's origin bar close. The page still says "Next 24 hours"; every time label is built from the
  data, never hard-coded. (Until Nov 2026 it was 24 bars.)
- **Calibration:** σ = **k × raw σ** (k ≈ 1.18 for HAR-RV; it was ≈ 0.66 for GARCH, whose raw σ was too wide), k = RMS(move / raw σ) over the
  trailing **250 matured, roll-free daily forecasts** (min 60), only forecasts whose 23 bars closed
  before the origin. **No look-ahead anywhere**: backfills must use only data before each origin.
- **Five ranges:** 80% = last × exp(±1.2816 σ); **85 / 90 / 95 / 99% = ±mXX σ**, mXX = the XXth
  percentile of |move / kσ| over the same 250 (measured multipliers ≈1.36 / 1.59 / 2.16 / 2.73).
  **90% is the headline** (card, alert, footer hit rate; `HEADLINE` in garch_vol.py). 99% is rough.
- **Cash session:** σ_cash = window variance × the cash hours' share of the hour-of-day profile
  (trailing 60 days); no k_cash. The page shows the **cash 90% range** = ±cash_m90 σ_cash, centred on
  the 1pm price, cash_m90 = 90th percentile of |cash move / σ_cash| over the trailing 250 finished
  sessions before that date (min 60; `vol_accuracy.assign_k_cash`). `CASH_LEVEL = 80` in
  vol_accuracy.py switches the page back to ±1.28 σ_cash ("Based on the 80% PI"). Measured from the
  9:00am NY bar close (half an hour before the open); labelled 2:30pm–9:00pm London (the real open).
- **Gates, all on the 250-forecast backfill, older / newer half.** With HAR-RV (Nov 2026 vol fix):
  A: 80% 80.0 / 79.2 (needs 75–85), 90% 90.4 / 88.0 (needs 86–94): pass.
  B: cash 90% 87.4 / 91.7 (191 days with a multiplier; needs 86–94): pass, so the cash block shows 90%.
  (With GARCH, Nov 2026 redesign: A 79.2 / 79.2 and 88.8 / 88.8; B 92.6 / 86.5.)
  C: volatility contest (`lab/vol_contest.py`, `lab/results/vol_contest.md`, redesign): GARCH kept then,
  because no model was ≥5% narrower with lower QLIKE in both halves. The vol-fix gate (stress coverage,
  clustering, calm width, QLIKE; `lab/vol_contest.py --fix`) then picked HAR-RV.
  D: regime HMM (`lab/regime.py`, `lab/results/regime.md`): failed stability (median run 3.5 days < 10),
  so **no regime chip** on the page; no direction is shown anywhere.
- **Chart fan:** analytic bands (kσ × hour-of-day profile) for all five levels, so the last-step band
  edges equal the card's ranges exactly; the 100 paths are normal draws (kept in the JSON, not drawn).
- **Kronos is experimental:** its lab 80% band held ~46% (target 80%). Off by default; its P10/P90
  paths are drawn only if `latest.json` is from the same bar as the vol forecast and not stale.
  Kronos is its own job (~9 min) and lands ~10 min after the forecast with its own Pages deploy
  (`deploy-kronos`, shared `pages-deploy` group). Until then the checkbox reads "Kronos (updating…)"
  and the page re-checks `latest.json` every minute; "not run today" only 30 min after the forecast
  was published (`generated_utc`).
- **Events:** `forecast/events_2026_2027.yaml` (FOMC 14:00; CPI, NFP, PCE, GDP advance, retail sales
  08:30 NY). `covered_from` 2026-01-01 / `covered_until` 2027-12-31; outside that the flag says
  "not loaded". **Update every January** from the official calendars cited in the file.
- **Backfill results, HAR-RV (3 Nov 2025 – 5 Nov 2026, 250 forecasts, 23h window, older / newer half):**
  80% 80.0 / 79.2, 85% 83.2 / 83.2, 90% 90.4 / 88.0, 95% 93.6 / 96.0, 99% 98.4 / 99.2.
- **Chart controls:** Last Price · Kronos (experimental) · 99 / 95 / 90 / 85 / 80% band buttons.
  Selected bands' upper and lower prices are always labelled at the right edge (de-overlapped).
  The chart always shows the full 1pm → 1pm window; the cash-session box, the "Cash" view and the
  "Levels" checkboxes were removed (NQ_CHART_CASH_REMOVAL.md). The cash session stays in the top
  card and in vol_latest.json.
- **Fed odds (`fed_odds.json`):** Polymarket crowd betting odds for the next FOMC decision after the
  origin (date from the events YAML). Gamma `/public-search` finds "Fed decision in <Month>?" (checked
  against the market questions' "<Month> <year>" or the end date, never a hard-coded slug), Gamma
  `/events/{id}` gives the outcome markets, CLOB `/prices-history` (fidelity 60) the hourly "Yes"
  prices; Hike / Cut = sum of all increase / decrease markets, Hold = no change. Use the default
  `requests` User-Agent (Cloudflare returns 403 to urllib's). Sparklines have a 10-point minimum scale.
  Window and timestamps = the Treasuries' (`market_window`, see below). Prices are bid-ask
  midpoints: CLOB `/midpoint` at the window end when the job runs within 30 min of it (a later re-run
  uses the history, never prices after the origin), the price history (Polymarket's displayed price =
  midpoint, last trade only if the spread > $0.10) before it. Buckets summed into Hike / Hold / Cut and
  normalised to 1 at every point (they sum to ~101-102%); shown % rounded (largest remainder) to total
  exactly 100; "pts" = shown end minus shown start (sums to 0).
- **Since US close (`since_close.json`, top of the right column):** oil (`CL=F`, %), VIX (`^VIX`, points;
  Yahoo's hourly ^VIX has Cboe's extended hours from 3:15am New York, checked 9 Nov 2026, so no VIX future
  is needed) and the dollar (`DX-Y.NYB`, %; `DX=F` is empty on Yahoo) from the previous 4:00pm New York
  close (the last bar ending by then; Friday's on Mondays) to the origin bar close. Label e.g. "Since US
  close · Thu 9pm – Fri 1pm" from the real times. A fall is teal and a rise red for all three (good / bad
  for NQ); a missing value shows "–", and the line hides if all three are missing. Context only: the
  overnight move had no skill for the cash session (lab/results/direction.md, M3).
- **Treasuries (`treasuries.json`) and the shared window (`market_window` in page_data.py):** window =
  the past 24 hours (previous origin bar close → this one, label "Past 24h"), or, if the ZT=F hourly bars
  have a gap > 3 h in between (weekend, holiday), only the trading since the reopen: from the first bar's
  open (Sunday 6pm New York = 11pm London normally, read from the data so clock-change weeks and
  holiday reopens are right) to the 1pm London origin close, label e.g. "Past 14h · Sun 11pm – Mon 1pm".
  Friday afternoon after 1pm is not shown on Mondays. Both the Treasuries and the Fed odds use the
  window's exact hourly timestamps; one shared label sits above "US Treasuries" (both headings plain).
  Fallback if ZT=F fails: the NQ trading hours. Yields, change in **bp** (up = red, down = teal):
  10Y = `^TNX` hourly if it covers ≥ 80% of the window (it's a cash-hours index, so usually not
  overnight), else `10Y=F` (CBOT 10-Year Yield futures, hourly around the clock), else ZN=F converted
  (duration 6.5, approximate). 2Y = `2YY=F` only if it has clean hourly data for 60 trading days (≥ 18
  bars a day on 95% of them; on 9 Nov 2026 it had ~5 a day and stopped 13 Oct, so no); else derived from
  ZT=F: the latest FRED DGS2 close before the origin's day, moved by −(ZT price % change since ~3pm NY
  that day) ÷ `ZT_DURATION` = 1.9, marked approximate (* in the page, explained in Details). The page
  still renders the old futures-price format (`move_pct`) of files written before 10 Nov 2026.
- **News (`news.json`):** headlines published from the previous cash-session close (4pm New York on the
  previous trading day) to the origin bar close (1pm London), **never after it**, however late the job
  runs (on 9 Nov the forced 4pm run showed 3:59pm headlines; fixed). 23 free RSS feeds: Yahoo per-ticker
  (^NDX + 15 largest names), CNBC Technology / Markets (Market Insider, id 20409666) / Earnings,
  MarketWatch Top Stories / Breaking, Nasdaq.com Markets / Technology. Not used: Yahoo `NQ=F` (stale
  since Oct 2026), MarketWatch MarketPulse (stopped 2025). Nasdaq.com's firewall drops requests whose
  User-Agent contains a URL, so it gets its own UA (`SLOW_FEEDS`: 30 s timeout, one retry).
  Everything else lives in `forecast/news_rules.yaml` (tune there, no code change):
  relevance = a Nasdaq-100 company, a macro term, or a sector term with a market word, all in the
  **headline**; dropped: live blogs, video, opinion, any "?" headline, listicles and promo shapes ("X vs. Y",
  "This $N Billion Warning", "Is X a Buy", "N stocks to"), and syndicated low-quality publishers
  (Motley Fool, Zacks, Simply Wall St, Insider Monkey, GuruFocus, InvestorPlace, 24/7 Wall St, TIKR,
  Trefis, MarketBeat, Stocktwits, press-release wires; Benzinga only for listicles) by link domain or,
  for Yahoo-hosted items, by the publisher named on the article page (fetched in parallel; the list
  then shows that publisher, e.g. "Barron's", "MT Newswires"). Bars: **3** = Fed / macro data / tariffs /
  export controls, or results / guidance with a Nasdaq-100 company as the subject (named in the first
  25 characters), or a major event at a top-8 subject, or a market shock about a top-8 name or the
  Nasdaq; **2** = hard news only: a named action (results, guidance, probe, lawsuit / ruling, deal,
  launch, outage, upgrade / downgrade, layoffs, CEO / CFO exit, buyback, production halt, focus-list
  change) with a Nasdaq-100 subject or a sector term; **1** = commentary, interviews, features, board
  changes below C-level, earnings-date notices, previews, week-ahead calendars. **Market wraps**
  (`market_wrap`: pre-bell / futures / open / close summaries) and **movers roundups** (`movers_roundup`)
  are not listed and not in the split (counted in `stats.wraps`). **Morning brief:** the latest
  `market_wrap` published 6:00am–1:00pm London on the origin's day is pinned first (`brief` in news.json:
  a raised row with a grey BRIEF tag, no bars, not in the split). **Macro shocks** (`macro_shock`: oil /
  OPEC / Middle East / Iran, tariffs / China trade / export controls) widen relevance: 3 bars with a
  `macro_shock_us` term (US inflation, the Fed, a market-wide shift), else 2. Macro terms about another
  economy only (`foreign_macro`, e.g. "China wholesale inflation") don't count. Rating changes need a
  Nasdaq-100 subject; there is no strategist / analyst-note category. `split` in news.json logs the
  3 / 2 / 1 mix of all non-wrap stories and of the listed non-wrap ones (target ~20 / 40 / 40 on a typical
  day; measured 9 Nov, a weekend window: 5 / 15 / 80 of 20 stories; over 6 days of feed items:
  7 / 19 / 74 of 70; most 1s are wraps, roundups and commentary).
  Clustering: headlines within **24 h** naming a common Nasdaq-100 company with a shared `event_words`
  group, or the same main company and a shared proper name (e.g. "Huang"; not in Title Case headlines),
  or near-identical titles, are one story (the 9 Nov Nvidia probe: 20 headlines, one row), shown with
  its most significant headline (ties: `source_rank`, then the earliest) at the earliest time.
  Up to 12 stories: most significant first, then newest, at most 6 per outlet; listed newest first,
  3 visible, the rest scroll. Yahoo per-ticker feeds hold only ~20 items, so a re-run for a past time
  finds mostly CNBC.
- **Times are London only, 12-hour ("1:00pm")**, on the page and in the alert; New York appears only
  in Details. The disclaimer "Volatility only, not direction. Not investment advice." lives in Details.

## Files

| What | Where |
|---|---|
| Model, calibration, fan, events, CLI table | `forecast/garch_vol.py` |
| Daily run (window check, retries, outputs, alert, cash range) | `forecast/run_vol.py` |
| Calendar / Treasuries / Fed odds / news files (fail-soft) | `forecast/page_data.py`; news filter and significance in `forecast/news_rules.yaml` |
| Late-forecast watchdog | `forecast/vol_watchdog.py`, `.github/workflows/vol_watchdog.yml` |
| Evening driver check (lab) | `lab/driver_check.py`, `.github/workflows/driver_check.yml` → `lab/results/driver_check.*` |
| Accuracy log, cash-session backtest, backfill | `forecast/vol_accuracy.py` |
| ntfy alert | `forecast/vol_alert.py` |
| Kronos run, band check | `forecast/run_forecast.py`, `forecast/band_check.py` |
| Data download, cleaning, rolls; trading calendar | `forecast/fetch.py`, `forecast/nq_calendar.py` |
| Page (vanilla JS, no build; design in `design/`) | `site/index.html`, `vol.js` (data + everything but the chart), `app.js` (SVG chart), `style.css` (`body.nq` = main page) |
| Accuracy page | `site/accuracy.html`, `site/accuracy.js`, `site/lab/*.png` |
| Phase 6 lab (research) | `lab/`, report in `lab/results/report.md` |
| Vol contest, vol fix, regime study (lab only; `lab/requirements-lab.txt`) | `lab/daily_origins.py`, `lab/vol_contest.py` (`--fix`), `lab/regime.py` |

## Commands (from the repo root, `.venv` is Python 3.11)

```bash
./.venv/bin/python forecast/garch_vol.py --now "2026-11-05 08:10"            # table for a past day (NY time)
./.venv/bin/python forecast/run_vol.py --now "2026-11-05 08:10" --force --out /tmp/vol  # full daily job, elsewhere, no alert
./.venv/bin/python forecast/run_vol.py --now "2026-11-05 08:10" --force --out /tmp/vol --fail=news  # simulate a failed extra
./.venv/bin/python forecast/vol_accuracy.py --out /tmp/vol                  # hit-rate report: 80-99% ranges and cash
./.venv/bin/python forecast/vol_accuracy.py --backfill --all --out /tmp/vol_a  # redo the 1-year backfill (scratch first)
./.venv/bin/python lab/regime.py && ./.venv/bin/python lab/vol_contest.py   # research gates C and D
./.venv/bin/python lab/vol_contest.py --fix                                 # vol-fix scoring (lab/results/vol_fix.md)
./.venv/bin/python forecast/run_forecast.py --origin 2026-11-06T12:00:00Z --horizon 23   # Kronos (writes site/data!)
./.venv/bin/python forecast/vol_alert.py [--send | --new-topic]             # alert text / test send
./.venv/bin/python -m http.server 8765 -d site                              # preview the page
```

GitHub: Actions → garch-vol → Run workflow (tick **force** to forecast outside the window, which also
alerts; tick **test alert** to check the secret). Actions → forecast → Run workflow for Kronos only.

## Incidents

- **9 Nov 2026, no forecast at 1pm:** GitHub never started the 13:05 UTC garch-vol run (no run exists;
  its only in-window slot in EST). Fixed: 7 cron slots (4 in-window in EST and EDT), the vol-watchdog
  workflow, and a forced re-run that published at 15:59 UTC with the alert.
- **10 Nov 2026, again no 1pm run:** none of the 5 morning slots started (GitHub's scheduler, not a code
  error; the evening driver check ran 6 h late too). A manual dispatch published at 13:46 UTC with the
  alert; the first Pages deploy failed ("try again later"), the Kronos deploy published at 13:56. Fixed:
  an external timer (cron-job.org) as the primary trigger, late runs still forecast once, the watchdog
  acts whenever it runs after 09:30 NY. Then (11 Nov) the vol-timer workflow replaced the external
  timer, whose test calls never reached GitHub.

## Rules for future changes

- Wait for the user's OK after each step; show it working (tables, screenshots) first. Don't push
  without being asked (unless a plan explicitly says to, like NQ_REDESIGN_PLAN.md and NQ_VOL_FIX_PLAN.md did).
- Never introduce look-ahead. Any change to the model, calibration or ranges: re-run
  `vol_accuracy.py --backfill --all` into a scratch folder first and report 80/95/99 (and cash) hit
  rates on both halves. If the 80% range leaves 75–85% or the 90% range leaves 86–94% on either
  half, stop and tell the user.
- Keep "Volatility only, not direction. Not investment advice." in the page's Details, the accuracy
  page and the README.
- Don't hand-edit `site/data/*`; it's written by the scripts and the bot. Before committing, `git pull
  --rebase`; if the bot has committed data since, take its data files and regenerate rather than merge.
- Page CSS/JS links carry a version tag (`style.css?v=20261109j`, same in index.html and accuracy.html):
  bump it whenever site code changes, or browsers keep the old file for Pages' 10-minute cache.
- New page code goes in its own script or a self-contained block; the page must work at 375 px
  wide with no sideways scrolling, London times only. Check with Playwright at 1280 and 375 px
  (`playwright-cli` is in `~/.local/node-v24.19.0-darwin-arm64/bin`).
- Anything new fetched for the page goes in `page_data.py`: after the alert, fail-soft, free sources only.
- Pin versions (`forecast/requirements*.txt`, action SHAs). Keep everything free (Actions, Pages, ntfy).
- Commit messages end with the Co-Authored-By line used in this repo's history.
