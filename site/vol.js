// NQ forecasts: data loading and everything on the page except the chart (app.js draws that).
// Reads data/vol_latest.json (daily GARCH forecast), vol_history.jsonl, vol_status.json,
// calendar.json, treasuries.json, news.json, and Kronos's latest.json / status.json.
// All times are London, 12-hour ("1:00pm"); New York appears only in Details.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const OVERDUE_MS = 90 * 60e3;          // the daily forecast is "late" this long after it was due
  const LON = "Europe/London", NYC = "America/New_York";

  // ---- formatting -------------------------------------------------------------------------
  const parts = (d, tz) => {
    const o = {};
    for (const p of new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short",
      hour: "numeric", minute: "2-digit", hourCycle: "h23" }).formatToParts(d)) o[p.type] = p.value;
    return o;
  };
  const clock = (d, tz = LON) => {               // 1:00pm, 12:42pm, 11:58am
    const p = parts(d, tz), h = +p.hour;
    return `${h % 12 || 12}:${p.minute}${h < 12 ? "am" : "pm"}`;
  };
  const hourOnly = (d, tz = LON) => clock(d, tz).replace(":00", "");      // 1pm, 12am
  const day = (d, tz = LON) => parts(d, tz).weekday;                      // Fri
  const date = (d, tz = LON) => { const p = parts(d, tz); return `${p.weekday} ${p.day} ${p.month}`; };   // Fri 6 Nov
  const at = (d) => `${day(d)} ${clock(d)}`;
  const num = (x, dp = 0) => x.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const T = (iso) => new Date(iso);
  const plusH = (iso, h) => new Date(T(iso).getTime() + h * 3600e3);

  // ---- data -------------------------------------------------------------------------------
  const getJSON = (p) => fetch(`${p}?t=${Date.now()}`, { cache: "no-store" }).then((r) => {
    if (!r.ok) throw new Error(`${p}: HTTP ${r.status}`);
    return r.json();
  });
  const getLines = (p) => fetch(`${p}?t=${Date.now()}`, { cache: "no-store" })
    .then((r) => (r.ok ? r.text() : ""))
    .then((t) => t.split("\n").filter((s) => s.trim()).map((s) => JSON.parse(s)))
    .catch(() => []);
  const soft = (p) => getJSON(p).catch(() => null);

  const ready = Promise.all([
    getJSON("data/vol_latest.json"), soft("data/vol_status.json"), getLines("data/vol_history.jsonl"),
    soft("data/calendar.json"), soft("data/treasuries.json"), soft("data/news.json"),
    soft("data/latest.json"), soft("data/status.json"),
  ]).then(([vol, status, hist, calendar, tsy, news, kronos, kstatus]) =>
    ({ vol, status, hist, calendar, tsy, news, kronos, kstatus }));

  // Kronos is usable only if it was made from the same bar as the GARCH forecast and isn't stale.
  const kronosState = (D) => {
    const k = D.kronos, v = D.vol;
    if (!k || !k.last_bar_utc) return { ok: false, why: "not run today" };
    if (T(k.last_bar_utc).getTime() !== T(v.origin_utc).getTime()) return { ok: false, why: "not run today" };
    if (k.stale || (D.kstatus && D.kstatus.ok === false)) return { ok: false, why: "not run today (last run failed)" };
    return { ok: true };
  };

  // ---- render -----------------------------------------------------------------------------
  function header(D) {
    const v = D.vol, close = T(v.forecast_time_utc);
    $("h-when").innerHTML = `${esc(date(close))} · ${esc(clock(close))} London · <span class="nq-price">${num(v.last_close)}</span>`;
    const next = v.next_forecast_utc ? T(v.next_forecast_utc) : null;
    $("h-sub").textContent = `Nasdaq-100 futures${next ? ` · next update ${at(next)}` : ""}`;
    document.title = `NQ ${date(close)} · ±${num(v.headline_pts)} pts`;
    // stale / late: the only warning on the page
    const failed = (D.status && D.status.ok === false) || v.stale;
    const late = next && Date.now() > next.getTime() + OVERDUE_MS;
    const b = $("banner");
    if (failed || late) {
      b.textContent = failed
        ? `The latest daily run failed. Showing the forecast from ${at(close)}.`
        : `The daily forecast due ${at(next)} hasn't arrived yet. Showing the one from ${at(close)}.`;
      b.hidden = false;
    }
  }

  function blocks(D) {
    const v = D.vol, lv = v.headline || 90;
    const a = T(v.forecast_time_utc), b = plusH(v.horizon_end_utc, 1);
    $("b-24h-when").textContent = `${at(a)} – ${at(b)}`;
    $("b-24h-pts").textContent = `±${num(v.headline_pts)} pts`;
    $("b-24h-pct").textContent = `±${v.headline_pct.toFixed(2)}%`;
    const r = v[`range${lv}`];
    $("b-24h-range").textContent = `${num(r[0])} – ${num(r[1])}`;
    $("b-note").textContent = `Based on the ${lv}% PI`;
    const c = v.cash || {};
    if (c.pts && c.range && c.start_utc && c.end_utc) {
      const open = new Date(T(c.start_utc).getTime() + 30 * 60e3);        // the 9:30am New York open
      $("b-cash-when").textContent = `${at(open)} – ${at(T(c.end_utc))}`;
      $("b-cash-pts").textContent = `±${num(c.pts)} pts`;
      $("b-cash-pct").textContent = `±${c.pct.toFixed(2)}%`;
      $("b-cash-range").textContent = `${num(c.range[0])} – ${num(c.range[1])}`;
      if (c.level !== lv) { $("b-cash-note").textContent = `Based on the ${c.level}% PI`; $("b-cash-note").hidden = false; }
      $("b-cash").hidden = false;
    }
  }

  const signal = (n) => {
    const fill = (i) => (i < n ? "#5EE0C6" : "#2A353C");
    const lbl = n >= 3 ? "High impact" : "Medium impact";
    return `<svg width="13" height="10" viewBox="0 0 13 10" role="img" aria-label="${lbl}"><title>${lbl}</title>` +
      `<rect x="0" y="6" width="3" height="4" rx="1" fill="${fill(0)}"/><rect x="5" y="3" width="3" height="7" rx="1" fill="${fill(1)}"/>` +
      `<rect x="10" y="0" width="3" height="10" rx="1" fill="${fill(2)}"/></svg>`;
  };

  const spark = (pts) => {
    const lo = Math.min(...pts), hi = Math.max(...pts), n = pts.length;
    const y = (p) => (hi > lo ? 18 - 16 * (p - lo) / (hi - lo) : 10);
    const xy = pts.map((p, i) => `${(84 * i / (n - 1)).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 84 20" preserveAspectRatio="none" aria-hidden="true"><polyline points="${xy}" fill="none" ` +
      `stroke="#5EE0C6" stroke-width="1.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
  };

  function side(D) {
    const c = D.calendar, t = D.tsy;
    if (c && c.ok && c.items && c.items.length) {
      $("cal-rows").innerHTML = c.items.map((e) => {
        const tm = T(e.time_utc);
        return `<div class="nq-cal${e.today ? " today" : ""}"><span class="d">${e.today ? "Today" : esc(date(tm))}</span>` +
          `<span class="t">${esc(clock(tm))}</span><span class="n">${esc(e.name)}</span>${signal(e.impact)}</div>`;
      }).join("");
      $("cal").hidden = false;
    }
    const rows = t && t.ok ? (t.rows || []).filter((r) => r.move_pct != null && r.spark && r.spark.length > 1) : [];
    if (rows.length) {
      const a = T(t.window_start_utc), b = T(t.window_end_utc);
      $("tsy-h").textContent = `US Treasuries · ${day(a)} ${hourOnly(a)} – ${day(b)} ${hourOnly(b)}`;
      $("tsy-rows").innerHTML = rows.map((r) =>
        `<div class="nq-tsy"><span class="l">${esc(r.label)}</span>${spark(r.spark)}` +
        `<span class="m${r.move_pct < 0 ? " neg" : ""}">${r.move_pct >= 0 ? "+" : "−"}${Math.abs(r.move_pct).toFixed(2)}%</span>` +
        `<span class="y">${r.yield_pct != null ? `Yield ${r.yield_pct.toFixed(2)}%` : ""}</span></div>`).join("");
      $("tsy").hidden = false;
      $("tsy-h").classList.toggle("nq-h2", !$("cal").hidden);
    }
    $("side").hidden = $("cal").hidden && $("tsy").hidden;
  }

  function news(D) {
    const n = D.news;
    if (!(n && n.ok && n.items && n.items.length)) return;
    $("news-rows").innerHTML = n.items.map((i) =>
      `<div class="nq-item"><div class="meta"><span class="src">${esc(i.source)}</span><span class="tm">${esc(clock(T(i.published_utc)))}</span></div>` +
      `<a class="hl" href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></div>`).join("");
    $("news").hidden = false;
  }

  function footer(D) {
    const v = D.vol, lv = v.headline || 90, key = `inside${lv}`;
    const m = D.hist.filter((r) => typeof r[key] === "boolean").slice(-250);
    if (m.length) {
      const hit = (100 * m.filter((r) => r[key]).length) / m.length;
      $("f-hit").textContent = `${lv}% PI – ${hit.toFixed(0)}% hit rate`;
      $("f-hit").parentElement.firstElementChild.textContent = `Last ${m.length} days:`;
    }
    const a = T(v.forecast_time_utc), b = plusH(v.horizon_end_utc, 1), mu = v.multipliers, c = v.cash || {};
    const ks = kronosState(D);
    const ev = (v.events && v.events.items) || [];
    const rows = [
      ["Window", `${at(a)} – ${at(b)} London: ${v.horizon || 23} trading hours, to the same time the next trading day ` +
        `(New York ${clock(a, NYC)} to ${clock(b, NYC)}). The daily break and weekends are skipped.`],
      ["PI", "Prediction interval: the range the price should end inside, with that probability, at the end of the window. " +
        "The chart can show the 99, 95, 90, 85 and 80% intervals; the card shows the 90%."],
      ["Model", `GARCH(1,1), Student-t, ${num(v.model.n_returns)} hourly returns, calibrated ×${v.calibration.k.toFixed(2)} ` +
        `(last ${v.calibration.n} forecasts); 85 / 90 / 95 / 99% use measured multipliers (fat tails): ` +
        `×${mu.m85.toFixed(2)} / ×${mu.m90.toFixed(2)} / ×${mu.m95.toFixed(2)} / ×${mu.m99.toFixed(2)} of σ ${v.sigma_pct.toFixed(2)}%; ` +
        `80% = ±1.28σ. The 99% rests on few observations (rough).`],
      ["Recent moves", v.realised_rms_5d_pct && v.realised_rms_20d_pct
        ? `average 1pm → 1pm move ${v.realised_rms_5d_pct.toFixed(2)}% (5 days) · ${v.realised_rms_20d_pct.toFixed(2)}% (20 days), root mean square`
        : "not enough history"],
    ];
    if (c.pts) {
      const open = new Date(T(c.start_utc).getTime() + 30 * 60e3);
      rows.push(["Cash session", `New York ${clock(open, NYC)}–${clock(T(c.end_utc), NYC)} = ${clock(open)}–${clock(T(c.end_utc))} London. ` +
        `The cash window is measured from the 9:00am New York bar, half an hour before the open. ` +
        `The cash range is centred on the ${clock(a)} price. ${c.level}% range = ` +
        (c.level === 90 ? `measured multiplier ×${c.mult.toFixed(2)}` : "±1.28") + ` × the cash σ (${c.variance_share ? Math.round(100 * c.variance_share) : "?"}% of the window's variance).`]);
    }
    if (ev.length) rows.push(["Releases", ev.map((e) => `${e.name} ${at(T(e.time_utc))}`).join(" · ") +
      ". Realised moves are often bigger on release days."]);
    if (v.roll_note) rows.push(["Roll", v.roll_note]);
    rows.push(["Fridays", "On Fridays the window runs over the weekend: weekend gap risk isn't fully in Friday's range."]);
    rows.push(["Kronos", "Kronos-small (experimental) can be overlaid on the chart (its P10 and P90 paths). Its 80% band held ~46% of the time vs the 80% target. " +
      (ks.ok ? "Today's run is from the same bar." : `Today: ${ks.why}.`)]);
    rows.push(["Data", "Yahoo Finance NQ=F, hourly, delayed and unofficial, roll-adjusted. Treasuries: ZT=F and ZN=F note futures (Yahoo), " +
      "yields FRED DGS2 (2Y) and Yahoo ^TNX (10Y), latest close before the forecast. Calendar: BLS, BEA, Census and the Fed schedules. " +
      "Headlines: Yahoo Finance and CNBC RSS, a 1pm snapshot."]);
    rows.push(["Updated", `${at(T(v.generated_utc))} London`]);
    $("details").innerHTML = rows.map(([k, x]) => `<dt>${esc(k)}</dt><dd>${esc(x)}</dd>`).join("") +
      `<dt></dt><dd class="disc">Volatility only, not direction. Not investment advice.</dd>`;
  }

  window.NQ = { ready, kronosState, fmt: { clock, hourOnly, day, date, at, num, esc, T, plusH } };

  ready.then((D) => {
    for (const f of [header, blocks, side, news, footer]) {
      try { f(D); } catch (e) { console.error(f.name, e); }
    }
  }).catch((e) => {
    $("h-when").textContent = "Forecast unavailable";
    const b = $("banner");
    b.textContent = `Couldn't load the forecast (${e.message}).`;
    b.hidden = false;
  });
})();
