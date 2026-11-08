# NQ GARCH 24h Volatility Forecast: Build Plan (add-on to nq-kronos)

Every US trading day, about an hour before the Nasdaq cash open (09:30 New York), forecast how much NQ futures are likely to move over the next 24 trading hours, using GARCH. GARCH was the best-calibrated model in the Phase 6 lab (+24h P10–P90 held the actual close 84% of the time, out of sample). The forecast is published on the existing site, and optionally pushed to your phone.

Give this file to Claude Code inside `~/nq-kronos` and say:
> Read GARCH_VOL_PLAN.md and build it step by step. Stop after each step, show me it working, and wait for my OK.

---

## 1. What it produces (once per US trading day)

Forecast **as of the 08:00 New York hourly bar close** (the last complete bar before 08:30 NY), for the next 24 tradable NQ hours:

| Output | Meaning |
|---|---|
| Expected 24h move (σ) | one standard deviation of the 24h return, in % and NQ points |
| 80% range | P10–P90 price range for +24h from simulated paths |
| 68% range | ±1σ in points around the last close |
| Expected range for the cash session | σ over the 7 hours 09:00–16:00 NY |
| Regime | today's σ as a percentile of the last 252 days' 08:00 forecasts: Calm (<25th), Normal, Elevated (>75th), High (>90th) |
| vs realised | today's forecast σ vs the last 5 and 20 trading days' realised 24h moves |
| Event flag | "US data release today at 08:30 / 10:00 NY" or "FOMC today" where known (see step 4) |
| Model details | GARCH(1,1), Student-t, fitted on the last 2,000 hourly returns; ω, α, β, ν and persistence (α+β) |

Not a direction call. Volatility only.

## 2. Model (reuse the lab)

- Reuse the code in `lab/baselines.py` that produced the validated GARCH bands: same data cleaning and roll adjustment (`forecast/fetch.py`), GARCH(1,1) with Student-t errors and zero mean, fitted on the trailing **2,000 hourly log returns** (percent units), using the `arch` library.
- Forecast 24 steps ahead; simulate **10,000 paths** (cheap) for quantiles; also report the analytic σ from summed variance forecasts. They should agree within a few %.
- **Exclude** the overnight weekend gap return and the daily-break gap from fitting if the lab did; match the lab's choices exactly so the validated calibration carries over. Document it.
- Roll windows: if the 24h horizon crosses a roll date, still forecast but show a "roll this week" note.
- Put the model in `forecast/garch_vol.py` with a pure function `forecast_vol(bars) -> dict` so the lab and the site share it.

## 3. Schedule (GitHub Actions)

GitHub cron runs in UTC only and can start 5–20+ minutes late, so:

1. New workflow `.github/workflows/garch_vol.yml` with **two** cron triggers, `5 12 * * 1-5` and `5 13 * * 1-5` (12:05 and 13:05 UTC), plus `workflow_dispatch`.
2. The script decides whether to run:
   - It's a NQ trading day (use `nq_calendar`; skip US holidays).
   - New York local time is between **08:00 and 09:25**.
   - The 07:00–08:00 NY bar is closed and present in the data (retry up to 3 times, 2 min apart, if Yahoo is slow).
   - Today's forecast hasn't already been made (idempotent; the second trigger exits quietly).
   - Otherwise exit 0 without changes.
   This handles US/UK daylight-saving mismatches automatically and still lands before 08:30 NY when GitHub's scheduler is late.
3. On success: write `site/data/vol_latest.json`, append to `site/data/vol_history.jsonl`, commit, deploy Pages (same pattern as `forecast.yml`).
4. On failure: write the error to `site/data/vol_status.json`, keep yesterday's forecast visible, mark it "stale", and fail the run (GitHub emails you).

## 4. Event flag (simple, free)

- Hard-code a small YAML file `forecast/events_2026_2027.yaml` with the scheduled dates of: FOMC decisions, CPI, PCE, payrolls (NFP), GDP advance, and retail sales, from official release calendars (BLS, BEA, Census, Federal Reserve). Claude Code should fetch them from the official sites, cite each source URL in the file, and show me the list to check.
- If today has one, show the event name and NY time on the page and in the alert. GARCH doesn't know about events, so the note says: "Scheduled release today: realised move often exceeds the GARCH range."
- A README note says the YAML needs updating each January.

## 5. Page

Add a **"24h volatility (GARCH)"** card above the Kronos chart on the existing site:
- Big number: expected 24h move ±X pts (±Y%), with the 80% range underneath.
- Regime badge (Calm / Normal / Elevated / High) and the event flag if any.
- Small sparkline of the last 60 trading days' forecast σ, with realised 24h moves as dots.
- "Forecast at 08:00 New York (13:00 London) · next forecast <date time>", with times computed and shown in both zones.
- Calibration line, once ≥20 matured: "Last 60 forecasts: actual move inside 80% range N% of the time."

## 6. Accuracy log

- Each day, when the +24h bar of an earlier forecast closes, record the actual 24h move and whether it was inside the 80% range (reuse the band-check logic).
- **Backfill 1 year** of daily 08:00 NY forecasts on first run (GARCH is fast: refit per day), so the regime percentile and calibration line work from day one. Fit only on data before each forecast time.

## 7. Phone alert (optional)

- Use **ntfy.sh** (free, no account): install the ntfy app on your phone and subscribe to a hard-to-guess topic name, stored as a GitHub Actions secret `NTFY_TOPIC` (Claude Code generates a random one and tells me what to subscribe to).
- After a successful forecast, POST one line, e.g.:
  `NQ 24h vol: ±312 pts (±1.15%) · 80%: 26,820–27,560 · Elevated (78th pct) · CPI 08:30 NY`
- If the secret isn't set, skip silently.

## 8. Done when

- `python forecast/garch_vol.py --now "<a past weekday 08:10 NY>"` prints the forecast table for that day using only data up to then.
- The 1-year backfill reports an 80%-range hit rate within ~75–85%; if not, stop and tell me.
- A manual workflow run on a closed day exits cleanly with "not a forecast window".
- The page shows the card on desktop and phone.
- (Optional) a test ntfy message arrives.

## 9. Limits (say so on the page/README)
- Yahoo data is delayed and unofficial.
- GARCH assumes tomorrow looks like the recent past; it reacts to shocks after they happen and ignores scheduled events (hence the flag).
- Volatility only, not direction. Not investment advice.

## Cost
£0: GitHub Actions, Pages and ntfy are free.
