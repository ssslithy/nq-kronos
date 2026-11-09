# NQ direction model: literature review and research plan

Status: **research only**. Nothing goes on the live page or into alerts unless a model passes
the gates in Part 4. Written 9 Nov 2026 for Claude Code in `~/nq-kronos`. `CLAUDE.md` rules
apply: no look-ahead, pinned versions, free data only.

## Part 1: what the literature says (Tier 1 candidates)

| # | Effect | Evidence | Status / caveats | Our use |
|---|---|---|---|---|
| 1 | **Index short-term reversal** | Baltussen, van Bekkum & Da (JFE 2018), 20 indexes, 15 countries. Daily index autocorrelation flipped from positive to **negative after ~1999**: pooled AR(1) −0.026 daily, −0.082 weekly. Futures have been negative since launch. Attributed to indexing/arbitrage. A trade against the past 5 days (MAC(5)) has Sharpe ~0.67 on the S&P 500 since 1999 | Robust across countries, with a causal story. Small per day | Past 5-day return as a predictor, expected sign **negative** |
| 2 | **Overnight drift after sell-offs** | NY Fed, Boyarchenko et al. (2021), S&P 500 futures 1998–2019. Overnight returns are positive, peaking 2–3am ET at the European open. **Strongest after sell-offs** (negative closing order imbalance): ~12% annualised in European hours after the worst sell-offs. Little reversal after rallies | Asymmetric: works after down days. Reflects dealer inventory compensation | Previous cash-session return, **down moves only** (min(ret, 0)). Expected sign **negative** (a bigger drop means more drift up) |
| 3 | **Turn of the month** | Lakonishok & Smidt (1988); McConnell & Xu (2008), 1926–2005: days −1 to +3 average 0.15%/day vs 0.01% on other days. Carchano & Pardo tested 188 calendar effects on S&P/DAX/Nikkei futures 1991–2008, and **turn-of-month was the only one significant and profitable** in S&P futures. A practitioner backtest to 2024 suggests it still holds | Most robust calendar effect; post-2009 academic evidence is thinner | Flag: the 1pm→1pm window covers trading day −1 to +3 of the month. Expected **positive** |
| 4 | **Pre-FOMC drift** | Lucca & Moench (2015): about +49 bp in the 24h before FOMC, 1994–2011. **Kurov, Wolfe & Gilbert (2021): it disappeared from 2016** (≈ +9 bp, no different from normal days). It survives only when uncertainty (VIX/TYVIX) is high | Probably dead in normal conditions. Kept only with a high-uncertainty condition | Flag (FOMC in window) × VXN high. Expected **positive**. Low prior |
| 5 | **Market intraday momentum** | Gao, Han, Li & Zhou (JFE 2018): first half hour predicts last half hour (SPY). Out of sample in 11–12 of 16 developed markets 2000–2017 (Nottingham study). **Baltussen, Da, Lammers & Martens (JFE 2021)**, 60+ futures 1974–2020: the **rest-of-day return (prior close → 30 min before close) predicts the last 30 min**, with equity R² ≈ 2.5% and OOS R² ≈ 2.9%. It's stronger when dealers are **short gamma**, and reverses over 1–3 days | The best-documented intraday effect. R² fell from 3.4% (pre-2000) to 2.3% (2000–2020). Rosa (J Futures Mkts 2022): the overnight-return version **fails out of sample**, and thresholds help | Target: the **last hour of the cash session** (3–4pm NY = 8–9pm London). Predictor: return from the previous cash close to 3pm NY. Expected **positive**, stronger when volatility is high (a short-gamma proxy) |
| 6 | **Extreme fear spikes** | VIX at a 2-year high → higher S&P returns over the next 1–5 days (1991–2017), but it's almost entirely in the extreme bucket (<1% of days) | Too rare for ~480 hourly-sample days. Testable on long daily history only | VXN at a 252-day high flag. Expected **positive**. Long-history test only |

**Weak or no support, so not Tier 1:**
- Overnight move predicting the next cash session at the index level. "Tug of war" (Lou, Polk & Skouras) is a cross-sectional stock effect. Rosa (2022) finds the overnight → last-half-hour link fails out of sample.
- First hour predicting the **whole** rest of the session. The literature target is the **last** half hour.
- Time-series momentum. It's a months-horizon effect, not a daily one.
- Day of week.

## Part 2: models to test (fixed before any results are seen)

All models are logit (logistic regression) with standardised inputs and an L2 penalty. The
penalty strength is chosen by walk-forward cross-validation inside the training window only.

| Model | Target (up = 1) | Inputs | Data |
|---|---|---|---|
| **M1. Next 24h** | sign of the 1pm→1pm London return (origin bar close → +23 bars) | #1 past 5-day return; #2 min(previous cash-session return, 0); #3 turn-of-month flag; #4 FOMC-in-window × VXN-high flag; realised-vol level (conditioner) | Hourly (~480 days) for the real target. **Plus** a daily close-to-close proxy on NQ=F since 2000 for #1–#4 |
| **M2. Last hour** | sign of the 3–4pm NY bar (8–9pm London) | #5 return from the previous 4pm NY close to the 3pm NY bar close; the same × high-vol flag (HAR σ in its top tercile, known at 3pm) | Hourly |
| **M3. Cash session from 1pm** (low prior, a control) | sign of the 9am→4pm NY cash-window return | overnight move to 8am NY; #1 past 5-day return; realised-vol level | Hourly |
| **Baseline** | always predict the training-window base rate | none | none |

Expected signs are stated above. A significant coefficient with the **wrong** sign counts as
a failure, not a discovery.

