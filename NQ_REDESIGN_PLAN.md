# NQ forecasts: redesign + research, one run

Written for Claude Code in `~/nq-kronos`. Read `CLAUDE.md` first: all its rules still apply
(no look-ahead, pinned versions, free services only, 375 px phone layout, never hand-edit
`site/data/*`). This plan overrides `CLAUDE.md` in exactly three places, marked **OVERRIDE**.

**OVERRIDE 1 (one go):** the user wants this done in one run, without stopping after each
step for an OK. Work through Parts A–G in order. Instead of the user's OK, use the
**gates** written into each part. A gate that fails changes what you build, as each part
says; it is not a reason to stop. **Stop only for the STOP conditions** (listed at the end),
and then report.

Reference design, in `design/`:
- `design/redesign_desktop.png`, a screenshot at 1280 px with the 90% band, the Cash box and
  Kronos turned on. Ignore the blue link colour: links use the existing teal.
- `design/Main.dc.html`, the design source. The markup, colours and spacing are what to copy.
  Its script block holds the toggle logic (multi-select bands, label de-overlap). Every
  number in it is sample data.

Rebuild the page to match this design pixel for pixel at 1280 px, and make it work at 375 px.

---

## Part A: horizon 1pm → 1pm, more ranges, re-backfill

1. **Horizon = 23 tradable hours.** The origin bar closes at 1:00pm London (08:00 New
   York). 23 bars, with the one-hour daily break skipped, end at 1:00pm London the next
   trading day. That is "Fri 1:00pm – Mon 1:00pm" on Fridays. In US/UK clock-change mismatch
   weeks the origin closes at 12:00 London, so build every label from the data, never
   hard-coded.
   - Set `HORIZON = 23` in `forecast/garch_vol.py`.
   - Pass `--horizon 23` to Kronos in the workflow. Don't change Kronos's own default.
   - Follow the horizon through everything that assumed 24: calibration k, the matured-forecast
     check, accuracy filling, the fan, the alert text and the README.
   - On the page, the card label stays "Next 24 hours". The times beside it show the real
     window.
2. **Five ranges: 99 / 95 / 90 / 85 / 80%.**
   - 90% and 85% use **measured multipliers**, exactly like m95 and m99 today: the 90th and
     85th percentile of |move / kσ| over the same trailing 250 matured forecasts.
   - 80% stays ±1.2816 kσ.
   - All five go into `vol_latest.json`, the history rows (`inside85`, `inside90` beside the
     existing `inside80/95/99`) and the chart fan, so the +23h band edges equal the card numbers.
   - **The 90% range is now the headline.** It is the big ±pts / ±% / price range on the
     card, and the one in the alert.
3. **Re-run the backfill** into a scratch folder:
   `vol_accuracy.py --backfill --all --out /tmp/vol_a`. Report the hit rate for all five ranges
   on both halves.

**Gate A:**
- 80% must be 75–85% on both halves (the existing rule).
- 90% must be 86–94% on both halves.

If 90% misses, check the multiplier code for look-ahead and off-by-one errors first. If it
still misses after that, it's a **STOP**.

If both pass, regenerate the real `site/data` history with the new horizon. Use the
documented backfill path, not hand edits.

## Part B: cash session at 90%

The cash range has only been checked at 80% (82–83%). The page now shows the 90% cash range.

1. Add a cash 90% range: raw cash σ × the same measured-multiplier approach. Use a separate
   percentile of |cash move / cash σ| over the trailing 250 matured cash results. Don't reuse
   the 24h multiplier.
2. Backfill it and report the hit rate on both halves. Add `cash_inside90` to the history rows.
3. **Window and label:**
   - The model's cash window is the 09:00–16:00 New York bars, which is 2:00–9:00pm London.
   - The page label will say "NYC Cash Session, 2:30pm – 9:00pm" (the real open).
   - Add one line to Details: "measured from the 9:00am New York bar, half an hour before the
     open".
   - The cash range is centred on the 1:00pm price. Say that in Details too.

