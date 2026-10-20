(() => {
"use strict";
const S = { metric: "spend", mode: "share", range: "daily", lab: null, sort: { labs: "share_month", models: "share_month" }, dir: { labs: -1, models: -1 } };
let D = null;
const charts = {};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (x, d = 1) => (x == null ? "–" : (x * 100).toFixed(d) + "%");
const usd = (x) => x >= 1e9 ? "$" + (x / 1e9).toFixed(2) + "B" : x >= 1e6 ? "$" + (x / 1e6).toFixed(x >= 1e7 ? 1 : 2) + "M" : x >= 1e3 ? "$" + (x / 1e3).toFixed(0) + "K" : "$" + x.toFixed(0);
const tok = (x) => x >= 1e12 ? (x / 1e12).toFixed(2) + "T" : x >= 1e9 ? (x / 1e9).toFixed(x >= 1e11 ? 0 : 1) + "B" : x >= 1e6 ? (x / 1e6).toFixed(0) + "M" : String(Math.round(x));
const fmtv = (x) => (S.metric === "spend" ? usd(x) : tok(x));
const sum = (a) => a.reduce((s, x) => s + x, 0);
const day = (s) => new Date(s + "T00:00:00Z");
const short = (s) => day(s).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const labMeta = (id) => D.lab_meta[id] || D.lab_meta.other;
const mob = () => window.innerWidth < 600;

const base = () => ({
  animationDuration: 300,
  textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
  grid: { left: 8, right: mob() ? 8 : 150, top: 18, bottom: 28, containLabel: true },
  tooltip: { trigger: "axis", confine: true, backgroundColor: "#fff", borderColor: "#e7e6e1", textStyle: { color: "#1d1d1f", fontSize: 12 } },
});
const legend = () => mob()
  ? { type: "scroll", bottom: 0, left: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 11 } }
  : { type: "scroll", orient: "vertical", right: 0, top: 10, bottom: 10, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 12 } };
function mk(id) {
  if (!charts[id]) charts[id] = echarts.init($(id), null, { renderer: "canvas" });
  return charts[id];
}
function axisTip(params, isShare, totals, fmt = fmtv) {
  const rows = params.filter((p) => p.value > 0).sort((a, b) => b.value - a.value);
  const head = `<div style="font-weight:600;margin-bottom:3px">${esc(params[0].axisValueLabel)}</div>`;
  return head + rows.slice(0, 14).map((p) => {
    const v = isShare ? pct(p.value / 100) : fmt(p.value);
    const extra = !isShare && totals ? ` <span style="color:#888">${pct(p.value / totals[p.dataIndex])}</span>` : "";
    return `<div style="display:flex;gap:14px;justify-content:space-between"><span>${p.marker}${esc(p.seriesName)}</span><b>${v}${extra}</b></div>`;
  }).join("") + (rows.length > 14 ? `<div style="color:#888">+${rows.length - 14} more</div>` : "");
}

function stacked(id, xs, series, opts = {}) {
  const share = S.mode === "share" && !opts.forceAbs;
  const fmt = opts.fmt || fmtv;
  const totals = xs.map((_, i) => sum(series.map((s) => s.data[i] || 0)));
  const out = series.map((s) => ({
    name: s.name, type: "line", stack: "t", symbol: "none", smooth: 0.25, sampling: "lttb",
    lineStyle: { width: 0.6, color: s.color }, itemStyle: { color: s.color },
    areaStyle: { color: s.color, opacity: 0.88 }, emphasis: { focus: "series" },
    data: s.data.map((v, i) => (share ? (totals[i] ? (100 * v) / totals[i] : 0) : v)),
    markLine: s.markLine,
  }));
  const c = mk(id);
  c.setOption({
    ...base(), legend: legend(),
    grid: { ...base().grid, top: opts.top || 18, bottom: mob() ? 54 : 28 },
    tooltip: { ...base().tooltip, formatter: (p) => axisTip(p, share, totals, fmt) },
    xAxis: { type: "category", boundaryGap: false, data: xs, axisLabel: { formatter: short, color: "#888", hideOverlap: true }, axisLine: { lineStyle: { color: "#ddd" } }, axisTick: { show: false } },
    yAxis: { type: "value", max: share ? 100 : null, splitLine: { lineStyle: { color: "#f0efea" } }, axisLabel: { color: "#888", formatter: (v) => (share ? v + "%" : fmt(v)) } },
    series: out,
  }, true);
}