**Historical FOMC dates** for the daily test (2000–2026): build them from the Fed's
historical calendar pages (federalreserve.gov FOMC historical materials). Use announcement
days only. Note the 2:15pm → 2:00pm time change and the unscheduled meetings; exclude
unscheduled ones.

## Part 3: Tier 2, our own variables (exploratory, stricter)

Test these only after Tier 1, one at a time, each added to the best Tier 1 model:
- price at 1pm vs yesterday's 90% range (position inside the band, or a break);
- HAR σ minus GARCH σ (stress building or fading);
- VXN 1-day change;
- 10Y yield overnight change (from the daily `^TNX` long history; use the futures version for hourly).

- **Fed odds (user request):** the change over the prior 1pm→1pm window in the
  next-meeting "hawkishness" from Polymarket:
  - hawkishness = P(hike) − P(cut), summed across the outcome buckets;
  - roll to the following meeting the day after each decision.

  Rebuild the history from Polymarket's free price-history endpoint for every past
  "Fed decision in <month>?" event you can find. **Report the first date with usable
  data.** Expected sign: **negative** (a hawkish shift means NQ lower next).

  Polymarket's history is short, so also build a **long-history proxy**: the change in the
  2Y Treasury yield over the same window (FRED `DGS2` daily, 2000+; ZT=F for the hourly
  version). Test the proxy on the daily M1 history and the Polymarket series only on the
  dates it covers. If Polymarket covers fewer than 150 scored days, report it as
  "insufficient data", not pass/fail, and keep logging it daily (it already is, in
  `fed_odds.json`).
- Kronos and the HMM regime: already shown to be coin flips.
- News and calendar flags other than FOMC.

Tier 2 must clear the Part 4 gates **after a Holm correction** for the number of Tier 2
variables tried. Anything that passes is labelled "experimental".

## Part 4: test protocol and gates

1. **Holdout:** lock the last 6 months (from ~9 May 2026). No fitting, tuning or peeking
   until the final step. Score it once.
2. **Walk-forward** on everything before the holdout. Refit at least monthly and predict
   only the next period.
   - **Hourly models (M1 real target, M2, M3):** use an **expanding** window starting
     after the first 120 days, not a rolling 250. The hourly data covers only ~2 years
     (about 480 days), so a rolling 250 would leave almost nothing to score.
   - **Daily proxy:** rolling 5-year window, from 2000.
   - **Same period as HAR-RV:** besides the full scored period, report every hourly model
     on exactly the HAR-RV vol-fix window (the 304 origins, 19 Aug 2025 – 5 Nov 2026, and
     its last 250). The page's range and direction numbers can then be compared on the
     same days. The gates use the full scored period plus the holdout.
3. **Score**, all reported for the development period and the holdout:
   - Brier score vs the baseline: the Brier skill score (BSS);
   - log loss;
   - hit rate;
   - a calibration table (deciles, or 3 buckets if n is small);
   - coefficients with signs.
4. **Luck check:** a permutation test (shuffle targets in blocks, 1,000×), giving a p-value
   for the BSS.
5. **Gate (to be shown on the page):**
   - BSS > 0 in **both halves** of development **and** in the holdout;
   - permutation p < 0.05 (Holm-adjusted across M1–M3);
   - signs as expected;
   - calibration within ±5 points per bucket;
   - for the daily proxy (M1): the effect also holds 2000–2012 vs 2013–2026.
6. **Economic sanity**, for information only: the mean next-period return when P(up) > 0.55
   vs < 0.45, and a naive long/short return after 1 tick + fees. A direction edge that
   doesn't survive costs isn't worth showing.

## Part 5: deliverables

- `lab/direction.py` (reusable; `--model M1|M2|M3 --tier 1|2`).
- `lab/results/direction.md`: one verdict per model (**real edge / maybe / coin flip**), the
  tables above, and a plain-English summary first.
- **No changes** to `site/`, `forecast/` or the workflows. Commit the lab files and results
  only.

## Sources

- Baltussen, van Bekkum & Da (2018), *Indexing and stock market serial dependence around the world*, JFE. https://personal.eur.nl/vanbekkum/2018%20JFE%20BaltussenVanBekkumDa.pdf
- Boyarchenko, Larsen & Whelan (2021), *The overnight drift in US equity returns*, NY Fed Liberty Street. https://libertystreeteconomics.newyorkfed.org/2021/05/the-overnight-drift-in-us-equity-returns
- McConnell & Xu (2008), turn of the month; summary and references: https://quantpedia.com/strategies/turn-of-the-month-in-equity-indexes
- Carchano & Pardo, 188 calendar anomalies in index futures (summary): https://cxoadvisory.com/calendar-effects/stock-index-futures-calendar-effects
- Kurov, Wolfe & Gilbert (2021), *The disappearing pre-FOMC announcement drift*, Finance Research Letters. https://www.skidmore.edu/economics/documents/KurovWolfeGilbert-TheDisappearingPre-FOMC-Announce-Drift-200914.pdf
- Baltussen, Da, Lammers & Martens (2021), *Hedging demand and market intraday momentum*, JFE. https://www3.nd.edu/~zda/intramom.pdf
- Intraday time-series momentum in 16 developed markets (out-of-sample study): https://nottingham-repository.worktribe.com/OutputFile/5273196
- Rosa (2022), *Understanding intraday momentum strategies*, J. Futures Markets. https://econpapers.repec.org/RePEc:wly:jfutmk:v:42:y:2022:i:12:p:2218-2234
- VIX rank and short-horizon S&P returns (summary of Bandelli & Wang 2018): https://cxoadvisory.com/equity-premium/revisiting-vix-as-stock-return-predictor