**Gate B:** the cash 90% hit rate must be 86–94% on both halves.
- **If it passes:** show the cash range as designed.
- **If it fails:** still show the cash ±pts and range, but switch that block to the 80% cash
  range, which already passed. Change its small note to "Based on the 80% PI". Say so in the
  final report.

## Part C: research, volatility contest (decides the model)

Put this in a new `lab/vol_contest.py`, with results in `lab/results/vol_contest.md`.

- Use the same 250 daily origins as the backfill, the 23h horizon and the walk-forward method.
- Every model may use only data before each origin. Pin any new library in
  `forecast/requirements-vol.txt` (or a lab-only requirements file if it's not used in
  production).

**Candidates:** each must produce one σ for the next 23h.
1. **GARCH**: the current production model, which is the baseline.
2. **GARCH + VXN**: blend log σ_GARCH with the VXN-implied daily σ (`^VXN` from yfinance:
   use the latest value known before the origin, scaled to 23h). Fit the blend weight on the
   prior window only.
3. **HAR-RV**: daily realised variance from hourly returns; regress on 1-, 5- and 22-day lags
   using a rolling window.
4. **XGBoost** on realised-variance features. The target is the log of the next 23h realised
   variance. Features:
   - the last 1, 5 and 22 days' realised variance
   - the last overnight range
   - VXN
   - day of week
   - an event-in-horizon flag and the event kind
   - the GARCH σ

   Retrain monthly on a rolling window, with no future rows.
5. **GARCH + regime**: GARCH σ times a factor for the Part D regime state. Fit the factor on
   the prior window only.

Each candidate gets the same k calibration and measured multipliers as production.

**Scoring:**

| Measure | Applied to |
|---|---|
| Hit rate | 80 / 90 / 95%, both halves |
| Mean 90% width | in points |
| QLIKE loss | on 23h realised variance |

**Gate C, to replace GARCH**, a candidate must pass all four:
- 80% is 75–85% and 90% is 86–94% on both halves;
- its mean 90% width is at least 5% narrower than GARCH;
- its QLIKE is lower than GARCH;
- it beats GARCH in **both** halves on width and QLIKE (not just the total).

If several pass, take the simplest (fewest moving parts). If none pass, **keep GARCH**,
write that up, and move on. That is a perfectly good result.

If a winner replaces GARCH in production:
- Its daily run must finish inside the existing workflow without new paid services.
- Re-run Gate A with it.
- Update CLAUDE.md "Key design decisions" with what changed and the numbers.

## Part D: research, regime filter (decides whether the chip appears)

Put this in a new `lab/regime.py`, with results in `lab/results/regime.md`.

