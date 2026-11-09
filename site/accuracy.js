// Accuracy page: reads data/vol_history.jsonl (one line per daily vol forecast) and draws
// the 1pm -> 1pm and cash-session hit charts, the hit-rate tables (80/85/90/95/99%) and the
// rolling 60-day hit rate of the 90% range (the headline). Also the live Kronos daily check.
"use strict";
(() => {
  const Z80 = 1.2816, DAYS = 250, ROLL = 60;
  const KINDS = { FOMC: "FOMC", CPI: "CPI", NFP: "Payrolls", PCE: "PCE", GDP: "GDP (adv.)", RETAIL: "Retail sales" };
  const COL = { band: "rgba(45,212,191,0.16)", outer: "rgba(45,212,191,0.07)", edge: "rgba(45,212,191,0.4)",
                dot: "#9fb0b6", out: "#f05252", ev: "#f59e0b", grid: "#172025", axis: "#73858c",
                teal: "#2dd4bf", cash: "#a78bfa", target: "rgba(213,225,228,0.5)" };
  const $ = (id) => document.getElementById(id);
  const mon = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", month: "short", year: "2-digit" });
  const day = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  const pct = (v, dp = 1) => (v == null ? "—" : v.toFixed(dp) + "%");
  let ROWS = [];

  const rate = (rows, key) => {
    const m = rows.filter((r) => typeof r[key] === "boolean");
    return { v: m.length ? (100 * m.filter((r) => r[key]).length) / m.length : null, n: m.length };
  };

  // ---- SVG chart helpers -----------------------------------------------------------------
  function frame(el, h, yMin, yMax, n) {
    const W = Math.max(280, el.clientWidth), H = h, pl = 38, pr = 8, pt = 8, pb = 20;
    const X = (i) => pl + (n <= 1 ? 0 : (i * (W - pl - pr)) / (n - 1));
    const Y = (v) => pt + ((yMax - v) / (yMax - yMin)) * (H - pt - pb);
    return { W, H, pl, pr, pt, pb, X, Y };
  }
  function yAxis(f, yMin, yMax, step, unit) {
    let s = "";
    for (let v = Math.ceil(yMin / step) * step; v <= yMax + 1e-9; v += step) {
      const y = f.Y(v).toFixed(1);
      s += `<line x1="${f.pl}" x2="${f.W - f.pr}" y1="${y}" y2="${y}" stroke="${v === 0 ? "#2a353b" : COL.grid}"/>` +
           `<text x="${f.pl - 4}" y="${(+y + 3).toFixed(1)}" text-anchor="end">${+v.toFixed(2)}${unit}</text>`;
    }
    return s;
  }
  function xMonths(f, rows) {
    let s = "", last = "", lastX = -Infinity;
    rows.forEach((r, i) => {
      const m = mon.format(new Date(r.origin_utc)), x = f.X(i);
      if (m !== last) {
        // label each new month, skipping any that would crowd the previous label (phones)
        if (last && x > f.pl + 20 && x < f.W - f.pr - 18 && x - lastX >= 48) {
          s += `<text x="${x.toFixed(1)}" y="${f.H - 5}" text-anchor="middle">${m}</text>`;
          lastX = x;
        }
        last = m;
      }
    });
    return s;
  }
  const area = (f, rows, hi, lo, fill) =>
    `<polygon fill="${fill}" points="${rows.map((r, i) => `${f.X(i).toFixed(1)},${f.Y(hi(r)).toFixed(1)}`).join(" ")} ` +
    `${rows.map((r, i) => `${f.X(i).toFixed(1)},${f.Y(lo(r)).toFixed(1)}`).reverse().join(" ")}"/>`;
  const svg = (f, body, label) =>
    `<svg viewBox="0 0 ${f.W} ${f.H}" width="${f.W}" height="${f.H}" role="img" aria-label="${label}">` +
    `<g font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="9.5" fill="${COL.axis}">${body}</g></svg>`;
  const niceStep = (span) => [0.5, 1, 2, 2.5, 5].find((s) => span / s <= 6) || 5;

  // ---- 1. 24h chart ----------------------------------------------------------------------
  function chart24(el) {
    const rows = ROWS.filter((r) => r.move_pct != null).slice(-DAYS);
    if (rows.length < 2) { el.innerHTML = '<p class="empty">not enough matured forecasts yet</p>'; return; }
    const lim = Math.max(...rows.map((r) => Math.max(Math.abs(r.move_pct), (r.m95 || 2) * r.sigma_pct))) * 1.05;
    const m90 = (r) => (r.m90 || 1.645) * r.sigma_pct;
    const f = frame(el, el.clientWidth < 500 ? 220 : 280, -lim, lim, rows.length);
    let b = yAxis(f, -lim, lim, niceStep(2 * lim), "%") + xMonths(f, rows);
    if (rows.some((r) => r.m95)) b += area(f, rows, (r) => (r.m95 || Z80) * r.sigma_pct, (r) => -(r.m95 || Z80) * r.sigma_pct, COL.outer);
    b += area(f, rows, m90, (r) => -m90(r), COL.band);
    rows.forEach((r, i) => {
      const out = r.inside90 === false, ev = r.events && r.events.length;
      b += `<circle cx="${f.X(i).toFixed(1)}" cy="${f.Y(r.move_pct).toFixed(1)}" r="${ev ? 2.8 : 2.2}" fill="${out ? COL.out : COL.dot}"` +
           `${ev ? ` stroke="${COL.ev}" stroke-width="1.2"` : ""}><title>${day.format(new Date(r.origin_utc))}: ` +
           `move ${r.move_pct >= 0 ? "+" : "−"}${Math.abs(r.move_pct).toFixed(2)}%, 90% range ±${m90(r).toFixed(2)}%` +
           `${ev ? " · " + r.events.join(", ") : ""}${r.backfill ? " · Backfill" : ""}</title></circle>`;
    });
    el.innerHTML = svg(f, b, "1pm to 1pm moves against the 90% and 95% forecast ranges");
  }

  // ---- 2. cash chart ---------------------------------------------------------------------
  function chartCash(el) {
    const rows = ROWS.filter((r) => r.cash_move_pct != null && r.cash_sigma_pct && r.cash_m90).slice(-DAYS);
    if (rows.length < 2) { el.innerHTML = '<p class="empty">not enough finished cash sessions yet</p>'; return; }
    const band = (r) => r.cash_m90 * r.cash_sigma_pct;
    const lim = Math.max(...rows.map((r) => Math.max(Math.abs(r.cash_move_pct), band(r)))) * 1.12;
    const f = frame(el, el.clientWidth < 500 ? 220 : 280, -lim, lim, rows.length);
    let b = yAxis(f, -lim, lim, niceStep(2 * lim), "%") + xMonths(f, rows);
    b += area(f, rows, band, (r) => -band(r), COL.band);
    rows.forEach((r, i) => {
      const x = f.X(i).toFixed(1), out = r.cash_inside90 === false, ev = r.events_today && r.events_today.length;
      if (r.cash_high_pct > band(r)) b += `<line x1="${x}" x2="${x}" y1="${f.Y(band(r)) - 7}" y2="${f.Y(band(r)) - 2}" stroke="${COL.ev}" stroke-width="1.2"/>`;
      if (r.cash_low_pct < -band(r)) b += `<line x1="${x}" x2="${x}" y1="${f.Y(-band(r)) + 2}" y2="${f.Y(-band(r)) + 7}" stroke="${COL.ev}" stroke-width="1.2"/>`;
      b += `<circle cx="${x}" cy="${f.Y(r.cash_move_pct).toFixed(1)}" r="${ev ? 2.8 : 2.2}" fill="${out ? COL.out : COL.dot}"` +
           `${ev ? ` stroke="${COL.ev}" stroke-width="1.2"` : ""}><title>${day.format(new Date(r.origin_utc))}: ` +
           `cash move ${r.cash_move_pct >= 0 ? "+" : "−"}${Math.abs(r.cash_move_pct).toFixed(2)}% (high ${r.cash_high_pct.toFixed(2)}%, low ${r.cash_low_pct.toFixed(2)}%), ` +
           `90% range ±${band(r).toFixed(2)}%${ev ? " · " + r.events_today.join(", ") : ""}</title></circle>`;
    });
    el.innerHTML = svg(f, b, "Cash-session moves against the 90% cash range");
  }

  // ---- 3. tables -------------------------------------------------------------------------
  function groups(rows, evKey) {
    const half = Math.floor(rows.length / 2);
    const g = [["all", rows], ["older half", rows.slice(0, half)], ["newer half", rows.slice(half)], ["last 60", rows.slice(-60)],
               [evKey === "events" ? "release in the window" : "release that day", rows.filter((r) => r[evKey] && r[evKey].length)],
               [evKey === "events" ? "no release in the window" : "no release that day", rows.filter((r) => Array.isArray(r[evKey]) && !r[evKey].length)]];
    for (const [k, name] of Object.entries(KINDS)) {
      const sel = rows.filter((r) => r[evKey] && r[evKey].includes(k));
      if (sel.length) g.push([`· ${name}`, sel]);
    }
    return g;
  }
  const cell = (x, target) => {
    if (x.v == null) return "<td>—</td>";
    const off = Math.abs(x.v - target) > (target >= 95 ? 3 : 5);
    return `<td class="${off ? "warn" : ""}">${pct(x.v)}</td>`;
  };
  function tables() {
    const m24 = ROWS.filter((r) => typeof r.inside80 === "boolean");
    const LV = [80, 85, 90, 95, 99];
    $("table-24h").innerHTML = `<thead><tr><th>days</th>${LV.map((l) => `<th>${l}%</th>`).join("")}<th>n</th></tr></thead><tbody>` +
      groups(m24, "events").map(([name, g]) => `<tr><td>${name}</td>${LV.map((l) => cell(rate(g, `inside${l}`), l)).join("")}` +
        `<td>${g.length}</td></tr>`).join("") + "</tbody>";
    const mc = ROWS.filter((r) => typeof r.cash_inside80 === "boolean");
    $("table-cash").innerHTML = `<thead><tr><th>days</th><th>90%</th><th>touch 90%</th><th>80%</th><th>RMS z</th><th>n</th></tr></thead><tbody>` +
      groups(mc, "events_today").map(([name, g]) => {
        const z = g.length ? Math.sqrt(g.reduce((s, r) => s + (r.cash_move_pct / r.cash_sigma_pct) ** 2, 0) / g.length) : null;
        return `<tr><td>${name}</td>${cell(rate(g, "cash_inside90"), 90)}<td>${pct(rate(g, "cash_touch90").v)}</td>${cell(rate(g, "cash_inside80"), 80)}` +
          `<td>${z == null ? "—" : z.toFixed(2)}</td><td>${g.length}</td></tr>`;
      }).join("") + "</tbody>";
    const live = ROWS.filter((r) => !r.backfill && typeof r.inside80 === "boolean").length;
    $("table-note").textContent =
      `Halves split each set in time order. The 90% cash range needs 60 earlier sessions for its measured multiplier, so it starts later (its n is smaller than the 80% column's). ` +
      `"Touch" = the session high or low went outside the 90% cash range at some point, even if the 16:00 close came back inside. ` +
      `RMS z = RMS(cash move / cash σ); 1.00 means σ is right on average. Release rows use the event calendar, which starts 1 Jan 2026, so earlier days count in neither. ` +
      `Amber = more than 5 points (3 for 95%/99%) from the target. ${live} of these forecasts were made live; the rest are the 1-year backfill (re-run in Nov 2026 for the 23-hour window).`;
  }

  // ---- 4. rolling hit rate ---------------------------------------------------------------
  function chartRoll(el) {
    const roll = (rows, key) => rows.map((r, i) => (i + 1 < ROLL ? null :
      (100 * rows.slice(i + 1 - ROLL, i + 1).filter((x) => x[key]).length) / ROLL));
    const r24 = ROWS.filter((r) => typeof r.inside90 === "boolean");
    const rc = ROWS.filter((r) => typeof r.cash_inside90 === "boolean");
    const all = ROWS.filter((r) => typeof r.inside90 === "boolean" || typeof r.cash_inside90 === "boolean");
    if (all.length < ROLL + 1) { el.innerHTML = '<p class="empty">needs more than 60 matured forecasts</p>'; return; }
    const idx = new Map(all.map((r, i) => [r.origin_utc, i]));
    const y24 = roll(r24, "inside90"), yc = roll(rc, "cash_inside90");
    const vals = y24.concat(yc).filter((v) => v != null);
    const lo = Math.min(60, Math.floor(Math.min(...vals) / 5) * 5), hi = Math.max(100, Math.ceil(Math.max(...vals) / 5) * 5);
    const f = frame(el, el.clientWidth < 500 ? 170 : 200, lo, hi, all.length);
    const line = (rows, ys, col) => {
      const pts = rows.map((r, i) => (ys[i] == null ? null : `${f.X(idx.get(r.origin_utc)).toFixed(1)},${f.Y(ys[i]).toFixed(1)}`)).filter(Boolean);
      return `<polyline points="${pts.join(" ")}" fill="none" stroke="${col}" stroke-width="1.6"/>`;
    };
    let b = yAxis(f, lo, hi, 10, "%") + xMonths(f, all);
    b += `<line x1="${f.pl}" x2="${f.W - f.pr}" y1="${f.Y(90)}" y2="${f.Y(90)}" stroke="${COL.target}" stroke-dasharray="4 3"/>`;
    b += line(rc, yc, COL.cash) + line(r24, y24, COL.teal);
    el.innerHTML = svg(f, b, "Rolling 60-day hit rate of the 90% range, 1pm to 1pm and cash session");
  }

  // live daily Kronos runs vs their own P10-P90 (data/band_check.json)
  async function kronosLive() {
    try {
      const b = await (await fetch(`data/band_check.json?t=${Date.now()}`, { cache: "no-store" })).json();
      const d = b.daily || { n: 0 };
      $("kronos-live").textContent = d.n
        ? `Live daily Kronos runs so far: ${d.n} matured; its P10–P90 held ${(100 * d.hit_rate).toFixed(0)}% (target 80%).`
        : "Live daily Kronos runs: none matured yet.";
    } catch (e) { /* optional */ }
  }

  function render() {
    chart24($("chart-24h")); chartCash($("chart-cash")); chartRoll($("chart-roll"));
  }

  async function load() {
    const r = await fetch(`data/vol_history.jsonl?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) { $("acc-intro").textContent = "Could not load data/vol_history.jsonl."; return; }
    ROWS = (await r.text()).split("\n").filter((s) => s.trim()).map((s) => JSON.parse(s))
      .sort((a, b) => Date.parse(a.origin_utc) - Date.parse(b.origin_utc));
    const m = ROWS.filter((x) => typeof x.inside90 === "boolean");
    const h = rate(m, "inside90"), c = rate(ROWS, "cash_inside90");
    $("acc-intro").innerHTML = `Daily volatility forecasts (HAR-RV since Nov 2026; the whole year is re-forecast with it) made from the 7:00–8:00am New York bar (1pm London), each using only data available then, ` +
      `for the 23 trading hours to 1pm London the next trading day. ` +
      `${m.length} matured forecasts (${day.format(new Date(m[0].origin_utc))} to ${day.format(new Date(m[m.length - 1].origin_utc))}): ` +
      `the actual move landed inside the 90% range <b>${pct(h.v)}</b> of the time; cash session <b>${pct(c.v)}</b> of ${c.n}.`;
    tables(); render();
    kronosLive();
  }

  let t = null;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => ROWS.length && render(), 150); });
  load();
})();
