// NQ Kronos forecast page: reads data/latest.json, draws candles + forecast fan on one canvas.
"use strict";

const POLL_MS = 5 * 60 * 1000;
const CANDLES_FOR = { 24: 48, 7: 14 };           // candles per view: the fan gets ~1/3 of the width
const STALE_MS = 2 * 3600e3;                      // stale if no new forecast for 2h of open market
// ?now=2026-11-09T03:00:00Z pretends it's that time (for checking the status pill).
const NOW_OVERRIDE = new URLSearchParams(location.search).get("now");
const nowMs = () => (NOW_OVERRIDE ? Date.parse(NOW_OVERRIDE) : Date.now());
const C = {
  grid: "#172025", axis: "#73858c", text: "#d5e1e4",
  up: "#26a69a", down: "#ef5350", wick: "#5d6f76",
  pathUp: "rgba(34,197,94,0.15)", pathDn: "rgba(240,82,82,0.15)",
  band: "rgba(45,212,191,0.13)", band2: "rgba(45,212,191,0.10)", bandEdge: "rgba(45,212,191,0.45)",
  gold: "#f2c14e", now: "#2dd4bf", sep: "#1b2429", bg: "#090c0e",
};

const S = { data: null, status: null, horizon: 24, showPaths: true, hoverSlot: null };
const $ = (id) => document.getElementById(id);

// ---- formatting -------------------------------------------------------------
const fmtPx = (v) => v.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
};
const lonHour = (d) => Number(F.lonHour.format(d)) % 24;

