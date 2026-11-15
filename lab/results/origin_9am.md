# Origin study: 08:00 vs 09:00 New York

A = forecast at 08:00 NY (the 07:00-08:00 bar, current); B = forecast at 09:00 NY (the 08:00-09:00 bar). Same bars (25 Jun 2024 – 13 Nov 2026), same production backfill (HAR-RV, rolling k, measured multipliers, trailing 250, no look-ahead), 316 days scored for both (2025-08-07 – 2026-11-12). Every cell is A / B. Widths are the full 90% range as % of price. The cash session is 09:00-16:00 NY measured from the 09:00 price for both (under B that is the origin price).

| days | n | 24h 80% hit | 24h 90% hit | 24h 90% width % | QLIKE | cash 90% hit (n) | cash 90% width % |
|---|--:|--:|--:|--:|--:|--:|--:|
| all | 316 | 80.4 / 82.0 | 88.6 / 89.2 | 3.78 / 3.83 | 0.331 / 0.316 | 89.1 / 88.7 (256) | 3.03 / 3.03 |
| last 250 | 250 | 80.4 / 82.8 | 89.6 / 90.0 | 4.08 / 4.14 | 0.289 / 0.286 | 89.2 / 88.8 (250) | 3.04 / 3.03 |
| older half | 125 | 81.6 / 84.8 | 91.2 / 91.2 | 3.95 / 4.00 | 0.306 / 0.303 | 88.8 / 88.8 (125) | 2.93 / 2.96 |
| newer half | 125 | 79.2 / 80.8 | 88.0 / 88.8 | 4.22 / 4.28 | 0.272 / 0.269 | 89.6 / 88.8 (125) | 3.16 / 3.11 |

## Release days (08:30 NY: CPI, payrolls, PCE, GDP, retail sales; calendar from 2026)

32 release days, 179 other days since 2026-01-09. On a release day A forecasts before the number and B after it. B's 24-hour window then holds the *next* morning's 08:30 release in its last half hour, so the last row scores B's forecast made the day before a release (A's column there is the release-day forecast again, for comparison).

| days | n | 24h 80% hit | 24h 90% hit | 24h 90% width % | QLIKE | cash 90% hit (n) | cash 90% width % |
|---|--:|--:|--:|--:|--:|--:|--:|
| release days | 32 | 81.2 / 81.2 | 90.6 / 87.5 | 4.25 / 4.41 | 0.221 / 0.261 | 87.5 / 90.6 (32) | 3.12 / 3.17 |
| other days (2026) | 179 | 78.8 / 82.1 | 88.8 / 89.9 | 4.11 / 4.16 | 0.265 / 0.265 | 89.4 / 88.3 (179) | 3.05 / 3.03 |
| release next morning (B, day before) | 32 | 81.2 / 87.5 | 90.6 / 87.5 | 4.25 / 4.11 | 0.221 / 0.395 | 87.5 / 87.5 (32) | 3.12 / 2.98 |

## Gate B

- pass: 24h 80% in 75-85% (both halves)
- pass: 24h 90% in 86-94% (both halves)
- pass: cash 90% in 86-94% (both halves)
- pass: QLIKE no worse than A (last 250)

**Decision: switch to the 09:00 New York origin.**

Run: `python lab/origin_9am.py`.
