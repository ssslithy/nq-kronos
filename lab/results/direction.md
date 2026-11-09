# NQ direction research (Parts 2–5 of NQ_DIRECTION_RESEARCH.md)

Run 09 Nov 2026 12:47. Research only: nothing here is on the page or in the alert. Holdout (09 May 2026 onward) scored once, in this run.

## Summary

**No direction model passes the gates. Nothing should go on the page.** Every model's probabilities stay close to the base rate (NQ goes up a bit more than half the time), and none beats that base rate reliably out of sample.

- **M1 (Next 24h (1pm -> 1pm London)): coin flip.** Development BSS +0.0006 (older -0.0002, newer +0.0015), holdout +0.0002; hit rate 51.9% vs up rate 53.2%; permutation p 0.102 (Holm 0.204).
- **M2 (Last hour (3-4pm NY = 8-9pm London)): coin flip.** Development BSS +0.0038 (older -0.0014, newer +0.0090), holdout -0.0214; hit rate 51.5% vs up rate 50.0%; permutation p 0.030 (Holm 0.090).
- **M3 (Cash session 9am -> 4pm NY (control)): coin flip.** Development BSS -0.0072 (older -0.0189, newer +0.0046), holdout -0.0240; hit rate 56.8% vs up rate 56.8%; permutation p 0.730 (Holm 0.730).
- **M1 daily proxy (2000–2026): coin flip.** BSS -0.0013 on 5,919 days; 2000–2012 -0.0008, 2013–2026 -0.0017; holdout -0.0030; permutation p 0.753.
- **Tier 2** (9 tests, on M2, M1, the M1 daily proxy): M2+band_pos insufficient data; M2+har_garch insufficient data; M2+vxn_chg coin flip; M2+yld10_chg coin flip; M2+fed_hawk coin flip; M2+yld2_chg coin flip; M1+fed_hawk coin flip; M1+yld2_chg coin flip; M1_daily+yld2_chg coin flip.
- **Fed odds:** Polymarket history is usable from 27 Jun 2024; 316 scored development days, so it was tested (not "insufficient data"). Verdict: on M2 coin flip, on M1 coin flip.

## Verdicts

| model | verdict | BSS dev (older / newer) | BSS holdout | perm p (Holm) | signs | calibration | other |
|---|---|--:|--:|--:|---|---|---|
| M1: Next 24h (1pm -> 1pm London) | **coin flip** | -0.0002 / +0.0015 | +0.0002 | 0.102 (0.204) | yes | **no** | daily eras **no** |
| M2: Last hour (3-4pm NY = 8-9pm London) | **coin flip** | -0.0014 / +0.0090 | -0.0214 | 0.030 (0.090) | **no** | **no** |  |
| M3: Cash session 9am -> 4pm NY (control) | **coin flip** | -0.0189 / +0.0046 | -0.0240 | 0.730 (0.730) | yes | **no** |  |
| M1_daily: Next day, daily proxy (NQ=F close-to-close since 2000) | **coin flip** | -0.0014 / -0.0013 | -0.0030 | 0.753 (0.753) | **no** | **no** |  |

Gate (Part 4): BSS > 0 in both development halves **and** the holdout; permutation p < 0.05 after Holm across M1–M3; expected signs; calibration within ±5 points per bucket; M1 also needs the daily proxy to hold in 2000–2012 and 2013–2026. Verdict: **real edge** = all gates; **maybe** = BSS > 0 in both development halves with unadjusted p < 0.10; otherwise **coin flip**. (M1_daily's p is not in the Holm set; it is M1's long-history check.)

## Tier 1 detail

### M1: Next 24h (1pm -> 1pm London)

Inputs: past5, downcash, tom, fomc_vxn, lrv. Development 2025-01-27 – 2026-05-08; holdout 2026-05-11 – 2026-11-05.

