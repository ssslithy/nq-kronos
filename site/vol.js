// NQ forecasts: data loading and everything on the page except the chart (app.js draws that).
// Reads data/vol_latest.json (daily vol forecast), vol_history.jsonl, vol_status.json,
// calendar.json, treasuries.json, fed_odds.json, news.json and since_close.json.
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
    soft("data/fed_odds.json"), soft("data/since_close.json"),
  ]).then(([vol, status, hist, calendar, tsy, news, fed, since]) =>
    ({ vol, status, hist, calendar, tsy, news, fed, since }));

  // ---- render -----------------------------------------------------------------------------
  function header(D) {
    const v = D.vol, close = T(v.forecast_time_utc);
    $("h-when").innerHTML = `${esc(date(close))} · ${esc(clock(close))} London · <span class="nq-price">${num(v.last_close)}</span>`;
    const next = v.next_forecast_utc ? T(v.next_forecast_utc) : null;
    $("h-sub").textContent = `Nasdaq-100 futures${next ? ` · Next Update ${at(next)}` : ""}`;
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
    // Which day and period: dated ends; "Next 24 hours" only for a normal 23-trading-hour window
    const hrs = v.horizon || 23;
    $("b-24h-label").textContent = hrs === 23 ? "Next 24 hours," : `Next ${hrs} trading hours,`;
    $("b-24h-when").innerHTML = `<span class="nq-span">${esc(`${date(a)} ${clock(a)}`)} –</span> ` +   // may break after the dash
      `<span class="nq-span">${esc(`${date(b)} ${clock(b)}`)}</span>`;
    const ecs = v.early_closes || [];
    if (ecs.length) {
      $("b-24h-incl").textContent = ecs.map((e) => `Includes the ${date(T(e.time_utc))} early close (${clock(T(e.time_utc))})`).join(" · ") +
        ". No forecast that day.";
      $("b-24h-incl").hidden = false;
    }
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
    const lbl = n >= 3 ? "High impact" : n === 2 ? "Medium impact" : "Low impact";
    return `<svg width="13" height="10" viewBox="0 0 13 10" role="img" aria-label="${lbl}"><title>${lbl}</title>` +
      `<rect x="0" y="6" width="3" height="4" rx="1" fill="${fill(0)}"/><rect x="5" y="3" width="3" height="7" rx="1" fill="${fill(1)}"/>` +
      `<rect x="10" y="0" width="3" height="10" rx="1" fill="${fill(2)}"/></svg>`;
  };

  // minSpan: smallest vertical range (data units), so a nearly flat line looks flat, not jittery
  const spark = (pts, color = "#5EE0C6", minSpan = 0) => {
    let lo = Math.min(...pts), hi = Math.max(...pts);
    const n = pts.length;
    if (hi - lo < minSpan) { const m = (hi + lo) / 2; lo = m - minSpan / 2; hi = m + minSpan / 2; }
    const y = (p) => (hi > lo ? 18 - 16 * (p - lo) / (hi - lo) : 10);
    const xy = pts.map((p, i) => `${(84 * i / (n - 1)).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 84 20" preserveAspectRatio="none" aria-hidden="true"><polyline points="${xy}" fill="none" ` +
      `stroke="${color}" stroke-width="1.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
  };
  const FED_COLOR = { Hike: "#F2B84B", Hold: "#93A1A9", Cut: "#5EE0C6" };
  // shown % (normalised, rounded to total 100 in page_data.py); "<1%" for a non-zero share rounded to 0
  const pct = (r) => { const v = r.pct != null ? r.pct : Math.round(100 * r.p); return v === 0 && r.p > 0 ? "<1%" : `${v}%`; };
  const ptsChange = (x) => { const r = Math.round(x); return r === 0 ? "0 pts" : `${r > 0 ? "+" : "−"}${Math.abs(r)} pts`; };

  // "Since US close": oil, VIX, dollar from the previous 4pm New York close to the forecast time.
  // Falls are teal, rises red (lower oil, volatility and dollar are good for NQ); missing = "–".
  function since(D) {
    const s = D.since;
    const cells = s && s.ok ? (s.cells || []) : [];
    if (!cells.some((c) => c.value != null)) return;
    $("since-h").textContent = s.window_label || "Since US close";
    $("since-cells").innerHTML = cells.map((c) => {
      if (c.value == null) return `<div class="c"><span class="k">${esc(c.label)}</span><span class="v na">–</span></div>`;
      const r = c.unit === "pct" ? Math.round(c.value * 10) / 10 : Math.round(c.value * 10) / 10;
      const cls = r > 0 ? "up" : r < 0 ? "down" : "flat";
      const txt = `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(1)}${c.unit === "pct" ? "%" : ""}`;
      return `<div class="c" title="${esc(c.ticker)}"><span class="k">${esc(c.label)}</span><span class="v ${cls}">${txt}</span></div>`;
    }).join("");
    $("since").hidden = false;
  }

  function side(D) {
    since(D);
    const c = D.calendar, t = D.tsy;
    if (c && c.ok && c.items && c.items.length) {
      $("cal-rows").innerHTML = c.items.map((e) => {
        const tm = T(e.time_utc);
        return `<div class="nq-cal${e.today ? " today" : ""}"><span class="d">${e.today ? "Today" : esc(date(tm))}</span>` +
          `<span class="t">${esc(clock(tm))}</span><span class="n">${esc(e.name)}</span>${e.impact ? signal(e.impact) : ""}</div>`;
      }).join("");
      $("cal").hidden = false;
      $("cal-h").classList.toggle("nq-h2", !$("since").hidden);
    }
    // yields: change in basis points over the window; up = red (prices down), down = teal
    // older treasuries.json files (before 10 Nov 2026) hold futures price moves (move_pct) instead
    const rows = t && t.ok ? (t.rows || []).filter((r) => (r.change_bp != null || r.move_pct != null) && r.spark && r.spark.length > 1) : [];
    const bp = (x) => { const r = Math.round(x); return r === 0 ? "0bp" : `${r > 0 ? "+" : "−"}${Math.abs(r)}bp`; };
    if (rows.length) {
      $("tsy-h").textContent = "US Treasuries";
      $("tsy-rows").innerHTML = rows.map((r) => {
        if (r.change_bp == null) {                       // old format: futures price move in %
          return `<div class="nq-tsy"><span class="l">${esc(r.label)}</span>${spark(r.spark)}` +
            `<span class="m${r.move_pct < 0 ? " neg" : ""}">${r.move_pct >= 0 ? "+" : "−"}${Math.abs(r.move_pct).toFixed(2)}%</span>` +
            `<span class="y">${r.yield_pct != null ? `Yield ${r.yield_pct.toFixed(2)}%` : ""}</span></div>`;
        }
        const dir = Math.round(r.change_bp) > 0 ? " up" : Math.round(r.change_bp) < 0 ? " down" : " flat";
        const col = dir === " up" ? "#F07A84" : dir === " down" ? "#5EE0C6" : "#93A1A9";
        return `<div class="nq-tsy"><span class="l">${esc(r.label)}</span>${spark(r.spark, col)}` +
          `<span class="m${dir}" title="${esc(r.source || "")}">${bp(r.change_bp)}</span>` +
          `<span class="y">${r.yield_pct != null ? `Yield ${r.yield_pct.toFixed(2)}%${r.approx ? "*" : ""}` : ""}</span></div>`;
      }).join("");
      $("tsy").hidden = false;
    }
    const fd = D.fed;
    const frows = fd && fd.ok ? (fd.rows || []).filter((r) => r.p != null && r.spark && r.spark.length > 1) : [];
    if (frows.length) {
      $("fed-h").textContent = `Fed · ${fd.meeting_label} Decision · Polymarket`;
      if (fd.event && fd.event.url) $("fed-h").href = fd.event.url;
      $("fed-h").title = `${fd.event ? fd.event.title : ""} Crowd betting odds, not a forecast from this site.`.trim();
      $("fed-rows").innerHTML = frows.map((r) =>
        `<div class="nq-tsy nq-fed"><span class="l">${esc(r.label)}</span>${spark(r.spark, FED_COLOR[r.label], 0.10)}` +
        `<span class="m" style="color:${FED_COLOR[r.label] || "#C9D4D9"}">${pct(r)}</span>` +
        `<span class="y">${ptsChange(r.change_pts)}</span></div>`).join("");
      $("fed").hidden = false;
      $("fed-h").classList.toggle("nq-h2", !$("tsy").hidden);
    }
    // one shared window label for the Treasuries and the Fed odds (built from the Treasuries' window)
    const win = rows.length ? t : (frows.length ? fd : null);
    if (win) {
      const a = T(win.window_start_utc), b = T(win.window_end_utc);
      $("mkt-win").textContent = win.window_label || `${day(a)} ${hourOnly(a)} – ${day(b)} ${hourOnly(b)}`;
      $("mkt-win").hidden = false;
      if ($("tsy").hidden) $("fed-h").classList.remove("nq-h2");
    }
    $("side").hidden = $("since").hidden && $("cal").hidden && $("tsy").hidden && $("fed").hidden;
  }

  function news(D) {
    const n = D.news;
    if (!(n && n.ok && ((n.items && n.items.length) || n.brief))) return;
    const when = (t) => `${day(t)} ${clock(t)}`;                                     // "Mon 11:39am"
    const box = $("news-rows");
    const b = n.brief;      // the pinned pre-market wrap: first, raised, grey BRIEF tag, no bars
    const briefRow = b ? `<div class="nq-item brief"><div class="nq-item-main"><div class="meta"><span class="src">` +
      `<span class="nq-tag">Brief</span>${esc(b.source)}</span><span class="tm">${esc(when(T(b.published_utc)))}</span></div>` +
      `<a class="hl" href="${esc(b.link)}" target="_blank" rel="noopener noreferrer">${esc(b.title)}</a></div></div>` : "";
    box.innerHTML = briefRow + n.items.map((i) =>
      `<div class="nq-item"><div class="nq-item-main"><div class="meta"><span class="src">${esc(i.source)}</span>` +
      `<span class="tm">${esc(when(T(i.published_utc)))}</span></div>` +
      `<a class="hl" href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></div>` +
      `${signal(i.impact || 1)}</div>`).join("");
    $("news").hidden = false;
    // show 3 rows at a time; the rest scroll (height follows the first three rows)
    const fit = () => {
      const rows = [...box.children].slice(0, 3);
      if (!rows.length) return;
      box.style.height = "";
      // from the list's top to the third row's bottom (margins included, e.g. the raised brief row)
      const h = rows[rows.length - 1].getBoundingClientRect().bottom - box.getBoundingClientRect().top + 1;
      box.style.height = box.children.length > 3 ? `${Math.ceil(h)}px` : "";
    };
    fit();
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(fit, 150); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
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
    const ev = (v.events && v.events.items) || [];
    const rows = [
      ["Window", `${at(a)} – ${at(b)} London: ${v.horizon || 23} trading hours, to the next forecast (9:00am New York on the next full trading day) ` +
        `(New York ${clock(a, NYC)} to ${clock(b, NYC)}). The daily break, weekends and holidays are skipped.` +
        (v.early_closes || []).map((e) => ` Includes the ${date(T(e.time_utc))} early close (1:00pm New York): ` +
          `${e.hours} hours that day (9:00am–1:00pm New York); there is no forecast that day.`).join("") +
        (v.window_factor && Math.abs(v.window_factor - 1) > 0.005 ? ` σ scaled ×${Math.sqrt(v.window_factor).toFixed(2)} for this window's hours.` : "")],
      ["PI", "Prediction interval: the range the price should end inside, with that probability, at the end of the window. " +
        "The chart can show the 99, 95, 90, 85 and 80% intervals; the card shows the 90%."],
      ["Model", (v.model.har
        ? `HAR-RV: the next 23 hours' variance from the last 1, 5 and 22 days' realised variance (hourly returns), ` +
          `refitted daily on the last ${v.model.har.n} days. Chosen in Nov 2026 because it reacts faster after shocks than the hourly GARCH it replaced. `
        : `GARCH(1,1), Student-t, ${num(v.model.n_returns)} hourly returns. `) +
        `Calibrated ×${v.calibration.k.toFixed(2)} ` +
        `(last ${v.calibration.n} forecasts); 85 / 90 / 95 / 99% use measured multipliers (fat tails): ` +
        `×${mu.m85.toFixed(2)} / ×${mu.m90.toFixed(2)} / ×${mu.m95.toFixed(2)} / ×${mu.m99.toFixed(2)} of σ ${v.sigma_pct.toFixed(2)}%; ` +
        `80% = ±1.28σ. The 99% rests on few observations (rough).`],
      ["Recent moves", v.realised_rms_5d_pct && v.realised_rms_20d_pct
        ? `average ${hourOnly(a)} → ${hourOnly(b)} move ${v.realised_rms_5d_pct.toFixed(2)}% (5 days) · ${v.realised_rms_20d_pct.toFixed(2)}% (20 days), root mean square`
        : "not enough history"],
    ];
    if (c.pts) {
      const open = new Date(T(c.start_utc).getTime() + 30 * 60e3);
      rows.push(["Cash session", `New York ${clock(open, NYC)}–${clock(T(c.end_utc), NYC)} = ${clock(open)}–${clock(T(c.end_utc))} London. ` +
        `The cash window and its range start from the ${clock(a)} forecast price (${clock(a, NYC)} New York, half an hour before the open). ` +
        `${c.level}% range = ` +
        (c.level === 90 ? `measured multiplier ×${c.mult.toFixed(2)}` : "±1.28") + ` × the cash σ (${c.variance_share ? Math.round(100 * c.variance_share) : "?"}% of the window's variance).`]);
    }
    if (ev.length) rows.push(["Releases", ev.map((e) => `${e.name} ${at(T(e.time_utc))}`).join(" · ") +
      ". Realised moves are often bigger on release days."]);
    if (v.roll_note) rows.push(["Roll", v.roll_note]);
    rows.push(["Fridays", "On Fridays the window runs over the weekend: weekend gap risk isn't fully in Friday's range."]);
    const ty = (D.tsy && D.tsy.rows) || [];
    const src = (lbl) => { const r = ty.find((x) => x.label === lbl); return r && r.source ? r.source : "n/a"; };
    if (D.since && D.since.ok) rows.push(["Since US close", "Oil (CL=F, % change), VIX (Cboe ^VIX, which trades from 3:15am New York, " +
      `change in points) and the dollar index (DX-Y.NYB, % change) from the previous US cash close (4:00pm New York, or 1:00pm after an early close) to ${hourOnly(a)} London. ` +
      `Falls are teal, rises red. Context only: overnight moves are already in the ${hourOnly(a)} price and don't predict the session ` +
      "(tested: the overnight move had no skill for the cash session)."]);
    rows.push(["Treasuries", `Yield changes in basis points (up = red). Window: the past 24 hours (${hourOnly(a)} → ${hourOnly(a)} London), or since the ` +
      "market reopened after a weekend or holiday. Mondays cover only the new week's trading (since Sunday's reopen). " +
      `Friday-afternoon moves after ${hourOnly(a)} aren't shown. ` + "The Fed odds use the same window and hourly timestamps. " +
      `10Y: ${src("10Y")}. 2Y: ${src("2Y")}.` + (ty.some((x) => x.approx) ? " * = approximate: derived from the note future's price change " +
      "÷ its duration (2Y: 1.9) and added to the latest official close (FRED DGS2 for the 2Y)." : "")]);
    rows.push(["Data", "Yahoo Finance NQ=F, hourly, delayed and unofficial, roll-adjusted. Calendar: BLS, BEA, Census and the Fed schedules. " +
      "Headlines: Yahoo Finance, CNBC, MarketWatch and Nasdaq.com RSS, published between the previous US cash close (4pm New York, 1pm after an early close) and the forecast time; " +
      "the bars are a rule-based significance (Fed and macro, the biggest Nasdaq names' earnings and major news = 3). " +
      "BRIEF = the latest pre-market market wrap (from 6am London to the forecast time), pinned first; other market wraps and movers roundups are left out."]);
    if (D.fed && D.fed.ok) rows.push(["Fed odds", `Polymarket crowd betting odds for the ${D.fed.meeting_label} FOMC decision ` +
      "(Hike = all increase outcomes, Cut = all decrease outcomes), hourly over the same window as the Treasuries. " +
      "Each outcome's price is its bid–ask midpoint; the three are scaled to add up to 100% (Polymarket's buckets usually sum to a " +
      "little over 100%), and the shown whole numbers are rounded to total exactly 100. " +
      "They are what bettors are pricing, not a forecast from this site."]);
    rows.push(["Updated", `${at(T(v.generated_utc))} London`]);
    $("details").innerHTML = rows.map(([k, x]) => `<dt>${esc(k)}</dt><dd>${esc(x)}</dd>`).join("") +
      `<dt></dt><dd class="disc">Volatility only, not direction. Not investment advice.</dd>`;
  }

  window.NQ = { ready, fmt: { clock, hourOnly, day, date, at, num, esc, T, plusH } };

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
