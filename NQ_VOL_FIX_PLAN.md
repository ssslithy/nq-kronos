# NQ forecasts: fix slow reaction after shocks + chart tweaks

Written for Claude Code in `~/nq-kronos`. `CLAUDE.md` rules apply: no look-ahead, pinned
versions, free services, never hand-edit `site/data/*`, 375 px phone layout. Like
`NQ_REDESIGN_PLAN.md`, run Parts 1–4 in one go: gates replace the user's OK. Stop only for
the STOP conditions at the end. When everything passes, push and deploy.

## Why

The full-year 90% hit rate is fine (88.8%), but the misses come in clusters. Three of the
last 7 forecasts broke the 90% range:

| Origin | Move |
|---|--:|
| 29 Oct | −2.8% |
| 2 Nov | −1.7% |
| 5 Nov | −4.4% |

Through the whole sell-off, the model rated volatility "Calm" (0–20th percentile). Hourly
GARCH has a persistence of about 0.86 per hour, so a shock fades within about a day. It
misses the multi-day volatility clustering that follows big drops. The fix must cut the
clustered misses without making ordinary days' ranges much wider.

## Part 1: new scoring (add to `lab/vol_contest.py`, keep the old tables)

1. Use **all origins the data allows**, not just 250. Hourly data goes back about 729 days.
   After the 2,000-bar fit window and 250 matured forecasts for calibration, that leaves
   roughly 300+ scored origins. Also report the last-250 view for comparison.
2. For each model, add these measures to the existing hit rates, width and QLIKE:
   - **Christoffersen independence test** on the 90% misses (LR statistic, p-value). A low
     p-value means the misses cluster.
   - **Miss after miss:** the share of 90% misses that come within 5 origins of a previous
     miss.
   - **Stress coverage:** the 90% hit rate on stress origins. A stress origin is one where
     the previous origin's |move| was greater than 2× its forecast σ, or where VXN is in the
     top quartile of its trailing year. Use data known at the origin only.
   - **Calm width:** the mean 90% width on the other origins.
   - **Last-60 coverage:** the 90% hit rate over the most recent 60 scored origins.

## Part 2: candidates

Every candidate goes through the same production calibration (k and the measured
multipliers, trailing 250, no look-ahead).

| Candidate | Definition |
|---|---|
| A. GARCH | Current production. Baseline. |
| B. GARCH + daily floor | σ = max(σ_GARCH, c · RV_5d), where RV_5d is the RMS of the last 5 daily (23h) realised moves. Fit c on the prior window only; also try RV_10d. |
| C. GARCH + VXN | Re-score the existing blend under the new measures. |
| D. HAR-RV | Re-score the existing model. |
| E. Two-speed GARCH | Blend log σ_GARCH(hourly) with log σ from a daily GARCH(1,1)-t on daily returns. Fit the weight on the prior window. |

## Gate (to replace GARCH in production, all must hold)

1. Coverage: 80% range in 75–85% and 90% range in 86–94%, on both halves of the full
   sample.
2. Clustering improves: Christoffersen p > 0.05, **or** miss-after-miss at most two-thirds
   of GARCH's.
3. Stress coverage of the 90% range ≥ 85%, and at least 5 points better than GARCH.
4. Calm width no more than 5% wider than GARCH's.
5. QLIKE no worse than GARCH, overall and on each half.

If several candidates pass, take the simplest. If none pass, keep GARCH. In that case add
one line to the page footer under the hit rate: "Last 20 days: X of 20 inside". Count it
from the history so clustering is visible. Write the result to
`lab/results/vol_fix.md`.

If a candidate passes:
- Put it in `forecast/garch_vol.py` behind one constant (for example `VOL_MODEL`).
- Re-run `vol_accuracy.py --backfill --all` into a scratch folder.
- Re-check Gate A from the redesign plan (80%: 75–85, 90%: 86–94 on both halves) and the
  cash 90% gate (86–94 on both halves). If either fails, don't stop: keep GARCH, add the
  "Last 20 days" footer line, and continue with Parts 3–4.
- Then regenerate `site/data` through the documented backfill path.
- Update CLAUDE.md "Key design decisions" with the change and its numbers.
- Keep the daily run inside the existing workflow limits.

## Part 3: page tweaks

1. **Levels control.** Remove the "PI" label: the checkbox that toggles the band price
   labels is now called **"24h"**, so the row reads "Levels  ☑ 24h  ☑ Cash". The band labels
   themselves stay as they are ("90%  27,550").
