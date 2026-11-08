"use strict";
// Pit lap viewer front end. Plain JS and SVG, no internet, no libraries.

const S = { days: [], idx: 0, day: null, view: "board", corner: null, version: -1, unit: "kmh" };
const $ = (s, el = document) => el.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

try { S.view = localStorage.getItem("lapviewer.view") || "board"; } catch (e) { /* storage blocked */ }

function fmtLap(sec) {
  if (sec == null || isNaN(sec)) return "";
  const m = Math.floor(sec / 60), s = sec - m * 60;
  return `${m}:${s.toFixed(3).padStart(6, "0")}`;
}
const f1 = (x) => (x == null || isNaN(x) ? "" : x.toFixed(1));
const f2 = (x) => (x == null || isNaN(x) ? "" : x.toFixed(2));
const spd = (kph) => (kph == null ? null : S.unit === "mph" ? kph * 0.621371 : kph);
const unit = () => (S.unit === "mph" ? "mph" : "km/h");
const short = (label) => String(label || "").split(" ")[0];
const turnsIn = (label) => (String(label || "").match(/T\d+/g) || []);
const dirName = (d) => (d === "CW" ? "Clockwise" : d === "CCW" ? "Counterclockwise" : d || "");
const niceDate = (iso) => {
  const d = new Date(iso + "T12:00:00");
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
};

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

// ---------------------------------------------------------------- tooltip
const tip = $("#tip");
function showTip(html, ev) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let x = ev.clientX + 16, y = ev.clientY + 16;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 16;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 16;
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}
const hideTip = () => { tip.hidden = true; };

// ---------------------------------------------------------------- data
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function loadDays(keepId) {
  S.days = await api("/api/days");
  const i = S.days.findIndex((d) => d.id === keepId);
  S.idx = i >= 0 ? i : 0;
  await loadDay();
}

async function loadDay() {
  const d = S.days[S.idx];
  S.day = d ? await api("/api/day/" + encodeURIComponent(d.id)) : null;
  if (S.day) {
    S.unit = S.day.speed_unit === "mph" ? "mph" : "kmh";
    const worst = [...S.day.corners].sort((a, b) => b.lost_s - a.lost_s)[0];
    if (!S.day.corners.some((c) => c.name === S.corner)) S.corner = worst ? worst.name : null;
  }
  render();
}

async function poll() {
  try {
    const st = await api("/api/status");
    const s = $("#status");
    const errs = Object.entries(st.errors || {});
    if (st.state === "working") {
      s.textContent = st.message || "Working";
      s.className = "status busy";
    } else {
      s.textContent = errs.length ? `${errs.length} file problem${errs.length > 1 ? "s" : ""}` : (st.last_scan ? `Checked ${st.last_scan}` : "");
      s.className = "status" + (errs.length ? " busy" : "");
      s.title = errs.map(([k, v]) => `${k}: ${v}`).join("\n");
    }
    S.folder = st.xrk_folder;
    if (st.version !== S.version) {
      const first = S.version === -1;
      S.version = st.version;
      await loadDays(first ? null : S.days[S.idx]?.id);
    } else if (!S.days.length) render();
  } catch (e) {
    console.error(e);
    $("#status").textContent = "Viewer stopped";
    $("#status").className = "status busy";
  }
}

// ---------------------------------------------------------------- shell
function render() {
  hideTip();
  const d = S.day, main = $("#main");
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.view === S.view)));
  $("#prev").disabled = S.idx >= S.days.length - 1;
  $("#next").disabled = S.idx <= 0;
  if (!d) {
    $("#dayname").textContent = "No days yet";
    main.innerHTML = `<div class="empty"><p>No sessions yet.</p>
      <p>Drop AiM <b>.xrk</b> files anywhere on this page, or put them in<br><code>${esc(S.folder || "the xrk folder")}</code></p>
      <p class="note">New files are picked up on their own. A day takes about half a minute to analyse.</p></div>`;
    return;
  }
  $("#dayname").innerHTML = `${esc(niceDate(d.date))} &middot; ${esc(d.vehicle || "")}<span class="dir">${esc(dirName(d.direction))}</span>`;
  main.innerHTML = "";
  if (S.view === "map") renderMap(main, d);
  else if (S.view === "corners") renderCorners(main, d);
  else renderBoard(main, d);
}

