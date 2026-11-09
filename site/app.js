/* PsychoPal feed. Loads data/index.json, then one week at a time (data/weeks/<week>.json).
   Marks (like / dislike / star) live in localStorage "pp-marks", shared with the archive and synced by archive/sync.js. */
(() => {
"use strict";
const STREAMS = {
  psychedelics:     {label: "Psychedelic research",       c: "#8b5cf6", i: "✺"},
  neuropsychopharm: {label: "Neuropsychopharmacology",    c: "#e07b2a", i: "⚗︎"},
  neuroimaging:     {label: "Neuroimaging",               c: "#0f9f95", i: "◉"},
  comp_neuro:       {label: "Computational neuroscience", c: "#2f86d0", i: "∑"},
  neuroscience:     {label: "Neuroscience",               c: "#5b6fd6", i: "✧"},
  bio_psychiatry:   {label: "Biological psychiatry",      c: "#d6457a", i: "✚"},
  brain_body:       {label: "Brain–body coupling",        c: "#e0604a", i: "♥︎"},
  neurodynamics:    {label: "Neurodynamics",              c: "#6f9e1e", i: "∿"},
  wider:            {label: "Wider science",              c: "#64748b", i: "✦"},
  tools:            {label: "Tools & methods",            c: "#b8860b", i: "⚙︎"},
};
const SIDE = ["wider", "tools"];   // shown after your streams, never in "For you" stream sections
const TOOL_KINDS = ["tool_release", "tool_paper", "tool_repo"];
const KINDS = {
  paper:        {label: "Paper",       c: null,      i: null},
  trial:        {label: "New trial",   c: "#8b5cf6", i: "⊕"},
  catchup:      {label: "Catch-up",    c: "#9a7b4f", i: "↺"},
  funding:      {label: "Funding",     c: "#c98a1b", i: "£"},
  conference:   {label: "Conference",  c: "#c0392b", i: "◎"},
  training:     {label: "Training",    c: "#2e8b57", i: "✎︎"},
  tool_release: {label: "New release", c: "#b8860b", i: "⚙︎"},
  tool_paper:   {label: "Methods",     c: "#b8860b", i: "⚙︎"},
  tool_repo:    {label: "New on GitHub", c: "#b8860b", i: "⚙︎"},
};
const TABS = [
  ["all", "For you", () => true],
  ["papers", "Papers", c => ["paper", "trial", "catchup"].includes(c.kind)],
  ["funding", "Funding", c => c.kind === "funding"],
  ["events", "Events", c => c.kind === "conference"],
  ["training", "Training", c => c.kind === "training"],
  ["tools", "Tools & methods", c => TOOL_KINDS.includes(c.kind)],
  ["saved", "★ Saved", c => !!M.star[c.id]],
];
const ICON = {
  like: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M7 10v11H3V10h4Zm0 0 4-8a3 3 0 0 1 3 3v4h5.5a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18.1 21H7"/></svg>',
  dis: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M17 14V3h4v11h-4Zm0 0-4 8a3 3 0 0 1-3-3v-4H4.5a2 2 0 0 1-2-2.3l1.4-8A2 2 0 0 1 5.9 3H17"/></svg>',
  star: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9L12 3Z"/></svg>',
  starOn: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9L12 3Z"/></svg>',
  share: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v13M7 8l5-5 5 5M5 14v6h14v-6"/></svg>',
  open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/></svg>',
  claude: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>',
};
const ASKABLE = ["paper", "catchup", "tool_paper", "trial"];
const $ = id => document.getElementById(id);
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const safeUrl = u => /^https?:\/\//i.test(u || "") ? u : "";
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtDate = s => { if (!s) return ""; const p = String(s).slice(0, 10).split("-"); if (p.length === 1) return p[0];
  if (p.length === 2) return `${MON[+p[1] - 1]} ${p[0]}`; return `${+p[2]} ${MON[+p[1] - 1]} ${p[0]}`; };
const daysTo = s => { if (!s || !/^\d{4}-\d{2}-\d{2}/.test(s)) return null; const d = new Date(s.slice(0, 10) + "T23:59:59");
  return Math.ceil((d - new Date()) / 864e5); };

// ---- marks (shared with archive; synced by sync.js) ----
const KEY = "pp-marks";
const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } };
let M = load();
const norm = () => { for (const k of ["like", "dislike", "star", "status", "ni", "more"]) M[k] = M[k] || {}; };
norm();
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(M)); } catch (e) {} };
window.addEventListener("load", () => window.PPSync && PPSync.on(KEY, s => { M = s || {}; norm(); render(); }));

