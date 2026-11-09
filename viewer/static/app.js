"use strict";
// Pit lap viewer front end. Plain JS and SVG, no internet, no libraries.

const S = { days: [], idx: 0, day: null, view: "board", corner: null, lap: null, session: null, stintX: "min", version: -1, unit: "kmh" };
const $ = (s, el = document) => el.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

try {
  S.view = localStorage.getItem("lapviewer.view") || "board";
  S.stintX = localStorage.getItem("lapviewer.stintx") === "lap" ? "lap" : "min";
} catch (e) { /* storage blocked */ }

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
  $('.tab[data-view="engine"]').hidden = !hasEngine(d);      // only for days whose logs carry engine channels
  $("#prev").disabled = S.idx >= S.days.length - 1;
  $("#next").disabled = S.idx <= 0;
  if (!d) {
    $("#dayname").textContent = "No days yet";
    main.innerHTML = `<div class="empty"><p>No sessions yet.</p>
      <p>Drop AiM <b>.xrk</b> files anywhere on this page, or put them in<br><code>${esc(S.folder || "the xrk folder")}</code></p>
      <p class="note">New files are picked up on their own. A day takes about half a minute to analyse.</p></div>`;
    return;
  }
  const where = d.track && d.track.name ? `${esc(d.track.name)} &middot; ` : "";
  $("#dayname").innerHTML = `<span class="what">${esc(niceDate(d.date))} &middot; ${where}${esc(d.vehicle || "")}</span><span class="dir">${esc(dirName(d.direction))}</span>`;
  main.innerHTML = "";
  if (S.view === "map") renderMap(main, d);
  else if (S.view === "corners") renderCorners(main, d);
  else if (S.view === "delta") renderDelta(main, d);
  else if (S.view === "grip") renderGrip(main, d);
  else if (S.view === "stint") renderStint(main, d);
  else if (S.view === "engine") renderEngine(main, d);
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
  if (d.chicane) tiles.push([d.chicane.name || "Chicane", `~${f1(d.chicane.per_lap_s)} s`, `per lap, range ${f2(d.chicane.low_s)} to ${f2(d.chicane.high_s)} s`]);
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
// how this day's turns got their numbers: the track's own table, or riding order when it has none
function turnNote(d) {
  const t = d.track || {};
  if (t.turn_numbers === "table") return t.turns_note || "Turn numbers are from this track's turn table.";
  if (t.turn_numbers === "riding order") {
    return "Turn numbers are in riding order from the start/finish line: this track has no turn table yet, so they may not match the official map, and they differ between directions.";
  }
  return "";
}

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
    <p class="note">Lost = best lap's time through the corner minus your best time in each part of it (brake-in and drive-out) on any lap today. Sum of all corners = ${f2(d.gap_s)} s.</p>
    <p class="note">${esc(turnNote(d))}</p>`;
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
  requestAnimationFrame(() => { if (parent.isConnected) drawChart(parent, opts); });   // the view may have changed already
}

// a round tick step that leaves at most maxTicks gridlines over the range
function niceStep(range, maxTicks) {
  for (const st of [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]) if (range / st <= maxTicks) return st;
  return 1000;
}

// Line chart against distance. Defaults suit speed; pass yFmt/tipFmt/minRange for other units.
//   zero: true      keep 0 in view (the caller draws the zero series)
//   series[].end    text to print at the line's right-hand end
//   series[].dash   SVG dash pattern
function drawChart(parent, { x0, x1, series, marks = [], shade = null, apexes = [], hover = true, small = false,
  minRange = 10, zero = false, yFmt = (y) => String(Math.round(y)), tipFmt = (v) => `<b>${v.toFixed(1)}</b> ${unit()}` }) {
  const W = Math.max(300, parent.clientWidth), H = Math.max(60, parent.clientHeight);
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const fs = rem * (small ? 0.7 : 0.8);
  const ends = series.some((sr) => sr.end);
  const m = { l: fs * 3.4, r: fs * (ends ? 4.4 : 0.8), t: fs * 0.6, b: fs * (small ? 2.6 : 2.4) };
  if (H - m.t - m.b < 10) return null;    // no room to draw (the panel is still collapsing)
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, parent);
  const all = [];
  series.forEach((sr) => sr.pts.forEach(([x, y]) => { if (x >= x0 && x <= x1) all.push(y); }));
  let lo = Math.min(...all), hi = Math.max(...all);
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (hi - lo < minRange) { const mid = (hi + lo) / 2; lo = mid - minRange / 2; hi = mid + minRange / 2; }
  // the lines fill the plot whatever its height; gridlines sit on round values inside that range
  const yr = hi - lo, y0 = lo - yr * 0.08, y1 = hi + yr * 0.06;
  const stepY = niceStep(y1 - y0, Math.max(2, Math.floor((H - m.t - m.b) / (fs * 1.7))));
  const X = (x) => m.l + ((x - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (y) => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);
  for (const [a, b] of shade || []) {
    const xa = X(Math.max(a, x0)), xb = X(Math.min(b, x1));
    if (xb > xa) el("rect", { x: xa, y: m.t, width: xb - xa, height: H - m.t - m.b, fill: "var(--surface-2)" }, svg);
  }
  for (let i = Math.ceil(y0 / stepY - 1e-9); i * stepY <= y1 + 1e-9; i++) {
    const y = i * stepY;
    el("line", { x1: m.l, x2: W - m.r, y1: Y(y), y2: Y(y), stroke: "var(--grid)", "stroke-width": 1 }, svg);
    const t = el("text", { x: m.l - 8, y: Y(y), "text-anchor": "end", "dominant-baseline": "middle", "font-size": fs * 0.85, class: "muted" }, svg);
    t.textContent = yFmt(Math.abs(y) < stepY / 1000 ? 0 : y, stepY);
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
  const endLabels = [];
  for (const sr of series) {
    const pts = sr.pts.filter(([x]) => x >= x0 && x <= x1);
    const attrs = { points: pts.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" "), fill: "none",
      stroke: sr.color, "stroke-width": sr.width || Math.max(2, rem * 0.13), "stroke-linejoin": "round" };
    if (sr.dash) attrs["stroke-dasharray"] = sr.dash;
    el("polyline", attrs, svg);
    if (sr.end && pts.length) endLabels.push({ y: Y(pts[pts.length - 1][1]), text: sr.end, color: sr.color });
    const near = sr.minRange ? pts.filter(([x]) => x >= sr.minRange[0] && x <= sr.minRange[1]) : pts;
    if (sr.minDot && near.length) {
      const mn = near.reduce((a, b) => (b[1] < a[1] ? b : a));
      el("circle", { cx: X(mn[0]), cy: Y(mn[1]), r: Math.max(4, rem * 0.3), fill: sr.color, stroke: "var(--surface-1)", "stroke-width": 2 }, svg);
      const lt = el("text", { x: X(mn[0]) + (sr.minBelow ? -1 : 1) * fs * 0.6, y: Y(mn[1]) + fs * (sr.minBelow ? 1.4 : -0.9), "text-anchor": sr.minBelow ? "end" : "start", "font-size": fs, "font-weight": 700 }, svg);
      lt.textContent = mn[1].toFixed(0);
    }
  }
  // end labels: a dot in the series colour and the value in text ink, nudged apart where lines end close together
  endLabels.sort((a, b) => a.y - b.y);
  for (let i = 1; i < endLabels.length; i++) endLabels[i].ty = Math.max(endLabels[i].y, (endLabels[i - 1].ty ?? endLabels[i - 1].y) + fs * 1.05);
  for (const e of endLabels) {
    el("circle", { cx: W - m.r, cy: e.y, r: Math.max(3, rem * 0.18), fill: e.color, stroke: "var(--surface-1)", "stroke-width": 2 }, svg);
    const t = el("text", { x: W - m.r + fs * 0.5, y: e.ty ?? e.y, "dominant-baseline": "middle", "font-size": fs * 0.9, "font-weight": 650 }, svg);
    t.textContent = e.text;
  }
  if (hover) {
    const cross = el("line", { y1: m.t, y2: H - m.b, stroke: "var(--text-muted)", "stroke-width": 1, visibility: "hidden" }, svg);
    const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
    hit.addEventListener("pointermove", (ev) => {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      const x = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      const vals = series.map((sr) => {
        let best = sr.pts[0], bd = Infinity;
        for (const p of sr.pts) { const dd = Math.abs(p[0] - x); if (dd < bd) { bd = dd; best = p; } }
        return `<span style="color:${sr.color}">&#9644;</span> ${esc(sr.name)}: ${tipFmt(best[1], sr)}`;
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

