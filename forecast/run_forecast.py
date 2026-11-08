"""Run Kronos-small on the latest NQ hourly bars and write the site's forecast JSON.

    python forecast/run_forecast.py                 # 100 paths, 24h, CPU
    python forecast/run_forecast.py --samples 50
    python forecast/run_forecast.py --origin 2026-11-09T13:00:00Z   # the daily run (garch_vol.yml)

Daily schedule: garch_vol.yml runs this once per US trading day with --origin = the start of
the 07:00-08:00 NY bar the GARCH forecast used, so both models forecast from the same bar.

On any data or model failure the previous site/data/latest.json is left untouched,
site/data/status.json records the error, and the script exits non-zero.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "forecast"))
sys.path.insert(0, str(ROOT / "vendor" / "kronos"))

import band_check  # noqa: E402
import fetch  # noqa: E402
import nq_calendar as cal  # noqa: E402

log = logging.getLogger("forecast")

DATA_DIR = ROOT / "site" / "data"
LATEST = DATA_DIR / "latest.json"
HISTORY = DATA_DIR / "history.jsonl"
STATUS = DATA_DIR / "status.json"
BAND_CHECK = DATA_DIR / "band_check.json"

MODEL_REPO, MODEL_REV = "NeoQuasar/Kronos-small", "901c26c1332695a2a8f243eb2f37243a37bea320"
TOKENIZER_REPO, TOKENIZER_REV = "NeoQuasar/Kronos-Tokenizer-base", "0e0117387f39004a9016484a186a908917e22426"
# Sampling costs ~1.5 s per path on an Apple-silicon CPU (Kronos recomputes the full
# 512-bar context every step), so 100 paths take ~3-4 min; that's accepted (Actions
# minutes are free on a public repo, budget ~6 min). Sampled in batches of 50.
CONTEXT, HORIZON, SAMPLES = 512, 24, 100
T, TOP_P = 1.0, 0.9
CANDLES_SHOWN = 120
HISTORY_STEPS = (1, 6, 12, 24)
DATA_SOURCE = "Yahoo Finance (NQ=F), delayed"


def iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def r2(a):
    return np.round(np.asarray(a, dtype=float), 2).tolist()


def write_json_atomic(path: Path, obj, **kw) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, **kw))
    os.replace(tmp, path)


def write_status(ok: bool, error: str | None = None) -> None:
    """status.json: last run outcome, plus the NQ session calendar for the next 14 days so
    the page can say 'market closed · next update …' without its own holiday logic."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    spans = cal.sessions((now - pd.Timedelta(days=1)).to_pydatetime(),
                         (now + pd.Timedelta(days=14)).to_pydatetime())
    write_json_atomic(STATUS, {"ok": ok, "checked_utc": iso(now), "error": error,
                               "sessions_utc": [[iso(a), iso(b)] for a, b in spans]}, indent=1)


def load_predictor(device: str):
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer  # vendor/kronos

    tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_REPO, revision=TOKENIZER_REV)
    model = Kronos.from_pretrained(MODEL_REPO, revision=MODEL_REV)
    tokenizer.eval(), model.eval()
    n_params = sum(p.numel() for p in model.parameters())
    torch.set_grad_enabled(False)
    return KronosPredictor(model, tokenizer, device=device, max_context=CONTEXT), n_params


