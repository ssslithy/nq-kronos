# NQ forecasts: tweaks round 2

Run in one go in `~/nq-kronos`, then push and deploy. `CLAUDE.md` rules apply: no
look-ahead, free sources only, fail-soft data blocks, never hand-edit `site/data/*`, 375 px
layout. Check against the live page at 1280 px and 375 px (Playwright screenshots) before
pushing.

## 1. Text and format

- **Capital letter after every middle dot (·)** in headings and meta lines across the page
  and the accuracy page. Examples:
  - "Nasdaq-100 futures · Next Update Tue 1:00pm" ("Next Update" exactly as written).
  - "US Calendar · London"
  - "US Treasuries · Fri 1pm – Mon 1pm"
  - "Fed · Dec 9 Decision · Polymarket"
  - "Hourly price · Forecast to Tue 1:00pm London"

  Numbers and times after a dot stay as they are ("· 27,099", "· 1:00pm"). Apply it in the
  page code, not by editing data.
- **US calendar date wrapping:** "Wed 25 Nov" currently breaks so that "Nov" drops to a
  second line. Keep each date on one line (`white-space: nowrap`), and widen the date
  column so it fits at 1280 px and 375 px. All rows must line up the same way.

## 2. Fed odds: same window as the Treasuries

- The change ("+7 pts") and the sparkline must cover **exactly the Treasuries window**: the
  previous forecast origin → this origin. On Mondays that's **Fri 1pm – Mon 1pm**.
- Polymarket trades through the weekend, but sample it **at the same hourly timestamps as
  the ZT/ZN bars**, so weekend hours are skipped, just like the yields.
- Heading: "Fed · Dec 9 Decision · Polymarket", with the window shown the same way as the
  Treasuries heading. Put it on a second small line if it doesn't fit on one at 375 px.

## 3. Tech news: right time window, better quality, more of it, significance bars

1. **Time window:** headlines published from the **previous cash-session close** (4:00pm New
   York on the previous trading day; 9:00pm London normally, 8:00pm in clock-change mismatch
   weeks; Friday's close on Mondays) **up to the forecast origin time** (the 1pm London bar
   close).
   - Use the origin time, not the moment the job runs. A late or re-run job must not show
     anything published after 1pm. Today's run went out at ~4pm and showed 3:59pm headlines,
     which is exactly the bug to fix.
2. **More and better sources** (free RSS only, pinned `feedparser`; check each feed works
   and drop any that don't):
   - Yahoo Finance headline RSS for `NQ=F`, `^NDX` and the ~15 largest Nasdaq-100 names;
   - CNBC Technology, Markets and Earnings;
   - MarketWatch markets/top stories;
   - Nasdaq.com markets.
3. **Quality filter:**
   - Keep only items that mention a Nasdaq-100 company, chips/AI/cloud/big tech as a
     sector, or a macro driver (Fed, rates, inflation, jobs, tariffs, export controls).
   - Drop live blogs ("Live updates"), video pages, opinion pieces, quizzes and lifestyle or
     product-review items.
   - De-duplicate near-identical stories across feeds (fuzzy title match), keeping the
     earliest.
4. **Significance (1–3 bars, same 3-bar signal as the calendar),** rule-based from a new
   `forecast/news_rules.yaml` so it can be tuned without code changes:
   - **3:** macro or Fed news, tariffs/export controls/antitrust rulings; earnings, guidance
     or major news from the ~8 largest Nasdaq-100 names (by index weight);
   - **2:** other Nasdaq-100 company news; sector-wide semis/AI/cloud stories; rating
     changes on the largest names;
   - **1:** everything else that passes the filter.

   Store `impact` per item in `news.json`.
5. **Display:**
   - Up to ~12 items in a list that **shows 3 rows at a time and scrolls** (fixed height,
     `overflow-y: auto`, thin scrollbar, works with touch on a phone). Newest first.
   - Each row: source (teal) · **day + time** ("Mon 11:39am", or "Sun 9:05pm") on the
     right, then the headline. Leave **more space between the source/time line and the
     headline** (about 6–8px), so the title doesn't sit tight against it.
   - The significance bars sit at the **right** of each row, aligned like the calendar's.
6. Fail-soft as before: if every feed fails, hide the block. The forecast and alert are
   never affected.

## 4. Kronos timing

Kronos does update daily: today's run used the same 1pm origin. But it's a separate step
that takes ~9 minutes, so it lands about 10 minutes after the rest of the page. Until then,
the page shows yesterday's (or Friday's) Kronos as "not run today".

- Keep the alert and the main forecast first; never delay them for Kronos.
- When Kronos's origin ≠ the forecast origin, change the checkbox tooltip and label to
  "Kronos (updating…)", not "not run today". Only show "not run today" if it is still
  missing 30 minutes after the forecast.
- Make sure the Kronos publish triggers a Pages deploy (the shared deploy concurrency group,
  as now), so it appears without waiting for the next day.

## 5. Today's late run

Write two lines in `CLAUDE.md` "Incidents": why today's scheduled run didn't publish at
13:05 UTC, and what was fixed. If the cause was GitHub skipping or delaying the cron, add a
**watchdog**:
- a second scheduled check at ~13:45 UTC;
- if `vol_status.json`'s origin isn't today's, it re-dispatches the vol workflow once and
  sends an ntfy note saying the forecast is late.

## 6. Verify and push

- Run the job for today's origin into a scratch folder, and confirm:
  - no news item is later than 1:00pm London;
  - the Fed odds window equals the Treasuries window;
  - the calendar dates don't wrap.
- Take screenshots at 1280 px and 375 px, including the news list scrolled.
- Run `git pull --rebase`, commit, push and deploy. Check the live site.
- Report back briefly with the screenshots.
