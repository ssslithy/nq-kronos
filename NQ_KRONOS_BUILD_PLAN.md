# NQ Kronos Forecast: Build Plan

A standalone web page showing Nasdaq-100 futures (NQ) hourly candles, with a Kronos-generated fan of possible paths for the next 24 hours. It updates itself every hour while the market is open and costs nothing to run.

This file is the spec. Put it in an empty folder, open Claude Code or Codex there, and say:
> Read NQ_KRONOS_BUILD_PLAN.md and build it phase by phase. Stop after each phase, show me it working, and wait for my OK before the next one.

Phases 1–5 are the website. Phase 6 is a separate offline strategy lab that tests trading rules built on the forecasts.

---

## 1. What it does

- Pulls the latest NQ hourly bars (OHLCV).
- Feeds the last 512 bars to Kronos-small.
- Generates **100 sampled future paths**, 24 hourly steps each.
- Shows on one page:
  - recent candles (last ~5 trading days)
  - a "NOW" line
  - all 100 paths as faint lines (green above the last close, red below)
  - the **median path** in gold
  - the **10th–90th percentile band** as a shaded area
  - labels for the median, upper (P90) and lower (P10) at +24h
  - side panel: model, parameters, context length, horizon, samples, last update time (London and New York), data source
- Refreshes automatically every hour during NQ trading hours.

Not in scope: trading, alerts, accounts, user input beyond a few toggles.

## 2. Stack (all free)

| Piece | Choice | Notes |
|---|---|---|
| Language | Python 3.11 | Kronos is PyTorch; 3.11 is the safe version for it |
| Data | `yfinance`, ticker `NQ=F`, interval `1h` | Delayed, unofficial; about 730 days of hourly history |
| Model | Kronos-small from github.com/shiyu-coder/Kronos (weights `NeoQuasar/Kronos-small`, tokenizer `NeoQuasar/Kronos-Tokenizer-base`) | ~25M parameters; runs on CPU |
| Scheduler | GitHub Actions cron | Free on a **public** repo |
| Hosting | GitHub Pages | Free on a public repo |
| Front end | One static `index.html` + vanilla JS + `<canvas>`, or TradingView `lightweight-charts` for candles with a canvas overlay for the fan | No framework, no build step |

## 3. Repo layout

```
nq-kronos/
├── forecast/
│   ├── fetch.py          # pull + clean + roll-adjust NQ bars
│   ├── calendar.py       # NQ trading hours, future timestamps, roll dates
│   ├── run_forecast.py   # load Kronos, sample paths, write JSON
│   └── requirements.txt  # pinned versions
├── vendor/kronos/        # Kronos repo as a git submodule, pinned to a commit
├── site/
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── data/
│       ├── latest.json   # current forecast + recent candles
│       └── history.jsonl # one line per past forecast (for Phase 4)
├── lab/                  # Phase 6: offline strategy research, not part of the site
│   ├── build_forecasts.py
│   ├── baselines.py
│   ├── strategies.py
│   ├── backtest.py
│   ├── cache/            # saved historical forecasts (gitignored)
│   └── results/          # report.md + charts
├── .github/workflows/forecast.yml
└── README.md
```

## 4. Phases

### Phase 1: Data (`fetch.py`, `calendar.py`)
1. Download `NQ=F` at `1h` for the last 60 days (enough for 512 bars plus buffer).
2. Normalise to UTC timestamps; columns `open, high, low, close, volume`. Add `amount = close × volume`, since Kronos expects an `amount` column. Check the repo README for the exact required columns.
3. Drop the in-progress bar (the current, incomplete hour).
4. **Trading calendar:** NQ trades Sunday 18:00 to Friday 17:00 New York time, with a daily break 17:00–18:00 NY. Write a function that, given the last bar time, returns the next 24 *tradable* hourly timestamps, skipping the daily break and the weekend. Handle US daylight-saving correctly, using `zoneinfo` with `America/New_York`.
5. **Roll adjustment:** NQ rolls quarterly (Mar/Jun/Sep/Dec, around the 2nd Thursday). Yahoo's continuous series can jump at the roll.
   - Build a roll-date calendar.
   - If a roll falls inside the 512-bar window, back-adjust earlier bars by the price gap at the roll (additive).
   - Store a `roll_adjusted: true/false` flag and the roll date for the UI.