// ---------------------------------------------------------------- 1. session board
function renderBoard(main, d) {
  const flags = d.tire_flags || [];
  const tiles = [
    ["Best lap", d.best, `${d.best_lap.session} lap ${d.best_lap.lap}`],
    ["Ideal lap", d.ideal, `best of every section, ${d.laps_used} laps`],
    ["Gap to ideal", `${f2(d.gap_s)} s`, `whole corners only: ${fmtLap(d.ideal_whole_corners_s)}`],
  ];
  if (d.chicane) tiles.push(["Turn 6 chicane", `~${f1(d.chicane.per_lap_s)} s`, `per lap, range ${f2(d.chicane.low_s)} to ${f2(d.chicane.high_s)} s`]);
  tiles.push(["Tire signal", flags.length ? flags.map((f) => short(f.turn)).join(", ") : "None",
    flags.length ? "mid-corner speed fell every session" : "no turn slowed every session"]);
  const t = document.createElement("div");
  t.className = "tiles";
  t.innerHTML = tiles.map(([k, v, s]) => `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join("");
  main.appendChild(t);

  const ss = d.sessions;
  const scale = Math.max(...ss.map((s) => Math.max(s.on_track_min || 0, s.target_min || 0)), 1);
  const rows = ss.map((s) => {
    const met = s.target_min != null && s.on_track_min >= s.target_min - 0.05;
    const fill = s.on_track_min == null ? 0 : (100 * s.on_track_min) / scale;
    const tick = s.target_min == null ? null : (100 * s.target_min) / scale;
    const badge = s.target_min == null ? "" : met
      ? `<span class="badge good">&#10003; target ${s.target_min} min</span>`
      : `<span class="badge short">&#9888; ${f1(s.target_min - s.on_track_min)} min short of ${s.target_min}</span>`;
    const fade = s.fade_s == null ? `<span class="badge muted">too few laps</span>`
      : s.fade_s > 0.2 ? `<span class="badge short">&#9660; ${f2(s.fade_s)} s slower late</span>`
      : `<span class="badge good">&#10003; no fade</span>`;
    return `<tr>
      <td><span class="big">${s.number}</span><span class="sub">${s.warmup ? "warm-up" : esc(s.session)}</span></td>
      <td>${esc((s.start || "").slice(0, 5))}<span class="sub">${esc(s.warmup ? s.session : "")}</span></td>
      <td><span class="big">${f1(s.on_track_min)}</span> min ${badge}
        <div class="lenbar" title="On track ${f1(s.on_track_min)} min, target ${s.target_min ?? "-"} min">
          <div class="fill" style="width:${fill}%;background:${met ? "var(--good)" : "var(--warning)"}"></div>
          ${tick == null ? "" : `<div class="tick" style="left:${tick}%"></div>`}</div></td>
      <td class="num">${s.laps_full}<span class="sub">${s.laps_clean} clean</span></td>
      <td class="num"><span class="big">${esc(s.best)}</span><span class="sub">lap ${esc(s.best_lap_no)}</span></td>
      <td class="num">${s.gap_to_ideal_s == null ? "" : "+" + f2(s.gap_to_ideal_s) + " s"}<span class="sub">${s.session_ideal_s ? "session ideal " + fmtLap(s.session_ideal_s) : ""}</span></td>
      <td class="num">${s.std_dev_s == null ? "" : "&plusmn;" + f2(s.std_dev_s) + " s"}<span class="sub">${s.spread_s == null ? "" : "spread " + f2(s.spread_s) + " s"}</span></td>
      <td>${fade}</td></tr>`;
  }).join("");
  const p = document.createElement("div");
  p.className = "panel";
  p.innerHTML = `<table class="board"><thead><tr>
      <th>Session</th><th>Out</th><th>On track vs target</th><th class="num">Laps</th><th class="num">Best</th>
      <th class="num">Gap to day ideal</th><th class="num">Consistency</th><th>Fade</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="note">${esc(d.ramp.label)}: ${d.ramp.targets_min.join(", ")} min. Session 1 is the warm-up; read the day from session 2.
      Laps: AiM's out and in laps never count. Clean = within 7% of the session's median lap. ${esc(dirName(d.direction))}: compare only with other ${esc(dirName(d.direction).toLowerCase())} days.</p>`;
  main.appendChild(p);
}

