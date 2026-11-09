/* PsychoPal · cross-device sync of browser marks via the GitHub contents API (ported from the Neurodynamics Digest).
   Syncs localStorage keys pp-marks and pp-search-sent to user-state.json on the repo's `user-state` branch
   (own branch: no Pages rebuilds, no clashes with the weekly push to main). The weekly run reads that file to learn
   from likes / dislikes / saves. Token: fine-grained PAT (only this repo, Contents read/write), kept in THIS browser's
   localStorage (pp-sync-token) only. Merge: every leaf (e.g. star -> id) carries a timestamp; newest wins per leaf,
   deletions are tombstones, so two devices never wipe each other's marks. No key = local only, as before. */
(function(){
const REPO=(window.PP_CONFIG&&window.PP_CONFIG.repo)||"",BRANCH="user-state",PATH="user-state.json";
const OWNER=REPO.split("/")[0]||"",RNAME=REPO.split("/")[1]||"PsychoPal";
const KEYS=["pp-marks","pp-search-sent"],TK="pp-sync-token",MK="pp-sync-meta",SEP="\t",DEBOUNCE=4000;
const API=(()=>{try{return localStorage.getItem("pp-sync-api")||"https://api.github.com"}catch(e){return"https://api.github.com"}})();
const ls={get:k=>{try{return localStorage.getItem(k)}catch(e){return null}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}},del:k=>{try{localStorage.removeItem(k)}catch(e){}}};
const J=s=>{try{return JSON.parse(s)}catch(e){return null}};
// ---- flatten a store to leaves: top-level objects become "key\tsub"; everything else is one leaf ----
function flat(o){const f={};if(!o||typeof o!=="object")return f;
 for(const[k,v]of Object.entries(o)){if(v&&typeof v==="object"&&!Array.isArray(v)){for(const[s,x]of Object.entries(v))f[k+SEP+s]=JSON.stringify(x);if(!Object.keys(v).length)f[k+SEP]="{}"}else f[k]=JSON.stringify(v)}return f}
function unflat(f){const o={};for(const[k,e]of Object.entries(f)){if(e.d)continue;const i=k.indexOf(SEP);
 if(i<0){o[k]=J(e.v);continue}const a=k.slice(0,i),b=k.slice(i+1);o[a]=o[a]&&typeof o[a]==="object"?o[a]:{};if(b!=="")o[a][b]=J(e.v)}return o}
// meta = {stores:{key:{leaf:{v,t,d?}}}, sha, synced}
function loadMeta(){const m=J(ls.get(MK))||{};m.stores=m.stores||{};return m}
let meta=loadMeta();const saveMeta=()=>ls.set(MK,JSON.stringify(meta));
// Record local edits: compare the live store with the last-known leaves; stamp changes now, deletions as tombstones.
function track(now){now=now||Date.now();let changed=false;
 for(const key of KEYS){const raw=ls.get(key),first=!meta.stores[key];const cur=flat(J(raw)||{}),known=meta.stores[key]||{};
  for(const[k,v]of Object.entries(cur)){const e=known[k];if(!e||e.d||e.v!==v){known[k]={v,t:first?1:now};changed=true}}
  for(const[k,e]of Object.entries(known))if(!e.d&&!(k in cur)){known[k]={v:null,t:now,d:1};changed=true}
  meta.stores[key]=known}
 if(changed)saveMeta();return changed}
// Merge remote leaves into local meta (newest t wins; ties: tombstone wins). Returns {localChanged:Set(keys), remoteNeeds:bool}
function mergeIn(remote){const out={local:new Set(),push:false};remote=remote||{};
 for(const key of new Set([...KEYS,...Object.keys(remote)])){const L=meta.stores[key]=meta.stores[key]||{},R=remote[key]||{};
  for(const k of new Set([...Object.keys(L),...Object.keys(R)])){const a=L[k],b=R[k];
   if(a&&!b){out.push=true;continue}if(b&&!a){L[k]=b;out.local.add(key);continue}
   if(a.t===b.t&&!!a.d===!!b.d&&a.v===b.v)continue;
   // newest wins; on a timestamp tie a removal wins, then the larger value (deterministic on every device)
   const localWins=a.t!==b.t?a.t>b.t:!!a.d!==!!b.d?!!a.d:String(a.v)>String(b.v);
   if(localWins)out.push=true;else{L[k]=b;out.local.add(key)}}}
 for(const key of out.local)if(KEYS.includes(key))ls.set(key,JSON.stringify(unflat(meta.stores[key])));
 saveMeta();return out}
// ---- GitHub contents API ----
const b64e=s=>{const b=new TextEncoder().encode(s);let r="";for(let i=0;i<b.length;i+=0x8000)r+=String.fromCharCode.apply(null,b.subarray(i,i+0x8000));return btoa(r)};
const b64d=s=>new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\s/g,"")),c=>c.charCodeAt(0)));
const tok=()=>ls.get(TK);
const hdr=t=>({"Accept":"application/vnd.github+json","Authorization":"Bearer "+t,"X-GitHub-Api-Version":"2022-11-28"});
async function getRemote(t){const r=await fetch(`${API}/repos/${REPO}/contents/${PATH}?ref=${BRANCH}&_=${Date.now()}`,{headers:hdr(t),cache:"no-store"});
 if(r.status===404)return{sha:null,data:{version:1,stores:{}}};if(!r.ok)throw httpErr(r);const j=await r.json();
 return{sha:j.sha,data:J(b64d(j.content||""))||{version:1,stores:{}}}}
