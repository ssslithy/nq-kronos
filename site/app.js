// NQ forecasts chart: 48 hourly candles, then the forecast fan to the end of the window
// (1pm -> 1pm London), as stacked translucent bands for the selected prediction intervals.
// Every selected band's upper and lower price is labelled at the right edge.
// Geometry follows design/Main.dc.html: an SVG on a 1000 x 408 grid, plot x 56..872.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ORDER = [99, 95, 90, 85, 80];
  const X0 = 56, X1 = 872, YT = 40, YB = 360;
  const C = { grid: "#1E272D", axis: "#93A1A9", up: "#6FD3B8", down: "#D9545F", last: "#F2B84B", band: "rgba(52,211,182,0.13)",
    bandEdge: "#34D3B6", pi: "#5EE0C6", now: "#5B6A72", nowText: "#C9D4D9", kronos: "#B79CFF" };
  const S = { shown: new Set(), kronos: false, D: null, kOk: false };

  const pressed = (b, on) => b.setAttribute("aria-pressed", on ? "true" : "false");

  function controls(D) {
    const lv = D.vol.headline || 90;
    S.shown = new Set([lv]);
    const box = $("pi-buttons");
    box.innerHTML = ORDER.map((l) => `<button type="button" data-l="${l}" aria-pressed="${l === lv}">${l}%</button>`).join("");
    box.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      const l = +b.dataset.l;
      S.shown.has(l) ? S.shown.delete(l) : S.shown.add(l);
      pressed(b, S.shown.has(l)); draw();
    });
    const ks = NQ.kronosState(D);
    S.kOk = ks.ok;
    const k = $("show-kronos"), kl = $("kronos-label");
    if (!ks.ok) {
      k.disabled = true; kl.classList.add("off"); kl.title = `Kronos (experimental): ${ks.why}`;
    }
    k.addEventListener("change", (e) => { S.kronos = e.target.checked; draw(); });
    const { at, plusH } = NQ.fmt;
    $("chart-title").textContent = `Hourly price · forecast to ${at(plusH(D.vol.horizon_end_utc, 1))} London`;
  }

  // nice tick step for the price axis
  function ticks(lo, hi) {
    for (const s of [25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000]) {
      const a = Math.ceil(lo / s) * s, out = [];
      for (let v = a; v <= hi; v += s) out.push(v);
      if (out.length <= 6) return out;
    }
    return [];
  }

  function draw() {
    const D = S.D; if (!D) return;
    const v = D.vol, f = v.fan, { num, T } = NQ.fmt;
    const el = $("chart");
    const compact = el.clientWidth < 700;
    const font = compact ? 20 : 13, gap = compact ? 22 : 15;
    const cd = v.candles, n = cd.length, H = f.p50.length;
    const step = (X1 - X0) / (n + H);
    const xc = (i) => X0 + (i + 0.5) * step;                 // candle i centre
    const xO = xc(n - 1);
    const xs = (j) => xO + j * step;                          // forecast step j (0 = origin)
    // visible area: the whole grid, or on phones the last ~13 candles plus the forecast
    const vb = compact ? [xO - 150, 16, 1012 - (xO - 150), 392] : [0, 0, 1000, 408];
    const vis = cd.filter((k, i) => xc(i) >= vb[0] + 4);
    // y range: visible candles, every band (so toggling a band never rescales), Kronos
    let lo = Math.min(...vis.map((k) => k.l)), hi = Math.max(...vis.map((k) => k.h));
    for (const l of ORDER) { lo = Math.min(lo, ...f.bands[l].lo); hi = Math.max(hi, ...f.bands[l].hi); }
    const kq = S.kOk && D.kronos ? D.kronos.quantiles : null;
    if (kq) { lo = Math.min(lo, ...kq.p10.slice(0, H)); hi = Math.max(hi, ...kq.p90.slice(0, H)); }
    const pad = (hi - lo) * 0.02; lo -= pad; hi += pad;
    const y = (p) => YB - (YB - YT) * (p - lo) / (hi - lo);
    const last = v.last_close, yL = y(last);
    const out = [];
    out.push(`<svg viewBox="${vb.join(" ")}" role="img" aria-label="NQ hourly candles with the forecast ranges">`);
    // grid + price labels
    const tk = ticks(lo, hi);
    out.push(`<g stroke="${C.grid}" stroke-width="1">` + tk.map((p) => `<line x1="${X0}" y1="${y(p).toFixed(1)}" x2="${X1}" y2="${y(p).toFixed(1)}"/>`).join("") + "</g>");
    out.push(`<g fill="${C.axis}" font-size="${font}" font-family="JetBrains Mono, monospace" text-anchor="end">` +
      tk.map((p) => `<text x="48" y="${(y(p) + 4).toFixed(1)}">${num(p)}</text>`).join("") + "</g>");
    // bands, widest first so the narrower ones stack on top
    for (const l of ORDER) {
      if (!S.shown.has(l)) continue;
      const b = f.bands[l];
      const up = b.hi.map((p, j) => `L${xs(j + 1).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
      const dn = b.lo.map((p, j) => `L${xs(j + 1).toFixed(1)},${y(p).toFixed(1)}`).reverse().join(" ");
      out.push(`<path d="M${xO.toFixed(1)},${yL.toFixed(1)} ${up} ${dn} Z" fill="${C.band}" stroke="${C.bandEdge}" stroke-opacity="0.45" stroke-width="1"/>`);
    }
    // last price
    out.push(`<line x1="${xO.toFixed(1)}" y1="${yL.toFixed(1)}" x2="${xs(H).toFixed(1)}" y2="${yL.toFixed(1)}" stroke="${C.last}" stroke-width="2" stroke-dasharray="6 5"/>`);
    // Kronos P10 / P90 paths (experimental)
    if (S.kronos && kq) {
      const kl = D.kronos.last_close;
      const path = (arr) => `M${xO.toFixed(1)},${y(kl).toFixed(1)} ` + arr.slice(0, H).map((p, j) => `L${xs(j + 1).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
      out.push(`<g fill="none" stroke="${C.kronos}" stroke-width="2" opacity="0.9"><path d="${path(kq.p90)}"/><path d="${path(kq.p10)}"/></g>`);
    }
    // candles
    const bw = step * 0.62;
    for (const [col, isUp] of [[C.up, true], [C.down, false]]) {
      out.push(`<g stroke="${col}" fill="${col}" stroke-width="1.4">`);
      cd.forEach((k, i) => {
        if ((k.c >= k.o) !== isUp) return;
        const x = xc(i), top = y(Math.max(k.o, k.c)), bot = y(Math.min(k.o, k.c));
        out.push(`<line x1="${x.toFixed(1)}" y1="${y(k.h).toFixed(1)}" x2="${x.toFixed(1)}" y2="${y(k.l).toFixed(1)}"/>` +
          `<rect x="${(x - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, bot - top).toFixed(1)}"/>`);
      });
      out.push("</g>");
    }
    // now marker
    const xn = xO + step / 2;
    out.push(`<line x1="${xn.toFixed(1)}" y1="24" x2="${xn.toFixed(1)}" y2="372" stroke="${C.now}" stroke-width="1" stroke-dasharray="3 4"/>` +
      `<text x="${(xn + 6.4).toFixed(1)}" y="${compact ? 40 : 36}" fill="${C.nowText}" font-size="${font}" font-family="Inter, sans-serif">now</text>`);
    // right-hand labels: last price, then every selected band's edges, nudged apart
    out.push(`<g font-family="JetBrains Mono, monospace" font-size="${font}">`);
    out.push(`<text x="878" y="${(yL + 4).toFixed(1)}" fill="${C.last}">${num(last)}</text>`);
    const tops = [], bots = [];
    for (const l of ORDER) {
      if (!S.shown.has(l)) continue;
      const b = f.bands[l];
      tops.push({ y: y(b.hi[H - 1]) + 4, t: `${l}%  ${num(b.hi[H - 1])}` });
      bots.push({ y: y(b.lo[H - 1]) + 4, t: `${l}%  ${num(b.lo[H - 1])}` });
    }
    // nudge outwards, away from the last-price label: tops upwards, bottoms downwards
    let lim = yL + 4 - gap;
    for (const t of tops.sort((a, b) => b.y - a.y)) { t.y = Math.min(t.y, lim); lim = t.y - gap; }
    lim = yL + 4 + gap;
    for (const t of bots.sort((a, b) => a.y - b.y)) { t.y = Math.max(t.y, lim); lim = t.y + gap; }
    for (const t of tops.concat(bots)) out.push(`<text x="878" y="${t.y.toFixed(1)}" fill="${C.pi}" xml:space="preserve">${t.t}</text>`);
    out.push("</g>");
    // time axis (London): bar closes; always the origin close and the window end
    const times = cd.map((k) => T(k.t)).concat(v.future_utc.map((t) => T(t)));
    const closeAt = (i) => new Date(times[i].getTime() + 3600e3);
    const xb = (i) => (i < n ? xc(i) : xs(i - n + 1)) + step / 2;   // boundary after slot i
    const end = n + H - 1;
    const lab = [{ i: n - 1, req: true }, { i: end, req: true }];
    const minGap = compact ? 170 : 110;
    const lonHour = (d) => +new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", hour: "numeric", hourCycle: "h23" }).format(d);
    for (const pref of [0, 8, 16, 4, 12, 20]) {
      for (let i = 0; i < end; i++) {
        if (lonHour(closeAt(i)) !== pref) continue;
        if (lab.every((q) => Math.abs(xb(q.i) - xb(i)) >= minGap)) lab.push({ i });
      }
    }
    const xmin = compact ? vb[0] + 40 : 70;
    out.push(`<g fill="${C.axis}" font-size="${font}" font-family="Inter, sans-serif" text-anchor="middle">` +
      lab.filter((q) => xb(q.i) >= xmin).map((q) => {
        const d = closeAt(q.i);
        const x = q.i === end ? Math.min(xb(q.i), 1000 - (compact ? 50 : 30)) : xb(q.i);
        return `<text x="${x.toFixed(1)}" y="${compact ? 400 : 396}">${NQ.fmt.day(d)} ${NQ.fmt.hourOnly(d)}</text>`;
      }).join("") + "</g>");
    out.push("</svg>");
    el.innerHTML = out.join("");
  }

  let rt;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(draw, 120); });
  NQ.ready.then((D) => { S.D = D; controls(D); draw(); }).catch((e) => console.error(e));
})();