// ---------------------------------------------------------------- 2. track map
function lossColor(v, max) {
  const steps = ["var(--seq-0)", "var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)"];
  const k = Math.min(steps.length - 1, Math.floor((Math.max(0, v) / max) * steps.length));
  return steps[k];
}

function sectionAt(d, secs) {
  for (const s of secs) {
    if (s.wrap ? d >= s.d0 || d <= s.d1 : d >= s.d0 && d <= s.d1) return s;
  }
  return secs[0];
}

function renderMap(main, d) {
  const wrap = document.createElement("div");
  wrap.className = "split";
  main.appendChild(wrap);
  const left = document.createElement("div");
  left.className = "panel col";
  const right = document.createElement("div");
  right.className = "panel";
  right.style.overflow = "auto";
  wrap.append(left, right);

  const maxLoss = Math.max(0.3, ...d.sections.map((s) => s.lost_s));
  left.innerHTML = `<h2>Where the ${esc(d.best)} lap lost time to the ${esc(d.ideal)} ideal</h2>
    <div class="legend"><span>Seconds lost per section</span><span class="scale">${[0, 1, 2, 3, 4].map((k) => `<span style="background:var(--seq-${k})"></span>`).join("")}</span>
    <span>0 to ${f2(maxLoss)} s</span><span>&#9632; start/finish &nbsp; &#10148; direction</span></div>`;
  const P = d.path;
  const xs = P.x, ys = P.y.map((v) => -v);   // north up
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const span = Math.max(maxX - minX, maxY - minY);
  const pad = span * 0.12;
  const box = document.createElement("div");
  box.className = "chart";
  left.appendChild(box);
  const svg = el("svg", { viewBox: `${minX - pad} ${minY - pad} ${maxX - minX + 2 * pad} ${maxY - minY + 2 * pad}`,
    role: "img", "aria-label": "Track map coloured by time lost" }, box);
  const lw = span * 0.022, fs = span * 0.042;
  el("polyline", { points: xs.map((x, i) => `${x},${ys[i]}`).join(" ") + ` ${xs[0]},${ys[0]}`, fill: "none",
    stroke: "var(--surface-2)", "stroke-width": lw * 1.9, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
  // contiguous runs of points in one section
  let run = [], cur = null;
  const flush = () => {
    if (run.length < 2) return;
    const s = cur;
    const pl = el("polyline", { points: run.map((i) => `${xs[i]},${ys[i]}`).join(" "), fill: "none",
      stroke: lossColor(s.lost_s, maxLoss), "stroke-width": lw, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg);
    const hit = el("polyline", { points: pl.getAttribute("points"), fill: "none", stroke: "transparent", "stroke-width": lw * 3.5 }, svg);
    hit.addEventListener("pointermove", (ev) => showTip(`<b>${esc(s.name)}</b><br>Best lap ${s.best_s.toFixed(2)} s, ideal ${s.ideal_s.toFixed(2)} s<br>Lost <b>${s.lost_s.toFixed(2)} s</b> (ideal from ${esc(s.ideal_from)})`, ev));
    hit.addEventListener("pointerleave", hideTip);
  };
  for (let i = 0; i < xs.length; i++) {
    const s = sectionAt(P.d[i], d.sections);
    if (s !== cur) {
      if (cur) { run.push(i); flush(); }
      run = [i]; cur = s;
    } else run.push(i);
  }
  run.push(0); flush();
  // labels
  const top = [...d.corners].sort((a, b) => b.lost_s - a.lost_s).slice(0, 3).filter((c) => c.lost_s >= 0.05);
  const placed = [];
  const clearance = (px, py) => {
    let m = Infinity;
    for (let i = 0; i < xs.length; i += 2) m = Math.min(m, Math.hypot(xs[i] - px, ys[i] - py));
    for (const [qx, qy] of placed) m = Math.min(m, Math.hypot(qx - px, qy - py) * 0.8);
    return m;
  };
  for (const a of d.apexes) {
    // put each label where it is farthest from the track and the other labels
    const ay = -a.y;
    let lx = a.x, ly = ay, bestC = -1;
    for (const r of [0.065, 0.09]) for (let k = 0; k < 16; k++) {
      const th = (k / 16) * 2 * Math.PI, px = a.x + Math.cos(th) * span * r, py = ay + Math.sin(th) * span * r;
      const cl = clearance(px, py) - (r > 0.07 ? span * 0.01 : 0);
      if (cl > bestC) { bestC = cl; lx = px; ly = py; }
    }
    placed.push([lx, ly]);
    const c = d.corners.find((c) => c.name.split("+").includes(a.label));
    const hot = c && top.includes(c) && c.name.split("+").slice(-1)[0] === a.label;
    const t = el("text", { x: lx, y: ly, "text-anchor": "middle", "dominant-baseline": "middle", "font-size": fs, "font-weight": 700 }, svg);
    t.textContent = short(a.label);
    if (hot) {
      const t2 = el("text", { x: lx, y: ly + fs * 1.05, "text-anchor": "middle", "dominant-baseline": "middle", "font-size": fs * 0.8, class: "sec" }, svg);
      t2.textContent = `${c.lost_s.toFixed(2)} s`;
    }
  }
  // start/finish and direction
  const sq = lw * 1.4;
  el("rect", { x: xs[0] - sq / 2, y: ys[0] - sq / 2, width: sq, height: sq, fill: "var(--text-primary)" }, svg);
  const j = Math.min(12, xs.length - 1);
  const ang = Math.atan2(ys[j] - ys[0], xs[j] - xs[0]);
  const ox = Math.sin(ang) * lw * 2.2, oy = -Math.cos(ang) * lw * 2.2;
  const ax = xs[0] + ox, ay2 = ys[0] + oy, bx = xs[j] + ox, by = ys[j] + oy;
  el("line", { x1: ax, y1: ay2, x2: bx, y2: by, stroke: "var(--text-primary)", "stroke-width": lw * 0.35 }, svg);
  const h = lw * 1.1;
  el("polygon", { points: `${bx},${by} ${bx - h * Math.cos(ang - 0.45)},${by - h * Math.sin(ang - 0.45)} ${bx - h * Math.cos(ang + 0.45)},${by - h * Math.sin(ang + 0.45)}`, fill: "var(--text-primary)" }, svg);

  // ranked list
  const ranked = [...d.corners].filter((c) => c.lost_s > 0.005).sort((a, b) => b.lost_s - a.lost_s).slice(0, 7);
  const mx = Math.max(...ranked.map((c) => c.lost_s), 0.01);
  right.innerHTML = `<h2>Corners, most time lost first</h2>
    <ul class="rank">${ranked.map((c) => `<li><span>${esc(c.name.replace(/\+/g, " + "))}</span>
      <span><span class="rbar" style="display:block;width:${(100 * c.lost_s) / mx}%"></span></span>
      <span class="rv">${c.lost_s.toFixed(2)} s</span></li>`).join("")}</ul>
    <p class="note">Lost = best lap's time through the corner minus your best time in each part of it (brake-in and drive-out) on any lap today. Sum of all corners = ${f2(d.gap_s)} s.</p>`;
}

// ---------------------------------------------------------------- 3. corner compare
function extTrace(d) {
  const L = d.lap_length_m, T = d.trace, D = [], B = [], I = [];
  for (const off of [-L, 0, L]) T.d.forEach((x, i) => { D.push(x + off); B.push(T.best_kph[i]); I.push(T.ideal_kph[i]); });
  return { D, B, I };
}

function lineChart(host, opts) {
  // draw after layout settles, so the chart is sized to its box
  const parent = document.createElement("div");
  parent.className = "chart";
  host.appendChild(parent);
  requestAnimationFrame(() => drawChart(parent, opts));
}

function drawChart(parent, { x0, x1, series, marks = [], shade = null, apexes = [], hover = true, small = false }) {
  const W = Math.max(300, parent.clientWidth), H = Math.max(60, parent.clientHeight);
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const fs = rem * (small ? 0.7 : 0.8);
  const m = { l: fs * 3.2, r: fs * 0.8, t: fs * 0.6, b: fs * (small ? 2.6 : 2.4) };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, parent);
  const all = [];
  series.forEach((s) => s.pts.forEach(([x, y]) => { if (x >= x0 && x <= x1) all.push(y); }));
  let y0 = Math.min(...all), y1 = Math.max(...all);
  const yr = Math.max(10, y1 - y0);
  const stepY = yr > 80 ? 20 : yr > 40 ? 10 : 5;
  y0 = Math.floor((y0 - yr * 0.08) / stepY) * stepY;
  y1 = Math.ceil((y1 + yr * 0.05) / stepY) * stepY;
  const X = (x) => m.l + ((x - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (y) => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);
  for (const [a, b] of shade || []) {
    const xa = X(Math.max(a, x0)), xb = X(Math.min(b, x1));
    if (xb > xa) el("rect", { x: xa, y: m.t, width: xb - xa, height: H - m.t - m.b, fill: "var(--surface-2)" }, svg);
  }
  for (let y = y0; y <= y1; y += stepY) {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(y), y2: Y(y), stroke: "var(--grid)", "stroke-width": 1 }, svg);
    const t = el("text", { x: m.l - 8, y: Y(y), "text-anchor": "end", "dominant-baseline": "middle", "font-size": fs * 0.85, class: "muted" }, svg);
    t.textContent = y;
  }
  for (const a of apexes) {
    if (a.d < x0 || a.d > x1) continue;
    const tt = el("text", { x: X(a.d), y: H - m.b + fs * 1.1, "text-anchor": "middle", "font-size": fs * 0.85, "font-weight": 650 }, svg);
    tt.textContent = short(a.label);
  }
  for (const mk of marks) {
    if (mk.x < x0 || mk.x > x1) continue;
    el("line", { x1: X(mk.x), x2: X(mk.x), y1: m.t, y2: H - m.b, stroke: mk.color, "stroke-width": 2, "stroke-dasharray": "6 5" }, svg);
    const mt = el("text", { x: X(mk.x) + (mk.left ? -6 : 6), y: m.t + fs * 0.9 + (mk.row || 0) * fs * 1.05, "text-anchor": mk.left ? "end" : "start", "font-size": fs * 0.85, class: "sec" }, svg);
    mt.textContent = mk.label;
  }
  for (const s of series) {
    const pts = s.pts.filter(([x]) => x >= x0 && x <= x1);
    el("polyline", { points: pts.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" "), fill: "none",
      stroke: s.color, "stroke-width": s.width || Math.max(2, rem * 0.13), "stroke-linejoin": "round" }, svg);
    const near = s.minRange ? pts.filter(([x]) => x >= s.minRange[0] && x <= s.minRange[1]) : pts;
    if (s.minDot && near.length) {
      const mn = near.reduce((a, b) => (b[1] < a[1] ? b : a));
      el("circle", { cx: X(mn[0]), cy: Y(mn[1]), r: Math.max(4, rem * 0.3), fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2 }, svg);
      const lt = el("text", { x: X(mn[0]) + (s.minBelow ? -1 : 1) * fs * 0.6, y: Y(mn[1]) + fs * (s.minBelow ? 1.4 : -0.9), "text-anchor": s.minBelow ? "end" : "start", "font-size": fs, "font-weight": 700 }, svg);
      lt.textContent = mn[1].toFixed(0);
    }
  }
  if (hover) {
    const cross = el("line", { y1: m.t, y2: H - m.b, stroke: "var(--text-muted)", "stroke-width": 1, visibility: "hidden" }, svg);
    const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
    hit.addEventListener("pointermove", (ev) => {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      const x = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      const vals = series.map((s) => {
        let best = s.pts[0], bd = Infinity;
        for (const p of s.pts) { const dd = Math.abs(p[0] - x); if (dd < bd) { bd = dd; best = p; } }
        return `<span style="color:${s.color}">&#9644;</span> ${esc(s.name)}: <b>${best[1].toFixed(1)}</b> ${unit()}`;
      });
      showTip(`${x.toFixed(0)} m<br>${vals.join("<br>")}`, ev);
    });
    hit.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
  }
  return svg;
}

