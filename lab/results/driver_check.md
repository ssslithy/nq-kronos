# Driver check: did the morning snapshot catch what moved NQ by the close?

12 trading days with a morning snapshot (2pm London; 1pm until 13 Nov 2026) and closing wraps (2026-11-10 – 2026-11-25). Drivers = companies and themes named in the day's closing market wraps; caught = also named in the snapshot's news items, the morning brief, or a Since-US-close cell. For review only: no rule is changed automatically.

**Caught by the snapshot: 50 of 74 drivers (68%).**

| driver | named at the close (days) | caught by the snapshot | missed |
|---|--:|--:|--:|
| Nvidia | 12 | 12 | 0 |
| ai_chips | 11 | 11 | 0 |
| earnings | 8 | 7 | 1 |
| amd | 5 | 2 | 3 |
| inflation | 5 | 4 | 1 |
| Tesla | 4 | 2 | 2 |
| fed | 3 | 1 | 2 |
| china | 3 | 3 | 0 |
| Amazon | 3 | 1 | 2 |
| yields | 2 | 1 | 1 |
| Alphabet | 2 | 0 | 2 |
| advanced micro devices | 2 | 0 | 2 |
| vix | 2 | 2 | 0 |
| tariffs | 2 | 2 | 0 |
| costco | 1 | 0 | 1 |
| intel | 1 | 0 | 1 |
| cisco | 1 | 0 | 1 |
| oil | 1 | 1 | 0 |
| Microsoft | 1 | 1 | 0 |
| analog devices | 1 | 0 | 1 |
| Apple | 1 | 0 | 1 |
| Broadcom | 1 | 0 | 1 |
| Meta | 1 | 0 | 1 |
| on semiconductor | 1 | 0 | 1 |

Most common misses: amd (3), Alphabet (2), Tesla (2), advanced micro devices (2), Amazon (2).

Run: `python lab/driver_check.py` (the evening workflow does it).