6. Basic sanity checks: no duplicate timestamps, no zero/NaN prices, at least 512 bars. If any check fails, exit non-zero and **do not overwrite** the last good forecast.

**Done when:** `python forecast/fetch.py` prints the last 5 bars and the next 24 forecast timestamps, and they look right in both London and New York time.

### Phase 2: Forecast (`run_forecast.py`)
1. Add Kronos as a submodule pinned to a specific commit. Read its README and use its own predictor class (tokenizer + model + predictor). Don't reimplement.
2. Load Kronos-small and the tokenizer from Hugging Face; cache weights in the Actions cache so they aren't re-downloaded every run.
3. Context: last 512 bars. Horizon: 24. Sampling: temperature `T=1.0`, `top_p=0.9`.
4. **Generate 100 separate paths.** If the predictor averages over `sample_count`, call it with `sample_count=1` in a loop (or batched) and keep each path. Set a fixed seed per run so a rerun reproduces the same fan.
5. From the paths compute, per future step: median, P10, P25, P75, P90 of `close`.
6. Write `site/data/latest.json`:
   ```json
   {
     "generated_utc": "...", "last_bar_utc": "...", "last_close": 0,
     "model": "Kronos-small", "params": "24.7M", "context": 512, "horizon": 24, "samples": 100,
     "roll_adjusted": false, "roll_date": null, "data_source": "Yahoo Finance (NQ=F), delayed",
     "candles": [{"t": "...", "o": 0, "h": 0, "l": 0, "c": 0, "v": 0}],
     "future_t": ["..."],
     "paths": [[0, 0]],
     "quantiles": {"p10": [], "p25": [], "p50": [], "p75": [], "p90": []}
   }
   ```
   Round prices to 2 dp to keep the file small. `candles` holds only the last ~120 bars for display.
7. Append one line to `site/data/history.jsonl` with: generated time, last close, and p10/p50/p90 at +1h, +6h, +12h and +24h.
8. Log the runtime. Target: under 3 minutes on a GitHub Actions CPU runner. If slower, reduce to 50 samples and note it.

**Done when:** running `python forecast/run_forecast.py` locally writes a valid `latest.json` and prints the median and P10/P90 at +24h.

### Phase 3: Page (`site/`)
1. Dark theme in the style of the reference dashboard (near-black background, teal accents, monospaced labels).
2. Chart:
   - candles on the left, a vertical "NOW" line at the last bar
   - to the right of NOW: 100 faint paths (alpha ~0.15; green if ending above the last close, red if below)
   - shaded P10–P90 band, gold median line
   - right-edge labels for P90, median and P10 at +24h, plus the last close
3. Side panel: model info, context/horizon/samples, last update in London and New York time, "data delayed" note, a roll-adjustment badge when relevant.
4. Bottom strip: "Next 24h" summary: median change in points and %, the P10–P90 range in points, and the share of paths ending above the last close.
5. Toggles: show/hide individual paths; horizon view 7h / 24h.
6. Reads `data/latest.json`; polls every 5 minutes for a newer `generated_utc`.
7. Fully usable on a phone (stacked layout below ~700px wide).
8. Footer disclaimer: "Experimental model output on delayed data. Not investment advice."

**Done when:** opening the page via a local server (`python -m http.server -d site`) shows the chart from the Phase 2 JSON, on desktop and phone widths.

### Phase 4 (optional): Band check
A small panel, no backtest engine.
1. For each line in `history.jsonl` whose +24h time has passed, look up the actual NQ close at that time.
2. Show over the last 30 matured forecasts:
   - **Band hit rate:** % of times the actual close landed inside P10–P90. About 80% means the fan is well calibrated; much lower means it is overconfident.
   - **Median error:** average absolute points between the median and the actual close, next to a naive "no change" forecast's error.
3. Show "collecting data, N/30" until enough forecasts have matured.

**Done when:** the panel shows numbers that can be checked by hand against two or three history lines.

### Phase 5: Automate and publish
1. `.github/workflows/forecast.yml`:
   - schedule: hourly at minute 7 (`7 * * * *`, UTC); the script itself exits early if NQ is closed (weekend or daily break)
   - steps: checkout with submodules, set up Python 3.11, restore pip and Hugging Face caches, run `run_forecast.py`, commit `site/data/*` only if changed, push
   - `workflow_dispatch` so it can be run manually