// ---- state ----
const S = {tab: "all", stream: "", weeks: [], loaded: [], loading: false, index: null};
try { const t = location.hash.slice(1) || sessionStorage.getItem("pp-tab"); if (t && TABS.some(x => x[0] === t)) S.tab = t; } catch (e) {}

async function getJSON(u) { const r = await fetch(u, {cache: "no-cache"}); if (!r.ok) throw new Error(r.status); return r.json(); }

async function boot() {
  try { S.index = await getJSON("data/index.json"); }
  catch (e) { $("feed").innerHTML = '<p class="empty">No edition yet. The first one appears after the weekly run.</p>'; return; }
  S.weeks = S.index.weeks || [];
  if (!S.weeks.length) { $("feed").innerHTML = '<p class="empty">No edition yet.</p>'; return; }
  await loadNext();
  observe();
}
async function loadNext() {
  if (S.loading || S.loaded.length >= S.weeks.length) return;
  S.loading = true;
  const w = S.weeks[S.loaded.length];
  try { const d = await getJSON(`data/weeks/${w.week}.json`); S.loaded.push(d); }
  catch (e) { S.loaded.push({week: w.week, start: w.start, end: w.end, cards: [], error: true}); }
  S.loading = false;
  render();
}
function observe() {
  if (!("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) loadNext(); }, {rootMargin: "600px"});
  const tick = () => { const b = document.querySelector(".more"); if (b) io.observe(b); };
  new MutationObserver(tick).observe($("feed"), {childList: true});
  tick();
}

// ---- rendering ----
function tabsHTML() {
  const all = S.loaded.flatMap(w => w.cards);
  $("tabs").innerHTML = TABS.map(([k, l, f]) => {
    const n = k === "all" ? "" : all.filter(c => f(c) && !M.dislike[c.id]).length;
    return `<button class="chip${S.tab === k ? " on" : ""}" data-tab="${k}">${l}${n !== "" ? `<span class="n">${n}</span>` : ""}</button>`;
  }).join("");
  const sb = $("streams");
  if (S.tab === "papers") {
    const papers = all.filter(c => c.kind === "paper" && !M.dislike[c.id]);
    const present = Object.keys(STREAMS).filter(s => papers.some(c => c.stream === s));
    sb.innerHTML = `<button class="chip${!S.stream ? " on" : ""}" data-stream="">All topics</button>` + present.map(s =>
      `<button class="chip${S.stream === s ? " on" : ""}" data-stream="${s}" style="--c:${STREAMS[s].c}"><span class="dot"></span>${esc(STREAMS[s].label)}<span class="n">${papers.filter(c => c.stream === s).length}</span></button>`).join("");
    sb.hidden = false;
  } else sb.hidden = true;
  const w = S.loaded[0];
  $("wk").textContent = w ? `${fmtDate(w.start).replace(/ \d{4}$/, "")} – ${fmtDate(w.end)}` : "";
}
function look(c) {
  const k = KINDS[c.kind] || KINDS.paper, s = STREAMS[c.stream] || STREAMS.neuroscience;
  return {c: k.c || s.c, i: k.i || s.i, label: c.kind === "paper" ? s.label : k.label};
}
function badges(c) {
  const b = [];
  if (c.top) b.push('<span class="bd top">TOP 5 FOR YOU</span>');
  if (c.kind === "paper" || c.kind === "catchup") {
    if (c.is_preprint) b.push('<span class="bd pre">PREPRINT</span>');
    else if (c.kind === "paper") b.push('<span class="bd">PEER REVIEWED</span>');
    if (c.is_review) b.push('<span class="bd">REVIEW</span>');
  }
  if (c.modality) b.push(`<span class="bd">${esc(c.modality)}</span>`);
  if (c.buzz && c.buzz.score) b.push(`<span class="bd hot" title="${esc(c.buzz.note || "")}">🔥 BUZZ ${esc(c.buzz.posts || "")}</span>`);
  if (c.phase) b.push(`<span class="bd">${esc(c.phase)}</span>`);
  if (c.citations) b.push(`<span class="bd">${esc(c.citations)} citations</span>`);
  const d = daysTo(c.deadline);
  if (d !== null && d >= 0) b.push(`<span class="bd dead">⏳ ${d === 0 ? "deadline today" : d === 1 ? "1 day left" : d + " days left"}</span>`);
  if (c.reason) b.push(`<span class="bd new">${esc(c.reason)}</span>`);
  if (c.eligibility_note) b.push(`<span class="bd">${esc(c.eligibility_note)}</span>`);
  for (const t of (c.ai_tags || []).slice(0, 2)) b.push(`<span class="bd">#${esc(t)}</span>`);
  return b.length ? `<div class="badges">${b.join("")}</div>` : "";
}
function sub(c) {
  const parts = [];
  if (c.venue) parts.push(esc(c.venue));
  if (c.kind === "funding" && c.funder) parts.push(esc(c.funder));
  if ((c.kind === "conference" || c.kind === "training") && c.event_dates) parts.push(esc(c.event_dates));
  if (c.location) parts.push(esc(c.location));
  if (c.deadline) parts.push("deadline " + esc(fmtDate(c.deadline)));
  else if (c.date && c.kind !== "funding") parts.push(esc(fmtDate(c.date)));
  return parts.join(" · ");
}
function authors(c) {
  const a = c.authors || [];
  if (!a.length) return "";
  const t = a.length > 4 ? `${a.slice(0, 3).join(", ")} … ${a[a.length - 1]}` : a.join(", ");
  return `<p class="au">${esc(t)}</p>`;
}
function card(c) {
  if (M.dislike[c.id]) return `<div class="hidden-note">Hidden: ${esc((c.title || "").slice(0, 60))}… <button data-undo="${c.id}">Undo</button></div>`;
  const L = look(c), url = safeUrl(c.url);
  return `<article class="card${c.top ? " top5" : ""}" style="--c:${L.c}">
  <div class="ch"><div class="av" aria-hidden="true">${L.i}</div><div><div class="who">${esc(L.label)}</div><div class="when">${sub(c)}</div></div></div>
  <h3>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(c.title)}</a>` : esc(c.title)}</h3>
  ${(c.sections || []).length ? `<dl class="abs">${c.sections.map(([k, v]) => `<div class="${esc(k.toLowerCase())}"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>`
    : c.post ? `<p class="post">${esc(c.post)}</p>` : ""}
  ${c.why_you ? `<p class="why"><b>${c.stream === "wider" ? "Why it's interesting" : "Why it matters for you"}:</b> ${esc(c.why_you)}</p>` : ""}
  ${c.why_picked ? `<p class="pick">Picked for: ${esc(c.why_picked)}</p>` : ""}
  ${authors(c)}${badges(c)}
  <div class="acts">
    <button class="like${M.like[c.id] ? " on" : ""}" data-like="${c.id}" aria-pressed="${!!M.like[c.id]}" title="More like this">${ICON.like}</button>
    <button class="dis" data-dis="${c.id}" title="Not for me (hides it, and similar items rank lower)">${ICON.dis}</button>
    <button class="star${M.star[c.id] ? " on" : ""}" data-star="${c.id}" aria-pressed="${!!M.star[c.id]}" title="Save">${M.star[c.id] ? ICON.starOn : ICON.star}</button>
    <button data-share="${c.id}" title="Share">${ICON.share}</button>
    ${ASKABLE.includes(c.kind) && url ? `<button class="ask" data-ask="${c.id}" title="Ask Claude about this paper (opens the share menu: pick the Claude app)">${ICON.claude}</button>` : ""}
    ${url ? `<a class="open" href="${esc(url)}" target="_blank" rel="noopener">${ICON.open} Open</a>` : ""}
  </div></article>`;
}
function section(title, cards, color) {
  if (!cards.length) return "";
  return `<div class="sec" style="--c:${color || "var(--accent)"}"><span class="dot"></span><h2>${esc(title)}</h2><span class="ln"></span></div>${cards.map(card).join("")}`;
}
function strip(w, kinds) {
  const items = (w.deadlines || []).filter(d => !kinds || kinds.includes(d.kind)).map(d => ({...d, days: daysTo(d.deadline)}))
    .filter(d => d.days !== null && d.days >= 0 && !M.dislike[d.id]).sort((a, b) => a.days - b.days);
  if (!items.length) return "";
  return `<div class="sec"><span class="dot"></span><h2>Upcoming deadlines</h2><span class="ln"></span></div><div class="strip">${items.map(d => {
    const k = KINDS[d.kind] || KINDS.funding, u = safeUrl(d.url);
    return `<a class="dl" style="--c:${k.c}" ${u ? `href="${esc(u)}" target="_blank" rel="noopener"` : ""}><div class="d">${d.days}<small>days</small></div><div class="t">${esc(d.name)}</div><div class="k">${esc(k.label)} · ${esc(d.label || "deadline")} ${esc(fmtDate(d.deadline))}</div></a>`;
  }).join("")}</div>`;
}
function weekHTML(w, first) {
  const cards = w.cards || [];
  const f = TABS.find(t => t[0] === S.tab)[2];
  const vis = cards.filter(f);
  const n = k => cards.filter(c => c.kind === k).length;
  const tools = cards.filter(c => TOOL_KINDS.includes(c.kind)).length;
  let h = (first && (w.alerts || []).length) ? `<div class="alert" role="status">${w.alerts.map(a => `<p>⚠︎ ${esc(a)}</p>`).join("")}</div>` : "";
  h += `<div class="edition"><b>${esc(w.label || w.week)}</b> · ${esc(fmtDate(w.start))} – ${esc(fmtDate(w.end))}<br>${n("paper")} papers · ${n("funding") + n("conference") + n("training")} opportunities · ${tools} tools${n("trial") ? ` · ${n("trial")} new trials` : ""}</div>`;
  if (w.error) return h + '<p class="empty">Could not load this week.</p>';
  if (S.tab === "all") {
    h += section("Top 5 for you", vis.filter(c => c.top), "var(--accent)");
    if (first) h += strip(w);
    h += section("Funding, events & training", vis.filter(c => ["funding", "conference", "training"].includes(c.kind)), "#c98a1b");
    for (const s of Object.keys(STREAMS)) if (!SIDE.includes(s)) h += section(STREAMS[s].label, vis.filter(c => c.kind === "paper" && !c.top && c.stream === s), STREAMS[s].c);
    h += section("New psychedelic trials", vis.filter(c => c.kind === "trial"), KINDS.trial.c);
    h += section("Catch-up: classics worth knowing", vis.filter(c => c.kind === "catchup"), KINDS.catchup.c);
    h += section("Tools & methods", vis.filter(c => TOOL_KINDS.includes(c.kind)), STREAMS.tools.c);
    h += section("Wider science", vis.filter(c => c.kind === "paper" && !c.top && c.stream === "wider"), STREAMS.wider.c);
    if (w.health && w.health.length) h += `<div class="health">Run notes: ${w.health.map(esc).join(" · ")}</div>`;
  } else if (S.tab === "papers") {
    const ps = vis.filter(c => !S.stream || c.stream === S.stream);
    for (const s of Object.keys(STREAMS)) h += section(STREAMS[s].label, ps.filter(c => c.kind === "paper" && c.stream === s), STREAMS[s].c);
    h += section("New psychedelic trials", S.stream && S.stream !== "psychedelics" ? [] : ps.filter(c => c.kind === "trial"), KINDS.trial.c);
    h += section("Catch-up", ps.filter(c => c.kind === "catchup"), KINDS.catchup.c);
  } else if (S.tab === "saved") {
    h = vis.length ? section(`${w.label || w.week}`, vis) : "";
  } else {
    const kinds = {funding: ["funding"], events: ["conference"], training: ["training"]}[S.tab];
    if (first && kinds) h += strip(w, kinds);
    h += vis.length ? vis.map(card).join("") : '<p class="empty">Nothing new here this week.</p>';
  }
  return h;
}
function render() {
  tabsHTML();
  if (!S.loaded.length) return;
  let h = S.loaded.map((w, i) => weekHTML(w, i === 0)).join("");
  if (S.tab === "saved") h = (h || '<p class="empty">Nothing saved yet. Tap ☆ on a card to keep it.</p>') +
    '<p class="foot"><a href="archive/?view=star">All saved items in the archive →</a></p>' +
    (Object.keys(M.star).length ? '<p class="foot"><button class="exp" data-export="ris">Export all saved papers for Zotero (.ris)</button></p>' : "");
  if (S.loaded.length < S.weeks.length) h += `<button class="more" data-more="1">Load previous week (${esc(S.weeks[S.loaded.length].label || S.weeks[S.loaded.length].week)})</button>`;
  else h += '<p class="foot">That\'s every edition. <a href="archive/">Search the archive →</a></p>';
  h += `<p class="foot">PsychoPal · <a href="archive/">Archive</a> · <a href="archive/search.html">Literature search</a> · <a href="#" data-sync-open>Sync</a></p>`;
  $("feed").innerHTML = h;
}

// ---- interactions ----
const byId = id => { for (const w of S.loaded) for (const c of w.cards || []) if (c.id === id) return c; return null; };
const stamp = (k, id) => { M.ts = M.ts || {}; M.ts[k + "|" + id] = Date.now(); };
document.addEventListener("click", async e => {
  const b = e.target.closest("button,[data-sync-open]"); if (!b) return;
  const d = b.dataset;
  if (d.syncOpen !== undefined) { e.preventDefault(); window.PPSync && PPSync.open(); return; }
  if (d.tab) { S.tab = d.tab; S.stream = ""; try { sessionStorage.setItem("pp-tab", S.tab); history.replaceState(null, "", S.tab === "all" ? location.pathname : "#" + S.tab); } catch (x) {} window.scrollTo({top: 0}); }
  else if (d.stream !== undefined) S.stream = d.stream;
  else if (d.like) { M.like[d.like] ? delete M.like[d.like] : (M.like[d.like] = 1, stamp("like", d.like), delete M.dislike[d.like]); save(); }
  else if (d.dis) { M.dislike[d.dis] = 1; delete M.like[d.dis]; stamp("dislike", d.dis); save(); }
  else if (d.undo) { delete M.dislike[d.undo]; save(); }
  else if (d.star) { M.star[d.star] ? delete M.star[d.star] : (M.star[d.star] = 1, stamp("star", d.star)); save(); }
  else if (d.share) {
    const c = byId(d.share); if (!c) return; const url = safeUrl(c.url) || location.href;
    try { if (navigator.share) await navigator.share({title: c.title, text: c.post || c.title, url}); else { await navigator.clipboard.writeText(`${c.title} ${url}`); b.title = "Link copied"; } } catch (x) {}
    return;
  }
  else if (d.ask) { const c = byId(d.ask); if (c) await askClaude(c); return; }
  else if (d.export) { await exportRIS(); return; }
  else if (d.more) { loadNext(); return; }
  else return;
  render();
});

// ---- "Ask Claude": hand the paper to the Claude app through the share menu (or copy the prompt on a computer) ----
function toast(msg) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}
function claudePrompt(c) {
  const what = c.kind === "trial" ? "registered clinical trial" : "paper";
  const au = (c.authors || []).length ? c.authors[0] + (c.authors.length > 1 ? " et al." : "") : "";
  const link = c.doi ? `https://doi.org/${c.doi}` : safeUrl(c.url);
  const ref = [c.title, [au, c.venue, String(c.date || "").slice(0, 4)].filter(Boolean).join(", "), link].filter(Boolean).join("\n");
  return `Please read this ${what} and summarise it for me: the research question, the design and sample, the main results ` +
    `(with effect sizes or key numbers), the main limitations, and how it fits with the existing literature. ` +
    `I am a postdoc in psychedelic neuroscience (PET, EEG, fMRI; psilocybin, DMT, ketamine), so point out anything relevant to that.\n\n${ref}`;
}
async function askClaude(c) {
  const text = claudePrompt(c);
  try {
    if (navigator.share) { await navigator.share({text}); return; }
    await navigator.clipboard.writeText(text); toast("Prompt copied: paste it into Claude");
  } catch (e) { if (e && e.name !== "AbortError") toast("Could not open the share menu"); }
}

// ---- export saved papers as RIS (Zotero, EndNote, Mendeley all import it) ----
async function exportRIS() {
  let items = [];
  try { items = await getJSON("archive/items.json"); } catch (e) { toast("Could not load the archive (offline?)"); return; }
  const saved = items.filter(i => M.star[i.id] && ["paper", "catchup", "tool_paper", "trial"].includes(i.kind));
  if (!saved.length) { toast("No saved papers to export yet"); return; }
  const line = (k, v) => v ? `${k}  - ${String(v).replace(/\s+/g, " ").trim()}\r\n` : "";
  const ris = saved.map(i => {
    const d = String(i.date || ""), pre = i.pub_status === "Preprint";
    let r = line("TY", i.kind === "trial" ? "GEN" : pre ? "UNPB" : "JOUR") + line("TI", i.title);
    for (const a of i.authors || []) { const p = String(a).trim().split(/\s+/); r += line("AU", p.length > 1 ? `${p.pop()}, ${p.join(" ")}` : a); }
    r += line("T2", i.venue_clean || i.venue) + line("PY", d.slice(0, 4)) + line("DA", d.length >= 10 ? d.slice(0, 10).replace(/-/g, "/") : "") +
      line("DO", i.doi) + line("UR", i.url) + line("AB", i.post) + line("KW", "PsychoPal") + line("N1", `Saved in PsychoPal (${i.week || ""})`);
    return r + "ER  - \r\n";
  }).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([ris], {type: "application/x-research-info-systems"}));
  a.download = `psychopal-saved-${new Date().toISOString().slice(0, 10)}.ris`;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  toast(`${saved.length} saved papers exported`);
}

// offline reading: the service worker keeps the app and recently opened weeks
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
boot();
})();