// ---------------------------------------------------------------- 4. time delta
// one fixed colour per session number, the same in every view
const sessionColor = (n) => (n >= 1 && n <= 6 ? `var(--session-${n})` : "var(--text-muted)");
const signed = (v, dp = 2) => (v > 0 ? "+" : v < 0 ? "&minus;" : "") + Math.abs(v).toFixed(dp);
const lapKey = (l) => `${l.session}:${l.lap}`;

// laps that can be picked on the delta view, in session then lap order (the best lap is the zero line)
function deltaLaps(d) {
  const D = d.delta;
  if (!D) return [];
  const order = new Map(d.sessions.map((x, i) => [x.session, i]));
  return [...D.laps].sort((a, b) => (order.get(a.session) ?? 99) - (order.get(b.session) ?? 99) || a.lap - b.lap);
}
const isRef = (d, l) => l.session === d.best_lap.session && l.lap === d.best_lap.lap;

// time through each corner (its brake-in and drive-out parts together) for one lap's section times
function cornerTimes(d, sec) {
  return d.corners.map((c) => d.sections.reduce((t, x, k) => (x.corner === c.name ? t + sec[k] : t), 0));
}

// upright bars either side of zero: up = time lost, down = time gained
function drawBars(parent, items, color) {
  const W = Math.max(300, parent.clientWidth), H = Math.max(60, parent.clientHeight);
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const fs = rem * 0.72;
  const m = { l: fs * 3.4, r: fs * 0.8, t: fs * 1.5, b: fs * 1.7 };
  if (H - m.t - m.b < 10) return;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Time lost or gained in each corner" }, parent);
  const top = Math.max(0.05, ...items.map((i) => i.v)), bot = Math.min(0, ...items.map((i) => i.v));
  const Y = (v) => m.t + ((top - v) / (top - bot)) * (H - m.t - m.b - (bot < 0 ? fs * 1.3 : 0));
  const band = (W - m.l - m.r) / items.length, bw = Math.min(band * 0.55, rem * 1.1), r = Math.min(4, bw / 2);
  el("line", { x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0), stroke: "var(--line)", "stroke-width": 1 }, svg);
  const zt = el("text", { x: m.l - 8, y: Y(0), "text-anchor": "end", "dominant-baseline": "middle", "font-size": fs * 0.85, class: "muted" }, svg);
  zt.textContent = "0";
  items.forEach((it, i) => {
    const cx = m.l + band * (i + 0.5), x = cx - bw / 2, ya = Y(0), yb = Y(it.v), up = it.v >= 0;
    if (Math.abs(yb - ya) >= 1) {
      const rr = Math.min(r, Math.abs(yb - ya)), k = up ? 1 : -1;     // rounded at the data end, square on the zero line
      el("path", { d: `M${x},${ya} L${x},${yb + k * rr} Q${x},${yb} ${x + rr},${yb} L${x + bw - rr},${yb} Q${x + bw},${yb} ${x + bw},${yb + k * rr} L${x + bw},${ya} Z`, fill: color }, svg);
    }
    const vt = el("text", { x: cx, y: up ? yb - fs * 0.45 : yb + fs * 1.05, "text-anchor": "middle", "font-size": fs * 0.85, "font-weight": 650 }, svg);
    vt.innerHTML = signed(it.v);
    const lt = el("text", { x: cx, y: H - fs * 0.45, "text-anchor": "middle", "font-size": fs * 0.85, class: "sec" }, svg);
    lt.textContent = it.label;
    const hit = el("rect", { x: cx - band / 2, y: m.t - fs, width: band, height: H - m.t, fill: "transparent" }, svg);
    hit.addEventListener("pointermove", (ev) => showTip(`<b>${esc(it.name)}</b><br>${it.v >= 0 ? "Lost" : "Gained"} <b>${Math.abs(it.v).toFixed(2)} s</b> ${esc(it.against)}`, ev));
    hit.addEventListener("pointerleave", hideTip);
  });
}

