// NQ forecasts page: candles + a 24h forecast fan on one canvas.
// Default: the daily GARCH fan (data/vol_latest.json). Toggle: Kronos-small (data/latest.json).
// Both are made once per US trading day from the 07:00-08:00 New York bar.
"use strict";

const POLL_MS = 5 * 60 * 1000;
const CANDLES_FOR = { 24: 48, 7: 14 };           // candles per view: the fan gets ~1/3 of the width
const OVERDUE_MS = 90 * 60e3;                     // stale if the daily forecast is this late
const KRONOS_NOTE = "Kronos (experimental): its 80% band held ~46% of the time vs the 80% target";
// ?now=2026-11-09T15:00:00Z pretends it's that time (for checking the status pill).
const NOW_OVERRIDE = new URLSearchParams(location.search).get("now");
const nowMs = () => (NOW_OVERRIDE ? Date.parse(NOW_OVERRIDE) : Date.now());
const C = {
  grid: "#172025", axis: "#73858c", text: "#d5e1e4",
  up: "#26a69a", down: "#ef5350",
  pathUp: "rgba(34,197,94,0.15)", pathDn: "rgba(240,82,82,0.15)", pathG: "rgba(45,212,191,0.10)",
  band: "rgba(45,212,191,0.16)", band2: "rgba(45,212,191,0.10)", bandOuter: "rgba(45,212,191,0.07)",
  bandEdge: "rgba(45,212,191,0.45)", outerEdge: "rgba(45,212,191,0.28)",
  gold: "#f2c14e", now: "#2dd4bf", sep: "#1b2429", bg: "#090c0e",
};

const S = { kronos: null, vol: null, status: null, kstatus: null, band: null, volHist: [], horizon: 24,
            showPaths: true, showKronos: false, hoverSlot: null };
const $ = (id) => document.getElementById(id);

// ---- formatting -------------------------------------------------------------
const fmtPx = (v) => v.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt0 = (v) => v.toLocaleString("en-GB", { maximumFractionDigits: 0 });
const fmtPts = (v) => (v >= 0 ? "+" : "−") + Math.abs(v).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = (v) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2) + "%";
const dtf = (tz, opts) => new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour12: false, ...opts });
// "Sat 07 Nov 18:43 GMT" / "Sat 07 Nov 13:43 EST": abbreviations derived from the UTC
// offset so they don't depend on the browser's ICU tz-name data.
function stamp(tz) {
  const f = dtf(tz, { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const hourOf = dtf(tz, { hour: "2-digit", hourCycle: "h23" });
  return {
    format(d) {
      const off = ((Number(hourOf.format(d)) - d.getUTCHours() + 36) % 24) - 12; // hours vs UTC
      const abbr = tz === "Europe/London" ? (off === 1 ? "BST" : "GMT") : (off === -4 ? "EDT" : "EST");
      return `${f.format(d).replace(",", "")} ${abbr}`;
    },
  };
}
const F = {
  lonFull: stamp("Europe/London"),
  nyFull: stamp("America/New_York"),
  lonTick: dtf("Europe/London", { weekday: "short", hour: "2-digit", minute: "2-digit" }),
  lonDay: dtf("Europe/London", { weekday: "short", day: "2-digit" }),
  lonHour: dtf("Europe/London", { hour: "2-digit", hourCycle: "h23" }),
  nyDay: dtf("America/New_York", { weekday: "short", day: "2-digit", month: "short" }),
  nyHM: dtf("America/New_York", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }),
  lonHM: dtf("Europe/London", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }),
};
const lonHour = (d) => Number(F.lonHour.format(d)) % 24;
// "Mon 09 Nov 08:00 NY (13:00 London)"
const nyLon = (d) => `${F.nyDay.format(d).replace(",", "")} ${F.nyHM.format(d)} NY (${F.lonHM.format(d)} London)`;

// ---- data -------------------------------------------------------------------
async function getJSON(url) {
  const r = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
async function getLines(url) {
  const r = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) return [];
  return (await r.text()).split("\n").filter((s) => s.trim()).map((s) => JSON.parse(s));
}