async function putRemote(t,data,sha){const body={message:"sync: user state",content:b64e(JSON.stringify(data,null,0)),branch:BRANCH};if(sha)body.sha=sha;
 const r=await fetch(`${API}/repos/${REPO}/contents/${PATH}`,{method:"PUT",headers:{...hdr(t),"Content-Type":"application/json"},body:JSON.stringify(body),cache:"no-store"});
 if(!r.ok)throw httpErr(r);const j=await r.json();return j.content&&j.content.sha}
function httpErr(r){const e=new Error("HTTP "+r.status);e.status=r.status;return e}
// ---- status + listeners ----
const subs={};let status=tok()?"pending":"nokey",detail="",timer=0,busy=null,again=false;
function setStatus(s,d){status=s;detail=d||"";paint()}
function notify(keys){for(const k of keys)(subs[k]||[]).forEach(fn=>{try{fn(J(ls.get(k))||{})}catch(e){console.error(e)}})}
async function syncNow(){const t=tok();track();if(!t||!REPO){setStatus("nokey");return}
 if(busy){again=true;return busy}
 busy=(async()=>{try{
  if(navigator.onLine===false)throw Object.assign(new Error("offline"),{offline:1});
  setStatus("syncing");
  for(let attempt=0;attempt<4;attempt++){
   const{sha,data}=await getRemote(t);const m=mergeIn(data.stores);notify(m.local);
   const known=sha!==null&&!m.push;if(known){meta.sha=sha;meta.synced=Date.now();saveMeta();setStatus("synced");return}
   const payload={version:1,_readme:data._readme||"PsychoPal marks (likes, dislikes, saves). Written by archive/sync.js; read by the weekly run. Never overwrite from builds.",updated:Date.now(),stores:meta.stores};
   try{meta.sha=await putRemote(t,payload,sha);meta.synced=Date.now();saveMeta();setStatus("synced");return}
   catch(e){if(e.status===409||e.status===422){await new Promise(r=>setTimeout(r,300+Math.random()*700));continue}throw e}}
  throw new Error("kept conflicting, will retry")
 }catch(e){if(e.offline||e instanceof TypeError)setStatus("offline","will retry when back online");
  else if(e.status===401)setStatus("error","key rejected (expired or revoked?)");else if(e.status===403||e.status===404)setStatus("error","key has no access to "+REPO+" (needs Contents: read and write)");
  else setStatus("error",e.message)}
 finally{busy=null;if(again){again=false;schedule(1500)}}})();return busy}