function renderDelta(main, d) {
  const D = d.delta;
  if (!D || !D.laps.length) {
    main.innerHTML = `<div class="empty"><p>No delta trace for this day yet.</p>
      <p class="note">It was analysed before this view existed. It is analysed again on its own; the status at the top right says when.</p></div>`;
    return;
  }
  const laps = deltaLaps(d);
  const pick = laps.find((l) => lapKey(l) === S.lap && !isRef(d, l)) || null;
  const num = new Map(d.sessions.map((x) => [x.session, x.number]));
  const pts = (cs) => D.d.map((x, i) => [x, cs[i] / 100]);
  const L = d.lap_length_m;
  const series = [{ name: "Best lap", color: "var(--series-best)", pts: [[0, 0], [L, 0]], width: 2 },
    { name: "Ideal", color: "var(--series-ideal)", pts: pts(D.ideal_cs), end: signed(D.ideal_cs[D.ideal_cs.length - 1] / 100).replace("&minus;", "−") }];
  const legend = [`<span><span class="sw" style="background:var(--series-best)"></span>Best lap ${esc(d.best)} (zero line)</span>`,
    `<span><span class="sw" style="background:var(--series-ideal)"></span>Ideal ${esc(d.ideal)}</span>`];
  for (const l of laps.filter((x) => x.session_best && !isRef(d, x))) {
    const n = num.get(l.session);
    if (pick && lapKey(pick) === lapKey(l)) continue;      // drawn as the picked lap instead
    series.push({ name: `Session ${n} best`, color: sessionColor(n), pts: pts(l.cs) });
    legend.push(`<span><span class="sw" style="background:${sessionColor(n)}"></span>Session ${n} best ${fmtLap(l.time_s)}</span>`);
  }
  if (pick) {
    const gap = pick.time_s - d.best_lap.time_s;
    series.push({ name: `${pick.session} lap ${pick.lap}`, color: "var(--text-primary)", pts: pts(pick.cs), width: 3,
      end: signed(pick.cs[pick.cs.length - 1] / 100).replace("&minus;", "−") });
    legend.push(`<span><span class="sw" style="background:var(--text-primary)"></span><b>${esc(pick.session)} lap ${pick.lap}</b> ${fmtLap(pick.time_s)} (${signed(gap)} s)</span>`);
  }

  const wrap = document.createElement("div");
  wrap.className = "split";
  main.appendChild(wrap);
  const left = document.createElement("div");
  left.className = "panel col";
  const right = document.createElement("div");
  right.className = "panel laps";
  wrap.append(left, right);

  left.innerHTML = `<h2>Seconds behind the best lap, around the lap
      <span class="note">(${esc(d.best_lap.session)} lap ${d.best_lap.lap}; a line that climbs is losing time there, one that falls is gaining)</span></h2>
    <div class="legend">${legend.join("")}</div>`;
  const apexes = d.apexes.map((a) => ({ ...a }));
  lineChart(left, { x0: 0, x1: L, apexes, series, zero: true, minRange: 0.2,
    yFmt: (y, st) => (y > 0 ? "+" : y < 0 ? "−" : "") + Math.abs(y).toFixed(st < 0.1 ? 2 : 1),
    tipFmt: (v) => `<b>${signed(v)} s</b>` });

  // by corner: the picked lap, or the ideal when none is picked
  const bestLap = d.laps.find((r) => r.session === d.best_lap.session && r.lap === d.best_lap.lap);
  const pickRow = pick && d.laps.find((r) => r.session === pick.session && r.lap === pick.lap);
  let items = null, title = "", color = "var(--series-ideal)";
  if (pick && pickRow && pickRow.sec_s && bestLap && bestLap.sec_s) {
    const a = cornerTimes(d, pickRow.sec_s), b = cornerTimes(d, bestLap.sec_s);
    items = d.corners.map((c, i) => ({ label: turnsIn(c.name).join("+") || c.name, name: c.name.replace(/\+/g, " + "), v: a[i] - b[i], against: "to the best lap" }));
    title = `By corner: ${esc(pick.session)} lap ${pick.lap} against the best lap`;
    color = "var(--text-secondary)";
  } else {
    items = d.corners.map((c) => ({ label: turnsIn(c.name).join("+") || c.name, name: c.name.replace(/\+/g, " + "), v: -c.lost_s, against: "by the ideal lap on the best lap" }));
    title = "By corner: the ideal lap against the best lap";
  }
  const bh = document.createElement("h2");
  bh.className = "sub";
  bh.innerHTML = `${title} <span class="note">(seconds; up = lost, down = gained)</span>`;
  left.appendChild(bh);
  const bars = document.createElement("div");
  bars.className = "chart bars";
  left.appendChild(bars);
  requestAnimationFrame(() => { if (bars.isConnected) drawBars(bars, items, color); });

  // lap picker
  const groups = d.sessions.map((sx) => {
    const mine = laps.filter((l) => l.session === sx.session);
    if (!mine.length) return "";
    const chips = mine.map((l) => {
      const ref = isRef(d, l), gap = l.time_s - d.best_lap.time_s;
      return `<button data-lap="${esc(lapKey(l))}" aria-pressed="${!!pick && lapKey(pick) === lapKey(l)}" class="${ref ? "ref" : ""}${l.used ? "" : " slow"}"
        ${ref ? "disabled" : ""} title="${ref ? "The best lap: the zero line" : `${signed(gap)} s to the best lap${l.used ? "" : "; more than 3% off, so not in the ideal"}`.replace("&minus;", "-")}">
        <span class="n">L${l.lap}</span><span>${fmtLap(l.time_s)}</span>${ref ? '<span class="tag">best</span>' : l.session_best ? '<span class="tag">session best</span>' : ""}</button>`;
    }).join("");
    return `<div class="lapgroup"><div class="lg-h"><span class="sw" style="background:${sessionColor(sx.number)}"></span>Session ${sx.number}<span class="note">${esc(sx.session)}, out ${esc((sx.start || "").slice(0, 5))}</span></div>
      <div class="lapchips">${chips}</div></div>`;
  }).join("");
  right.innerHTML = `<h2>Pick a lap to compare</h2>${groups}
    <p class="note">Up and down keys step through the laps; pick the same lap again to clear it. The ideal line falls where the best lap left time.
    Every lap in this direction that the GPS recorded fully is here. Greyed times are more than 3% off the best, so they are not part of the ideal.</p>`;
  right.addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-lap]");
    if (!b || b.disabled) return;
    S.lap = S.lap === b.dataset.lap ? null : b.dataset.lap;
    render();
  });
  const on = right.querySelector('button[aria-pressed="true"]');
  if (on) on.scrollIntoView({ block: "nearest" });
}

// ---------------------------------------------------------------- 5. grip (lean and the grip circle)
const inCorner = (dist, c) => (c.wrap ? dist >= c.d0 || dist <= c.d1 : dist >= c.d0 && dist <= c.d1);
const deg = (v) => (v == null || isNaN(v) ? "" : `${v.toFixed(1)}°`);