async function load(initial) {
  const [vol, kronos, status, band, volHist, kstatus] = await Promise.all([
    getJSON("data/vol_latest.json").catch(() => null),
    getJSON("data/latest.json").catch(() => null),
    getJSON("data/vol_status.json").catch(() => null),
    getJSON("data/band_check.json").catch(() => null),
    getLines("data/vol_history.jsonl").catch(() => []),
    getJSON("data/status.json").catch(() => null),
  ]);
  if (!vol && !kronos) { showBanner("Could not load forecast data."); return; }
  const changed = (vol && (!S.vol || vol.generated_utc !== S.vol.generated_utc)) ||
                  (kronos && (!S.kronos || kronos.generated_utc !== S.kronos.generated_utc));
  if (vol) S.vol = prepVol(vol);
  if (kronos) S.kronos = prepKronos(kronos);
  if (!S.vol) S.showKronos = true;                     // no GARCH data yet: Kronos is all there is
  S.status = status; S.band = band; S.volHist = volHist; S.kstatus = kstatus;
  renderBand();
  renderMeta();
  if (changed || initial) { renderSummary(); draw(); }
}

function prepKronos(d) {
  d.candles.forEach((c) => (c.d = new Date(c.t)));
  d.futureD = d.future_t.map((t) => new Date(t));
  return d;
}
function prepVol(v) {
  (v.candles || []).forEach((c) => (c.d = new Date(c.t)));
  v.futureD = (v.future_utc || []).map((t) => new Date(t));
  return v;
}

// The fan currently shown, in one shape for both models.
function view() {
  if (!S.showKronos && S.vol && S.vol.fan) {
    const v = S.vol, f = v.fan;
    return {
      kind: "garch", candles: v.candles, futureD: v.futureD, lastClose: v.last_close, paths: f.paths,
      q: { p025: f.p025, p10: f.p10, p50: f.p50, p90: f.p90, p975: f.p975 },
      bands: [["p975", "p025", C.bandOuter], ["p90", "p10", C.band]],
      edges: [["p975", C.outerEdge], ["p025", C.outerEdge], ["p90", C.bandEdge], ["p10", C.bandEdge]],
      tags: [["p975", "95%", C.outerEdge.replace("0.28", "0.8")], ["p90", "P90", C.bandEdge.replace("0.45", "0.95")],
             ["p10", "P10", C.bandEdge.replace("0.45", "0.95")], ["p025", "95%", C.outerEdge.replace("0.28", "0.8")]],
      pathColor: () => C.pathG,
    };
  }
  const d = S.kronos;
  return {
    kind: "kronos", candles: d.candles, futureD: d.futureD, lastClose: d.last_close, paths: d.paths,
    q: d.quantiles,
    bands: [["p90", "p10", C.band], ["p75", "p25", C.band2]],
    edges: [["p90", C.bandEdge], ["p10", C.bandEdge]],
    tags: [["p90", "P90", C.bandEdge.replace("0.45", "0.95")], ["p10", "P10", C.bandEdge.replace("0.45", "0.95")]],
    pathColor: (p, h, lc) => (p[h - 1] > lc ? C.pathUp : C.pathDn),
  };
}

// ---- side panel, header, status pill ------------------------------------------
function showBanner(msg) { const b = $("stale-banner"); b.textContent = msg; b.hidden = !msg; }