| period | n | up rate | BSS | log loss (base) | hit rate | mean P (sd) |
|---|--:|--:|--:|--:|--:|--:|
| development | 316 | 53.2% | +0.0006 | 0.6939 (0.6943) | 51.9% | 0.549 (0.023) |
| dev older half | 158 | 53.8% | -0.0002 | 0.6940 (0.6939) | 53.2% | 0.554 (0.024) |
| dev newer half | 158 | 52.5% | +0.0015 | 0.6938 (0.6946) | 50.6% | 0.545 (0.022) |
| holdout | 125 | 52.0% | +0.0002 | 0.6933 (0.6935) | 52.0% | 0.544 (0.001) |

| input | coefficient (final dev model, standardised) | expected | refits with expected sign |
|---|--:|---|--:|
| past5 | -0.0004 | − | 100% |
| downcash | -0.0013 | − | 100% |
| tom | +0.0003 | + | 47% |
| fomc_vxn | +0.0019 | + | 100% |
| lrv | +0.0002 | none | — |

C chosen: final 1e-04, median over refits 3e-03 (small C = strong shrinkage to the base rate). Permutation p = 0.102 (null 95th percentile BSS +0.0031).

Calibration (development, terciles of P):

| mean P | observed up | n |
|--:|--:|--:|
| 0.527 | 0.571 | 105 |
| 0.547 | 0.495 | 105 |
| 0.574 | 0.528 | 106 |

Calibration (holdout, terciles):

| mean P | observed up | n |
|--:|--:|--:|
| 0.543 | 0.429 | 42 |
| 0.543 | 0.537 | 41 |
| 0.544 | 0.595 | 42 |

Economic sanity (information only): mean next-period return when P > 0.55 vs P < 0.45, and a naive long/short (long if P > 0.5, short otherwise) after costs, against simply being long every period.

| period | mean ret P>0.55 (n) | mean ret P<0.45 (n) | days long | long/short net pts/trade | always long net pts/trade | long/short net total pts |
|---|--:|--:|--:|--:|--:|--:|
| development | +0.010% (143) | — (0) | 99% | +14.03 | +17.74 | +4,433 |
| holdout | — (0) | — (0) | 100% | -24.76 | -24.76 | -3,095 |

### M2: Last hour (3-4pm NY = 8-9pm London)

Inputs: rod, rod_hv. Development 2024-12-27 – 2026-05-08; holdout 2026-05-11 – 2026-11-06.

| period | n | up rate | BSS | log loss (base) | hit rate | mean P (sd) |
|---|--:|--:|--:|--:|--:|--:|
| development | 330 | 50.0% | +0.0038 | 0.6941 (0.6961) | 51.5% | 0.476 (0.028) |
| dev older half | 165 | 48.5% | -0.0014 | 0.6941 (0.6934) | 51.5% | 0.471 (0.006) |
| dev newer half | 165 | 51.5% | +0.0090 | 0.6941 (0.6987) | 51.5% | 0.481 (0.038) |
| holdout | 126 | 50.8% | -0.0214 | 0.7047 (0.6933) | 50.0% | 0.497 (0.069) |

| input | coefficient (final dev model, standardised) | expected | refits with expected sign |
|---|--:|---|--:|
| rod | -0.0170 | + | 56% |
| rod_hv | +0.2033 | + | 72% |

C chosen: final 1e-01, median over refits 1e-04 (small C = strong shrinkage to the base rate). Permutation p = 0.030 (null 95th percentile BSS +0.0021).

Calibration (development, terciles of P):

| mean P | observed up | n |
|--:|--:|--:|
| 0.455 | 0.555 | 110 |
| 0.474 | 0.445 | 110 |
| 0.498 | 0.500 | 110 |

Calibration (holdout, terciles):

| mean P | observed up | n |
|--:|--:|--:|
| 0.440 | 0.452 | 42 |
| 0.495 | 0.524 | 42 |
| 0.555 | 0.548 | 42 |