function mainChart() {
  const weekly = S.range === "weekly";
  document.querySelector('[data-ctl=metric] [data-v=spend]').disabled = weekly;
  const metric = weekly ? "tokens" : S.metric;
  $("main-title").textContent = (metric === "spend" ? "Spend" : "Token") + (S.mode === "share" ? " share" : "") + " by lab";
  let xs, series;
  if (weekly) {
    const w = D.weekly_labs;
    xs = w.weeks;
    const ids = Object.keys(w.series).sort((a, b) => (a === "other") - (b === "other") || sum(w.series[b].slice(-8)) - sum(w.series[a].slice(-8)));
    series = ids.map((id) => ({ name: labMeta(id).name, color: labMeta(id).color, data: w.series[id] }));
    $("main-note").textContent = "Weekly tokens from OpenRouter's market share chart. Makers outside each week's top 9 are in Other. OpenRouter doesn't publish spend this far back.";
  } else {
    xs = D.dates;
    series = D.labs.map((l) => ({ name: l.name, color: l.color, data: l[metric] }));
    const w = D.windows.month;
    $("main-note").textContent = `Daily, ${short(w[0])} to ${short(w[1])}. ` + (metric === "spend" ? "Dollar amounts are list-price estimates scaled to OpenRouter's reported spend shares." : "Input plus output tokens.");
  }
  stacked("main-chart", xs, series, { fmt: metric === "spend" ? usd : tok });
}

function delta(l) {
  if (l.share_prev_7d == null) return '<span class="mute">–</span>';
  const pp = (l.share_7d - l.share_prev_7d) * 100;
  const cls = pp > 0.05 ? "up" : pp < -0.05 ? "down" : "mute";
  return `<span class="${cls}">${pp > 0 ? "+" : ""}${pp.toFixed(1)} pp</span>`;
}
const modelById = () => Object.fromEntries(D.models.map((m) => [m.id, m]));

