/* PsychoPal · ⟳ button: "get new cards now" for a period you choose, on top of the automatic Monday edition.
   It starts the GitHub workflow (weekly.yml) with the sync key (fine-grained token: Contents AND Actions read+write), then follows
   the build and offers to show the new cards when it is done (about 10 minutes; the app can be closed meanwhile).
   Editions stay one per week: "this week so far" fills in the current week's edition and Monday's run completes it; cards shown
   in earlier weeks are never repeated. The period is sent as `end` (+ optional `start`); the workflow checks the dates. */
(function () {
const REPO = (window.PP_CONFIG && window.PP_CONFIG.repo) || "", API = "https://api.github.com", WF = "weekly.yml";
const TK = "pp-sync-token", ST = "pp-refresh", MAXDAYS = 28, POLL_MS = 15000, GIVE_UP_MS = 2 * 3600 * 1000;
const $ = id => document.getElementById(id);
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const ls = {get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } },
            set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} }, del: k => { try { localStorage.removeItem(k); } catch (e) {} }};
const hdr = t => ({"Accept": "application/vnd.github+json", "Authorization": "Bearer " + t, "X-GitHub-Api-Version": "2022-11-28"});

// ---- dates (local calendar days; noon avoids daylight-saving edges) ----
const pad = n => String(n).padStart(2, "0");
const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const day = s => new Date(s + "T12:00:00");
const add = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const today = () => { const t = new Date(); t.setHours(12, 0, 0, 0); return t; };
const monday = d => add(d, -((d.getDay() + 6) % 7));
const nice = s => day(s).toLocaleDateString("en-GB", {weekday: "short", day: "numeric", month: "short"});

let index = {weeks: []};
async function loadIndex() { try { index = await (await fetch("data/index.json", {cache: "no-cache"})).json(); } catch (e) {} }
// same rule as the automatic run: one edition from that Monday (or earlier) to that Sunday (or later)
const covered = (mon, sun) => (index.weeks || []).some(w => (w.start || "9999") <= mon && (w.end || "") >= sun);