function schedule(ms){clearTimeout(timer);if(!tok()){paint();return}setStatus("pending");timer=setTimeout(syncNow,ms==null?DEBOUNCE:ms)}
// Poll localStorage (pages write it directly) so no page has to call us on every change.
function poll(){if(track())schedule()}
setInterval(poll,1200);addEventListener("storage",e=>{if(KEYS.includes(e.key)){if(track())schedule()}});
addEventListener("online",()=>tok()&&schedule(500));
document.addEventListener("visibilitychange",()=>{if(document.hidden){if(status==="pending"){clearTimeout(timer);syncNow()}}else if(tok())schedule(300)});
addEventListener("pagehide",()=>{if(status==="pending")syncNow()});
// ---- UI: status pill + Connect dialog ----
const CSS=`#pp-sync{position:fixed;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:50;font:12px/1.2 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--panel,#fff);border:1px solid var(--line,#ddd);color:var(--mut,#555);padding:6px 11px;border-radius:999px;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.12)}
#pp-sync:hover{color:var(--tx,#111)}#pp-sync i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:#8a9097;vertical-align:0}
#pp-sync.synced i{background:#2f8a4b}#pp-sync.pending i,#pp-sync.syncing i{background:#d9a21b}#pp-sync.offline i{background:#4a90d9}#pp-sync.error i{background:#c2412d}
#pp-sd{position:fixed;inset:0;z-index:60;background:rgba(10,12,16,.55);display:none;align-items:flex-start;justify-content:center;overflow:auto;padding:30px 12px}
#pp-sd .bx{background:var(--panel,#fff);border:1px solid var(--line,#ddd);border-radius:14px;max-width:560px;width:100%;padding:18px 20px;color:var(--tx,#111);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
#pp-sd h2{margin:0 0 6px;font-size:19px}#pp-sd .m{font-size:13px;color:var(--mut,#555)}#pp-sd ol{padding-left:20px;margin:8px 0;font-size:14px}#pp-sd li{margin:4px 0}
#pp-sd input{width:100%;padding:10px;background:var(--panel2,#f3f3f3);border:1px solid var(--line,#ddd);border-radius:8px;color:var(--tx,#111);font-family:ui-monospace,Consolas,monospace;font-size:13px;margin:6px 0}
#pp-sd button{cursor:pointer;background:var(--panel2,#f3f3f3);border:1px solid var(--line,#ddd);border-radius:8px;color:var(--tx,#111);font-size:14px;padding:7px 12px;margin:4px 6px 0 0;font-family:inherit}#pp-sd button.pri{background:var(--accent,#6d4bd8);border-color:var(--accent,#6d4bd8);color:#fff}
#pp-sd a{color:var(--accent,#6d4bd8)}#pp-sd .msg{margin-top:8px;font-size:14px;min-height:1em}#pp-sd .ok{color:#2f8a4b}#pp-sd .bad{color:#c2412d}#pp-sd details{margin-top:8px}#pp-sd summary{cursor:pointer;color:var(--mut,#555);font-size:13px}`;
const LABEL={nokey:"local only · connect sync",pending:"changes pending",syncing:"syncing…",synced:"synced",offline:"offline",error:"sync error"};
let pill,dlg;
const ago=t=>{if(!t)return"never";const s=(Date.now()-t)/1000;return s<60?"just now":s<3600?Math.round(s/60)+" min ago":new Date(t).toLocaleString()};
function paint(){if(!pill)return;pill.className=status;pill.innerHTML=`<i></i>${LABEL[status]||status}`;pill.title=(detail?detail+" · ":"")+"Last synced: "+ago(meta.synced)+" · click for sync settings";if(dlg&&dlg.style.display==="flex")fill()}
const NEWTOKEN="https://github.com/settings/personal-access-tokens/new?name=PsychoPal+sync&description="+encodeURIComponent("PsychoPal: sync likes/saves. Only the PsychoPal repo, Contents read+write.")+(OWNER?"&target_name="+encodeURIComponent(OWNER):"")+"&expires_in=366&contents=write";
function fill(){const t=tok(),b=dlg.querySelector(".bx");
 if(!REPO){b.innerHTML=`<h2>⇅ Sync my marks across devices</h2><p>Sync is not set up yet: put your repository name (e.g. <code>yourname/PsychoPal</code>) in <code>config/settings.yaml</code> → <code>github_repo</code>; the next weekly run enables it. Until then, likes and saves stay in this browser.</p><button data-a="close">Close</button>`;return}
 b.innerHTML=`<h2>⇅ Sync my marks across devices</h2>
 <p class="m">Repo: <a href="https://github.com/${REPO}/blob/${BRANCH}/${PATH}" target="_blank" rel="noopener">${REPO}</a> · branch <b>${BRANCH}</b> · file ${PATH}<br>Status: <b>${LABEL[status]||status}</b>${detail?" · "+detail:""} · last synced ${ago(meta.synced)}</p>
 <p style="font-size:14px">Your likes, hides, saves and read marks are saved to that file, so every device sees the same marks and the weekly run learns what you like. Your key stays in <b>this browser only</b>; it is never written to the repo or a link. The repo is public, so marked item ids are visible to anyone who looks.</p>
 ${t?`<p class="ok">✓ Connected on this device (key …${t.slice(-4)}).</p><button class="pri" data-a="now">Sync now</button><button data-a="test">Test key</button><button data-a="off">Disconnect this device</button>`:
 `<b>Connect this device</b> (once per browser / device)
 <ol><li>On GitHub, open <a href="${NEWTOKEN}" target="_blank" rel="noopener">this pre-filled new-token page</a> (or: profile picture › <b>Settings</b> › <b>Developer settings</b> › <b>Personal access tokens</b> › <b>Fine-grained tokens</b> › <b>Generate new token</b>).</li>
 <li>Token name: e.g. "PsychoPal sync". <b>Expiration</b>: pick one (e.g. 1 year); you'll redo this step when it expires.</li>
 <li><b>Resource owner</b>: ${OWNER||"you"}. <b>Repository access</b>: <b>Only select repositories</b> › <b>${RNAME}</b>.</li>
 <li><b>Permissions</b> › Repository permissions › <b>Contents</b>: <b>Read and write</b> (Metadata: read is added automatically). Nothing else.</li>
 <li><b>Generate token</b>, copy it (starts <code>github_pat_</code>) and paste it below. You can reuse the same token on your other devices, or make one per device.</li></ol>
 <input id="pp-tk" type="password" placeholder="github_pat_…" autocomplete="off" spellcheck="false"><button class="pri" data-a="save">Test &amp; connect</button>`}
 <button data-a="close">Close</button><div class="msg" id="pp-msg"></div>
 <details><summary>How merging works</summary><p class="m">Each mark carries its own timestamp. On every sync the newest change per mark wins and removals are remembered, so marking different papers on two devices keeps both. If two devices save at the same moment, the later one re-reads the file, merges and retries. Without a key everything still works, saved in this browser only.</p></details>`}