- Use daily NQ=F closes, so a longer history is fine (daily data isn't capped at 729 days).
- Fit a **2-state Gaussian HMM** on daily log returns (`hmmlearn`, pinned). Refit weekly on a
  rolling window of about 3 years, using data before each origin only.
- Use the **filtered** probability, P(state | data up to the origin). Never the smoothed one,
  which is look-ahead.
- Label the states after each fit: the higher-mean, lower-variance state is "Bull".

**Gate D:** the chip may go live only if all three hold over the last 250 origins:
1. **Stable:** the median run length in one state is at least 10 trading days.
2. **Meaningful:** next-23h realised volatility in the Bear state is at least 1.3 times that in
   the Bull state. Test it with a block bootstrap, 95% interval above 1.0.
3. **Honest probability:** bucket the filtered P(Bull) into 0.5–0.7, 0.7–0.9 and 0.9–1.0.
   Within each bucket, the state that the next 20 days are later classified into (with
   filtered probabilities, judged later) matches at roughly the stated rate (±10 points).

Also report whether the regime predicts the **direction** of the next 23h:
- the hit rate and rank correlation, as was done for Kronos.

Don't show direction on the page unless it is clearly significant in both halves. The
expectation is that it won't be.

- **If Gate D passes:** the page shows the regime row as "Regime  [Bull | Bear]  82% state
  probability". Use the wording "state probability", not "model confidence", because it's
  the filtered HMM probability.
- **If it fails:** hide the regime row entirely (no placeholder). Write up why in
  `regime.md`.

## Part E: new data for the page

All of this is collected by the daily `vol` job, after the forecast is made. Every piece is
**fail-soft**: if a source fails, write the file with `"ok": false`, hide that block on the
page, and never fail or delay the forecast or the alert.

1. **US calendar** (`site/data/calendar.json`):
   - The next 3 events at or after the origin, from `events_2026_2027.yaml`.
   - Add an `impact` per type in the YAML:
     - 3 = NFP, CPI, FOMC
     - 2 = PCE, GDP advance, Retail sales
   - Fields: London date ("Today" when it's the origin's day), London time, name, impact.
   - The page draws the 3-bar signal; the bar count = impact.
2. **US Treasuries** (`site/data/treasuries.json`):
   - **% move:** 2Y and 10Y note futures `ZT=F` and `ZN=F` (yfinance, hourly), close of the
     bar before the previous origin → close of the origin bar, which is 1pm → 1pm London.
     On Mondays that runs from Friday 1pm. Store the % move and the hourly closes across
     that window for the sparkline.
   - **Yield:** 10Y from `^TNX`. 2Y from FRED `DGS2` (the free CSV at
     `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2`, latest value, no key). If it
     can't be fetched, leave the 2Y yield out.
   - Show both yields as "Yield x.xx%".
   - Heading: "US Treasuries · Thu 1pm – Fri 1pm", built from the window.
3. **Tech news** (`site/data/news.json`):
   - Headlines only: source, title, link, published time in London. Never article text.
   - Free RSS, pinned `feedparser`:
     - Yahoo Finance headline RSS for `NQ=F`, `^NDX` and the top Nasdaq names (NVDA, AAPL,
       MSFT, AMZN, GOOGL, META, AVGO, TSLA);
     - CNBC Technology RSS.
   - Keep the last 24h, de-duplicate by title, and take the 3 newest.
   - Each headline links to its source. Show the outlet name only as given in the feed.
   - This is a 1pm snapshot, which is fine for a daily glance.

## Part F: the page

Rebuild `site/index.html`, `vol.js`, `app.js` and `style.css` to the design. Keep the code
vanilla, with no build step.

**Header**
- The "NQ" badge.
- "Fri 6 Nov · 1:00pm London · 27,099". The price is the origin bar's close, in amber.
- Under it: "Nasdaq-100 futures · next update Mon 1:00pm".
- "Accuracy →" on the right.

**London time only, in 12-hour format** ("1:00pm", "12:42pm"), everywhere.
**OVERRIDE 2:** this replaces "times in both New York and London". New York appears only in
Details.

**Top card**
- **Left: two equal blocks.**
  - Block 1: "Next 24 hours, Fri 1:00pm – Mon 1:00pm"; the big ±pts; ±% to the right in
    smaller grey; the price range underneath.
  - Block 2: "NYC Cash Session, Fri 2:30pm – Fri 9:00pm", in the same layout.
  - Under both blocks: "Based on the 90% PI".
  - Then the regime row, only if Gate D passed.
- **Right column, next to the cash block** (exactly as in the design):
  - "US CALENDAR · LONDON": 3 rows of date, time, name and the 3-bar signal. "Today" and
    today's event name are in amber.
  - Under it, "US TREASURIES · …": 2Y and 10Y rows of label, sparkline (stretched to fill
    the gap), % move and "Yield x.xx%".
- **Full width below:** "TECH NEWS", with 3 rows of source (teal), time (right) and headline.
- **No amber event bar.** It was removed on purpose; the calendar replaces it.

**Chart card**
- **Title:** "Hourly price · forecast to Mon 1:00pm London".
- **Controls, left to right:**
  1. the "Last Price" legend;
  2. the "Kronos (experimental)" checkbox, off by default;
  3. range buttons 99% 95% 90% 85% 80%, **multi-select**, 90% on by default;
  4. a divider, then a "Cash" toggle button, off by default;
  5. the "Levels" checkboxes **PI** and **Cash**, both on by default, pushed right so that
     their right edge lines up with the right end of the chart gridlines.
- **Chart:**
  - 48 candles, then the +23h fan for each selected range, drawn as stacked translucent
    bands.
  - The last price as a dashed amber line, with its label at the right.
  - A "now" marker.
- **Levels: PI.** Label the upper and lower edge of **every** selected band at the right
  ("90%  27,546"), nudging labels apart so they never overlap.
- **Cash toggle.** Draw a dashed blue box from the first to the last cash bar, top and
  bottom at the cash range, with "Cash 27,429" above it and "26,773" below it.
  - Use the 90% cash range, or 80% if Gate B failed.
  - The Levels Cash checkbox hides these labels.
- **Kronos.** Show its path(s) only if `latest.json` has the same origin as the GARCH
  forecast and isn't stale. Otherwise disable the checkbox with a "not run today" tooltip.
- **Axis labels:** "Thu 8am", "Fri 1pm", and so on, in London time.

**Footer**
- "Last 250 days: 90% PI – XX% hit rate", from the history. "See accuracy →" on the right.
- A collapsed **Details** section containing:
  - PI meaning: the range the price should end inside, with that probability, at the end of
    the window;
  - the model, with its numbers;
  - recent moves;
  - data sources;
  - the cash-window note and the centring note from Part B;
  - a Friday note: weekend gap risk isn't fully in Friday's range;
  - **"Volatility only, not direction. Not investment advice."**, which CLAUDE.md requires.
    This is its home now; don't put it anywhere else on the main page.

**Remove from the main page:** the old 80% headline, any duplicate hit-rate rows, the
`vol-regime` percentile label (still in the JSON and on the accuracy page) and the event
banner.

**Accuracy page:** update it for five ranges, the 23h horizon and cash 90%. Keep its style.

**Alert (ntfy):** make it lead with the 90% numbers and the 1pm → 1pm window, in London time.

**Phone (375 px):**
- The two forecast blocks stack.
- The calendar/Treasuries column and the news go below them.
- Chart controls wrap, with Levels on its own line.
- No sideways scrolling.

## Part G: verify, document, push

1. Run the daily job for two past days into a scratch folder:
   `run_vol.py --now "2026-11-06 08:10" --out /tmp/vol` and a Monday. Check that every JSON
   validates and that a failed news, Treasuries or calendar fetch (simulate each one) still
   produces the forecast and the alert.
2. Preview the page with `python -m http.server`. Take Playwright screenshots at **1280 px**
   and **375 px** with:
   - default state;
   - all five bands on;
   - Cash on;
   - both Levels off;
   - Kronos on.

   Check them against `design/redesign_desktop.png`. Fix any overlap, clipping or sideways
   scroll.
3. Update `README.md` and `CLAUDE.md` (schedule, horizon, five ranges, new data files, gates
   and results). **OVERRIDE 3:** when everything above has passed, commit and push without
   asking (`git pull --rebase` first, as CLAUDE.md says). Then trigger a Pages deploy and open
   the live site to confirm it.
4. **Final report to the user**, short and in plain words:
   - the Gate A, B, C and D results as a small table;
   - whether GARCH was kept or replaced;
   - whether the regime chip is live;
   - anything that fell back;
   - the two live screenshots.

## STOP conditions (stop, report, don't push)

- Gate A still fails after you've ruled out a code bug.
- Any sign of look-ahead you can't remove.
- The daily job can't finish inside the existing workflow limits.
- A change would need a paid service or a new secret.
- You'd have to hand-edit `site/data/*` to make something work.