// cornering g (left and right) against acceleration and braking g, one dot per moment of the best lap
function drawGG(parent, d, corner) {
  const G = d.grip, W = Math.max(200, parent.clientWidth), H = Math.max(200, parent.clientHeight);
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const fs = rem * 0.75, size = Math.min(W, H), cx = W / 2, cy = H / 2;
  const env = G.envelope.g100.map((v) => v / 100), step = (G.envelope.step_deg * Math.PI) / 180;
  const pts = G.gg.d.map((x, i) => ({ d: x, lat: G.gg.lat100[i] / 100, lon: G.gg.lon100[i] / 100 }));
  const reach = Math.max(1.05, ...env, ...pts.map((p) => Math.hypot(p.lat, p.lon)));
  const rMax = Math.ceil(reach * 20 + 1) / 20, k = Math.min(H / 2 - fs * 1.7, W / 2 - fs * 5.5) / rMax;
  const X = (lat) => cx + lat * k, Y = (lon) => cy - lon * k;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Grip circle of the best lap" }, parent);
  for (let r = 0.5; r <= rMax + 1e-9; r += 0.5) {
    el("circle", { cx, cy, r: r * k, fill: "none", stroke: "var(--grid)", "stroke-width": 1 }, svg);
    const t = el("text", { x: cx + 4, y: cy - r * k - 3, "font-size": fs * 0.85, class: "muted" }, svg);
    t.textContent = `${r.toFixed(1)} g`;
  }
  el("line", { x1: cx - rMax * k, x2: cx + rMax * k, y1: cy, y2: cy, stroke: "var(--grid)", "stroke-width": 1 }, svg);
  el("line", { x1: cx, x2: cx, y1: cy - rMax * k, y2: cy + rMax * k, stroke: "var(--grid)", "stroke-width": 1 }, svg);
  for (const [txt, x, y, anchor] of [["accelerating", cx, cy - rMax * k - fs * 0.45, "middle"], ["braking", cx, cy + rMax * k + fs * 1.15, "middle"],
    ["left turn", cx - rMax * k - fs * 0.5, cy + fs * 0.3, "end"], ["right turn", cx + rMax * k + fs * 0.5, cy + fs * 0.3, "start"]]) {
    const t = el("text", { x, y, "text-anchor": anchor, "font-size": fs * 0.9, class: "sec" }, svg);
    t.textContent = txt;
  }
  el("polygon", { points: env.map((r, i) => `${X(r * Math.cos(i * step)).toFixed(1)},${Y(r * Math.sin(i * step)).toFixed(1)}`).join(" "),
    fill: "var(--surface-2)", "fill-opacity": 0.55, stroke: "var(--text-secondary)", "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  const mine = (p) => corner && inCorner(p.d, corner);
  const r0 = Math.max(2, rem * 0.09), r1 = Math.max(4, rem * 0.17);
  for (const p of pts) if (!mine(p)) el("circle", { cx: X(p.lat), cy: Y(p.lon), r: r0, fill: "var(--series-best)", "fill-opacity": corner ? 0.4 : 0.7 }, svg);
  for (const p of pts) if (mine(p)) el("circle", { cx: X(p.lat), cy: Y(p.lon), r: r1, fill: "var(--text-primary)", stroke: "var(--surface-1)", "stroke-width": 1.5 }, svg);
  // one hit area: the tooltip follows the nearest dot
  const outline = (lat, lon) => {
    const pos = ((Math.atan2(lon, lat) / step) % env.length + env.length) % env.length, lo = Math.floor(pos) % env.length, f = pos - Math.floor(pos);
    return env[lo] * (1 - f) + env[(lo + 1) % env.length] * f;
  };
  const hit = el("rect", { x: 0, y: 0, width: W, height: H, fill: "transparent" }, svg);
  hit.addEventListener("pointermove", (ev) => {
    const b = svg.getBoundingClientRect(), px = ((ev.clientX - b.left) / b.width) * W, py = ((ev.clientY - b.top) / b.height) * H;
    let best = null, bd = Infinity;
    for (const p of pts) { const dd = Math.hypot(X(p.lat) - px, Y(p.lon) - py); if (dd < bd) { bd = dd; best = p; } }
    if (!best || bd > rem * 1.5) { hideTip(); return; }
    const c = d.corners.find((x) => inCorner(best.d, x));
    const share = Math.round((100 * Math.hypot(best.lat, best.lon)) / outline(best.lat, best.lon));
    showTip(`<b>${esc(c ? c.name.replace(/\+/g, " + ") : "")}</b> at ${best.d.toFixed(0)} m<br>Cornering <b>${Math.abs(best.lat).toFixed(2)} g</b> ${best.lat >= 0 ? "right" : "left"} (about ${deg((Math.atan(Math.abs(best.lat)) * 180) / Math.PI)} of lean)<br>
      ${best.lon >= 0 ? "Accelerating" : "Braking"} <b>${Math.abs(best.lon).toFixed(2)} g</b><br>${share}% of the outline in that direction`, ev);
  });
  hit.addEventListener("pointerleave", hideTip);
}

function renderGrip(main, d) {
  const G = d.grip;
  if (!G || !G.turns.length) {
    main.innerHTML = `<div class="empty"><p>No lean and grip numbers for this day yet.</p>
      <p class="note">It was analysed before this view existed, or it has too few laps for a grip outline. It is analysed again on its own; the status at the top right says when.</p></div>`;
    return;
  }
  const c = d.corners.find((x) => x.name === S.corner) || d.corners[0];
  const gc = new Map(G.corners.map((x) => [x.name, x]));
  const chips = document.createElement("div");
  chips.className = "corner-list";
  chips.innerHTML = d.corners.map((x) => {
    const g = gc.get(x.name);
    return `<button aria-pressed="${x.name === c.name}" data-c="${esc(x.name)}"><span>${esc(x.name.replace(/\+/g, " + "))}</span>${g ? `<span class="lv">${g.near_limit_pct}%</span>` : ""}</button>`;
  }).join("");
  chips.addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (b) { S.corner = b.dataset.c; render(); }
  });
  main.appendChild(chips);

  const wrap = document.createElement("div");
  wrap.className = "split grip";
  main.appendChild(wrap);
  const left = document.createElement("div");
  left.className = "panel col";
  const right = document.createElement("div");
  right.className = "panel gripside";
  wrap.append(left, right);

  const g = gc.get(c.name);
  const near = Math.round(100 - G.near_limit * 100);
  const say = !g ? "No grip numbers for this corner on the best lap."
    : `${esc(c.name.replace(/\+/g, " + "))}: within ${near}% of the outline for <b>${g.near_limit_pct}%</b> of the corner. `
      + (g.entry_blend_pct == null ? "No brake zone before its deepest lean."
        : `From the hardest braking (${g.peak_brake_g.toFixed(2)} g) to the deepest lean, the least grip in use was <b>${g.entry_blend_pct}%</b> of the outline.`);
  left.innerHTML = `<h2>Grip circle, best lap ${esc(d.best)} <span class="note">(estimate from GPS)</span></h2>
    <div class="legend"><span><span class="dot" style="background:var(--series-best)"></span>each moment of the best lap</span>
    <span><span class="dot" style="background:var(--text-primary)"></span>${esc(c.name.replace(/\+/g, " + "))}</span>
    <span><span class="sw" style="background:var(--text-secondary)"></span>the grip you reached through the day, each way</span></div>`;
  const box = document.createElement("div");
  box.className = "chart";
  left.appendChild(box);
  const cap = document.createElement("p");
  cap.className = "gripcap";
  cap.innerHTML = `${say} <span class="note">Near 100% = braking traded for lean all the way in; low = the brake let go before leaning in.</span>`;
  left.appendChild(cap);
  requestAnimationFrame(() => { if (box.isConnected) drawGG(box, d, c); });

  // lean per turn
  const order = d.sessions.map((x) => x.session);
  const flagged = new Set((d.tire_flags || []).map((f) => short(f.turn)));
  const inPick = new Set(turnsIn(c.name));
  const rows = G.turns.map((t) => {
    const all = order.map((sn) => t.sessions.find((x) => x.session === sn) || null);    // null: no lap of that session counted
    const seq = all.filter(Boolean);
    const change = seq.length > 1 ? seq[seq.length - 1].deg - seq[0].deg : null;
    return `<tr class="${inPick.has(short(t.label)) ? "on" : ""}" data-c="${esc(t.corner)}">
      <td class="lbl">${flagged.has(short(t.label)) ? '<span class="flag" title="Tire signal: mid-corner speed and grip fell every session">&#9888;</span> ' : ""}${esc(t.label)}<span class="sub">${t.side === "R" ? "R" : "L"}</span></td>
      <td class="num big">${deg(t.best_deg)}</td>
      <td class="num">${deg(t.max_deg)}<span class="sub">${esc(t.max_from.replace(" lap ", " L"))}</span></td>
      <td class="seq">${all.map((x, i) => `<span title="Session ${d.sessions[i].number}${x ? `, ${x.laps} laps` : ": no lap within 3% of the best"}">${x ? x.deg.toFixed(1) : "&ndash;"}</span>`).join('<span class="arr">&rsaquo;</span>')}</td>
      <td class="num">${change == null ? "" : `${signed(change, 1)}&deg;`}</td></tr>`;
  }).join("");
  const gy = G.gyro;
  const cross = !gy ? "These logs have no gyro channels to cross-check the lean with."
    : gy.r >= 0.5 ? `Cross-check: the logger's gyro follows the GPS lean rate, r = ${gy.r.toFixed(2)} (1 = the same shape).`
      : `<span class="flag">&#9888; The logger's gyro does not follow the GPS lean rate today (r = ${gy.r.toFixed(2)}), so treat these angles with more caution.</span>`;
  right.innerHTML = `<h2>Lean angle by turn <span class="note">(estimate; deepest lean held for ${G.hold_s} s within ${G.lean_win_m.toFixed(0)} m of the apex, ${G.laps} laps)</span></h2>
    <table class="lean"><thead><tr><th>Turn</th><th class="num">Best lap</th><th class="num">Deepest today</th><th>By session, in order</th><th class="num">Change</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="note">Lean is the angle the GPS cornering force needs: bike and rider together, on a flat road. Hanging off, tire width, banking and crests are not in it, so the bike itself leans a few degrees more or less.
    By session is the middle value of that session's laps within 3% of the best (&ndash; where it has none), and Change is first to last; a turn where it falls through the day belongs next to the tire signal (&#9888; marks turns the tire check flagged).
    ${cross} The figure on each corner button is the share of that corner spent near the outline. Up and down keys change corner.</p>`;
  right.querySelector("tbody").addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-c]");
    if (tr) { S.corner = tr.dataset.c; render(); }
  });
}

