# NQ forecasts: move the daily origin to 9:00am New York

Run in one go in `~/nq-kronos`. `CLAUDE.md` rules apply: no look-ahead, free sources only,
never hand-edit `site/data/*`, 375 px layout.

**Don't run this between 8:00am and 10:00am New York on a weekday.** Evenings or weekends
are fine.

## Why

At the current 8:00am New York origin, the 8:30am releases (CPI, payrolls, PCE, GDP, retail
sales) fall inside the forecast window. At 9:00am they're already in the price. The cash
range then also starts from the real pre-open price: the cash window begins at the 9:00 bar,
so there's no more "centred on the 1pm price" caveat.

## 1. Re-backtest first (the gate)

Score both origins on the same days: **A = 08:00 NY (current)** and **B = 09:00 NY (new)**.
Use HAR-RV, the same calibration (rolling k, measured multipliers, trailing 250), all
scored origins, and the last 250.

- **New origin bar:** 08:00–09:00 NY, closing at 09:00.
- **Horizon:** 23 tradable hours, so 9:00am → 9:00am NY next trading day.
- **Cash window:** the 09:00–16:00 NY bars, starting **at** the origin.

**Gate B** (switch only if all hold):
- 24h: 80% range in 75–85%, 90% in 86–94%, on both halves;
- cash: 90% range in 86–94% on both halves;
- no worse than A on QLIKE;
- **report release days separately** (A vs B hit rate and width on CPI/NFP/PCE/GDP/retail
  days). The expectation is that B is narrower or more accurate on those days. Report it
  either way.

**If Gate B fails:** keep 08:00, change nothing else, and write up the results in
`lab/results/origin_9am.md`. Stop there.

## 2. If Gate B passes, move everything tied to the origin

- **Origin logic:** `run_vol.py` and `run_forecast.py` (Kronos), and the
  "already made / outside window" checks. Accept window: 09:00–10:25 NY.
- **Labels:** built from data, **anchored to New York time**. London shows 2:00pm normally
  and 1:00pm in the clock-change mismatch weeks.
  - "Next 24 hours, Mon 2:00pm – Tue 2:00pm"
  - "Next Update Tue 2:00pm"
  - the chart header and axis
- **Header price:** the 9:00 NY bar close.
- **News window:** previous US cash close → origin. Morning brief: 6:00am → origin.
- **"Since US close" line:** ends at the origin.
- **Treasuries / Fed odds window:** past 24h, or on Mondays since Sunday's reopen, ending at
  the origin.
- **Alert:** text and timing (≈ 9:08am NY).
- **Driver check:** compare against the snapshot at the new origin.
- **Workflows:**
  - `garch_vol.yml` GitHub cron slots shift one hour later (keep them as a backup);
  - the watchdog moves to ≈ 9:45am NY (two UTC slots to cover both US clock periods);
  - Details text and CLAUDE.md "Key design decisions".
- **Regenerate** `site/data` through the documented backfill path at the new origin.

## 3. The external timer (the user does this on cron-job.org; Claude Code can't)

Write the exact new settings for the user at the end of the report:
- job timezone **America/New_York**;
- weekdays at **09:07** and **09:47**;
- same URL, method, headers and body as now.

That way US clock changes are handled automatically.

## 4. Verify and push

- Run the job for a past weekday and a Monday into a scratch folder. Check:
  - labels;
  - windows;
  - the cash range starting at the origin price;
  - the alert text.
- Take screenshots at 1280 px and 375 px.
- Run `git pull --rebase`, commit, push and deploy (outside 8–10am NY). Check the live site.
- **Report:**
  - the A vs B table, including release days;
  - the decision;
  - the cron-job.org settings for the user.