def sample_paths(predictor, ctx: pd.DataFrame, future: list, n: int, seed: int,
                 batch: int = 50) -> np.ndarray:
    """n independent close-price paths, shape (n, horizon).

    KronosPredictor.predict averages over sample_count, so instead we use its own
    predict_batch with the same context repeated and sample_count=1: every batch row
    samples its own path and nothing is averaged.
    """
    import torch

    torch.manual_seed(seed)
    np.random.seed(seed)
    # Kronos derives minute/hour/weekday/day/month features from these; use exchange time.
    x_ts = pd.Series(ctx.index.tz_convert(cal.NY))
    y_ts = pd.Series(pd.DatetimeIndex(future).tz_convert(cal.NY))
    x_df = ctx[["open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)
    paths = []
    for start in range(0, n, batch):
        k = min(batch, n - start)
        out = predictor.predict_batch(df_list=[x_df] * k, x_timestamp_list=[x_ts] * k,
                                      y_timestamp_list=[y_ts] * k, pred_len=len(future),
                                      T=T, top_p=TOP_P, sample_count=1, verbose=False)
        paths.extend(o["close"].to_numpy() for o in out)
    return np.vstack(paths)


def build_outputs(bars: fetch.Bars, paths: np.ndarray, future: list, meta: dict) -> tuple[dict, dict]:
    ctx = bars.context(CONTEXT)
    last_t, last_close = ctx.index[-1], float(ctx["close"].iloc[-1])
    q = quantiles(paths)
    rolls = bars.rolls_in_context(CONTEXT)
    shown = bars.df.tail(CANDLES_SHOWN)
    latest = {
        "generated_utc": meta["generated_utc"],
        "last_bar_utc": iso(last_t),
        "last_close": round(last_close, 2),
        "model": "Kronos-small",
        "params": f"{meta['n_params'] / 1e6:.1f}M",
        "context": len(ctx),
        "horizon": len(future),
        "samples": int(paths.shape[0]),
        "sampling": {"T": T, "top_p": TOP_P, "seed": meta["seed"]},
        "roll_adjusted": bool(rolls),
        "roll_date": rolls[-1].roll_date if rolls else None,
        "roll_method": rolls[-1].method if rolls else None,   # "contract" | "estimated"
        "roll_gap": rolls[-1].gap if rolls else None,
        "data_source": DATA_SOURCE,
        "runtime_s": meta["runtime_s"],
        "candles": [{"t": iso(t), "o": round(r.open, 2), "h": round(r.high, 2), "l": round(r.low, 2),
                     "c": round(r.close, 2), "v": int(r.volume)} for t, r in shown.iterrows()],
        "future_t": [iso(t) for t in future],
        "paths": [r2(p) for p in paths],
        "quantiles": {k: r2(v) for k, v in q.items()},
        "share_up": round(float((paths[:, -1] > last_close).mean()), 3),
    }
    hist = history_line(meta["generated_utc"], last_t, last_close, q, future, paths.shape[0])
    return latest, hist


def quantiles(paths: np.ndarray) -> dict:
    return {f"p{p}": np.percentile(paths, p, axis=0) for p in (10, 25, 50, 75, 90)}


def history_line(generated_utc: str, last_t, last_close: float, q: dict, future: list,
                 samples: int, **extra) -> dict:
    hist = {"generated_utc": generated_utc, "last_bar_utc": iso(last_t),
            "last_close": round(float(last_close), 2), "samples": int(samples), **extra}
    for h in HISTORY_STEPS:
        if h <= len(future):
            hist[f"h{h}"] = {"t": iso(future[h - 1]), "p10": round(float(q["p10"][h - 1]), 2),
                             "p50": round(float(q["p50"][h - 1]), 2),
                             "p90": round(float(q["p90"][h - 1]), 2)}
    return hist


def write_band_check(bars: fetch.Bars, now_utc: str) -> dict:
    bc = band_check.evaluate(band_check.read_history(HISTORY), bars.df, bars.rolls, now_utc)
    write_json_atomic(BAND_CHECK, bc, indent=1)
    return bc


def append_history(line: dict) -> str:
    """One line per last bar. A rerun on the same last bar replaces that line (so history
    always matches the published latest.json) instead of adding a duplicate."""
    lines = [l for l in HISTORY.read_text().splitlines() if l.strip()] if HISTORY.exists() else []
    keep = [l for l in lines if json.loads(l).get("last_bar_utc") != line["last_bar_utc"]]
    action = "replaced (same last bar)" if len(keep) < len(lines) else "appended"
    keep.append(json.dumps(line))
    tmp = HISTORY.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(keep) + "\n")
    os.replace(tmp, HISTORY)
    return action


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=int(os.environ.get("NQ_SAMPLES", SAMPLES)))
    ap.add_argument("--horizon", type=int, default=int(os.environ.get("NQ_HORIZON", HORIZON)))
    ap.add_argument("--device", default=os.environ.get("KRONOS_DEVICE", "cpu"))
    ap.add_argument("--seed", type=int, default=None, help="default: derived from the last bar time")
    ap.add_argument("--origin", help="forecast from the bar starting at this UTC time (it must be the "
                                      "last closed bar); history lines get daily=true")
    ap.add_argument("--mark-stale", metavar="REASON",
                    help="don't forecast: mark the current latest.json stale and record REASON in status.json "
                         "(used by the daily workflow when Kronos fails or times out)")
    ap.add_argument("--skip-if-closed", action="store_true",
                    help="exit 0 if NQ is closed now and no bar closed in the last hour")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.mark_stale:
        if LATEST.exists():
            latest = json.loads(LATEST.read_text())
            latest["stale"] = True
            latest["stale_reason"] = args.mark_stale
            write_json_atomic(LATEST, latest, separators=(",", ":"))
        write_status(False, args.mark_stale)
        print(f"Kronos marked stale: {args.mark_stale}")
        return 0

    if args.skip_if_closed:
        now = pd.Timestamp.now(tz="UTC")
        just_closed = cal.is_tradable_hour((now - pd.Timedelta(hours=1)).floor("h"))
        if not cal.is_market_open(now.to_pydatetime()) and not just_closed:
            print("NQ is closed (weekend, holiday or daily break) and no bar just closed; nothing to do.")
            return 0
        # The run right after the daily break / Friday close still goes ahead, so the
        # session's final bar gets a forecast.

    t0 = time.perf_counter()
    origin = pd.Timestamp(args.origin) if args.origin else None
    try:
        bars = fetch.load_bars(now=(origin + pd.Timedelta(hours=1)).to_pydatetime() if origin is not None else None)
        if origin is not None and bars.df.index[-1] != origin:
            raise fetch.DataError(f"origin bar {iso(origin)} is not the last closed bar "
                                  f"(last is {iso(bars.df.index[-1])})")
    except fetch.DataError as e:
        log.error("data check failed, keeping previous forecast: %s", e)
        write_status(False, f"data: {e}")
        return 1
    ctx = bars.context(CONTEXT)
    last_t = ctx.index[-1]
    future = cal.next_tradable_hours(last_t.to_pydatetime(), args.horizon)
    seed = args.seed if args.seed is not None else int(last_t.timestamp() // 3600) % (2**31)
    t_data = time.perf_counter() - t0

    try:
        predictor, n_params = load_predictor(args.device)
        t_load = time.perf_counter() - t0 - t_data
        paths = sample_paths(predictor, ctx, future, args.samples, seed)
        if paths.shape != (args.samples, args.horizon) or not np.isfinite(paths).all():
            raise RuntimeError(f"bad model output: shape {paths.shape}, finite={np.isfinite(paths).all()}")
    except Exception as e:
        log.exception("forecast failed, keeping previous forecast")
        write_status(False, f"model: {e!r}")
        return 1
    t_total = time.perf_counter() - t0
    t_model = t_total - t_data - t_load

    meta = {"generated_utc": iso(pd.Timestamp.now(tz="UTC")), "n_params": n_params,
            "seed": seed, "runtime_s": round(t_total, 1)}
    latest, hist = build_outputs(bars, paths, future, meta)
    if origin is not None:
        latest["daily"] = hist["daily"] = True
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(LATEST, latest, separators=(",", ":"))
    appended = append_history(hist)
    bc = write_band_check(bars, meta["generated_utc"])
    write_status(True)

    q, lc = latest["quantiles"], latest["last_close"]
    print(f"\nKronos-small ({latest['params']} params), context {len(ctx)}, horizon {args.horizon}, "
          f"{args.samples} paths, T={T} top_p={TOP_P}, seed {seed}, device {args.device}")
    print(f"last bar  {cal.fmt_times(last_t)}   close {lc:.2f}")
    print(f"+{args.horizon}h      {cal.fmt_times(future[-1])}")
    for k in ("p90", "p75", "p50", "p25", "p10"):
        v = q[k][-1]
        print(f"  {k.upper():>3} {'(median)' if k == 'p50' else '        '} {v:10.2f}  "
              f"({v - lc:+8.2f} pts, {100 * (v / lc - 1):+.2f}%)")
    print(f"  P10-P90 range at +{args.horizon}h: {q['p90'][-1] - q['p10'][-1]:.2f} pts;  "
          f"paths ending above last close: {100 * latest['share_up']:.0f}%")
    print(f"roll_adjusted={latest['roll_adjusted']} roll_method={latest['roll_method']}")
    size = LATEST.stat().st_size
    print(f"wrote {LATEST.relative_to(ROOT)} ({size / 1024:.0f} KB); history {appended}; status ok")
    s = bc["h24"]
    print(f"band check: {bc['matured_24h']} matured +24h forecasts"
          + (f"; last {s['n']}: hit rate {100 * s['hit_rate']:.0f}%, median err {s['mae_median']:.1f} "
             f"vs naive {s['mae_naive']:.1f} pts" if s.get("n") else "")
          + ("" if bc["ready"] else f"  (collecting data, {min(bc['matured_24h'], bc['window'])}/{bc['window']})"))
    print(f"runtime {t_total:.1f}s  (data {t_data:.1f}s, model load {t_load:.1f}s, sampling {t_model:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