// ---------------------------------------------------------------- 6. stint
const fmtLapTenths = (sec) => { const mm = Math.floor(sec / 60); return `${mm}:${(sec - mm * 60).toFixed(1).padStart(4, "0")}`; };
// a gap of under a second without its leading zero, to keep the lap-by-corner table narrow
function fmtGap(v) {
  if (v == null || isNaN(v)) return "";
  const a = Math.abs(v), sign = v < -0.004 ? "−" : "";
  if (a < 0.005) return "·";
  return sign + (a >= 0.995 ? a.toFixed(1) : "." + String(Math.round(a * 100)).padStart(2, "0"));
}

function stintSessions(d) {
  const meta = new Map(d.sessions.map((x) => [x.session, x]));
  return ((d.stint && d.stint.sessions) || []).filter((x) => meta.has(x.session))
    .map((x) => ({ ...x, meta: meta.get(x.session) })).sort((a, b) => a.meta.number - b.meta.number);
}

// lap time against time on track, one line per session; hollow dots are laps that are not clean
function drawStint(parent, { sessions, pick, byLap }) {
  const W = Math.max(300, parent.clientWidth), H = Math.max(80, parent.clientHeight);
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const fs = rem * 0.78;
  const m = { l: fs * 4.2, r: fs * 2.6, t: fs * 0.9, b: fs * 2.7 };
  if (H - m.t - m.b < 20) return;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Lap times through each session" }, parent);
  const xOf = (l) => (byLap ? l.lap : l.end_min);
  const every = sessions.flatMap((sx) => sx.laps);
  const cleanT = every.filter((l) => l.clean).map((l) => l.time_s);
  const ts = cleanT.length ? cleanT : every.map((l) => l.time_s);
  let lo = Math.min(...ts), hi = Math.max(...ts);
  if (hi - lo < 1) { const mid = (hi + lo) / 2; lo = mid - 0.5; hi = mid + 0.5; }
  const y0 = lo - (hi - lo) * 0.1, y1 = hi + (hi - lo) * 0.12;
  const x0 = byLap ? 0.5 : 0, x1 = Math.max(...every.map(xOf)) * (byLap ? 1 : 1.02) + (byLap ? 0.5 : 0);
  const X = (x) => m.l + ((x - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (y) => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);
  const stepY = niceStep(y1 - y0, Math.max(2, Math.floor((H - m.t - m.b) / (fs * 1.7))));
  for (let i = Math.ceil(y0 / stepY - 1e-9); i * stepY <= y1 + 1e-9; i++) {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(i * stepY), y2: Y(i * stepY), stroke: "var(--grid)", "stroke-width": 1 }, svg);
    const t = el("text", { x: m.l - 8, y: Y(i * stepY), "text-anchor": "end", "dominant-baseline": "middle", "font-size": fs * 0.85, class: "muted" }, svg);
    t.textContent = fmtLapTenths(i * stepY);
  }
  const stepX = niceStep(x1 - x0, Math.max(3, Math.floor((W - m.l - m.r) / (fs * 4))));
  for (let i = Math.ceil(x0 / Math.max(stepX, byLap ? 1 : stepX)); i * Math.max(stepX, byLap ? 1 : stepX) <= x1; i++) {
    const x = i * Math.max(stepX, byLap ? 1 : stepX);
    if (x <= x0) continue;
    const t = el("text", { x: X(x), y: H - m.b + fs * 1.1, "text-anchor": "middle", "font-size": fs * 0.85, class: "muted" }, svg);
    t.textContent = x;
  }
  const xl = el("text", { x: (m.l + W - m.r) / 2, y: H - fs * 0.35, "text-anchor": "middle", "font-size": fs * 0.85, class: "sec" }, svg);
  xl.textContent = (byLap ? "Lap number" : "Minutes since the logger started") + "  \u00b7  dots: clean laps, rings: not clean, dashes: trend through the clean laps";
  const clip = el("clipPath", { id: "stintclip" }, svg);
  el("rect", { x: m.l - 2, y: m.t - 2, width: W - m.l - m.r + 4, height: H - m.t - m.b + 4 }, clip);
  const rr = Math.max(4, rem * 0.2);
  // the picked session is drawn last so it sits on top
  for (const sx of [...sessions].sort((a, b) => (a.session === pick) - (b.session === pick))) {
    const g = el("g", { opacity: pick && sx.session !== pick ? 0.55 : 1 }, svg);
    const color = sessionColor(sx.meta.number);
    el("polyline", { points: sx.laps.map((l) => `${X(xOf(l)).toFixed(1)},${Y(l.time_s).toFixed(1)}`).join(" "), fill: "none",
      stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "clip-path": "url(#stintclip)" }, g);
    const cl = sx.laps.filter((l) => l.clean);
    if (sx.trend && cl.length > 1) {      // fitted against minutes, so against lap number it is not quite straight
      el("polyline", { points: cl.map((l) => `${X(xOf(l)).toFixed(1)},${Y(sx.trend.at_0_s + sx.trend.s_per_min * l.end_min).toFixed(1)}`).join(" "),
        fill: "none", stroke: color, "stroke-width": 2, "stroke-dasharray": "7 6", "clip-path": "url(#stintclip)" }, g);
    }
    for (const l of sx.laps) {
      const over = l.time_s > y1, cx = X(xOf(l)), cy = over ? m.t + rr : Y(l.time_s);
      if (over) {
        el("polygon", { points: `${cx},${cy - rr} ${cx - rr},${cy + rr} ${cx + rr},${cy + rr}`, fill: "var(--surface-1)", stroke: color, "stroke-width": 2 }, g);
      } else {
        el("circle", { cx, cy, r: rr, fill: l.clean ? color : "var(--surface-1)", stroke: l.clean ? "var(--surface-1)" : color, "stroke-width": 2 }, g);
      }
      const hit = el("circle", { cx, cy, r: rr * 2.6, fill: "transparent" }, svg);
      hit.addEventListener("pointermove", (ev) => showTip(`<b>Session ${sx.meta.number}, lap ${l.lap}</b><br>${fmtLap(l.time_s)}${l.clean ? "" : " (not a clean lap)"}<br>ended at minute ${l.end_min.toFixed(1)}`, ev));
      hit.addEventListener("pointerleave", hideTip);
    }
    const last = sx.laps[sx.laps.length - 1];
    const lt = el("text", { x: X(xOf(last)) + rr + 6, y: Math.max(m.t + fs * 0.5, Y(Math.min(last.time_s, y1))), "dominant-baseline": "middle", "font-size": fs * 0.9, "font-weight": 650 }, g);
    lt.textContent = `S${sx.meta.number}`;
  }
}

