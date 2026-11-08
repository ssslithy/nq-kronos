// 24h volatility card (GARCH): reads data/vol_latest.json, vol_history.jsonl, vol_status.json.
// Self-contained (own helpers inside this function) so it can't clash with app.js.
(() => {
  "use strict";

  const POLL_MS = 5 * 60 * 1000;
  const SPARK_DAYS = 60;          // sparkline length, trading days
  const CALIB_MIN = 20;           // matured forecasts needed before the calibration line shows
  const Z80 = 1.2816;             // the 80% range is last x exp(+-1.2816 sigma)
  const OVERDUE_MS = 90 * 60e3;   // warn if no forecast this long after it was due
  const NOW_OVERRIDE = new URLSearchParams(location.search).get("now");   // same ?now= as app.js
  const nowMs = () => (NOW_OVERRIDE ? Date.parse(NOW_OVERRIDE) : Date.now());
  const $ = (id) => document.getElementById(id);

  const num = (v, dp = 0) => v.toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const dtf = (tz, o) => new Intl.DateTimeFormat("en-GB", { timeZone: tz, hourCycle: "h23", ...o });
  const hm = { ny: dtf("America/New_York", { hour: "2-digit", minute: "2-digit" }),
               lon: dtf("Europe/London", { hour: "2-digit", minute: "2-digit" }) };
  const dayNY = dtf("America/New_York", { weekday: "short", day: "2-digit", month: "short" });
  const ymdNY = dtf("America/New_York", { year: "numeric", month: "2-digit", day: "2-digit" });
  const dayNYshort =dtf("America/New_York", { day: "2-digit", month: "short" });
  // "Mon 09 Nov 08:00 New York (13:00 London)"; the day is New York's.
  const both = (iso, withDay = true) => {
    const d = new Date(iso);
    return `${withDay ? dayNY.format(d).replace(",", "") + " " : ""}${hm.ny.format(d)} New York (${hm.lon.format(d)} London)`;
  };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function get(url, parse = "json") {
    const r = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return parse === "json" ? r.json() : r.text();
  }

  async function load() {
    let f;
    try { f = await get("data/vol_latest.json"); } catch (e) { $("vol").hidden = true; return; }
    const [status, histText] = await Promise.all([
      get("data/vol_status.json").catch(() => null),
      get("data/vol_history.jsonl", "text").catch(() => ""),
    ]);
    const hist = histText.split("\n").filter((s) => s.trim()).map((s) => JSON.parse(s))
      .sort((a, b) => Date.parse(a.origin_utc) - Date.parse(b.origin_utc));
    render(f, status, hist);
  }

  function render(f, status, hist) {
    $("vol").hidden = false;

    // headline
    $("vol-pts").textContent = `±${num(f.sigma_pts)} pts`;
    $("vol-pct").textContent = `(±${num(f.sigma_pct, 2)}%)`;
    $("vol-r80").textContent = `${num(f.range80[0])} – ${num(f.range80[1])}`;
    $("vol-r68").textContent = `${num(f.range68[0])} – ${num(f.range68[1])}`;
    $("vol-cash").textContent = f.cash && f.cash.sigma_pts != null ? `±${num(f.cash.sigma_pts)} pts` : "—";

    const reg = $("vol-regime");
    reg.className = `vol-regime ${f.regime.toLowerCase()}`;
    reg.innerHTML = `${esc(f.regime)}<span class="p">${Math.round(f.percentile)}th pct</span>`;
    reg.title = `today's σ vs the last ${f.regime_days} daily forecasts: Calm < 25th, Elevated > 75th, High > 90th percentile`;

    $("vol-when").textContent = `Forecast at ${both(f.forecast_time_utc)} · next forecast ${both(f.next_forecast_utc)}`;

    // event flag
    const ev = f.events, evEl = $("vol-event");
    if (ev && ev.items && ev.items.length) {
      // "today" only when the release falls on the viewer's current New York date;
      // otherwise its date, so an older forecast never claims a past release is today.
      const todayNY = ymdNY.format(new Date(nowMs()));
      const isToday = (i) => ymdNY.format(new Date(i.time_utc)) === todayNY;
      const items = ev.items.map((i) => `<b>${esc(i.name)}</b> ${isToday(i) ? "today " + esc(i.ny.slice(-5)) : esc(i.ny)} NY`);
      const note = ev.items.some(isToday)
        ? "Scheduled release today: realised move often exceeds the GARCH range."
        : "Scheduled release inside the 24h horizon: realised move often exceeds the GARCH range.";
      evEl.className = "vol-event on";
      evEl.innerHTML = items.join("<br>") + `<span class="ev-note">${note}</span>`;
    } else {
      evEl.className = "vol-event";
      evEl.textContent = ev ? (ev.note || "No scheduled release in the 24h horizon.") : "Event flag not available for this forecast.";
    }

    // vs realised, model
    $("vol-real").textContent = `RMS 24h move ${num(f.realised_rms_5d_pct, 2)}% (5d) · ${num(f.realised_rms_20d_pct, 2)}% (20d)`;
    const m = f.model;
    $("vol-model").textContent = `${m.spec}, last ${num(m.n_returns)} hourly returns · ω ${num(m.omega, 3)} α ${num(m.alpha, 2)} β ${num(m.beta, 2)} ν ${num(m.nu, 1)} · α+β ${num(m.persistence, 2)} · σ scaled ×${num(f.calibration.k, 2)} (calibration)`;

    // warnings: failed run (stale) or overdue
    const warn = [];
    if (f.stale || (status && status.ok === false)) {
      warn.push(`Stale: the latest run failed${status && status.error ? ` (${status.error})` : ""}. Showing the forecast from ${both(f.forecast_time_utc)}.`);
    } else if (nowMs() > Date.parse(f.next_forecast_utc) + OVERDUE_MS) {
      warn.push(`Overdue: the forecast due ${both(f.next_forecast_utc)} hasn't arrived. Showing the one from ${both(f.forecast_time_utc)}.`);
    }
    if (f.roll_note) warn.push(f.roll_note);
    $("vol-warn").textContent = warn.join(" ");
    $("vol-warn").hidden = !warn.length;

    spark(hist.slice(-SPARK_DAYS));
    calib(hist);
  }

  // forecast sigma as a line, the 80% edge (1.2816 sigma) dashed, actual |24h move| as dots
  let lastRows = [];
  function spark(rows) {
    lastRows = rows;
    const box = $("vol-spark-svg");
    if (rows.length < 2) {
      box.innerHTML = `<div class="empty">builds up daily (${rows.length} forecast${rows.length === 1 ? "" : "s"} so far)</div>`;
      return;
    }
    const W = Math.max(200, Math.round(box.clientWidth || 400)), H = 84, padL = 30, padR = 4, padT = 4, padB = 14;
    const n = rows.length, pw = W - padL - padR, ph = H - padT - padB;
    const vals = rows.flatMap((r) => [r.sigma_pct * Z80, r.move_pct != null ? Math.abs(r.move_pct) : 0]);
    const top = Math.max(...vals) * 1.08;
    const X = (i) => padL + (n === 1 ? pw / 2 : (i * pw) / (n - 1));
    const Y = (v) => padT + ph - (v / top) * ph;
    const poly = (k) => rows.map((r, i) => `${X(i).toFixed(1)},${Y(r.sigma_pct * k).toFixed(1)}`).join(" ");
    const tick = top > 2 ? 1 : 0.5;
    let grid = "";
    for (let v = tick; v < top; v += tick) {
      grid += `<line x1="${padL}" x2="${W - padR}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="#172025"/>` +
        `<text x="${padL - 4}" y="${(Y(v) + 3).toFixed(1)}" text-anchor="end">${v}%</text>`;
    }
    const dots = rows.map((r, i) => {
      if (r.move_pct == null) return "";
      const out = r.inside80 === false;
      const day = dayNY.format(new Date(r.origin_utc)).replace(",", "");
      return `<circle cx="${X(i).toFixed(1)}" cy="${Y(Math.abs(r.move_pct)).toFixed(1)}" r="2.4" fill="${out ? "#f05252" : "#9fb0b6"}">` +
        `<title>${day}: σ ${r.sigma_pct.toFixed(2)}%, actual ${r.move_pct >= 0 ? "+" : "−"}${Math.abs(r.move_pct).toFixed(2)}%${out ? " (outside 80% range)" : ""}</title></circle>`;
    }).join("");
    const first = dayNYshort.format(new Date(rows[0].origin_utc)), last = dayNYshort.format(new Date(rows[n - 1].origin_utc));
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Forecast sigma and actual moves, last ${n} trading days">
      <g font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="9" fill="#73858c">${grid}
        <text x="${padL}" y="${H - 2}">${first}</text><text x="${W - padR}" y="${H - 2}" text-anchor="end">${last}</text></g>
      <polyline points="${poly(Z80)}" fill="none" stroke="rgba(45,212,191,0.55)" stroke-width="1" stroke-dasharray="3 2"/>
      <polyline points="${poly(1)}" fill="none" stroke="#2dd4bf" stroke-width="1.6"/>
      ${dots}</svg>`;
  }

  function calib(hist) {
    const matured = hist.filter((r) => typeof r.inside80 === "boolean").slice(-SPARK_DAYS);
    const el = $("vol-calib");
    if (matured.length < CALIB_MIN) {
      el.textContent = `Calibration line after ${CALIB_MIN} matured forecasts (${matured.length} so far).`;
      return;
    }
    const hit = (100 * matured.filter((r) => r.inside80).length) / matured.length;
    el.innerHTML = `Last ${matured.length} forecasts: actual move inside 80% range <b>${hit.toFixed(0)}%</b> of the time.`;
  }

  $("vol-model-btn").addEventListener("click", (e) => {
    const open = e.currentTarget.closest(".vol-mid").classList.toggle("show-model");
    e.currentTarget.setAttribute("aria-expanded", String(open));
  });
  new ResizeObserver(() => { if (lastRows.length > 1) spark(lastRows); }).observe($("vol-spark-svg"));
  load();
  setInterval(load, POLL_MS);
})();