2. **Cash toggle changes the chart.** When "Cash" is on:
   - The forecast part of the chart **ends at the last cash bar** (9:00pm London on a normal
     day). Draw the 24h bands only from now up to the end of the cash session. Cut the x-axis
     there and re-space the labels, so the cash window fills the forecast area instead of
     sitting in a thin slice.
   - Keep the dashed blue cash box, from the cash start to its end.
   - At the box's **left edge**, show the cash price levels with the matching 24h band levels
     at the end of the cash session, one label per edge. Format, using 90% as the example
     (the order is cash, then 24h, so they read as a pair):
     - top: `Cash 27,447 · 90% 27,3xx`
     - bottom: `Cash 26,755 · 90% 26,7xx`

     Here "90% 27,3xx" is the **24h band's value at 9:00pm**, not at the end of the 24h
     window. Show one 24h value per selected band, in the same de-overlapped stack as the
     right-edge labels. The "Cash" level checkbox hides the cash numbers; the "24h" level
     checkbox hides the band numbers.
   - The right-edge labels go to the 9:00pm values as well, because that's where the chart
     now ends.
   - When "Cash" is off, the chart returns to the full 1pm → 1pm view.
   - It must stay legible at 375 px. If the left-edge labels don't fit there, put them under
     the chart in one line, for example "At 9:00pm · Cash 26,755 – 27,447 · 90% 26,7xx –
     27,3xx".
3. **News feed.**
   - Find out why every headline came from CNBC on 6 Nov and fix it if it's a bug. Did the
     Yahoo feed return nothing, or was it all filtered out? Note the cause in `CLAUDE.md`.
   - Headlines not from the origin's day show the day as well: "Thu 11:39pm".

4. **Fed decision odds from Polymarket**, a new block under the Treasuries rows, in the same
   format. See `design/Main.dc.html` and the "FED · DEC 9 DECISION · POLYMARKET" block.
   - **Which market:** the event for the **next FOMC decision** after the origin. Take the
     date from `events_2026_2027.yaml`; right now that's "Fed decision in December?",
     resolving 9 Dec 2026. Find it through Polymarket's free public API, with no key and no
     account: the Gamma API (events and markets search). Match on the title for that month
     and year. Don't hard-code the slug, because it has a random suffix. Check the endpoints
     against Polymarket's current docs before relying on them. After each meeting resolves,
     the next run must pick up the following meeting automatically.
   - **Rows:** **Hike, Hold, Cut**. Sum the outcome buckets: Hike = all "increase" outcomes,
     Cut = all "decrease" outcomes. Each row shows:
     - the label;
     - a sparkline of that probability over the same 1pm → 1pm window as the Treasuries
       (hourly points from Polymarket's price-history endpoint);
     - the current probability (whole %, "<1%" below 0.5%);
     - the change over the window in points ("+4 pts").
   - **Colours:** Hike amber, Hold grey, Cut teal. Give each sparkline a minimum vertical
     scale of 10 points, so a flat 1% line looks flat, not jittery.
   - **Heading:** "FED · DEC 9 DECISION · POLYMARKET", built from the market's date. Link
     the heading to the Polymarket event page.
   - **Data:** write `site/data/fed_odds.json`. Fail-soft like the other blocks: if
     Polymarket is unreachable, or no market is found for the next meeting, hide the block.
     The forecast and the alert are never affected.
   - **Wording:** it's crowd betting odds, not a forecast from this site. Add one line to
     Details saying so.
   - **Layout:** the right column gets taller. Keep the news full width below the top row,
     and check the 1280 px and 375 px screenshots.

## Part 4: verify and push

1. Run the daily job for 6 Nov and a Monday into a scratch folder. Check that the JSONs
   validate and that the alert text is right.
2. Take Playwright screenshots at 1280 px and 375 px with:
   - default state;
   - Cash on;
   - Cash on plus 99/95/90/85/80 all on;
   - both Levels boxes off.

   Fix any overlap, clipping or sideways scroll.
3. Update README and CLAUDE.md. Run `git pull --rebase`, commit, push, deploy, and open the
   live site to confirm.
4. Report back briefly:
   - the new scoring table for all candidates, with the gate results;
   - which model is live;
   - screenshots of the default and Cash-on views.

## STOP conditions (stop, report, don't push)

- Any look-ahead you can't remove.
- The daily job can't finish inside the workflow limits.
- A change would need a paid service or a new secret.
