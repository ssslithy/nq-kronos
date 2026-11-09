# Vol fix: clustered misses after shocks

Scored on 304 daily 07:00 NY origins (19 Aug 2025 – 05 Nov 2026): every matured origin where all candidates have a production-calibrated forecast (k and measured multipliers from the trailing 250 matured, roll-free forecasts, min 60). Halves = first / last 152 origins. Stress origins: 108 of 304 (previous origin's |move| > 2x its calibrated GARCH sigma, or VXN in the top quartile of its trailing 252 closes; both known at the origin).

## Full sample

| model | 80% older / newer | 90% older / newer | 90% last 60 | misses | Christoffersen LR (p) | miss after miss | stress 90% | calm width (pts) | QLIKE all / older / newer |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| A. GARCH | 82.9 / 79.6 | 90.8 / 90.1 | 91.7 | 29 | 0.23 (0.634) | 52% | 86.1% | 1,085 | 0.4409 / 0.3974 / 0.4845 |
| B. GARCH + floor 5d | 81.6 / 78.3 | 88.8 / 87.5 | 90.0 | 36 | 1.70 (0.192) | 44% | 87.0% | 974 | 0.4320 / 0.4094 / 0.4546 |
| B. GARCH + floor 10d | 82.2 / 75.7 | 88.2 / 86.2 | 90.0 | 39 | 0.22 (0.636) | 49% | 85.2% | 988 | 0.4398 / 0.4250 / 0.4547 |
| C. GARCH + VXN | 88.8 / 76.3 | 92.8 / 86.2 | 88.3 | 32 | 0.70 (0.404) | 50% | 88.0% | 1,003 | 0.3560 / 0.3966 / 0.3153 |
| D. HAR-RV | 82.2 / 78.9 | 89.5 / 89.5 | 88.3 | 32 | 2.38 (0.123) | 38% | 92.6% | 946 | 0.3314 / 0.3893 / 0.2736 |
| E. Two-speed GARCH | 77.6 / 78.9 | 84.2 / 88.8 | 91.7 | 41 | 0.44 (0.507) | 46% | 85.2% | 908 | 0.4001 / 0.4186 / 0.3817 |

Christoffersen: LR of independence of the 90% misses, chi-squared(1); p < 0.05 means the misses cluster. Miss after miss: share of 90% misses with another miss in the previous 5 origins.

## Last 250 origins (04 Nov 2025 – 05 Nov 2026)

| model | 80% older / newer | 90% older / newer | 90% last 60 | misses | Christoffersen LR (p) | miss after miss | stress 90% | calm width (pts) | QLIKE all / older / newer |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| A. GARCH | 80.0 / 79.2 | 88.8 / 89.6 | 91.7 | 27 | 0.32 (0.569) | 56% | 85.3% | 1,117 | 0.4064 / 0.3080 / 0.5047 |
| B. GARCH + floor 5d | 78.4 / 78.4 | 86.4 / 87.2 | 90.0 | 33 | 1.85 (0.174) | 48% | 86.3% | 1,009 | 0.4070 / 0.3316 / 0.4825 |
| B. GARCH + floor 10d | 79.2 / 75.2 | 84.0 / 87.2 | 90.0 | 36 | 0.32 (0.572) | 50% | 84.3% | 1,026 | 0.4161 / 0.3542 / 0.4780 |
| C. GARCH + VXN | 83.2 / 76.8 | 88.8 / 86.4 | 88.3 | 31 | 1.21 (0.272) | 52% | 87.3% | 1,008 | 0.2983 / 0.2632 / 0.3335 |
| D. HAR-RV | 81.6 / 80.0 | 90.4 / 88.8 | 88.3 | 26 | 1.54 (0.215) | 42% | 92.2% | 1,035 | 0.2910 / 0.3075 / 0.2745 |
| E. Two-speed GARCH | 77.6 / 78.4 | 83.2 / 89.6 | 91.7 | 34 | 0.74 (0.391) | 50% | 85.3% | 984 | 0.3816 / 0.3725 / 0.3908 |

## Gate (full sample; to replace GARCH all five must hold)

| model | 1 coverage both halves | 2 clustering better | 3 stress ≥ 85% and ≥ GARCH + 5 | 4 calm width ≤ +5% | 5 QLIKE ≤ GARCH all and halves | passes |
|---|---|---|---|---|---|---|
| B. GARCH + floor 5d | yes | yes (p 0.192, 44% vs 52%) | no (87.0% vs 86.1%) | yes (-10.2%) | no | **no** |
| B. GARCH + floor 10d | yes | yes (p 0.636, 49% vs 52%) | no (85.2% vs 86.1%) | yes (-8.9%) | no | **no** |
| C. GARCH + VXN | no | yes (p 0.404, 50% vs 52%) | no (88.0% vs 86.1%) | yes (-7.6%) | yes | **no** |
| E. Two-speed GARCH | no | yes (p 0.507, 46% vs 52%) | no (85.2% vs 86.1%) | yes (-16.3%) | no | **no** |
| D. HAR-RV | yes | yes (p 0.123, 38% vs 52%) | yes (92.6% vs 86.1%) | yes (-12.8%) | yes | **yes** |

## Verdict

**D. HAR-RV replaces GARCH** (the only candidate that passes all five).

## Candidates

- A. GARCH: production (hourly GARCH(1,1)-t on 2,000 returns, 23-step analytic sigma).
- B. GARCH + daily floor: sigma = max(sigma_GARCH, c x RV_nd), RV_nd = RMS of the last n non-overlapping 23-bar % returns ending at the origin; c (grid 0–3, step 0.05) minimises best-scale QLIKE over the trailing 250 matured origins (min 100).
- C. GARCH + VXN: log blend with VXN / sqrt(252), weight fitted the same way (see vol_contest.md).
- D. HAR-RV: log RV(next 23h) on log RV of the last 1 / 5 / 22 days, OLS on the trailing 250 (min 100).
- E. Two-speed GARCH: log sigma = w log sigma_GARCH(hourly) + (1 - w) log sigma_daily, sigma_daily = one-step forecast of GARCH(1,1)-t on the last 120–500 non-overlapping 23-bar returns ending at the origin; w (grid 0–1, step 0.05) fitted like B.

- B. GARCH + floor 5d: fitted c median 1.35, range 1.05–1.55
- B. GARCH + floor 10d: fitted c median 1.25, range 0.75–1.85
- E. Two-speed GARCH: fitted w (weight on hourly GARCH) median 0.40, range 0.25–0.55

Run: `python lab/daily_origins.py && python lab/vol_contest.py --fix`.

## Production check (HAR-RV live, `VOL_MODEL = "har"` in forecast/garch_vol.py)

Production `har_sigmas()` reproduces this script's candidate D to 1e-12 on every origin, and
truncating the bars at an origin gives the same sigma (no look-ahead).
`python forecast/vol_accuracy.py --backfill --all` (250 matured, 3 Nov 2025 – 5 Nov 2026):

| check | target | older / newer half | result |
|---|---|---|---|
| Gate A 80% | 75–85% | 80.0% / 79.2% | pass |
| Gate A 90% | 86–94% | 90.4% / 88.0% | pass |
| Cash 90% (191 sessions with a measured multiplier) | 86–94% | 87.4% / 91.7% | pass |

The sell-off: 29 Oct and 5 Nov still broke the 90% range (−2.8%, −4.4%); 2 Nov is now inside.
HAR-RV sigma on 6 Nov was 2.53% vs GARCH's 1.07%.