// ---- dialog ----
let dlg;
function periods() {
  const t = today(), lm = add(monday(t), -7), lsun = add(lm, 6), tm = monday(t);
  const latestEnd = (index.weeks || []).map(w => w.end).sort().pop();
  let from = latestEnd ? add(day(latestEnd), 1) : tm;
  if (from > t) from = tm;
  if (from < add(t, 1 - MAXDAYS)) from = add(t, 1 - MAXDAYS);
  return {t, last: [iso(lm), iso(lsun)], lastDone: covered(iso(lm), iso(lsun)), week: [iso(tm), iso(t)], from: iso(from),
          min: iso(add(t, 1 - MAXDAYS)), max: iso(t)};
}
async function openDialog() {
  if (!dlg) {
    dlg = document.createElement("div"); dlg.id = "rf-dlg"; dlg.className = "rf-back";
    dlg.innerHTML = '<div class="rf-box" role="dialog" aria-modal="true" aria-label="Get new cards"></div>';
    document.body.appendChild(dlg);
    dlg.addEventListener("click", e => { if (e.target === dlg) close(); const b = e.target.closest("[data-rf]"); if (b) act(b.dataset.rf); });
    dlg.addEventListener("focusin", e => { if (e.target.id === "rf-from") dlg.querySelector('input[value="from"]').checked = true; });
  }
  dlg.style.display = "flex";
  const box = dlg.querySelector(".rf-box");
  const s = state();
  if (s && !s.done) { box.innerHTML = `<h2>⟳ A refresh is already running</h2><p>${esc(barText(s))}</p><div class="rf-acts"><button data-rf="close">OK</button></div>`; return; }
  if (!REPO || !ls.get(TK)) {
    box.innerHTML = `<h2>⟳ Get new cards now</h2>
      <p>To start a refresh from the app, connect sync first: the same key saves your 👍 / 👎 / ★ and starts refreshes.</p>
      <div class="rf-acts"><button class="pri" data-rf="connect">Connect sync</button><button data-rf="close">Cancel</button></div>`;
    return;
  }
  await loadIndex();
  const p = periods(), today1 = p.week[0] === p.week[1];
  box.innerHTML = `<h2>⟳ Get new cards now</h2>
    <p class="m">Builds new cards for the period you pick, on top of the automatic Monday edition. It takes about 10 minutes and you
    can close the app meanwhile. Cards already shown in earlier weeks are not repeated.</p>
    <label class="rf-opt"><input type="radio" name="rf" value="week"${p.lastDone ? " checked" : ""}>
      <span><b>This week so far</b><br><small>${today1 ? `${esc(nice(p.week[0]))} (today)` : `${esc(nice(p.week[0]))} – today`}</small></span></label>
    <label class="rf-opt"><input type="radio" name="rf" value="last"${p.lastDone ? "" : " checked"}>
      <span><b>Last week</b><br><small>${esc(nice(p.last[0]))} – ${esc(nice(p.last[1]))} · ${p.lastDone ? "already built: rebuild it" : "not built yet"}</small></span></label>
    <label class="rf-opt"><input type="radio" name="rf" value="from">
      <span><b>From a date to today</b><br><small><input type="date" id="rf-from" value="${p.from}" min="${p.min}" max="${p.max}"> up to 4 weeks</small></span></label>
    <div class="rf-acts"><button class="pri" data-rf="go">Start</button><button data-rf="close">Cancel</button></div>
    <div class="rf-msg" id="rf-msg"></div>`;
}
function close() { if (dlg) dlg.style.display = "none"; }
function msg(h, bad) { const m = $("rf-msg"); if (m) { m.className = "rf-msg" + (bad ? " bad" : ""); m.innerHTML = h; } }

async function act(a) {
  if (a === "close") return close();
  if (a === "connect") { close(); if (window.PPSync) PPSync.open(); return; }
  if (a !== "go") return;
  const p = periods(), pick = (dlg.querySelector('input[name="rf"]:checked') || {}).value;
  let inputs, label;
  if (pick === "last") { inputs = {end: p.last[1]}; label = `last week (${nice(p.last[0])} – ${nice(p.last[1])})`; }
  else if (pick === "from") {
    const f = ($("rf-from") || {}).value || "";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f) || f > p.max || f < p.min) { msg("Pick a start date within the last 4 weeks.", true); return; }
    inputs = {start: f, end: p.max}; label = `${nice(f)} – today`;
  } else { inputs = {end: p.max}; label = "this week so far"; }
  if (navigator.onLine === false) { msg("You're offline. Try again when you're connected.", true); return; }
  msg("Asking GitHub to start…");
  const t = ls.get(TK);
  let r;
  try {
    r = await fetch(`${API}/repos/${REPO}/actions/workflows/${WF}/dispatches`, {method: "POST", cache: "no-store",
      headers: {...hdr(t), "Content-Type": "application/json"}, body: JSON.stringify({ref: "main", inputs})});
  } catch (e) { msg("Couldn't reach GitHub. Check your connection and try again.", true); return; }
  if (r.status === 204 || r.status === 200) {
    save({t: Date.now(), end: inputs.end, label});
    close(); follow(); return;
  }
  if (r.status === 401) msg("GitHub rejected the sync key (expired or revoked?). Reconnect it with the sync pill, bottom right.", true);
  else if (r.status === 403 || r.status === 404)
    msg(`Your sync key can't start refreshes yet: it needs one more permission. On GitHub open
      <a href="https://github.com/settings/personal-access-tokens" target="_blank" rel="noopener">your fine-grained tokens</a> ›
      your PsychoPal key › <b>Edit</b> › Permissions › <b>Actions: Read and write</b> › <b>Update</b>. Then press Start again.`, true);
  else { let d = ""; try { d = (await r.json()).message || ""; } catch (e) {} msg(`GitHub refused the request (${r.status}${d ? ": " + esc(d) : ""}).`, true); }
}