function renderMeta() {
  const g = !S.showKronos && S.vol, v = S.vol, d = S.kronos;
  const src = g ? v : d;
  $("last-close").textContent = fmtPx(src.last_close);
  if (g) {
    const m = v.model;
    $("m-model").textContent = "GARCH(1,1), Student-t, calibrated";
    $("m-context").textContent = `${fmt0(m.n_returns)} hourly returns`;
    $("m-horizon").textContent = "24 h";
    $("m-samples").textContent = `${v.fan.paths.length} paths (illustration)`;
    $("m-sampling").textContent = `bands: k σ × hour-of-day profile; 95% ×${v.multipliers.m95.toFixed(2)}`;
    $("u-london").textContent = F.lonFull.format(new Date(v.generated_utc));
    $("u-ny").textContent = F.nyFull.format(new Date(v.generated_utc));
    $("u-lastbar").textContent = F.lonFull.format(new Date(v.origin_utc));
    $("d-source").textContent = "Yahoo Finance (NQ=F), delayed";
  } else if (d) {
    $("m-model").textContent = `${d.model} (${d.params})`;
    $("m-context").textContent = `${d.context} hourly bars`;
    $("m-horizon").textContent = `${d.horizon} h`;
    $("m-samples").textContent = `${d.samples} paths`;
    $("m-sampling").textContent = d.sampling ? `T=${d.sampling.T.toFixed(1)} · top_p=${d.sampling.top_p}` : "—";
    $("u-london").textContent = F.lonFull.format(new Date(d.generated_utc));
    $("u-ny").textContent = F.nyFull.format(new Date(d.generated_utc));
    $("u-lastbar").textContent = F.lonFull.format(new Date(d.last_bar_utc));
    $("d-source").textContent = d.data_source;
  }

  const badges = ['<span class="badge">data delayed</span>'];
  if (S.showKronos && d && d.roll_adjusted) {
    badges.push(`<span class="badge roll" title="Earlier bars back-adjusted by the contract roll gap">roll adjusted · ${d.roll_date}</span>`);
    if (d.roll_method === "estimated") {
      const gap = d.roll_gap != null ? ` (${fmtPts(d.roll_gap)} pts)` : "";
      badges.push(`<span class="badge est" title="The incoming contract's own prices weren't available, so the gap was estimated from the price jump and may be off">roll gap estimated${gap}</span>`);
    }
  }
  if (S.showKronos) badges.push(`<span class="badge est">${KRONOS_NOTE}</span>`);
  const ks = kronosStale();
  if (ks) badges.push(`<span class="badge est" title="${ks.why}">Kronos stale: last Kronos forecast from ${nyLon(ks.from)}</span>`);
  $("kronos-label").textContent = ks ? "Kronos (experimental · stale)" : "Kronos (experimental)";
  $("badges").innerHTML = badges.join("");

  // daily status pill: next update, or stale once the daily forecast is overdue
  const pill = $("status-pill");
  if (!v) { pill.textContent = "daily"; pill.className = "pill"; showBanner(""); return; }
  const next = new Date(v.next_forecast_utc), now = nowMs();
  const failed = v.stale || (S.status && S.status.ok === false);
  if (failed || now > next.getTime() + OVERDUE_MS) {
    const err = S.status && S.status.ok === false && S.status.error ? ` Last error: ${S.status.error}.` : "";
    pill.textContent = "stale"; pill.className = "pill stale";
    showBanner(failed
      ? `The latest daily run failed. Showing the forecast from ${nyLon(new Date(v.forecast_time_utc))}.${err}`
      : `The daily forecast due ${nyLon(next)} hasn't arrived. Showing the one from ${nyLon(new Date(v.forecast_time_utc))}.`);
  } else {
    pill.textContent = `daily · next update ${nyLon(next)}`;
    pill.className = "pill live";
    showBanner("");
  }
}

// Kronos is stale if its own daily run failed/timed out, or it isn't from the GARCH forecast's bar.
// GARCH's pill and banner are never affected by this.
function kronosStale() {
  const d = S.kronos, v = S.vol;
  if (!d) return null;
  const from = new Date(d.last_bar_utc);
  if (d.stale) return { from, why: d.stale_reason || "the latest daily Kronos run failed" };
  if (S.kstatus && S.kstatus.ok === false) return { from, why: S.kstatus.error || "the latest Kronos run failed" };
  if (v && Date.parse(d.last_bar_utc) !== Date.parse(v.origin_utc))
    return { from, why: "not from the same bar as today's GARCH forecast (Kronos may still be running)" };
  return null;
}

function renderSummary() {
  const V = view(), k = S.horizon - 1, q = V.q, lc = V.lastClose;
  $("sum-title").textContent = `Next ${S.horizon}h`;
  const set = (i, label, v, sub, cls) => {
    $(`s-k${i}`).textContent = label; const el = $(`s-v${i}`);
    el.textContent = v; el.className = "v " + (cls || ""); $(`s-sub${i}`).textContent = sub;
  };
  if (V.kind === "garch") {
    set(1, "80% range", `${fmt0(q.p90[k] - q.p10[k])} pts`, `${fmt0(q.p10[k])} – ${fmt0(q.p90[k])}`);
    set(2, "95% range", `${fmt0(q.p975[k] - q.p025[k])} pts`, `${fmt0(q.p025[k])} – ${fmt0(q.p975[k])}`);
    set(3, "direction", "none", "GARCH forecasts the size of the move, not its direction");
  } else {
    const med = q.p50[k] - lc, up = V.paths.filter((p) => p[k] > lc).length;
    set(1, "median change", `${fmtPts(med)} pts`, `${fmtPct((100 * med) / lc)} · median ${fmtPx(q.p50[k])}`, med >= 0 ? "pos" : "neg");
    set(2, "P10–P90 range", `${fmtPx(q.p90[k] - q.p10[k])} pts`, `${fmtPx(q.p10[k])} – ${fmtPx(q.p90[k])}`);
    set(3, "paths ending up", `${Math.round((100 * up) / V.paths.length)}%`, `${up} of ${V.paths.length} paths`);
  }
  $("legend").innerHTML = V.kind === "garch"
    ? '<span><i class="sw sw-med"></i>median</span><span><i class="sw sw-band"></i>80%</span><span><i class="sw sw-outer"></i>95%</span><span><i class="sw sw-g"></i>simulated paths</span>'
    : '<span><i class="sw sw-med"></i>median</span><span><i class="sw sw-band"></i>P10–P90</span><span><i class="sw sw-up"></i>path ends up</span><span><i class="sw sw-dn"></i>down</span>';
  $("chart-title").textContent = V.kind === "garch" ? "24h forecast · GARCH (daily)" : "24h forecast · Kronos (experimental)";
}

