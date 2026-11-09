"""Archive builder (ported from the Neurodynamics Digest build_archive_site.py).
state/items.jsonl (every card ever shown) -> site/archive/items.json with derived filter chips (drug, modality, disorder, stage,
publication status, method family), TF-IDF "More like this" links, and per-item pages with Zotero metadata (site/archive/item/<id>.html).
Never touches the user-state branch (likes/saves are user-owned)."""
import html as H, json, math, os, re
from collections import Counter, defaultdict
from .common import STATE, SITE, CONFIG, load_jsonl, write_json, log

SECTION = {"trial": "New psychedelic trials", "catchup": "Catch-up", "funding": "Funding", "conference": "Conferences",
           "training": "Training", "tool_release": "Tools & methods", "tool_paper": "Tools & methods",
           "tool_repo": "Tools & methods"}
STREAM_LABEL = {"psychedelics": "Psychedelic research", "neuropsychopharm": "Neuropsychopharmacology", "neuroimaging": "Neuroimaging",
                "comp_neuro": "Computational neuroscience", "neuroscience": "Neuroscience", "bio_psychiatry": "Biological psychiatry",
                "brain_body": "Brain–body coupling", "neurodynamics": "Neurodynamics", "wider": "Wider science", "tools": "Tools & methods"}
STOP = set("a an the and or of in on for to with by from at as is are was were be this that these those its it into vs via than not no "
           "we our their using use used based study studies new two one can may also".split())

def toks(t):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-]+", (t or "").lower()) if w not in STOP and len(w) > 2]

def rx(lst):
    return [re.compile(x) for x in lst]

def hit(pats, t):
    return any(p.search(t) for p in pats)

def derive(rows):
    with open(os.path.join(CONFIG, "tag_map.json"), encoding="utf-8") as f:
        TM = json.load(f)
    DRUG = {k: rx(v) for k, v in TM["drug_map"].items()}
    MOD = {k: rx(v) for k, v in TM["modality_map"].items()}
    DIS = {k: rx(v) for k, v in TM["disorder_map"].items() if not k.startswith("_")}
    MET = {k: rx(v) for k, v in TM.get("method_map", {}).items() if not k.startswith("_")}
    ST = TM["stage"]; PRE, CLIN = rx(ST["preclinical_patterns"]), rx(ST["clinical_patterns"])
    for r in rows:
        t = " ".join([r.get("title") or "", r.get("post") or "", r.get("snippet") or "", " ".join(r.get("ai_tags") or [])]).lower()
        r["section"] = STREAM_LABEL.get(r.get("stream"), "Papers") if r["kind"] == "paper" else SECTION.get(r["kind"], r["kind"])
        drugs = [k for k, v in DRUG.items() if hit(v, t)]
        if len(drugs) > 1 and "Psychedelics (general)" in drugs:
            drugs.remove("Psychedelics (general)")
        r["drug_groups"] = drugs
        r["modality_groups"] = [m for m in TM["modality_order"] if hit(MOD[m], t)]
        r["disorders"] = [k for k, v in DIS.items() if hit(v, t)]
        r["method_groups"] = [k for k, v in MET.items() if hit(v, t)]
        st = ""
        if r["kind"] in ST["study_types"]:
            if hit(PRE, t):
                st = "Pre-clinical"
            elif hit(CLIN, t) or any(m in ST["human_modalities"] for m in r["modality_groups"]):
                st = "Clinical"
        r["stage"] = st
        if r["kind"] == "trial":
            r["pub_status"] = "Registered"
        elif r["kind"] in ("paper", "catchup", "tool_paper"):
            r["pub_status"] = "Preprint" if r.get("is_preprint") else "Peer reviewed"
        else:
            r["pub_status"] = ""
        for pre, ov in TM.get("overrides", {}).items():
            if not pre.startswith("_") and (r.get("title") or "").startswith(pre):
                r.update(ov)

def similar(rows, k=6, thr=0.08):
    """TF-IDF cosine with an inverted index (fast enough for many thousands of items)."""
    docs = []
    for r in rows:
        c = Counter()
        for t in toks(r.get("title")):
            c[t] += 2
        for t in toks((r.get("post") or "") + " " + (r.get("snippet") or "")[:400]):
            c[t] += 1
        for f in ("drug_groups", "modality_groups", "method_groups"):
            for v in r.get(f) or []:
                c["tag:" + v.lower()] += 2
        docs.append(c)
    df = Counter(t for d in docs for t in d)
    N = len(docs)
    vecs = []
    for d in docs:
        v = {t: (1 + math.log(n)) * math.log((1 + N) / (1 + df[t])) for t, n in d.items()}
        nrm = math.sqrt(sum(x * x for x in v.values())) or 1
        vecs.append({t: x / nrm for t, x in v.items()})
    post = defaultdict(list)
    for i, v in enumerate(vecs):
        for t, x in v.items():
            if df[t] < N * 0.2:      # very common terms add little and cost a lot
                post[t].append((i, x))
    for i, v in enumerate(vecs):
        acc = defaultdict(float)
        for t, x in v.items():
            for j, y in post.get(t, ()):
                if j != i:
                    acc[j] += x * y
        top = sorted(((s, j) for j, s in acc.items() if s > thr), reverse=True)[:k]
        rows[i]["similar"] = [rows[j]["id"] for _, j in top]
        rows[i]["similar_w"] = [round(s, 3) for s, _ in top]

E = lambda x: H.escape(str(x or ""), quote=True)
def sections_html(r):
    """Structured abstract summary (Aim / Methods / Results / Conclusion) as a definition list."""
    return "".join(f'<p class="sx"><b>{E(k)}</b> {E(v)}</p>' for k, v in r.get("sections") or [])

