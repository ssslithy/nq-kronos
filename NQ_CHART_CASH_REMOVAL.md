# Remove the cash session and the Levels control from the chart

A small page-only change in `~/nq-kronos/site`. Do it in one go, then push. No model or
data changes. The `CLAUDE.md` rules apply.

1. **Chart controls:**
   - Remove the **Cash** toggle button and the divider before it.
   - Remove the whole **Levels** control (the label and its checkboxes).
   - The row now reads: Last Price · Kronos (experimental) · 99% 95% 90% 85% 80%.
2. **Band levels are always on.**
   - Every selected band shows its upper and lower price at the right edge
     ("90%  27,550"), just like the last-price label.
   - No toggle; keep the de-overlap nudging.
3. **Chart:**
   - Remove the cash box, its labels, and the cut-to-9pm "cash view" with its left-edge
     labels.
   - The chart always shows the full 1pm → 1pm forecast.
4. **Clean up:**
   - Delete the unused cash and levels code, state and CSS.
   - Keep the **NYC Cash Session** block in the top card, and the cash data in
     `vol_latest.json`.
5. **Check:**
   - Playwright screenshots at 1280 px and 375 px: default state, then all five bands on.
   - No console errors, no sideways scroll.
6. Run `git pull --rebase`, commit, push and deploy. Confirm on the live site.