// ---- following the build ----
const state = () => { try { return JSON.parse(ls.get(ST) || "null"); } catch (e) { return null; } };
const save = s => ls.set(ST, JSON.stringify(s));
let timer = 0;
function barText(s) {
  const min = Math.max(0, Math.round((Date.now() - s.t) / 60000));
  if (s.done === "ok") return `New cards for ${s.label} are ready.`;
  if (s.done) return `The refresh for ${s.label} didn't finish (${s.done}).`;
  if (!s.id) return `Asked GitHub to build new cards for ${s.label}; waiting for it to start… (${min} min)`;
  return `Building new cards for ${s.label}… ${min} min so far (usually about 10).`;
}
function paint() {
  const s = state(), bar = $("rf-bar"), btn = $("rf-btn");
  if (btn) btn.classList.toggle("spin", !!(s && !s.done));
  if (!bar) return;
  if (!s) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.innerHTML = `<div class="rfbar${s.done === "ok" ? " ok" : s.done ? " bad" : ""}">
    <span>${s.done ? (s.done === "ok" ? "✓ " : "✕ ") : '<i class="rf-dot"></i>'}${esc(barText(s))}</span>
    <span class="rf-acts">${s.done === "ok" ? '<button class="pri" data-rfb="show">Show</button>' : ""}${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">details</a>` : ""}${s.done ? '<button data-rfb="dismiss" aria-label="Dismiss">✕</button>' : ""}</span></div>`;
}
async function check() {
  const s = state(), t = ls.get(TK);
  if (!s || s.done || !t) return paint();
  if (Date.now() - s.t > GIVE_UP_MS) { save({...s, done: "no answer from GitHub after 2 hours"}); return paint(); }
  try {
    if (!s.id) {           // find our run: a workflow_dispatch run named "... refresh from the app, to <end>", started after we asked
      const r = await fetch(`${API}/repos/${REPO}/actions/workflows/${WF}/runs?event=workflow_dispatch&per_page=10`, {headers: hdr(t), cache: "no-store"});
      if (r.ok) {
        const run = ((await r.json()).workflow_runs || []).find(x => Date.parse(x.created_at) >= s.t - 120000 &&
          String(x.display_title || x.name || "").includes(`to ${s.end}`));
        if (run) save({...s, id: run.id, url: run.html_url});
        else if (Date.now() - s.t > 15 * 60000) save({...s, done: "GitHub never started it"});
      }
    } else {
      const r = await fetch(`${API}/repos/${REPO}/actions/runs/${s.id}`, {headers: hdr(t), cache: "no-store"});
      if (r.ok) {
        const run = await r.json();
        if (run.status === "completed") save({...s, done: run.conclusion === "success" ? "ok" : (run.conclusion || "failed")});
      }
    }
  } catch (e) {}                                                    // offline for a moment: try again on the next tick
  paint();
}
function follow() {
  clearTimeout(timer); paint();
  const tick = async () => { await check(); const s = state(); if (s && !s.done && !document.hidden) timer = setTimeout(tick, POLL_MS); };
  tick();
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) { const s = state(); if (s && !s.done) follow(); } });
document.addEventListener("click", e => {
  const b = e.target.closest("[data-rfb]");
  if (!b) return;
  if (b.dataset.rfb === "show") { ls.del(ST); location.reload(); }
  if (b.dataset.rfb === "dismiss") { ls.del(ST); paint(); }
});

function init() {
  const btn = $("rf-btn");
  if (btn) btn.addEventListener("click", openDialog);
  const s = state();
  if (s && s.done && Date.now() - s.t > 24 * 3600 * 1000) ls.del(ST);   // forget old results
  follow();
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