PAGE_CSS = (".sx{margin:6px 0}.sx b{display:inline-block;min-width:5.6em;font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut)}"
            ":root{--bg:#f6f5f2;--panel:#fff;--line:#e2dfd8;--tx:#1d2024;--mut:#5c636b;--accent:#6d4bd8}"
            "@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--panel:#171b21;--line:#2a3038;--tx:#e8eaed;--mut:#a2a9b1;--accent:#a78bfa}}"
            "body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.55}"
            "main{max-width:720px;margin:0 auto;padding:24px 16px 60px}a{color:var(--accent)}.m{color:var(--mut);font-size:14px}"
            "h1{font-size:23px;line-height:1.3;margin:10px 0 6px}.au{color:var(--mut);font-size:14px}"
            ".why{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px}"
            ".tags span{display:inline-block;border:1px solid var(--line);border-radius:6px;color:var(--mut);font-size:12px;padding:2px 7px;margin:0 4px 4px 0}"
            ".z{border:1px dashed var(--line);border-radius:12px;padding:8px 12px;font-size:14px;margin:18px 0;color:var(--mut)}")

def item_pages(rows, settings):
    d = os.path.join(SITE, "archive", "item")
    os.makedirs(d, exist_ok=True)
    keep = set()
    for r in rows:
        zot = r["kind"] in ("paper", "catchup", "tool_paper") and bool(r.get("doi") or r.get("url"))
        r["zotero"] = zot
        r["venue_clean"] = re.sub(r"\s*\((preprint|journal|paper|[a-z ]+)\)\s*$", "", r.get("venue") or "", flags=re.I).strip()
        r["detail_url"] = f"item/{r['id']}.html"
        meta = []
        if zot:
            meta = [f'<meta name="citation_title" content="{E(r.get("title"))}">']
            meta += [f'<meta name="citation_author" content="{E(a)}">' for a in r.get("authors") or []]
            if r["venue_clean"]:
                meta.append(f'<meta name="citation_journal_title" content="{E(r["venue_clean"])}">')
            if r.get("date"):
                meta.append(f'<meta name="citation_publication_date" content="{E(r["date"].replace("-", "/"))}">')
            if r.get("doi"):
                meta.append(f'<meta name="citation_doi" content="{E(r["doi"])}">')
            if r.get("url"):
                meta.append(f'<meta name="citation_abstract_html_url" content="{E(r["url"])}">')
        tags = [*r.get("drug_groups", []), *r.get("modality_groups", []), *r.get("disorders", []), *([r["stage"]] if r.get("stage") else []),
                *([r["pub_status"]] if r.get("pub_status") else []), *r.get("method_groups", []), *["#" + t for t in r.get("ai_tags") or []]]
        au = ", ".join(r.get("authors") or [])
        url = r.get("url") if re.match(r"^https?://", r.get("url") or "") else ""
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<title>{E(r.get("title"))} · PsychoPal</title>
{chr(10).join(meta)}
<link rel="icon" href="../../icons/icon.svg" type="image/svg+xml"><style>{PAGE_CSS}</style></head><body><main>
<p class="m"><a href="../">← Archive</a> · {E(r.get("section"))} · {E(r.get("week"))}</p>
<h1>{E(r.get("title"))}</h1>
{f'<div class="au">{E(au)}</div>' if au else ''}<div class="m">{E(r.get("venue"))}{(" · " + E(r.get("date"))) if r.get("date") else ""}{(" · doi:" + E(r.get("doi"))) if r.get("doi") else ""}{(" · deadline " + E(r.get("deadline"))) if r.get("deadline") else ""}</div>
<div class="tags" style="margin:12px 0">{"".join(f"<span>{E(t)}</span>" for t in tags)}</div>
{sections_html(r) or (f'<p>{E(r.get("post"))}</p>' if r.get("post") else '')}{f'<p class="why"><b>Why it matters for you:</b> {E(r.get("why_you"))}</p>' if r.get("why_you") else ''}
{'<div class="z">Zotero: click the Zotero Connector button in your browser toolbar to save this item.</div>' if zot else ''}
<p>{f'<a href="{E(url)}" target="_blank" rel="noopener">Open the {"paper" if zot else "page"} →</a>' if url else ''}</p>
</main></body></html>
"""
        fn = f"{r['id']}.html"
        keep.add(fn)
        p = os.path.join(d, fn)
        old = open(p, encoding="utf-8").read() if os.path.exists(p) else None
        if old != page:
            with open(p, "w", encoding="utf-8") as f:
                f.write(page)
    for fn in os.listdir(d):
        if fn.endswith(".html") and fn not in keep:
            os.remove(os.path.join(d, fn))

def build(settings=None):
    rows, seen = [], set()
    for r in reversed(load_jsonl(os.path.join(STATE, "items.jsonl"))):   # newest first; one row per id
        if r["id"] in seen:
            continue
        seen.add(r["id"]); rows.append(r)
    derive(rows)
    similar(rows)
    item_pages(rows, settings or {})
    keep = ("id", "kind", "stream", "section", "week", "title", "authors", "venue", "venue_clean", "date", "url", "doi", "post", "why_you",
            "ai_tags", "sections", "deadline", "event_dates", "location", "funder", "is_preprint", "is_review", "top", "drug_groups", "modality_groups",
            "disorders", "method_groups", "stage", "pub_status", "similar", "similar_w", "zotero", "detail_url", "citations", "buzz")
    out = [{k: r[k] for k in keep if r.get(k) not in (None, "", [], False)} for r in rows]
    write_json(os.path.join(SITE, "archive", "items.json"), out, indent=None)
    log(f"archive: {len(out)} items -> site/archive/items.json + item pages")
    return len(out)

if __name__ == "__main__":
    build()
