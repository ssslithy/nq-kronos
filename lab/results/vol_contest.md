# Part C: volatility contest

250 daily 07:00 NY origins (04 Nov 2025 – 05 Nov 2026), 23-hour horizon (1pm → 1pm London), walk-forward, each model then put through the production calibration (k and measured multipliers from the trailing 250 matured forecasts). Halves = first / last 125 origins.

## Hit rates (target 80 / 90 / 95%)

| model | 80% older | 80% newer | 90% older | 90% newer | 95% older | 95% newer | scored |
|---|--:|--:|--:|--:|--:|--:|--:|
| GARCH | 80.0% | 79.2% | 88.8% | 89.6% | 95.2% | 95.2% | 250 |
| GARCH + VXN | 83.2% | 76.8% | 88.8% | 86.4% | 97.6% | 91.2% | 250 |
| HAR-RV | 81.6% | 80.0% | 90.4% | 88.8% | 93.6% | 96.0% | 250 |
| XGBoost | 83.2% | 81.6% | 90.4% | 88.8% | 96.0% | 94.4% | 250 |
| GARCH + regime | 79.2% | 84.8% | 86.4% | 91.2% | 94.4% | 96.0% | 250 |

## Sharpness and loss

| model | mean 90% width (pts) | vs GARCH | older | newer | QLIKE | older | newer |
|---|--:|--:|--:|--:|--:|--:|--:|
| GARCH | 1,159 | +0.0% | 1,077 | 1,241 | 0.4064 | 0.3080 | 0.5047 |
| GARCH + VXN | 1,089 | -6.1% | 1,078 | 1,100 | 0.2983 | 0.2632 | 0.3335 |
| HAR-RV | 1,133 | -2.2% | 1,035 | 1,231 | 0.2910 | 0.3075 | 0.2745 |
| XGBoost | 1,156 | -0.3% | 1,101 | 1,211 | 0.2836 | 0.2871 | 0.2801 |
| GARCH + regime | 1,172 | +1.1% | 989 | 1,355 | 0.3947 | 0.3324 | 0.4569 |

## Gate C (to replace GARCH, all four)

| model | coverage both halves | ≥ 5% narrower | lower QLIKE | beats GARCH on width and QLIKE in both halves | passes |
|---|---|---|---|---|---|
| GARCH + VXN | yes | yes | yes | no | **no** |
| HAR-RV | yes | no | yes | yes | **no** |
| XGBoost | yes | no | yes | no | **no** |
| GARCH + regime | yes | no | yes | no | **no** |

## Verdict

**GARCH is kept.** No candidate passed all four tests, so the production model is unchanged.

Notes: realised variance = sum of the 23 squared hourly % log returns after the origin (roll-adjusted series). Events are only known from 2026 (the YAML's coverage), so XGBoost sees them as missing before that. VXN: Yahoo `^VXN` daily close dated before the origin's New York date. The GARCH row differs slightly from the production backfill (79.2% / 88.8%) because the contest scores the last 250 origins with a full 23-bar outcome by position, while the backfill matches the horizon-end bar by calendar time (one origin with a missing Yahoo bar drops out). Near miss: GARCH + VXN was 6.1% narrower with lower QLIKE overall, but on the older half its width was not narrower than GARCH, so it fails the both-halves test.

Run: `python lab/regime.py && python lab/vol_contest.py`.