Economic sanity (information only): mean next-period return when P > 0.55 vs P < 0.45, and a naive long/short (long if P > 0.5, short otherwise) after costs, against simply being long every period.

| period | mean ret P>0.55 (n) | mean ret P<0.45 (n) | days long | long/short net pts/trade | always long net pts/trade | long/short net total pts |
|---|--:|--:|--:|--:|--:|--:|
| development | +0.078% (6) | +0.136% (24) | 6% | -0.69 | +0.36 | -228 |
| holdout | -0.004% (20) | -0.081% (19) | 28% | -4.94 | -6.53 | -622 |

### M3: Cash session 9am -> 4pm NY (control)

Inputs: overnight, past5, lrv. Development 2025-01-30 – 2026-05-08; holdout 2026-05-11 – 2026-11-06.

| period | n | up rate | BSS | log loss (base) | hit rate | mean P (sd) |
|---|--:|--:|--:|--:|--:|--:|
| development | 310 | 56.8% | -0.0072 | 0.6901 (0.6864) | 56.8% | 0.551 (0.034) |
| dev older half | 155 | 56.8% | -0.0189 | 0.6971 (0.6874) | 56.8% | 0.552 (0.044) |
| dev newer half | 155 | 56.8% | +0.0046 | 0.6832 (0.6855) | 56.8% | 0.550 (0.018) |
| holdout | 126 | 47.6% | -0.0240 | 0.7175 (0.7048) | 47.6% | 0.564 (0.035) |

| input | coefficient (final dev model, standardised) | expected | refits with expected sign |
|---|--:|---|--:|
| overnight | -0.1103 | none | — |
| past5 | -0.0014 | − | 100% |
| lrv | +0.1377 | none | — |

C chosen: final 1e-01, median over refits 1e-02 (small C = strong shrinkage to the base rate). Permutation p = 0.730 (null 95th percentile BSS +0.0040).

Calibration (development, terciles of P):

| mean P | observed up | n |
|--:|--:|--:|
| 0.527 | 0.621 | 103 |
| 0.545 | 0.524 | 103 |
| 0.582 | 0.558 | 104 |

Calibration (holdout, terciles):

| mean P | observed up | n |
|--:|--:|--:|
| 0.528 | 0.500 | 42 |
| 0.560 | 0.500 | 42 |
| 0.604 | 0.429 | 42 |

Economic sanity (information only): mean next-period return when P > 0.55 vs P < 0.45, and a naive long/short (long if P > 0.5, short otherwise) after costs, against simply being long every period.

| period | mean ret P>0.55 (n) | mean ret P<0.45 (n) | days long | long/short net pts/trade | always long net pts/trade | long/short net total pts |
|---|--:|--:|--:|--:|--:|--:|
| development | +0.069% (123) | — (0) | 98% | +9.58 | +7.02 | +2,969 |
| holdout | -0.223% (76) | — (0) | 98% | -38.65 | -34.46 | -4,869 |

### M1_daily: Next day, daily proxy (NQ=F close-to-close since 2000)

Inputs: past5, downcash, tom, fomc_vxn, lrv. Development 2002-10-25 – 2026-05-08; holdout 2026-05-11 – 2026-11-06.

| period | n | up rate | BSS | log loss (base) | hit rate | mean P (sd) |
|---|--:|--:|--:|--:|--:|--:|
| development | 5919 | 55.0% | -0.0013 | 0.6897 (0.6891) | 54.1% | 0.542 (0.027) |
| dev older half | 2959 | 54.3% | -0.0014 | 0.6918 (0.6911) | 52.8% | 0.527 (0.026) |
| dev newer half | 2960 | 55.8% | -0.0013 | 0.6877 (0.6870) | 55.5% | 0.558 (0.018) |
| 2000–2012 (scored) | 2563 | 53.8% | -0.0008 | 0.6927 (0.6923) | 52.0% | 0.522 (0.024) |
| 2013–2026 (scored) | 3356 | 56.0% | -0.0017 | 0.6874 (0.6866) | 55.7% | 0.558 (0.017) |
| holdout | 126 | 50.8% | -0.0030 | 0.6983 (0.6967) | 50.8% | 0.551 (0.004) |