function renderCorners(main, d) {
  const c = d.corners.find((x) => x.name === S.corner) || d.corners[0];
  const L = d.lap_length_m;
  const { D, B, I } = extTrace(d);
  const best = D.map((x, i) => [x, spd(B[i])]);
  const ideal = D.map((x, i) => [x, spd(I[i])]);
  const c0 = c.wrap ? c.d0 - L : c.d0, c1 = c.d1;
  const ph = d.corner_phases.filter((p) => p.corner === c.name);
  const pb = ph.find((p) => p.which === "best lap"), pf = ph.find((p) => p.which === "fastest through this corner");
  const apexes = [];
  for (const off of [-L, 0, L]) d.apexes.forEach((a) => apexes.push({ ...a, d: a.d + off }));

  // full-lap strip
  const strip = document.createElement("div");
  strip.className = "panel strip";
  strip.innerHTML = `<div class="legend"><span><span class="sw" style="background:var(--series-best)"></span>Best lap ${esc(d.best)} (${esc(d.best_lap.session)} lap ${d.best_lap.lap})</span>
    <span><span class="sw" style="background:var(--series-ideal)"></span>Ideal ${esc(d.ideal)} (your best in each section)</span>
    <span class="note">Speed in ${unit()} around the lap. Shaded: ${esc(c.name)}. Pick a corner below, or use the up and down keys.</span></div>`;
  main.appendChild(strip);
  const chips = document.createElement("div");
  chips.className = "corner-list";
  main.appendChild(chips);
  const wrap = document.createElement("div");
  wrap.className = "split";
  main.appendChild(wrap);
  lineChart(strip, { x0: 0, x1: L, small: true, shade: c0 < 0 ? [[0, c1], [c0 + L, L]] : [[c0, c1]], apexes,
    series: [{ name: "Best lap", color: "var(--series-best)", pts: best, width: 2 }, { name: "Ideal", color: "var(--series-ideal)", pts: ideal, width: 2 }] });

  const left = document.createElement("div");
  left.className = "panel col";
  const right = document.createElement("div");
  right.className = "panel";
  right.style.overflow = "auto";
  right.style.display = "flex";
  right.style.flexDirection = "column";
  right.style.gap = ".8rem";
  wrap.append(left, right);

  left.innerHTML = `<h2>${esc(c.name.replace(/\+/g, " + "))} &middot; lost ${c.lost_s.toFixed(2)} s
    <span class="note">(best lap ${c.best_s.toFixed(2)} s, ideal ${c.ideal_s.toFixed(2)} s; speed in ${unit()})</span></h2>`;
  const marks = [];
  const okB = (p) => p && p.brake_at_m != null && !isNaN(p.brake_at_m);
  if (okB(pb)) marks.push({ x: pb.brake_at_m, color: "var(--series-best)", label: "best lap brakes", row: 0,
    left: okB(pf) && pf.lap !== pb.lap && pf.brake_at_m > pb.brake_at_m });
  if (okB(pf) && pf.lap !== pb?.lap) {
    const dd = pf.brake_at_m - (okB(pb) ? pb.brake_at_m : pf.brake_at_m);
    marks.push({ x: pf.brake_at_m, color: "var(--text-muted)", row: 1, left: dd < 0,
      label: okB(pb) ? `fastest run brakes ${Math.abs(dd).toFixed(0)} m ${dd < 0 ? "earlier" : "later"}` : "fastest run brakes" });
  }
  const md = c.wrap && c.min_d > c1 ? c.min_d - L : c.min_d;
  const minRange = [Math.max(c0, md - 80), Math.min(c1, md + 80)];
  const bestMinBelow = (() => {
    const win = (pts) => pts.filter(([x]) => x >= minRange[0] && x <= minRange[1]);
    const mb = Math.min(...win(best).map((p) => p[1])), mi = Math.min(...win(ideal).map((p) => p[1]));
    return mb <= mi;
  })();
  lineChart(left, { x0: c0 - 40, x1: c1 + 40, apexes, marks,
    series: [{ name: "Best lap", color: "var(--series-best)", pts: best, minDot: true, minBelow: bestMinBelow, minRange },
      { name: "Ideal", color: "var(--series-ideal)", pts: ideal, minDot: true, minBelow: !bestMinBelow, minRange }] });

  // corner list
  const list = chips;
  const sorted = [...d.corners].sort((a, b) => b.lost_s - a.lost_s);
  list.innerHTML = sorted.map((x) => `<button aria-pressed="${x.name === c.name}" data-c="${esc(x.name)}"><span>${esc(x.name.replace(/\+/g, " + "))}</span><span class="lv">${x.lost_s.toFixed(2)} s</span></button>`).join("");
  list.addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (b) { S.corner = b.dataset.c; render(); }
  });

  // facts
  if (pb) {
    const u = unit();
    const rowsF = [
      ["Entry", (p) => `${spd(p.entry_kph).toFixed(0)} ${u}`],
      ["Brakes", (p) => (p.brake_at_m == null || isNaN(p.brake_at_m) ? "no hard braking" : `${(p.min_at_m - p.brake_at_m).toFixed(0)} m before slowest`)],
      ["Peak braking", (p) => `${p.peak_brake_g.toFixed(2)} g`],
      ["Slowest", (p) => `${spd(p.min_kph).toFixed(0)} ${u}`],
      ["Exit", (p) => `${spd(p.exit_kph).toFixed(0)} ${u}`],
      ["Corner time", (p) => `${p.corner_s.toFixed(2)} s`],
    ];
    const other = pf || pb;
    const fb = document.createElement("div");
    fb.innerHTML = `<h2>Best lap vs fastest single run through it</h2>
      <div class="facts"><span></span><span class="h">Best lap (${esc(pb.lap.replace(" lap ", " L"))})</span><span class="h">Fastest here (${esc(other.lap.replace(" lap ", " L"))})</span>
      ${rowsF.map(([k, fn]) => `<span class="lbl">${k}</span><span>${fn(pb)}</span><span>${fn(other)}</span>`).join("")}</div>`;
    right.appendChild(fb);
  }

  // tire signal for the turns in this corner
  const turns = turnsIn(c.name);
  const sessions = [...new Set(d.tire.map((r) => r.session))];
  const tb = document.createElement("div");
  tb.className = "tirebox";
  const lines = turns.map((t) => {
    const rows = d.tire.filter((r) => short(r.turn) === t);
    if (!rows.length) return "";
    const flagged = (d.tire_flags || []).some((f) => short(f.turn) === t);
    const seq = sessions.map((s) => rows.find((r) => r.session === s)).filter(Boolean)
      .map((r) => `${spd(r.min_kph).toFixed(0)}`).join(" &rarr; ");
    return `<div>${flagged ? '<span class="flag">&#9888; ' : "<span>"}${esc(rows[0].turn)}</span>: slowest ${seq} ${unit()} by session</div>`;
  }).join("");
  const allFlags = (d.tire_flags || []).map((f) => short(f.turn)).join(", ");
  tb.innerHTML = `<h2>Tire signal</h2>${lines || '<div class="note">No tire numbers for this corner.</div>'}
    <div class="note">${allFlags ? `Today it flags ${esc(allFlags)}: mid-corner speed and grip fell every session.` : "No turn lost mid-corner speed and grip every session today."}</div>`;
  right.appendChild(tb);
}

