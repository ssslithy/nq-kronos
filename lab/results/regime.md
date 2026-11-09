# Part D: regime filter (2-state Gaussian HMM)

Origins: the last 250 matured daily 07:00 NY origins (04 Nov 2025 – 05 Nov 2026). Daily NQ=F returns since 2000, rolls de-jumped; weekly refits on the last 756 days (107 fits, 5 starts each); filtered probabilities only. Bull = lower-variance state; it was also the higher-mean state in 107 of 107 fits.

## Gate D

| test | result | needed | pass |
|---|---|---|---|
| 1 stable: median run length | 3.5 trading days (14 runs; Bull 208 / Bear 42 days) | ≥ 10 | no |
| 2 meaningful: Bear / Bull next-23h realised vol | 1.36 (95% block bootstrap 1.04–1.52) | ≥ 1.3, interval > 1.0 | yes |
| 3 honest probability | 3 bucket(s) judged | within ±10 pts | yes |

Test 3 detail (stated state's probability vs the share of the next 20 origins later filtered into that state):

| bucket | n | mean stated prob | later same state | within ±10 pts |
|---|--:|--:|--:|---|
| 0.5–0.7 | 18 | 63% | 55% | yes |
| 0.7–0.9 | 40 | 82% | 72% | yes |
| 0.9–1.0 | 172 | 98% | 90% | yes |

## Direction (not shown on the page unless clearly significant in both halves)

| part | n | hit rate (Bull → up, Bear → down) | binomial p | rank corr P(Bull) vs next-23h move | p |
|---|--:|--:|--:|--:|--:|
| all | 250 | 49.2% | 0.624 | -0.068 | 0.285 |
| older half | 125 | 52.8% | 0.296 | -0.248 | 0.005 |
| newer half | 125 | 45.6% | 0.858 | +0.009 | 0.921 |

Direction significant in both halves: **no**. No direction is shown anywhere.

## Verdict

**Gate D failed**: the regime chip is hidden (no placeholder).
Why: states flip too often (median run 3.5 days < 10).

Latest origin Thu 05 Nov 2026: P(Bull) = 0.39.

Run: `python lab/regime.py`.