// ---- band check: daily GARCH vs Kronos, then Kronos detail -----------------------------
const lonShort = dtf("Europe/London", { weekday: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const shortT = (iso) => lonShort.format(new Date(iso)).replace(",", "");
const MIN_COMPARE = 20;

function renderBand() {
  const b = S.band, el = $("band");
  el.hidden = false;

  // daily comparison, live forecasts only (backfilled GARCH lines and Kronos lab runs excluded)
  const live = S.volHist.filter((r) => !r.backfill && typeof r.inside80 === "boolean").slice(-60);
  const gHit = live.length ? (100 * live.filter((r) => r.inside80).length) / live.length : null;
  const kd = b && b.daily ? b.daily : { n: 0, matured: 0 };
  const cell = (name, hit, n, ref) => {
    const ok = n >= MIN_COMPARE;
    const cls = !ok ? "" : hit < 70 || hit > 90 ? "warn" : "good";
    return `<div><div class="k">${name}</div>
      <div class="v ${cls}">${ok ? hit.toFixed(0) + "%" : `collecting, ${n}/${MIN_COMPARE}`}</div>
      <div class="sub">${ok ? `actual inside 80% band, last ${n} daily forecasts` : `${n} live daily forecast${n === 1 ? "" : "s"} matured so far`} · ${ref}</div></div>`;
  };
  const bf = S.volHist.filter((r) => r.backfill && typeof r.inside80 === "boolean");
  const bfHit = bf.length ? (100 * bf.filter((r) => r.inside80).length) / bf.length : null;
  $("cmp-stats").innerHTML =
    cell("GARCH (daily)", gHit, live.length, bfHit != null ? `1-year backfill: ${bfHit.toFixed(1)}% of ${bf.length}` : "") +
    cell("Kronos (experimental)", kd.hit_rate != null ? 100 * kd.hit_rate : null, kd.n || 0, "lab: ~46%");

  if (!b) { $("band-detail").hidden = true; return; }
  $("band-detail").hidden = false;
  const s = b.h24, win = b.window;
  $("band-sub").textContent = b.ready
    ? `last ${s.n} matured +24h Kronos forecasts (any origin)`
    : `${b.matured_24h} matured +24h Kronos forecast${b.matured_24h === 1 ? "" : "s"}, ${b.pending_24h} pending`;

  if (b.ready) {
    const hit = 100 * s.hit_rate;
    const verdict = hit < 70 ? "band too narrow (overconfident)" : hit > 90 ? "band too wide (underconfident)" : "close to calibrated";
    const better = s.mae_median < s.mae_naive;
    $("band-stats").innerHTML = `
      <div><div class="k">band hit rate</div><div class="v ${hit < 70 || hit > 90 ? "warn" : "good"}">${hit.toFixed(0)}%</div>
        <div class="sub">actual inside P10–P90 · target ≈ 80% · ${verdict}</div></div>
      <div><div class="k">median error</div><div class="v ${better ? "good" : "warn"}">${fmtPx(s.mae_median)} pts</div>
        <div class="sub">naive "no change": ${fmtPx(s.mae_naive)} pts · ${better ? "median beats naive" : "naive is as good or better"}</div></div>
      <div><div class="k">avg band width</div><div class="v">${fmtPx(s.avg_width)} pts</div>
        <div class="sub">P90 − P10 at +24h</div></div>`;
  } else {
    $("band-stats").innerHTML = `<div class="collecting">collecting data, ${Math.min(b.matured_24h, win)}/${win}</div>`;
  }

  const hz = Object.entries(b.by_horizon).filter(([, v]) => v.n)
    .map(([h, v]) => `+${h}h <b>${(100 * v.hit_rate).toFixed(0)}%</b> (n=${v.n})`);
  $("band-hz").innerHTML = hz.length ? `Kronos band hit rate by horizon: ${hz.join(" · ")}` : "";

  $("band-rows").innerHTML = b.recent.map((r) => `
    <tr><td>${shortT(r.made_for_bar)}${r.backfill ? '<span class="bf" title="backfilled: computed afterwards using only data up to this bar">bf</span>' : ""}</td>
      <td>${shortT(r.target)}</td><td>${fmtPx(r.p10)}</td><td>${fmtPx(r.p50)}</td><td>${fmtPx(r.p90)}</td>
      <td>${fmtPx(r.actual)}</td><td class="${r.inside ? "in" : "out"}">${r.inside ? "yes" : "no"}</td>
      <td>${fmtPx(r.err_median)}</td><td>${fmtPx(r.err_naive)}</td></tr>`).join("")
    || `<tr><td colspan="9" style="text-align:left;color:var(--muted)">no matured forecasts yet</td></tr>`;

  const notes = ["Times are London. A forecast matures once its +24h bar has closed; the actual is that bar's close.",
                 "Rows before 9 Nov 2026 are from the old hourly schedule."];
  if (b.backfill_in_window) notes.push(`${b.backfill_in_window} of these were backfilled ("bf"): run afterwards with fewer paths, using only data up to each bar.`);
  if (b.missing_24h) notes.push(`${b.missing_24h} skipped because Yahoo has no bar at the target time.`);
  $("band-note").textContent = notes.join(" ");
}

// ---- chart --------------------------------------------------------------------
const canvas = $("chart"), ctx = canvas.getContext("2d");
let geo = null; // geometry of the last draw, for hover

function quantile(sorted, p) {               // linear interpolation, like numpy's default
  const i = (sorted.length - 1) * p, a = Math.floor(i), b = Math.ceil(i);
  return sorted[a] + (sorted[b] - sorted[a]) * (i - a);
}

function niceStep(span, target) {
  const raw = span / target, mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
}

function draw() {
  if (!S.vol && !S.kronos) return;
  const V = view();
  const wrap = $("chart-wrap"), dpr = window.devicePixelRatio || 1;
  const W = wrap.clientWidth, H = wrap.clientHeight;
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const narrow = W < 560;
  const pad = { l: narrow ? 50 : 64, r: narrow ? 74 : 96, t: 14, b: 26 };
  const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
  const h = S.horizon;
  const candles = V.candles.slice(-CANDLES_FOR[h]);
  const n = candles.length, slots = n + h;
  const sw = pw / slots;
  const X = (i) => pad.l + (i + 0.5) * sw;          // slot centre; candles 0..n-1, step k at n-1+k
  const paths = V.paths.map((p) => p.slice(0, h));
  const q = Object.fromEntries(Object.entries(V.q).map(([k, v]) => [k, v.slice(0, h)]));
  const lc = V.lastClose;

  // y range: visible candles + the widest band (GARCH 95%) or per-step P5-P95 of the paths
  let lo = Infinity, hi = -Infinity;
  for (const c of candles) { lo = Math.min(lo, c.l); hi = Math.max(hi, c.h); }
  if (V.kind === "garch") {
    lo = Math.min(lo, ...q.p025); hi = Math.max(hi, ...q.p975);
  } else {
    for (let k = 0; k < h; k++) {
      const col = paths.map((p) => p[k]).sort((a, b) => a - b);
      lo = Math.min(lo, quantile(col, 0.05)); hi = Math.max(hi, quantile(col, 0.95));
    }
  }
  const m = (hi - lo) * 0.06; lo -= m; hi += m;
  const Y = (v) => pad.t + ((hi - v) / (hi - lo)) * ph;

  // grid + y labels
  const step = niceStep(hi - lo, narrow ? 5 : 7);
  ctx.font = `${narrow ? 10 : 11}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textBaseline = "middle"; ctx.textAlign = "right";
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    const y = Math.round(Y(v)) + 0.5;
    ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
    ctx.fillStyle = C.axis;
    ctx.fillText(v.toLocaleString("en-GB", { maximumFractionDigits: step < 1 ? 2 : 0 }), pad.l - 6, y);
  }

  // session-gap separators and x labels (London time)
  const times = candles.map((c) => c.d).concat(V.futureD.slice(0, h));
  const minLabelPx = narrow ? 64 : 84;
  const every = [3, 6, 12, 24].find((e) => (e * sw) >= minLabelPx) || 24;
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  let lastLabelX = -Infinity;
  for (let i = 0; i < times.length; i++) {
    const x = X(i);
    if (i > 0 && times[i] - times[i - 1] > 3600e3) {          // weekend / daily break / holiday gap
      ctx.strokeStyle = C.sep; ctx.setLineDash([2, 3]);
      const gx = Math.round(x - sw / 2) + 0.5;
      ctx.beginPath(); ctx.moveTo(gx, pad.t); ctx.lineTo(gx, pad.t + ph); ctx.stroke(); ctx.setLineDash([]);
    }
    const hr = lonHour(times[i]);
    if (hr % every === 0 && x - lastLabelX >= minLabelPx) {
      const lab = hr === 0 || every === 24 ? F.lonDay.format(times[i]) : F.lonTick.format(times[i]);
      ctx.fillStyle = C.axis; ctx.fillText(lab, x, pad.t + ph + 7);
      lastLabelX = x;
    }
  }

  // everything below draws inside the plot rectangle only
  ctx.save();
  ctx.beginPath(); ctx.rect(pad.l, pad.t, pw, ph); ctx.clip();

  const fx = (k) => X(n - 1 + k);                            // k = 0 is the last candle
  const area = (top, bot, fill) => {
    ctx.beginPath(); ctx.moveTo(fx(0), Y(lc));
    top.forEach((v, i) => ctx.lineTo(fx(i + 1), Y(v)));
    for (let i = bot.length - 1; i >= 0; i--) ctx.lineTo(fx(i + 1), Y(bot[i]));
    ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
  };
  V.bands.forEach(([a, b, fill]) => area(q[a], q[b], fill));
  const line = (vals, color, width, dash) => {
    ctx.beginPath(); ctx.moveTo(fx(0), Y(lc));
    vals.forEach((v, i) => ctx.lineTo(fx(i + 1), Y(v)));
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []); ctx.stroke(); ctx.setLineDash([]);
  };

  if (S.showPaths) {
    ctx.lineWidth = 1; ctx.lineJoin = "round";
    for (const p of paths) {
      ctx.beginPath(); ctx.moveTo(fx(0), Y(lc));
      p.forEach((v, i) => ctx.lineTo(fx(i + 1), Y(v)));
      ctx.strokeStyle = V.pathColor(p, h, lc); ctx.stroke();
    }
  }
  V.edges.forEach(([k, col]) => line(q[k], col, 1, [3, 3]));
  line(q.p50, C.gold, 2);

  // candles
  const bw = Math.max(1, Math.min(9, sw * 0.62));
  candles.forEach((c, i) => {
    const x = X(i), up = c.c >= c.o, col = up ? C.up : C.down;
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, Y(c.h)); ctx.lineTo(Math.round(x) + 0.5, Y(c.l)); ctx.stroke();
    const y1 = Y(Math.max(c.o, c.c)), y2 = Y(Math.min(c.o, c.c));
    ctx.fillStyle = col; ctx.fillRect(x - bw / 2, y1, bw, Math.max(1, y2 - y1));
  });

  // last close line + FORECAST line at the origin bar's close
  ctx.strokeStyle = "rgba(213,225,228,0.35)"; ctx.setLineDash([1, 3]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad.l, Math.round(Y(lc)) + 0.5); ctx.lineTo(pad.l + pw, Math.round(Y(lc)) + 0.5); ctx.stroke();
  ctx.setLineDash([4, 3]); ctx.strokeStyle = C.now;
  const nx = Math.round(fx(0)) + 0.5;
  ctx.beginPath(); ctx.moveTo(nx, pad.t); ctx.lineTo(nx, pad.t + ph); ctx.stroke(); ctx.setLineDash([]);
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.font = `600 ${narrow ? 10 : 11}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  const lab = "08:00 NY";
  const lw = ctx.measureText(lab).width + 8;
  ctx.fillStyle = C.bg; ctx.fillRect(nx - lw / 2, pad.t, lw, 15);
  ctx.fillStyle = C.now; ctx.fillText(lab, nx, pad.t + 2);
  ctx.restore();

  // right-edge tags at +h (de-overlapped)
  const k = h - 1;
  const tags = V.tags.map(([key, label, color]) => ({ v: q[key][k], label, color }))
    .concat([{ v: q.p50[k], label: "MED", color: C.gold }, { v: lc, label: "LAST", color: C.text }])
    .map((t) => ({ ...t, y: Y(t.v) })).sort((a, b) => a.y - b.y);
  const gap = narrow ? 24 : 26;
  for (let i = 1; i < tags.length; i++) tags[i].y = Math.max(tags[i].y, tags[i - 1].y + gap);
  const over = tags[tags.length - 1].y - (pad.t + ph - 10);
  if (over > 0) tags.forEach((t) => (t.y -= over));
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  const tx = pad.l + pw + 6;
  for (const t of tags) {
    ctx.strokeStyle = t.color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.l + pw - 2, Y(t.v)); ctx.lineTo(tx - 1, t.y); ctx.stroke();
    ctx.font = `600 ${narrow ? 9 : 10}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    ctx.fillStyle = t.color; ctx.fillText(t.label, tx + 2, t.y - 6);
    ctx.font = `${narrow ? 10 : 11}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    ctx.fillStyle = C.text; ctx.fillText(t.v.toLocaleString("en-GB", { maximumFractionDigits: 0 }), tx + 2, t.y + 6);
  }

  geo = { pad, pw, ph, sw, n, h, candles, q, X, Y, V };
  drawHover();
}

function drawHover() {
  const tip = $("tip");
  if (!geo || S.hoverSlot == null) { tip.hidden = true; return; }
  const { pad, ph, n, candles, q, X, V } = geo, i = S.hoverSlot;
  const x = Math.round(X(i)) + 0.5;
  ctx.strokeStyle = "rgba(213,225,228,0.28)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke();
  let html;
  if (i < n) {
    const c = candles[i];
    html = `<div class="t">${F.lonFull.format(c.d)}</div>O ${fmtPx(c.o)}  H ${fmtPx(c.h)}<br>L ${fmtPx(c.l)}  C ${fmtPx(c.c)}`;
  } else {
    const k = i - n, lc = V.lastClose;
    const outer = V.kind === "garch" ? `95% ${fmt0(q.p025[k])} – ${fmt0(q.p975[k])}<br>` : "";
    html = `<div class="t">+${k + 1}h · ${F.lonFull.format(V.futureD[k])}</div>${outer}` +
      `P90 ${fmtPx(q.p90[k])}<br><span style="color:${C.gold}">MED ${fmtPx(q.p50[k])}</span> (${fmtPts(q.p50[k] - lc)})<br>P10 ${fmtPx(q.p10[k])}`;
  }
  tip.innerHTML = html; tip.hidden = false;
}

function onPointer(e) {
  if (!geo) return;
  const r = canvas.getBoundingClientRect();
  const i = Math.floor((e.clientX - r.left - geo.pad.l) / geo.sw);
  const slot = i >= 0 && i < geo.n + geo.h ? i : null;
  if (slot !== S.hoverSlot) { S.hoverSlot = slot; draw(); }
}

// ---- controls -----------------------------------------------------------------
document.querySelectorAll(".seg button").forEach((b) =>
  b.addEventListener("click", () => {
    S.horizon = Number(b.dataset.h);
    document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
    S.hoverSlot = null; renderSummary(); draw();
  }));
$("show-paths").addEventListener("change", (e) => { S.showPaths = e.target.checked; draw(); });
$("show-kronos").addEventListener("change", (e) => {
  S.showKronos = e.target.checked && !!S.kronos;
  e.target.checked = S.showKronos;
  S.hoverSlot = null; renderMeta(); renderSummary(); draw();
});
canvas.addEventListener("pointermove", onPointer);
canvas.addEventListener("pointerdown", onPointer);
canvas.addEventListener("pointerleave", () => { S.hoverSlot = null; draw(); });
new ResizeObserver(() => draw()).observe($("chart-wrap"));

load(true);
setInterval(load, POLL_MS);
setInterval(renderMeta, 60 * 1000);   // keep the pill fresh