// ---------------------------------------------------------------- input
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setView(t.dataset.view)));
function setView(v) {
  S.view = v;
  try { localStorage.setItem("lapviewer.view", v); } catch (e) { /* ignore */ }
  render();
}
$("#prev").addEventListener("click", () => { if (S.idx < S.days.length - 1) { S.idx++; loadDay(); } });
$("#next").addEventListener("click", () => { if (S.idx > 0) { S.idx--; loadDay(); } });
$("#fs").addEventListener("click", toggleFs);
function toggleFs() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.();
}
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "1") setView("board");
  else if (e.key === "2") setView("map");
  else if (e.key === "3") setView("corners");
  else if (e.key === "f" || e.key === "F") toggleFs();
  else if (e.key === "ArrowLeft") $("#prev").click();
  else if (e.key === "ArrowRight") $("#next").click();
  else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && S.view === "corners" && S.day) {
    const sorted = [...S.day.corners].sort((a, b) => b.lost_s - a.lost_s);
    const i = sorted.findIndex((c) => c.name === S.corner);
    const j = Math.max(0, Math.min(sorted.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
    S.corner = sorted[j].name;
    render();
    e.preventDefault();
  }
});
addEventListener("resize", () => { if (S.view !== "board") render(); });

// drag and drop .xrk files
let dragDepth = 0;
addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("#drop").hidden = false; });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop").hidden = true; } });
addEventListener("drop", async (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#drop").hidden = true;
  const files = [...e.dataTransfer.files].filter((f) => /\.(xrk|xrz)$/i.test(f.name));
  for (const [i, f] of files.entries()) {
    $("#status").textContent = `Copying ${f.name} (${i + 1} of ${files.length})`;
    $("#status").className = "status busy";
    await fetch("/api/upload?name=" + encodeURIComponent(f.name), { method: "POST", body: f });
  }
  if (!files.length) { $("#status").textContent = "Only .xrk or .xrz files"; }
});

poll();
setInterval(poll, 3000);