| input | coefficient (final dev model, standardised) | expected | refits with expected sign |
|---|--:|---|--:|
| past5 | -0.0025 | − | 60% |
| downcash | +0.0058 | − | 45% |
| tom | -0.0064 | + | 52% |
| fomc_vxn | +0.0095 | + | 70% |
| lrv | -0.0093 | none | — |

C chosen: final 3e-04, median over refits 1e-04 (small C = strong shrinkage to the base rate). Permutation p = 0.753 (null 95th percentile BSS -0.0001).

Calibration (development, deciles):

| mean P | observed up | n |
|--:|--:|--:|
| 0.487 | 0.559 | 592 |
| 0.513 | 0.530 | 592 |
| 0.527 | 0.551 | 592 |
| 0.538 | 0.546 | 592 |
| 0.546 | 0.570 | 591 |
| 0.551 | 0.542 | 592 |
| 0.555 | 0.559 | 592 |
| 0.559 | 0.573 | 592 |
| 0.565 | 0.527 | 592 |
| 0.582 | 0.546 | 592 |

Calibration (holdout, terciles):

| mean P | observed up | n |
|--:|--:|--:|
| 0.547 | 0.595 | 42 |
| 0.551 | 0.476 | 42 |
| 0.554 | 0.452 | 42 |

Economic sanity (information only): mean next-period return when P > 0.55 vs P < 0.45, and a naive long/short (long if P > 0.5, short otherwise) after costs, against simply being long every period.

| period | mean ret P>0.55 (n) | mean ret P<0.45 (n) | days long | long/short net pts/trade | always long net pts/trade | long/short net total pts |
|---|--:|--:|--:|--:|--:|--:|
| development | +0.051% (2781) | — (0) | 91% | +2.83 | +3.27 | +16,756 |
| holdout | -0.177% (73) | — (0) | 100% | -25.85 | -25.85 | -3,257 |

## Same days as the HAR-RV vol fix

Hourly models on the vol-fix window (304 origins, 19 Aug 2025 – 5 Nov 2026) and its last 250, joining walk-forward development predictions and frozen-model holdout predictions. Information only.

| model | window | n | BSS | hit rate |
|---|---|--:|--:|--:|
| M1 | vol-fix window (2025-08-19 – 2026-11-05) | 303 | +0.0001 | 51.8% |
| M1 | its last 250 (2025-11-03 – 2026-11-05) | 250 | +0.0011 | 51.2% |
| M2 | vol-fix window (2025-08-19 – 2026-11-05) | 302 | -0.0027 | 50.7% |
| M2 | its last 250 (2025-10-31 – 2026-11-05) | 250 | -0.0030 | 51.2% |
| M3 | vol-fix window (2025-08-19 – 2026-11-05) | 302 | -0.0087 | 54.0% |
| M3 | its last 250 (2025-10-31 – 2026-11-05) | 250 | -0.0097 | 52.8% |

## Tier 2 (added one at a time to M2, the best Tier 1 model by development BSS)

fed_hawk and its 2Y-yield proxy are also tried on M1 (the next 24h, which the Fed-odds idea is about), and yld2_chg on the M1 daily proxy since 2000. All of these are in the Holm set.

Each test compares the model with the variable against the same model without it, on the same rows. p = block permutation of the added variable (1,000×); Holm across all Tier 2 tests. A test with fewer than 150 scored development days is "insufficient data" (it still counts in the Holm set, with p = 1).