// ---- market hours ---------------------------------------------------------------
// Primary source: status.json "sessions_utc" written by the Python calendar (knows holidays).
// Fallback: the weekly Globex schedule (Sun 18:00 -> Fri 17:00 NY, 17:00-18:00 break).
const nyParts = dtf("America/New_York", { weekday: "short", hour: "2-digit", hourCycle: "h23" });
function weeklyOpen(ms) {
  const p = Object.fromEntries(nyParts.formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
  const hr = Number(p.hour) % 24, wd = p.weekday;
  if (hr === 17 || wd === "Sat") return false;
  if (wd === "Sun" && hr < 18) return false;
  if (wd === "Fri" && hr >= 17) return false;
  return true;
}
function marketState(now) {
  const spans = (S.status && S.status.sessions_utc || []).map(([a, b]) => [Date.parse(a), Date.parse(b)]);
  if (spans.length && now >= spans[0][0] && now < spans[spans.length - 1][1]) {
    const cur = spans.find(([a, b]) => now >= a && now < b);
    if (cur) return { open: true, sessionStart: cur[0] };
    const next = spans.find(([a]) => a > now);
    return { open: false, nextOpen: next ? next[0] : null };
  }
  const H = 3600e3, hour0 = Math.floor(now / H) * H;
  if (weeklyOpen(hour0)) {
    let s = hour0; while (weeklyOpen(s - H)) s -= H;
    return { open: true, sessionStart: s };
  }
  let t = hour0 + H; for (let i = 0; i < 24 * 8 && !weeklyOpen(t); i++) t += H;
  return { open: false, nextOpen: t };
}
const lonNext = dtf("Europe/London", { weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

function ago(ms) {
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

// ---- data -------------------------------------------------------------------
async function getJSON(url) {
  const r = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

async function load(initial) {
  try {
    const [data, status, band] = await Promise.all([
      getJSON("data/latest.json"),
      getJSON("data/status.json").catch(() => null),
      getJSON("data/band_check.json").catch(() => null),
    ]);
    const changed = !S.data || data.generated_utc !== S.data.generated_utc;
    S.status = status;
    S.band = band;
    renderBand();
    if (changed) S.data = prep(data);
    renderMeta();
    if (changed || initial) { renderSummary(); draw(); }
  } catch (e) {
    console.error(e);
    if (!S.data) showBanner(`Could not load forecast data (${e.message}).`);
  }
}

function prep(d) {
  d.candles.forEach((c) => (c.d = new Date(c.t)));
  d.futureD = d.future_t.map((t) => new Date(t));
  return d;
}

// ---- side panel, header, banner ---------------------------------------------
function showBanner(msg) { const b = $("stale-banner"); b.textContent = msg; b.hidden = !msg; }

function renderMeta() {
  const d = S.data; if (!d) return;
  const gen = new Date(d.generated_utc);
  $("last-close").textContent = fmtPx(d.last_close);
  $("m-model").textContent = `${d.model} (${d.params})`;
  $("m-context").textContent = `${d.context} hourly bars`;
  $("m-horizon").textContent = `${d.horizon} h`;
  $("m-samples").textContent = `${d.samples} paths`;
  $("m-sampling").textContent = d.sampling ? `T=${d.sampling.T.toFixed(1)} · top_p=${d.sampling.top_p}` : "—";
  $("u-london").textContent = F.lonFull.format(gen);
  $("u-ny").textContent = F.nyFull.format(gen);
  $("u-lastbar").textContent = F.lonFull.format(new Date(d.last_bar_utc));
  $("d-source").textContent = d.data_source;

  const badges = ['<span class="badge">data delayed</span>'];
  if (d.roll_adjusted) {
    badges.push(`<span class="badge roll" title="Earlier bars back-adjusted by the contract roll gap">roll adjusted · ${d.roll_date}</span>`);
    if (d.roll_method === "estimated") {
      const gap = d.roll_gap != null ? ` (${fmtPts(d.roll_gap)} pts)` : "";
      badges.push(`<span class="badge est" title="The incoming contract's own prices weren't available, so the gap was estimated from the price jump and may be off">roll gap estimated${gap}</span>`);
    }
  }
  $("badges").innerHTML = badges.join("");

  const pill = $("status-pill"), now = nowMs(), m = marketState(now);
  if (!m.open) {
    pill.textContent = `market closed · next update ${m.nextOpen ? lonNext.format(new Date(m.nextOpen)).replace(",", "") + " London" : "—"}`;
    pill.className = "pill closed";
    showBanner("");
    return;
  }
  // Age counted from the later of the forecast time and this session's open, so the
  // weekend/daily-break pause isn't mistaken for a stalled pipeline.
  const openAge = now - Math.max(gen.getTime(), m.sessionStart);
  if (openAge > STALE_MS) {
    const err = S.status && S.status.ok === false && S.status.error ? ` Last error: ${S.status.error}.` : "";
    pill.textContent = "stale"; pill.className = "pill stale";
    showBanner(`Stale since ${F.lonFull.format(gen)} (London): no new forecast for over 2 hours while the market is open. Showing the last good forecast.${err}`);
  } else {
    pill.textContent = `updated ${ago(now - gen)}`; pill.className = "pill live";
    showBanner("");
  }
}

function renderSummary() {
  const d = S.data; if (!d) return;
  const k = S.horizon - 1, q = d.quantiles, lc = d.last_close;
  const med = q.p50[k] - lc, range = q.p90[k] - q.p10[k];
  const up = d.paths.filter((p) => p[k] > lc).length;
  $("sum-title").textContent = `Next ${S.horizon}h`;
  const mv = $("s-med"); mv.textContent = `${fmtPts(med)} pts`; mv.className = "v " + (med >= 0 ? "pos" : "neg");
  $("s-med-pct").textContent = `${fmtPct((100 * med) / lc)} · median ${fmtPx(q.p50[k])}`;
  $("s-range").textContent = `${fmtPx(range)} pts`;
  $("s-range-lvls").textContent = `${fmtPx(q.p10[k])} – ${fmtPx(q.p90[k])}`;
  $("s-up").textContent = `${Math.round((100 * up) / d.paths.length)}%`;
  $("s-up-n").textContent = `${up} of ${d.paths.length} paths`;
}

// ---- band check (Phase 4) -------------------------------------------------------
const lonShort = dtf("Europe/London", { weekday: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const shortT = (iso) => lonShort.format(new Date(iso)).replace(",", "");

function renderBand() {
  const b = S.band, el = $("band");
  if (!b) { el.hidden = true; return; }
  el.hidden = false;
  const s = b.h24, win = b.window;
  $("band-sub").textContent = b.ready
    ? `last ${s.n} matured +24h forecasts`
    : `${b.matured_24h} matured +24h forecast${b.matured_24h === 1 ? "" : "s"}, ${b.pending_24h} pending`;

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
  $("band-hz").innerHTML = hz.length ? `band hit rate by horizon: ${hz.join(" · ")}` : "";

  $("band-rows").innerHTML = b.recent.map((r) => `
    <tr><td>${shortT(r.made_for_bar)}${r.backfill ? '<span class="bf" title="backfilled: computed afterwards using only data up to this bar">bf</span>' : ""}</td>
      <td>${shortT(r.target)}</td><td>${fmtPx(r.p10)}</td><td>${fmtPx(r.p50)}</td><td>${fmtPx(r.p90)}</td>
      <td>${fmtPx(r.actual)}</td><td class="${r.inside ? "in" : "out"}">${r.inside ? "yes" : "no"}</td>
      <td>${fmtPx(r.err_median)}</td><td>${fmtPx(r.err_naive)}</td></tr>`).join("")
    || `<tr><td colspan="9" style="text-align:left;color:var(--muted)">no matured forecasts yet</td></tr>`;

  const notes = ["Times are London. A forecast matures once its +24h bar has closed; the actual is that bar's close."];
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
  const d = S.data; if (!d) return;
  const wrap = $("chart-wrap"), dpr = window.devicePixelRatio || 1;
  const W = wrap.clientWidth, H = wrap.clientHeight;
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const narrow = W < 560;
  const pad = { l: narrow ? 50 : 64, r: narrow ? 74 : 96, t: 14, b: 26 };
  const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
  const h = S.horizon;
  const candles = d.candles.slice(-CANDLES_FOR[h]);
  const n = candles.length, slots = n + h;
  const sw = pw / slots;
  const X = (i) => pad.l + (i + 0.5) * sw;          // slot centre; candles 0..n-1, step k at n-1+k
  const paths = d.paths.map((p) => p.slice(0, h));
  const q = Object.fromEntries(Object.entries(d.quantiles).map(([k, v]) => [k, v.slice(0, h)]));

  // y range: visible candles + per-step P5-P95 of the paths (outlier paths are clipped)
  let lo = Infinity, hi = -Infinity;
  for (const c of candles) { lo = Math.min(lo, c.l); hi = Math.max(hi, c.h); }
  for (let k = 0; k < h; k++) {
    const col = paths.map((p) => p[k]).sort((a, b) => a - b);
    lo = Math.min(lo, quantile(col, 0.05)); hi = Math.max(hi, quantile(col, 0.95));
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
  const times = candles.map((c) => c.d).concat(d.futureD.slice(0, h));
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

  // forecast band(s)
  const fx = (k) => X(n - 1 + k);                            // k = 0 is the last candle
  const lc = d.last_close;
  const area = (top, bot, fill) => {
    ctx.beginPath(); ctx.moveTo(fx(0), Y(lc));
    top.forEach((v, i) => ctx.lineTo(fx(i + 1), Y(v)));
    for (let i = bot.length - 1; i >= 0; i--) ctx.lineTo(fx(i + 1), Y(bot[i]));
    ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
  };
  area(q.p90, q.p10, C.band);
  area(q.p75, q.p25, C.band2);
  const line = (vals, color, width, dash) => {
    ctx.beginPath(); ctx.moveTo(fx(0), Y(lc));
    vals.forEach((v, i) => ctx.lineTo(fx(i + 1), Y(v)));
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []); ctx.stroke(); ctx.setLineDash([]);
  };

  // paths
  if (S.showPaths) {
    ctx.lineWidth = 1; ctx.lineJoin = "round";
    for (const p of paths) {
      ctx.beginPath(); ctx.moveTo(fx(0), Y(lc));
      p.forEach((v, i) => ctx.lineTo(fx(i + 1), Y(v)));
      ctx.strokeStyle = p[h - 1] > lc ? C.pathUp : C.pathDn; ctx.stroke();
    }
  }
  line(q.p90, C.bandEdge, 1, [3, 3]);
  line(q.p10, C.bandEdge, 1, [3, 3]);
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

  // last close line + NOW line
  ctx.strokeStyle = "rgba(213,225,228,0.35)"; ctx.setLineDash([1, 3]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad.l, Math.round(Y(lc)) + 0.5); ctx.lineTo(pad.l + pw, Math.round(Y(lc)) + 0.5); ctx.stroke();
  ctx.setLineDash([4, 3]); ctx.strokeStyle = C.now;
  const nx = Math.round(fx(0)) + 0.5;
  ctx.beginPath(); ctx.moveTo(nx, pad.t); ctx.lineTo(nx, pad.t + ph); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = C.now; ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.font = `600 ${narrow ? 10 : 11}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.fillStyle = C.bg; ctx.fillRect(nx - 17, pad.t, 34, 15);
  ctx.fillStyle = C.now; ctx.fillText("NOW", nx, pad.t + 2);
  ctx.restore();

  // right-edge tags (de-overlapped)
  const k = h - 1;
  const tags = [
    { v: q.p90[k], label: "P90", color: C.bandEdge.replace("0.45", "0.95") },
    { v: q.p50[k], label: "MED", color: C.gold },
    { v: q.p10[k], label: "P10", color: C.bandEdge.replace("0.45", "0.95") },
    { v: lc, label: "LAST", color: C.text },
  ].map((t) => ({ ...t, y: Y(t.v) })).sort((a, b) => a.y - b.y);
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

  geo = { pad, pw, ph, sw, n, h, candles, q, X, Y };
  drawHover();
}

function drawHover() {
  const tip = $("tip");
  if (!geo || S.hoverSlot == null) { tip.hidden = true; return; }
  const { pad, ph, n, candles, q, X } = geo, d = S.data, i = S.hoverSlot;
  const x = Math.round(X(i)) + 0.5;
  ctx.strokeStyle = "rgba(213,225,228,0.28)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke();
  let html;
  if (i < n) {
    const c = candles[i];
    html = `<div class="t">${F.lonFull.format(c.d)}</div>O ${fmtPx(c.o)}  H ${fmtPx(c.h)}<br>L ${fmtPx(c.l)}  C ${fmtPx(c.c)}`;
  } else {
    const k = i - n;   // future step index
    const lc = d.last_close;
    html = `<div class="t">+${k + 1}h · ${F.lonFull.format(d.futureD[k])}</div>` +
      `P90 ${fmtPx(q.p90[k])}<br><span style="color:${C.gold}">MED ${fmtPx(q.p50[k])}</span> (${fmtPts(q.p50[k] - lc)})<br>P10 ${fmtPx(q.p10[k])}`;
  }
  tip.innerHTML = html; tip.hidden = false;
}

function onPointer(e) {
  if (!geo) return;
  const r = canvas.getBoundingClientRect();
  // slots 0..n-1 are candles; slot n-1+k is forecast step k (1..h)
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
canvas.addEventListener("pointermove", onPointer);
canvas.addEventListener("pointerdown", onPointer);
canvas.addEventListener("pointerleave", () => { S.hoverSlot = null; draw(); });
new ResizeObserver(() => draw()).observe($("chart-wrap"));

load(true);
setInterval(load, POLL_MS);
setInterval(renderMeta, 60 * 1000);   // keep "updated N min ago" fresh