async function test(t){const r=await fetch(`${API}/repos/${REPO}`,{headers:hdr(t),cache:"no-store"});if(!r.ok)throw httpErr(r);const j=await r.json();
 await getRemote(t);return j}
function msg(h,c){const m=document.getElementById("pp-msg");if(m){m.className="msg "+(c||"");m.innerHTML=h}}
async function act(a){const t=tok();
 if(a==="close"){dlg.style.display="none";return}
 if(a==="now"){await syncNow();msg(status==="synced"?"Synced.":"Status: "+(LABEL[status]||status)+(detail?" · "+detail:""),status==="synced"?"ok":"bad");return}
 if(a==="off"){ls.del(TK);setStatus("nokey");fill();msg("Disconnected. Your marks stay in this browser. Delete the token on GitHub too if you no longer need it.","ok");return}
 if(a==="test"||a==="save"){const k=a==="save"?(document.getElementById("pp-tk").value||"").trim():t;if(!k){msg("Paste a token first.","bad");return}
  msg("Testing…");try{const j=await test(k);const w=!j.permissions||j.permissions.push;
   if(a==="save"){ls.set(TK,k);fill();await syncNow()}
   msg(`✓ Key works for <b>${j.full_name}</b>${w?"":" (but GitHub reports no write access: set Contents to Read and write)"}. ${a==="save"?"Status: "+(LABEL[status]||status)+(detail?" · "+detail:""):""}`,w?"ok":"bad")}
  catch(e){msg(e.status===401?"GitHub rejected this key (typo, expired or revoked).":e.status===404||e.status===403?"This key can't see "+REPO+". Under Repository access choose Only select repositories › "+RNAME+", and Contents: Read and write.":"Couldn't reach GitHub ("+e.message+"). Check your connection.","bad")}}}
function open(){dlg.style.display="flex";fill()}
function ui(){if(pill)return;const s=document.createElement("style");s.textContent=CSS;document.head.appendChild(s);
 pill=document.createElement("button");pill.id="pp-sync";pill.type="button";pill.addEventListener("click",open);document.body.appendChild(pill);
 dlg=document.createElement("div");dlg.id="pp-sd";dlg.innerHTML='<div class="bx" role="dialog" aria-label="Sync settings"></div>';document.body.appendChild(dlg);
 dlg.addEventListener("click",e=>{if(e.target===dlg){dlg.style.display="none";return}const b=e.target.closest("[data-a]");if(b)act(b.dataset.a)});
 document.querySelectorAll("[data-sync-open]").forEach(el=>el.addEventListener("click",e=>{e.preventDefault();open()}));paint()}
// ---- public API ----
window.PPSync={on(key,fn){(subs[key]=subs[key]||[]).push(fn)},syncNow,open:()=>{ui();open()},status:()=>status,_t:{flat,unflat,track,mergeIn,meta:()=>meta}};
track();
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",ui);else ui();
if(tok()&&REPO)setTimeout(syncNow,50);
})();