function renderStint(main, d) {
  const all = stintSessions(d);
  if (!all.length) {
    main.innerHTML = `<div class="empty"><p>No stint numbers for this day yet.</p>
      <p class="note">It was analysed before this view existed. It is analysed again on its own; the status at the top right says when.</p></div>`;
    return;
  }
  const longest = [...all].sort((a, b) => b.laps.length - a.laps.length)[0];
  const sx = all.find((x) => x.session === S.session) || longest;
  S.session = sx.session;
  const byLap = S.stintX === "lap";

  const top = document.createElement("div");
  top.className = "panel col stint-top";
  main.appendChild(top);
  const chips = all.map((x) => {
    const f = x.meta.fade_s;
    return `<button data-s="${esc(x.session)}" aria-pressed="${x.session === sx.session}"><span class="sw" style="background:${sessionColor(x.meta.number)}"></span>
      <span title="Session ${x.meta.number}, ${esc(x.session)}">S${x.meta.number}</span><span class="note">${x.laps.length} laps, ${f == null ? "too few for a fade" : `fade ${signed(f)} s`}</span></button>`;
  }).join("");
  top.innerHTML = `<div class="stint-head"><h2>Lap times by session</h2>
      <div class="corner-list stint-pick">${chips}</div>
      <button id="stintx" class="ghost" title="Bottom scale: ${byLap ? "lap number" : "minutes since the logger started"}. Click or press X to switch.">${byLap ? "Laps" : "Minutes"} &#8644;</button></div>`;
  top.querySelector(".stint-head").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.id === "stintx") toggleStintX();
    else { S.session = b.dataset.s; render(); }
  });
  const box = document.createElement("div");
  box.className = "chart";
  top.appendChild(box);
  requestAnimationFrame(() => { if (box.isConnected) drawStint(box, { sessions: all, pick: sx.session, byLap }); });

  // lap by corner for the picked session
  const cols = [];      // one per section, grouped under its corner
  d.corners.forEach((c) => d.sections.forEach((x, k) => { if (x.corner === c.name) cols.push({ k, corner: c, part: x.part || "", best: x.ideal_s }); }));
  const rowOf = new Map(d.laps.filter((r) => r.session === sx.session).map((r) => [r.lap, r]));
  const le = sx.late_early;
  const vals = [];
  sx.laps.forEach((l) => { const r = rowOf.get(l.lap); if (l.placed && r && r.sec_s) cols.forEach((c) => vals.push(r.sec_s[c.k] - c.best)); });
  const pos = vals.filter((v) => v > 0.02).sort((a, b) => a - b);
  const cap = Math.min(1, Math.max(0.2, pos.length ? pos[Math.floor(pos.length * 0.9)] : 0.2));
  const cell = (v) => {
    const step = v < 0.02 ? -1 : Math.min(4, Math.floor((v / cap) * 5));
    return `<td class="g${step < 0 ? "" : " seq" + step}">${fmtGap(v)}</td>`;
  };
  const head1 = d.corners.map((c) => `<th colspan="${cols.filter((x) => x.corner === c).length}" title="${esc(c.name.replace(/\+/g, " + "))}">${esc(turnsIn(c.name).join("+") || c.name)}</th>`).join("");
  const head2 = cols.map((c) => `<th class="p">${c.part === "whole" ? "" : esc(c.part)}</th>`).join("");
  const body = sx.laps.map((l) => {
    const r = rowOf.get(l.lap);
    const tag = le && le.early.includes(l.lap) ? '<span class="tag">early</span>' : le && le.late.includes(l.lap) ? '<span class="tag">late</span>' : "";
    const cells = l.placed && r && r.sec_s ? cols.map((c) => cell(r.sec_s[c.k] - c.best)).join("")
      : `<td class="na" colspan="${cols.length}">${esc((r && r.why_out) || "not placed on the track")}</td>`;
    return `<tr class="${l.clean ? "" : "dirty"}"><td class="lap">L${l.lap}${tag}</td><td class="t">${fmtLap(l.time_s)}${l.clean ? "" : '<span class="tag">not clean</span>'}</td>${cells}</tr>`;
  }).join("");
  const foot = le ? `<tr class="le"><td class="lap">late &minus; early</td><td class="t">${signed(le.lap_s)} s</td>${le.sec_s.length === d.sections.length
    ? cols.map((c) => `<td class="${Math.abs(le.sec_s[c.k]) >= 0.05 ? "strong" : ""}">${fmtGap(le.sec_s[c.k])}</td>`).join("") : ""}</tr>` : "";
  const sum = le
    ? `Last ${le.late.length} clean lap${le.late.length > 1 ? "s" : ""} against the first ${le.early.length}: <b>${signed(le.lap_s)} s</b> a lap.
       Into corners (braking and turn-in) <b>${signed(le.in_s)} s</b>, out of corners (drive) <b>${signed(le.out_s)} s</b>${Math.abs(le.whole_s) >= 0.005 ? `, corners with no split <b>${signed(le.whole_s)} s</b>` : ""}.`
    : "Too few clean laps to compare late laps with early ones (it takes three).";
  const bot = document.createElement("div");
  bot.className = "panel stint-bot";
  bot.innerHTML = `<h2>Session ${sx.meta.number} lap by corner <span class="note">(${esc(sx.session)}; seconds lost to your best time through each part today)</span></h2>
    <p class="stint-sum">${sum}</p>
    <div class="tablewrap"><table class="stint"><thead><tr><th rowspan="2" class="lap">Lap</th><th rowspan="2" class="t">Time</th>${head1}</tr><tr>${head2}</tr></thead>
    <tbody>${body}</tbody><tfoot>${foot}</tfoot></table></div>
    <p class="note"><span class="scale">${[0, 1, 2, 3, 4].map((k) => `<span style="background:var(--seq-${k})"></span>`).join("")}</span>
    0 to ${cap.toFixed(2)} s and over; &middot; = within 0.005 s of the best. in = brake point to slowest point, out = from there to the next brake point.
    Late &minus; early is the lap table's fade split by part (bold from 0.05 s). The trend needs ${d.stint.min_trend_laps} clean laps. Keys: up and down change session, X switches the chart's bottom scale.</p>`;
  main.appendChild(bot);
}