| test | rows from | dev n | BSS dev (older / newer) | gain vs base (dev) | holdout BSS (gain) | coef (expected) | perm p (Holm) | verdict |
|---|---|--:|--:|--:|--:|--:|--:|---|
| M2+band_pos | 2025-08-04 | 59 | — | — | — | — | — | **insufficient data** |
| M2+har_garch | 2025-08-01 | 69 | — | — | — | — | — | **insufficient data** |
| M2+vxn_chg | 2024-06-26 | 330 | -0.0009 / +0.0020 | -0.0033 | -0.0120 (+0.0094) | +0.008 (+) | 0.470 (1.000) | **coin flip** |
| M2+yld10_chg | 2024-06-27 | 330 | -0.0014 / +0.0043 | -0.0023 | -0.0117 (+0.0097) | +0.022 (−) | 0.360 (1.000) | **coin flip** |
| M2+fed_hawk | 2024-06-27 | 316 | -0.0080 / -0.0024 | -0.0054 | -0.0188 (-0.0027) | -0.083 (−) | 0.899 (1.000) | **coin flip** |
| M2+yld2_chg | 2024-06-27 | 330 | -0.0011 / +0.0089 | +0.0001 | -0.0174 (+0.0040) | -0.063 (−) | 0.138 (1.000) | **coin flip** |
| M1+fed_hawk | 2024-06-27 | 302 | -0.0079 / +0.0016 | -0.0012 | +0.0015 (+0.0012) | -0.058 (−) | 0.825 (1.000) | **coin flip** |
| M1+yld2_chg | 2024-06-27 | 316 | -0.0013 / +0.0031 | +0.0003 | +0.0002 (+0.0000) | -0.000 (−) | 0.160 (1.000) | **coin flip** |
| M1_daily+yld2_chg | 2000-10-19 | 5802 | -0.0009 / -0.0001 | +0.0009 | -0.0117 (-0.0031) | -0.022 (−) | 0.005 (0.045) | **coin flip** |

### Fed odds (Polymarket)

- Events found: 38 "Fed decision" / "Fed Interest Rates" events (Sep 2022 – Apr 2027 meetings), matched to scheduled FOMC decisions by end date; hourly history from the CLOB `/prices-history` endpoint (14-day requests, fidelity 60).
- **First date with usable data: 27 Jun 2024** (the hourly NQ sample starts 26 Jun 2024, so Polymarket covers essentially all of it).
- Days with a usable change: 574 (448 before the holdout); scored in the walk-forward: 316 → enough to test (≥ 150).
- The 2Y-yield proxy is tested on the daily M1 history (FRED DGS2, 2000+) and on the hourly sample (ZT=F).

## Method notes

- Hourly sample: the cached roll-adjusted NQ=F bars production uses (lab/cache/bars_hourly_23h.parquet, 26 Jun 2024 – 06 Nov 2026), one row per 07:00 NY origin. Bars are hourly, so "9am" is the close of the 8am bar, "3pm" the close of the 2pm bar, "4pm" the 3pm bar's close.
- Daily proxy: NQ=F close-to-close since Sep 2000 (Yahoo bar dated d closes 17:00 NY d), predicted at the previous close. Roll days patched with ^NDX (the switch day on Yahoo varies). downcash uses ^NDX open → close (2000's opens are partly stale; scoring starts after the 2-year minimum anyway).
- FOMC: scheduled decision days 2000–2027 from federalreserve.gov (historical pages to 2020, the calendar page after); unscheduled meetings, calls and notation votes excluded.
- No look-ahead: every input is known at the forecast time; models are refitted monthly on data before the month; C is chosen inside the training window; the holdout model is frozen at the end of development.
- Permutation p-values use the whole walk-forward (including the choice of C) on 1,000 block-shuffled series.
- Costs for the long/short check: 1 tick (0.25 pt) + ~$4.50 fees (0.225 pt) per round trip; daily proxy points use the previous NQ=F close.

Run: `python lab/direction.py` (development only) · `python lab/direction.py --holdout` (the one final run).
