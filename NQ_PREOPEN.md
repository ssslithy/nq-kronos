# NQ forecasts: pre-open context

Run in one go in `~/nq-kronos`, then push and deploy. `CLAUDE.md` rules apply: free
sources only, fail-soft blocks, never hand-edit `site/data/*`, 375 px layout. The design
reference is the "Since US close" line, the Treasuries block and the BRIEF row in
`design/Main.dc.html`. I'll copy the latest version there.

## 0. Check the previous change first

If the Treasuries/Fed window and yields change isn't live yet, do it in this run too:
- **Window:** past 24 hours; on Mondays, the 14 hours since Sunday's reopen.
- **Shared label** above the Treasuries.
- **Yields:** 10Y from `^TNX`; 2Y from `2YY=F`, or from ZT=F converted with duration 1.9.
- **Display:** changes in bp, with red when yields rise.

## 1. "Since US close" line (top of the right column, above the calendar)

- **Window:** the previous US cash close (4:00pm New York) → the 1pm London origin.
  Monday = Friday's close. Label it like the design: "SINCE US CLOSE · THU 9PM – FRI 1PM",
  built from the real times, including clock-change weeks.
- **Three cells:** each a small label with a mono value:
  - **Oil:** `CL=F`, % change.
  - **VIX:** change in points. Check whether Yahoo `^VIX` has values from Cboe's early
    trading hours (from 3:15am New York). If not, use the front VIX future. Label which one
    in Details.
  - **Dollar:** `DX-Y.NYB` (or `DX=F`), % change.
- **Colour by what's good for NQ:** a fall in any of the three is teal; a rise is red.
- **Fail-soft:** if a value is missing, show "–" in that cell. Hide the line if all three
  are missing.
- **Details:** add "Context only: overnight moves are already in the 1pm price and don't
  predict the session (tested: the overnight move had no skill for the cash session)."

## 2. News

- **Morning brief (permanent rule):** pin exactly one pre-market wrap, the latest
  published 6:00am–1:00pm London that matches the wrap rules. Show it first in a raised row
  with a grey "BRIEF" tag, as in the design. Closing wraps and movers roundups stay
  excluded. Briefs don't count toward the significance split.
- **Widen relevance only for macro shocks that hit NQ directly:**
  - oil/OPEC/Middle East/Iran;
  - tariffs/China/export controls.

  Score them 3 bars if the story is about US inflation, the Fed or a large market-wide
  shift; otherwise 2. **No** strategist or analyst-note category.

## 3. Fed odds: "pts" consistent with the displayed numbers

- "pts" = percentage points, the absolute difference (74% → 70% = −4 pts).
- Compute each change from the **displayed** (rounded, normalised to 100) whole-number
  percentages at the window start and end, so a row reading 71% → 76% always says +5 pts.
  The three changes must sum to 0.

## 4. Two-week driver check (validation, lab only)

- Add a small evening step: a separate scheduled job at about 22:30 London, or inside the
  existing evening Kronos run if there is one.
  - Fetch the day's **closing market wraps** from the same feeds.
  - Extract the main named drivers with a simple keyword/entity match: companies, oil,
    Fed/CPI/jobs, tariffs, and so on.
  - Append to `lab/results/driver_check.jsonl`: the date, the drivers, and whether each
    one appeared in that day's 1pm snapshot (news items, brief or the "Since US close"
    cells).
- After 10 trading days, write `lab/results/driver_check.md`: the % of drivers caught by
  1pm, and the most common misses. **Don't change any rules automatically.** It's for
  review.
- **Fail-soft.** It must never touch the 1pm forecast, the alert or the page.

## 5. Verify and push

- Run today's 1pm job into a scratch folder. Check:
  - the window labels;
  - the colour logic;
  - the brief selection;
  - Fed pts adding up to 0.
- Take screenshots at 1280 px and 375 px, for a Monday and a Tuesday run.
- Run `git pull --rebase`, commit, push and deploy. Check the live site.