function toggleStintX() {
  S.stintX = S.stintX === "lap" ? "min" : "lap";
  try { localStorage.setItem("lapviewer.stintx", S.stintX); } catch (e) { /* ignore */ }
  render();
}

// ---------------------------------------------------------------- 7. engine (only for logs with ECU channels)
const ROLE_LABEL = { rpm: "Engine speed", throttle: "Throttle", brake: "Brake", gear: "Gear" };
const GEAR_COLORS = ["var(--line)", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb", "#cde2fb"];
const hasEngine = (d) => !!(d && d.engine);

function channelNotes(E) {
  return Object.entries(ROLE_LABEL).map(([role, label]) => {
    const c = E.channels[role] || { state: "missing" };
    if (role === "gear" && c.state !== "ok") {
      if (E.gear_from === "rpm and speed") {
        return `<span class="ch ok"><b>Gear</b> worked out from revs and speed with the ${esc(E.gear_fit.bike)} gearbox (${E.gear_fit.pct}% of steady samples sit on a gear). Not measured: check it against a corner where you know your gear.</span>`;
      }
      return `<span class="ch off"><b>Gear</b> ${esc(E.gear_note || "no channel in these logs.")}</span>`;
    }
    if (c.state === "ok") return `<span class="ch ok"><b>${label}</b> from the ${esc(c.name)} channel.</span>`;
    if (c.state === "flat") return `<span class="ch warn"><span class="flag">&#9888;</span> <b>${label}</b>: the ${esc(c.name)} channel is in the log but never moves (${esc(c.note)}), so it is not shown. Check the logger's ECU setup.</span>`;
    if (c.state === "partial") return `<span class="ch warn"><span class="flag">&#9888;</span> <b>${label}</b>: the ${esc(c.name)} channel is not in every session, so it is not shown.</span>`;
    return `<span class="ch off"><b>${label}</b>: no channel in these logs.</span>`;
  }).join("");
}

// the track coloured by gear on the best lap
function drawGearMap(parent, d) {
  const E = d.engine, T = E.laps[E.best], P = d.path;
  const xs = P.x, ys = P.y.map((v) => -v);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const span = Math.max(maxX - minX, maxY - minY), pad = span * 0.1;
  const svg = el("svg", { viewBox: `${minX - pad} ${minY - pad} ${maxX - minX + 2 * pad} ${maxY - minY + 2 * pad}`, role: "img", "aria-label": "Track map coloured by gear" }, parent);
  const lw = span * 0.024, fs = span * 0.045;
  const gearAt = (dist) => T.gear[Math.max(0, Math.min(T.gear.length - 1, Math.round(dist / E.step_m)))];
  let run = [0], cur = gearAt(P.d[0]);
  const flush = () => {
    if (run.length < 2) return;
    el("polyline", { points: run.map((i) => `${xs[i]},${ys[i]}`).join(" "), fill: "none", stroke: GEAR_COLORS[cur] || GEAR_COLORS[0],
      "stroke-width": lw, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg);
  };
  for (let i = 1; i < xs.length; i++) {
    const g = gearAt(P.d[i]);
    run.push(i);
    if (g !== cur) { flush(); run = [i]; cur = g; }
  }
  run.push(0); flush();
  for (const a of d.apexes) {
    const dx = a.x - (minX + maxX) / 2, dy = -a.y - (minY + maxY) / 2, n = Math.hypot(dx, dy) || 1;
    const t = el("text", { x: a.x + (dx / n) * span * 0.07, y: -a.y + (dy / n) * span * 0.07, "text-anchor": "middle", "dominant-baseline": "middle", "font-size": fs, "font-weight": 700 }, svg);
    t.textContent = short(a.label);
  }
}

function renderEngine(main, d) {
  const E = d.engine;
  if (!E) {
    main.innerHTML = `<div class="empty"><p>No engine channels in this day's logs.</p>
      <p class="note">This view needs engine speed, throttle, brake or gear from the bike's ECU, logged through the Solo DL.</p></div>`;
    return;
  }
  const status = document.createElement("div");
  status.className = "panel chans";
  status.innerHTML = channelNotes(E);
  main.appendChild(status);
  const B = E.laps[E.best];
  if (!B) return;       // channels are in the log but none carries a signal: the notes above say which
  const c = d.corners.find((x) => x.name === S.corner) || d.corners[0];
  const ec = new Map(E.corners.map((x) => [x.name, x]));
  const chips = document.createElement("div");
  chips.className = "corner-list";
  chips.innerHTML = d.corners.map((x) => {
    const g = ec.get(x.name), b = g && g.best_lap;
    const tag = !b ? "" : b.apex_gear ? `gear ${b.apex_gear}` : b.apex_rpm != null ? `${b.apex_rpm} rpm` : "";
    return `<button aria-pressed="${x.name === c.name}" data-c="${esc(x.name)}"><span>${esc(x.name.replace(/\+/g, " + "))}</span><span class="lv">${tag}</span></button>`;
  }).join("");
  chips.addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (b) { S.corner = b.dataset.c; render(); }
  });
  main.appendChild(chips);

  const wrap = document.createElement("div");
  wrap.className = "split";
  main.appendChild(wrap);
  const left = document.createElement("div");
  left.className = "panel col";
  const right = document.createElement("div");
  right.className = "panel col engside";
  wrap.append(left, right);

  // traces through the picked corner: best lap against the fastest run through it
  const g = ec.get(c.name) || {};
  const F = E.laps[g.fastest] || B;
  const L = d.lap_length_m, c0 = c.wrap ? c.d0 - L : c.d0, c1 = c.d1;
  const ext = (arr) => { const out = []; for (const off of [-L, 0, L]) E.d.forEach((x, i) => out.push([x + off, arr[i]])); return out; };
  const apexes = [];
  for (const off of [-L, 0, L]) d.apexes.forEach((a) => apexes.push({ ...a, d: a.d + off }));
  const near = (m) => (m == null ? null : c.wrap && m > c1 ? m - L : m);
  const marks = [];
  const ab = near(g.best_lap && g.best_lap.apex_m), af = near(g.fastest_run && g.fastest_run.apex_m);
  if (ab != null) marks.push({ x: ab, color: "var(--series-best)", label: "best lap slowest", row: 0, left: af != null && af > ab });
  if (af != null && g.fastest !== E.best) marks.push({ x: af, color: "var(--series-ideal)", label: "fastest run slowest", row: 1, left: af < ab });
  if (B.gear) {         // gear changes on the best lap
    for (let i = 1; i < E.d.length; i++) {
      if (B.gear[i] && B.gear[i - 1] && B.gear[i] !== B.gear[i - 1]) {
        for (const off of [-L, 0, L]) marks.push({ x: E.d[i] + off, color: "var(--text-muted)", label: `${B.gear[i] > B.gear[i - 1] ? "↑" : "↓"}${B.gear[i]}`, row: 2 });
      }
    }
  }
  const same = g.fastest === E.best;
  left.innerHTML = `<h2>${esc(c.name.replace(/\+/g, " + "))} <span class="note">(best lap against the fastest run through it${same ? ", which is the best lap" : `, ${esc((g.fastest || "").replace(":", " lap "))}`})</span></h2>
    <div class="legend"><span><span class="sw" style="background:var(--series-best)"></span>Best lap ${esc(d.best)}</span>
    ${same ? "" : '<span><span class="sw" style="background:var(--series-ideal)"></span>Fastest run through this corner</span>'}
    ${B.gear ? '<span class="note">&uarr;4 = a change up to 4th on the best lap</span>' : ""}</div>`;
  const rows = [["rpm", "Engine speed (rpm)", 1500, (y) => String(Math.round(y)), (v) => `<b>${Math.round(v)}</b> rpm`],
    ["thr", "Throttle (% of its range)", 20, (y) => `${Math.round(y)}`, (v) => `<b>${Math.round(v)}</b>%`],
    ["brk", "Brake (% of its range)", 20, (y) => `${Math.round(y)}`, (v) => `<b>${Math.round(v)}</b>%`]].filter(([k]) => B[k]);
  rows.forEach(([k, title, minRange, yFmt, tipFmt], i) => {
    const h = document.createElement("div");
    h.className = "note chart-title";
    h.textContent = title;
    left.appendChild(h);
    const series = [{ name: "Best lap", color: "var(--series-best)", pts: ext(B[k]) }];
    if (!same && F[k]) series.push({ name: "Fastest run", color: "var(--series-ideal)", pts: ext(F[k]) });
    lineChart(left, { x0: c0 - 40, x1: c1 + 40, apexes, marks: i === 0 ? marks : marks.filter((m) => m.row < 2), series, minRange, yFmt, tipFmt });
  });

  // map by gear, then the numbers per corner
  if (B.gear) {
    const used = [...new Set(B.gear.filter((x) => x > 0))].sort();
    const mh = document.createElement("div");
    mh.innerHTML = `<h2>Gear round the best lap</h2><div class="legend">${used.map((x) => `<span><span class="sw" style="background:${GEAR_COLORS[x]}"></span>${x}</span>`).join("")}
      <span><span class="sw" style="background:${GEAR_COLORS[0]}"></span>changing gear or no fit</span></div>`;
    right.appendChild(mh);
    const mbox = document.createElement("div");
    mbox.className = "chart gearmap";
    right.appendChild(mbox);
    requestAnimationFrame(() => { if (mbox.isConnected) drawGearMap(mbox, d); });
  }
  const has = (k) => E.corners.some((x) => x.best_lap && x.best_lap[k] != null);
  const cols = [["apex_gear", "Gear", (v) => (v ? String(v) : "&ndash;")], ["apex_rpm", "Revs", (v) => String(v)], ["min_rpm", "Lowest", (v) => String(v)],
    ["exit_rpm", "At exit", (v) => String(v)], ["throttle_on_m", "Throttle on", (v) => `${v >= 0 ? "+" : "&minus;"}${Math.abs(v).toFixed(0)} m`],
    ["coast_s", "Coasting", (v) => `${v.toFixed(2)} s`]].filter(([k]) => has(k));
  const cellsOf = (o) => cols.map(([k, , f]) => `<td class="num">${o && o[k] != null ? f(o[k]) : ""}</td>`).join("");
  const tb = document.createElement("div");
  tb.className = "engtable";
  tb.innerHTML = `<h2>At the slowest point of each corner <span class="note">(best lap, then the fastest run through it in grey)</span></h2>
    <table class="lean eng"><thead><tr><th>Corner</th><th class="num">Speed</th>${cols.map(([, h]) => `<th class="num">${h}</th>`).join("")}</tr></thead><tbody>
    ${E.corners.map((x) => `<tr class="${x.name === c.name ? "on" : ""}" data-c="${esc(x.name)}"><td class="lbl">${esc(turnsIn(x.name).join("+") || x.name)}</td>
      <td class="num">${x.best_lap ? `${spd(x.best_lap.apex_kph).toFixed(0)} ${unit()}` : ""}</td>${cellsOf(x.best_lap)}</tr>
      ${x.fastest === E.best ? "" : `<tr class="alt" data-c="${esc(x.name)}"><td class="lbl sub2">${esc((x.fastest || "").replace(":", " L"))}</td>
      <td class="num">${x.fastest_run ? `${spd(x.fastest_run.apex_kph).toFixed(0)} ${unit()}` : ""}</td>${cellsOf(x.fastest_run)}</tr>`}`).join("")}</tbody></table>
    <p class="note">${E.full_throttle_pct != null ? `Full throttle for <b>${E.full_throttle_pct.toFixed(0)}%</b> of the best lap. ` : ""}Revs are as the ECU sends them (about 10 a second).
    ${has("throttle_on_m") ? "Throttle on = where the throttle comes back and stays on, in metres after the slowest point (minus = before it). Coasting = seconds in the corner with the throttle shut and the brake off. " : ""}Up and down keys change corner.</p>`;
  tb.querySelector("tbody").addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-c]");
    if (tr) { S.corner = tr.dataset.c; render(); }
  });
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
  else if (e.key === "4") setView("delta");
  else if (e.key === "5") setView("grip");
  else if (e.key === "6") setView("stint");
  else if (e.key === "7" && hasEngine(S.day)) setView("engine");
  else if ((e.key === "x" || e.key === "X") && S.view === "stint") toggleStintX();
  else if (e.key === "f" || e.key === "F") toggleFs();
  else if (e.key === "ArrowLeft") $("#prev").click();
  else if (e.key === "ArrowRight") $("#next").click();
  else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && (S.view === "grip" || S.view === "engine") && S.day) {
    const cs = S.day.corners, i = cs.findIndex((c) => c.name === S.corner);     // riding order
    S.corner = cs[Math.max(0, Math.min(cs.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))].name;
    render();
    e.preventDefault();
  } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && S.view === "corners" && S.day) {
    const sorted = [...S.day.corners].sort((a, b) => b.lost_s - a.lost_s);
    const i = sorted.findIndex((c) => c.name === S.corner);
    const j = Math.max(0, Math.min(sorted.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
    S.corner = sorted[j].name;
    render();
    e.preventDefault();
  } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && S.view === "delta" && S.day) {
    const laps = deltaLaps(S.day).filter((l) => !isRef(S.day, l));
    if (!laps.length) return;
    const i = laps.findIndex((l) => lapKey(l) === S.lap);
    const j = i < 0 ? (e.key === "ArrowDown" ? 0 : laps.length - 1) : Math.max(0, Math.min(laps.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
    S.lap = lapKey(laps[j]);
    render();
    e.preventDefault();
  } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && S.view === "stint" && S.day) {
    const all = stintSessions(S.day);
    if (!all.length) return;
    const i = all.findIndex((x) => x.session === S.session);
    S.session = all[Math.max(0, Math.min(all.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))].session;
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
