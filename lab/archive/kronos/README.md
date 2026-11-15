# Kronos (retired 15 Nov 2026)

Kronos-small ran once a day from the same bar as the vol forecast and could be overlaid on the
chart (its P10 and P90 paths). It was retired when the origin moved to 9:00am New York
(NQ_9AM_ORIGIN.md, decisions): its 80% band held only ~46% of the time in the Phase 6 lab
(target 80%; `lab/results/report.md`) and the page never used it for the ranges.

What was removed: the `kronos` and `deploy-kronos` jobs in `.github/workflows/garch_vol.yml`,
the manual Kronos run in `.github/workflows/forecast.yml` (now page deploys only), the chart
toggle and the `latest.json` / `status.json` loading on the page, and the live Kronos line on
the accuracy page.

What is here:

| File | What it was |
|---|---|
| `run_forecast.py` | the Kronos run (100 paths, `--origin`, `--mark-stale`) |
| `backfill_history.py` | seeded `history.jsonl` with past Kronos forecasts |
| `band_check.py` | scored Kronos's P10–P90 against actual closes |
| `requirements.txt` | its pinned set, including CPU torch |
| `data/latest.json`, `data/status.json` | the last live Kronos forecast (from the 08:00–09:00 New York bar of 13 Nov 2026, 13:00 UTC) and run status |
| `data/history.jsonl`, `data/band_check.json` | every Kronos forecast and its band check |

Run from the repo root (needs the `vendor/kronos` submodule and torch); it writes only to
`lab/archive/kronos/data/`, never to `site/data/`:

```bash
pip install -r lab/archive/kronos/requirements.txt
python lab/archive/kronos/run_forecast.py --origin 2026-11-13T13:00:00Z --horizon 23
```

`vendor/kronos` stays in the repo (and its git history) for the Phase 6 lab (`lab/build_forecasts.py`).