2. Enable GitHub Pages from the `site/` folder (via Actions deploy or a `/docs` alias).
3. README: what it is, how to run locally, how to change samples/horizon, data and model credits, disclaimer.

**Done when:** the Actions tab shows a green run, the Pages URL loads, and `generated_utc` advances on the next market hour.

### Phase 6: Strategy lab (offline, runs on the Mac, not on the site)
Goal: find out whether Kronos's forecasts add anything a simple GARCH model doesn't, using rules decided **before** looking at results.

**6a. Historical forecasts (`build_forecasts.py`)**
1. Use the same fetch and roll-adjust code as Phase 1, over all available hourly history (~2 years).
2. Walk forward through the last **6 months** first. At every **3rd** closed bar, generate a Kronos forecast using only bars up to that point: 512 context, 24 horizon, **30 samples**.
3. Save each forecast's quantiles (p10/p25/p50/p75/p90 at +1…+24h) and the share of paths ending up, to `lab/cache/kronos.parquet`. Reruns skip what's already cached.
4. Before the full run, time 10 forecasts, **print the estimated total runtime and wait for my OK**. Use Apple GPU (`mps`) if available, otherwise CPU.
5. Only after the 6-month results exist, ask whether to extend to the full 2 years.

**6b. Baselines (`baselines.py`)**
- **GARCH(1,1)** with Student-t errors (the `arch` library), refitted on a rolling window using only past data. Simulate 1,000 paths for the same timestamps and store the same quantiles.
- **Naive:** "no change" median, with a band from the trailing 20-day realised volatility.

**6c. Strategies (`strategies.py`)**, each run three times: with Kronos bands, GARCH bands, naive bands.
1. **Vol-sized trend:** a simple fixed rule (e.g. 20/100-bar moving-average crossover, long/short) where position size = target £ risk ÷ forecast band width. Compare with the same rule at fixed size.
2. **Distribution stops/targets:** same entry rule; stop just beyond P10 (P90 for shorts), target at P75 (P25); skip trades where reward ÷ risk < 1.
3. **Band break, fade vs follow:** if price closes outside P10–P90 within 6 hours of a forecast, (a) fade toward the median, or (b) follow the break. Fixed 6-hour exit, plus a stop at 1 band-width.

**6d. Backtest rules (`backtest.py`)**
- Signals use closed bars only; entries at the **next bar's open**.
- Instrument: MNQ ($2 per point). Costs: 1 tick slippage each way + $1.00 commission per side; double the slippage for bars between 22:00 and 07:00 London.
- Out-of-sample discipline: any parameter chosen on the first two-thirds of the period is reported separately on the final third.

**6e. Output (`lab/results/`)**
- `report.md` with one table per strategy: trades, win rate, avg trade (points and $), total net $, Sharpe (annualised, hourly), max drawdown, plus the in-sample/out-of-sample split.
- **Calibration table:** for Kronos, GARCH and naive, the % of times the actual price landed inside P10–P90 at +1h, +6h, +24h (target ≈ 80%), plus average band width.
- Equity-curve chart per strategy, all three band sources on one plot.
- A short plain-English verdict: does Kronos beat GARCH on calibration, and on any strategy after costs? State clearly if the answer is no.

**Done when:** `python lab/backtest.py` regenerates the report from cached forecasts in under a minute.

Not investment advice: the lab is for research, and any strategy that looks good goes to a demo account (e.g. a NAS100 CFD on cTrader) before real money.

## 5. Ground rules for the agent
- Pin every dependency version and the Kronos commit.
- Never commit API keys (none are needed).
- If a data fetch or forecast fails, keep the previous `latest.json` and mark the page "stale since …" rather than showing a broken chart.
- Check Kronos's licence in its repo and keep its attribution in the README and footer.
- GitHub's hourly cron can start a few minutes late; that's fine.

## 6. Known limits (say so on the page or README)
- Yahoo data is delayed and unofficial; it can have gaps or revisions.
- Hourly direction on NQ is close to unpredictable for any model. Read the fan's **width** (expected range), not the median, as the main signal.
- Kronos's published benchmarks come from its authors and are not yet independently replicated.

## 7. Cost
£0: public GitHub repo, Actions, Pages, Yahoo data and Kronos are all free. An optional custom domain is about £10 a year.