function table(el, key, cols, rows, onclick) {
  const k = S.sort[key], dir = S.dir[key];
  const col = cols.find((c) => c.k === k);
  const sorted = rows.slice().sort((a, b) => {
    const va = col.v ? col.v(a) : a[k], vb = col.v ? col.v(b) : b[k];
    return (typeof va === "string" ? va.localeCompare(vb) : (va ?? -1) - (vb ?? -1)) * dir;
  });
  el.innerHTML = `<thead><tr>${cols.map((c) => `<th data-k="${c.k}" class="${c.l ? "l" : ""} ${c.k === k ? "sorted" : ""}">${c.t}${c.k === k ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead>` +
    `<tbody>${sorted.map((r) => `<tr data-id="${esc(r.id)}" class="${onclick ? "click" : ""} ${S.lab === r.id ? "sel" : ""}">${cols.map((c) => `<td class="${c.l ? "l" : ""}">${c.f(r)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  el.querySelectorAll("th").forEach((th) => th.onclick = () => {
    const nk = th.dataset.k;
    if (S.sort[key] === nk) S.dir[key] *= -1; else { S.sort[key] = nk; S.dir[key] = cols.find((c) => c.k === nk).l ? 1 : -1; }
    table(el, key, cols, rows, onclick);
  });
  if (onclick) el.querySelectorAll("tbody tr").forEach((tr) => tr.onclick = () => onclick(tr.dataset.id));
}

function labsTable() {
  const mb = modelById();
  const maxShare = Math.max(...D.labs.map((l) => l.share_month));
  const cols = [
    { k: "name", t: "Lab", l: 1, f: (l) => `<span class="dot" style="background:${l.color}"></span>${esc(l.name)}` },
    { k: "share_day", t: "Yesterday", f: (l) => pct(l.share_day) },
    { k: "share_week", t: "7 days", f: (l) => pct(l.share_week) },
    { k: "share_month", t: "30 days", f: (l) => `${pct(l.share_month)}<span class="sharebar" style="width:${(60 * l.share_month) / maxShare}px;background:${l.color}"></span>` },
    { k: "chg", t: "Week over week", v: (l) => (l.share_prev_7d == null ? null : l.share_7d - l.share_prev_7d), f: delta },
    { k: "token_share_month", t: "Token share, 30d", f: (l) => pct(l.token_share_month) },
    { k: "ppm", t: "$ per M tokens", v: (l) => (l.tokens_30d ? (l.spend_30d / l.tokens_30d) * 1e6 : null), f: (l) => (l.tokens_30d ? "$" + ((l.spend_30d / l.tokens_30d) * 1e6).toFixed(2) : "–") },
    { k: "spend_30d", t: "Est. spend, 30d", f: (l) => usd(l.spend_30d) },
    { k: "top", t: "Top model", l: 1, v: (l) => (mb[l.top_model] || {}).name || "", f: (l) => `<span class="name">${esc((mb[l.top_model] || {}).name || "–")}</span>` },
  ];
  table($("labs"), "labs", cols, D.labs, (id) => { S.lab = id; $("mix-lab").value = id; labsTable(); mixChart(); launchChart(); });
}

function mixChart() {
  const lab = S.lab || D.labs[0].id;
  const L = D.labs.find((l) => l.id === lab);
  $("mix-title").textContent = `${L.name} model mix`;
  const key = S.metric;
  const ms = D.models.filter((m) => m.lab === lab);
  const recent = (m) => sum(m[key].slice(-7));
  ms.sort((a, b) => recent(b) - recent(a));
  const top = ms.slice(0, 9).filter((m) => recent(m) > 0 || sum(m[key]) > 0);
  const labTotal = L[key];
  const rest = labTotal.map((v, i) => Math.max(0, v - sum(top.map((m) => m[key][i]))));
  const hues = shades(L.color, top.length);
  const series = top.map((m, i) => ({ name: m.name, color: hues[i], data: m[key] }));
  if (sum(rest) > sum(labTotal) * 0.002) series.push({ name: `Other ${L.name}`, color: "#d4d4d0", data: rest });
  const launches = top.filter((m) => m.created && m.created >= D.dates[0]).map((m) => ({ xAxis: m.created, label: { formatter: m.name, color: "#777", fontSize: 10, position: "end" } }));
  if (series.length && launches.length) series[0].markLine = { symbol: "none", silent: true, lineStyle: { color: "#999", type: "dashed", width: 1 }, data: launches };
  stacked("mix-chart", D.dates, series, { top: launches.length ? 34 : 18 });
}

function shades(hex, n) {
  const c = hex.match(/\w\w/g).map((x) => parseInt(x, 16));
  const base = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22"];
  return Array.from({ length: n }, (_, i) => {
    if (i === 0) return hex;
    const t = i / (n + 1), mix = (a, b) => Math.round(a + (b - a) * t);
    const tgt = i % 2 ? [255, 255, 255] : [30, 30, 30];
    const blend = c.map((x, j) => mix(x, tgt[j]));
    return i < 4 ? "#" + blend.map((x) => x.toString(16).padStart(2, "0")).join("") : base[i % base.length];
  });
}

function launchChart() {
  const key = S.metric, cum = $("cum").checked;
  const start = D.dates[0];
  const ms = D.models.filter((m) => m.created && m.created > start && m.share_month > 0.0015).sort((a, b) => b.share_month - a.share_month).slice(0, 14);
  const series = ms.map((m, i) => {
    const i0 = D.dates.indexOf(m.created);
    let acc = 0;
    const lg = $("logy").checked;
    const pts = m[key].slice(Math.max(0, i0)).map((v, d) => { const y = cum ? (acc += v) : v; return [d, lg && y <= 0 ? null : y]; });
    const own = S.lab && m.lab === S.lab;
    return { name: m.name, type: "line", symbol: "none", smooth: 0.3, data: pts, lineStyle: { width: own ? 2.6 : 1.6, color: labMeta(m.lab).color, opacity: !S.lab || own ? 1 : 0.35 }, itemStyle: { color: labMeta(m.lab).color }, emphasis: { focus: "series" }, endLabel: { show: !mob() && i < 6, formatter: m.name, fontSize: 10, color: "#666" } };
  });
  mk("launch-chart").setOption({
    ...base(), grid: { left: 8, right: mob() ? 10 : 110, top: 18, bottom: 26, containLabel: true },
    tooltip: { ...base().tooltip, formatter: (p) => `<b>Day ${p[0].value[0]}</b><br>` + p.sort((a, b) => b.value[1] - a.value[1]).map((x) => `${x.marker}${esc(x.seriesName)} <b>${fmtv(x.value[1])}</b>`).join("<br>") },
    xAxis: { type: "value", name: "days since release", nameLocation: "middle", nameGap: 22, nameTextStyle: { color: "#888", fontSize: 11 }, axisLabel: { color: "#888" }, splitLine: { show: false }, minInterval: 1 },
    yAxis: { type: $("logy").checked ? "log" : "value", min: $("logy").checked ? (cum ? 1e3 : 100) : null, axisLabel: { color: "#888", formatter: fmtv }, splitLine: { lineStyle: { color: "#f0efea" } } },
    series,
  }, true);
}

function taskChart() {
  const T = D.tasks[S.metric === "spend" ? "spend" : "tokens"];
  const tasks = T.tasks.slice().sort((a, b) => b.share_of_total - a.share_of_total).slice(0, 18).reverse();
  const ids = D.labs.map((l) => l.id).filter((id) => id !== "other" && tasks.some((t) => (t.labs[id] || 0) > 0.002));
  ids.push("other");
  const nice = (t) => t.replace(/^\w+:/, "").replace(/_/g, " ");
  mk("task-chart").setOption({
    ...base(), legend: { ...legend(), ...(mob() ? {} : { orient: "horizontal", top: 0, right: "auto", left: 0, bottom: "auto" }) },
    grid: { left: 8, right: 14, top: mob() ? 10 : 30, bottom: mob() ? 50 : 10, containLabel: true },
    tooltip: { ...base().tooltip, trigger: "axis", axisPointer: { type: "shadow" }, formatter: (p) => { const t = tasks[p[0].dataIndex]; return `<b>${esc(nice(t.tag))}</b> <span style="color:#888">${pct(t.share_of_total)} of all ${S.metric === "spend" ? "spend" : "tokens"}</span><br>` + p.filter((x) => x.value > 0).sort((a, b) => b.value - a.value).map((x) => `${x.marker}${esc(x.seriesName)} <b>${x.value.toFixed(1)}%</b>`).join("<br>") + `<br><span style="color:#888">Top model: ${esc(t.top[0] ? (modelById()[t.top[0].model] || { name: t.top[0].model }).name : "–")}</span>`; } },
    xAxis: { type: "value", max: 100, axisLabel: { formatter: "{value}%", color: "#888" }, splitLine: { lineStyle: { color: "#f0efea" } } },
    yAxis: { type: "category", data: tasks.map((t) => `${nice(t.tag)}  ${pct(t.share_of_total, 0)}`), axisLabel: { color: "#444", fontSize: 11.5 }, axisTick: { show: false }, axisLine: { show: false } },
    series: ids.map((id) => ({ name: labMeta(id).name, type: "bar", stack: "t", barWidth: "68%", itemStyle: { color: labMeta(id).color }, emphasis: { focus: "series" }, data: tasks.map((t) => +(100 * (t.labs[id] || 0)).toFixed(2)) })),
  }, true);
  $("task-note").textContent = `Last ${T.window_days} days. Each bar splits one task's ${S.metric === "spend" ? "spend" : "tokens"} across labs (OpenRouter's top 10 models per task; the rest is in Other). Percent after the name is the task's share of all ${S.metric === "spend" ? "spend" : "tokens"}.`;
}

function spark(arr, color) {
  const n = arr.length, mx = Math.max(...arr, 1e-9), w = 70, h = 18;
  if (!n) return "";
  const pts = arr.map((v, i) => `${((i / Math.max(1, n - 1)) * w).toFixed(1)},${(h - (v / mx) * (h - 2) - 1).toFixed(1)}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}"><polyline fill="none" stroke="${color}" stroke-width="1.3" points="${pts}"/></svg>`;
}

function modelsTable() {
  const q = $("q").value.trim().toLowerCase(), lab = $("model-lab").value;
  let rows = D.models.filter((m) => (!lab || m.lab === lab) && (!q || m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q)));
  const total = rows.length;
  if (!q && !lab && !S.all) rows = rows.slice(0, 50);
  const cols = [
    { k: "name", t: "Model", l: 1, f: (m) => `<span class="dot" style="background:${labMeta(m.lab).color}"></span><span class="name" title="${esc(m.id)}">${esc(m.name)}</span>` },
    { k: "author", t: "Maker", l: 1, f: (m) => esc(m.lab === "other" ? m.author : labMeta(m.lab).name) },
    { k: "created", t: "Released", l: 1, f: (m) => (m.created ? esc(m.created) : "–") },
    { k: "share_day", t: "Yesterday", f: (m) => pct(m.share_day, 2) },
    { k: "share_week", t: "7 days", f: (m) => pct(m.share_week, 2) },
    { k: "share_month", t: "30 days", f: (m) => pct(m.share_month, 2) },
    { k: "trend", t: "Daily spend", v: (m) => sum(m.spend.slice(-7)), f: (m) => spark(m.spend, labMeta(m.lab).color) },
    { k: "tokens_month", t: "Tokens, 30d", f: (m) => tok(m.tokens_month) },
    { k: "price_in", t: "$/M in", f: (m) => (m.price_in ? "$" + m.price_in.toFixed(2) : "–") },
    { k: "price_out", t: "$/M out", f: (m) => (m.price_out ? "$" + m.price_out.toFixed(2) : "–") },
  ];
  table($("models"), "models", cols, rows);
  $("models-count").textContent = rows.length < total ? `Top ${rows.length} of ${total} models by 30-day spend.` : `${total} models`;
  $("more").hidden = !!(q || lab) || total <= 50;
  $("more").textContent = S.all ? "Show top 50" : `Show all ${total}`;
}

function appsTable() {
  const cols = [
    { k: "title", t: "App", l: 1, f: (a) => (a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>` : esc(a.title)) },
    { k: "tokens", t: "Tokens", f: (a) => tok(a.tokens) },
    { k: "requests", t: "Requests", f: (a) => tok(a.requests) },
    { k: "cat", t: "Category", l: 1, v: (a) => a.categories.join(", "), f: (a) => `<span class="mute name cat">${esc(a.categories.join(", ").replace(/-/g, " "))}</span>` },
  ];
  S.sort.apps = S.sort.apps || "tokens"; S.dir.apps = S.dir.apps || -1;
  table($("apps"), "apps", cols, D.apps.map((a, i) => ({ ...a, id: i })));
}

function ocChart() {
  const oc = D.open_closed;
  if (!oc.length) return;
  const xs = oc.map((w) => w.x);
  const keep = S.mode; S.mode = "share";
  stacked("oc-chart", xs, [
    { name: "Closed", color: "#3d3d42", data: oc.map((w) => w.ys.closed || 0) },
    { name: "Open", color: "#7fb069", data: oc.map((w) => w.ys.open || 0) },
  ], { fmt: tok });
  S.mode = keep;
}

function summary() {
  const ls = D.labs.filter((l) => l.id !== "other").slice(0, 4);
  const lead = ls[0];
  const ppm = (l) => (l.tokens_30d ? (l.spend_30d / l.tokens_30d) * 1e6 : 0);
  const tot = sum(D.labs.map((l) => l.spend_30d));
  $("summary").innerHTML = `Last 30 days: ${ls.map((l) => `<b>${esc(l.name)} ${pct(l.share_month)}</b>`).join(", ")} of spend on OpenRouter, about ${usd(tot)} in total at list prices. ` +
    `${esc(lead.name)} gets that share from ${pct(lead.token_share_month)} of tokens, at $${ppm(lead).toFixed(2)} per million.`;
  $("updated").textContent = "Updated " + new Date(D.fetched_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

function renderAll() { mainChart(); labsTable(); mixChart(); launchChart(); taskChart(); modelsTable(); appsTable(); ocChart(); }

function wire() {
  document.querySelectorAll(".seg").forEach((seg) => seg.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b || b.disabled) return;
    seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    S[seg.dataset.ctl] = b.dataset.v;
    if (seg.dataset.ctl === "range" && b.dataset.v === "weekly") { /* tokens only */ }
    if (seg.dataset.ctl === "metric") { mixChart(); launchChart(); taskChart(); }
    if (seg.dataset.ctl === "mode") mixChart();
    mainChart();
  }));
  const sel = $("mix-lab"), ml = $("model-lab");
  D.labs.forEach((l) => { sel.add(new Option(l.name, l.id)); ml.add(new Option(l.name, l.id)); });
  sel.value = S.lab = D.labs[0].id;
  sel.onchange = () => { S.lab = sel.value; labsTable(); mixChart(); launchChart(); };
  $("cum").onchange = launchChart; $("logy").onchange = launchChart;
  $("more").onclick = () => { S.all = !S.all; modelsTable(); };
  $("q").oninput = modelsTable; ml.onchange = modelsTable;
  let t; window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { Object.values(charts).forEach((c) => c.resize()); renderAll(); }, 150); });
}

fetch("/api/data").then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }).then((d) => {
  D = d; summary(); wire(); renderAll();
}).catch((e) => { $("summary").textContent = "Couldn't load data (" + e.message + "). The collector may still be running its first fetch; try again in a minute."; });
})();
