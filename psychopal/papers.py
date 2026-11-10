"""Weekly paper finder: query free scholarly APIs for the window, merge/dedupe, drop anything already shown, score (scoring.py),
then pick ~25 papers: 'Top 5 for you' + a minimum per stream, plus up to N tools/methods papers.
Adapted from the Neurodynamics Digest fetch_candidates.py ("version B" pipeline). No LLM."""
import datetime as dt, json, os, re, time
from concurrent.futures import ThreadPoolExecutor
from . import sources
from .common import OUT, STATE, norm_doi, norm_title, load_jsonl, daterange_ok, item_id, log
from .scoring import load_config, score_record, is_review, is_dropped, Profile, Tfidf, rec_text

ALL_SOURCES = ["pubmed", "europepmc", "openalex", "biorxiv", "medrxiv", "arxiv"]
SOURCE_PREF = {"pubmed": 0, "europepmc": 1, "openalex": 2, "biorxiv": 3, "medrxiv": 3, "europepmc-preprint": 4, "arxiv": 3}
PREPRINT_SOURCES = ("biorxiv", "medrxiv", "arxiv", "europepmc-preprint")

def merge(all_recs):
    by_doi, by_title, merged = {}, {}, []
    for r in all_recs:
        d, t = r.get("doi") or "", norm_title(r.get("title"))
        if not t:
            continue
        hit = by_doi.get(d) if d else None
        if hit is None and len(t) > 25:
            hit = by_title.get(t)
        if hit is None:
            r["sources"] = [r["source"]]; r["groups"] = set(r["groups"])
            merged.append(r)
            if d: by_doi[d] = r
            by_title[t] = r
            continue
        hit["sources"] = sorted(set(hit["sources"]) | {r["source"]})
        hit["groups"] |= set(r["groups"])
        hit["pubtypes"] = sorted(set(hit.get("pubtypes") or []) | set(r.get("pubtypes") or []))
        if len(r.get("abstract") or "") > len(hit.get("abstract") or ""):
            hit["abstract"] = r["abstract"]
        if SOURCE_PREF.get(r["source"], 9) < SOURCE_PREF.get(hit["source"], 9):
            for k in ("title", "authors", "venue", "date", "url", "source"):
                if r.get(k):
                    hit[k] = r[k]
        if d and not hit.get("doi"):
            hit["doi"] = d
        if d:
            by_doi[d] = hit
        if not hit.get("affiliations") and r.get("affiliations"):
            hit["affiliations"] = r["affiliations"]
    return merged

def shown_keys(skip_week=None):
    """DOIs and normalised titles of every card shown in earlier editions (state/items.jsonl). Re-running a week is idempotent."""
    dois, titles = set(), set()
    for r in load_jsonl(os.path.join(STATE, "items.jsonl")):
        if skip_week and r.get("week") == skip_week:
            continue
        if r.get("doi"):
            dois.add(norm_doi(r["doi"]))
        if r.get("title"):
            titles.add(norm_title(r["title"]))
    return dois, titles

def fetch_all(cfg, start, end, want):
    bulk = cfg["bulk_sources"]
    jobs = {
        "pubmed": lambda: sources.pubmed(cfg, start, end, log),
        "europepmc": lambda: sources.europepmc(cfg, start, end, log),
        "openalex": lambda: sources.openalex(cfg, start, end, log),
        "biorxiv": lambda: sources.rxiv("biorxiv", bulk["biorxiv_categories"], start, end, log),
        "medrxiv": lambda: sources.rxiv("medrxiv", bulk["medrxiv_categories"], start, end, log),
        "arxiv": lambda: sources.arxiv(bulk["arxiv_categories"], start, end, log),
    }
    def run(name):
        ts = time.time()
        try:
            return name, jobs[name](), None, round(time.time() - ts, 1)
        except Exception as e:
            return name, None, f"{type(e).__name__}: {e}", round(time.time() - ts, 1)
    stats, recs = {}, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for name, res, err, secs in ex.map(run, [s for s in want if s in jobs]):
            log(f"papers/{name}: {'FAILED ' + err if err else 'ok'} ({secs}s)")
            if err:
                stats[name] = {"hits": 0, "seconds": secs, "error": err}
            else:
                rr, info = res
                stats[name] = {"hits": len(rr), "seconds": secs, **{k: v for k, v in info.items() if k != "records"}}
                recs += rr
    return recs, stats

SIDE_STREAMS = ("tools", "wider")    # picked separately (Tools & methods cards; low-priority Wider science)

def similarity(a, b):
    """Topic overlap of two scored papers: the larger of title and abstract TF-IDF cosine (vectors precomputed in run())."""
    from .scoring import Tfidf
    return max(Tfidf.cos(a.get("_tv") or {}, b.get("_tv") or {}), Tfidf.cos(a.get("_av") or {}, b.get("_av") or {}))

def too_similar(x, chosen, thr):
    return any(similarity(x, c) >= thr for c in chosen)

def quotas(cfg, settings):
    """{stream: (min, max)} in order of importance: settings.yaml stream_quota first, then any other paper stream with the
    generic min_per_stream / max_per_stream."""
    q = {}
    for s, v in (settings.get("stream_quota") or {}).items():
        if s in cfg["streams"] and s not in SIDE_STREAMS:
            lo, hi = (v, v) if isinstance(v, int) else (v[0], v[1])
            q[s] = (int(lo), int(hi))
    for s in cfg["streams"]:
        if s not in SIDE_STREAMS and s not in q:
            q[s] = (settings.get("min_per_stream", 2), settings.get("max_per_stream", 8))
    return q

def select(cfg, scored, settings):
    """Top N for you, then each stream's minimum (stream_quota in settings.yaml, in order of importance), then fill to papers_total
    by score without exceeding any stream's maximum.
    Variety rule: after the Top 5, a paper that is very similar to one already picked is skipped (the next one in line gets the slot)."""
    total, top_n = settings.get("papers_total", 25), settings.get("top_for_you", 5)
    quota = quotas(cfg, settings)
    min_score = settings.get("min_paper_score", 4.0)
    thr = settings.get("max_similarity", 0.22)
    papers = [x for x in scored if x["stream"] and x["stream"] not in SIDE_STREAMS and x["score"] >= min_score]
    papers.sort(key=lambda x: -x["score"])
    chosen, ids, per, skipped = [], set(), {}, set()
    def take(x, top=False):
        x["top"] = top
        chosen.append(x); ids.add(x["id"]); per[x["stream"]] = per.get(x["stream"], 0) + 1
    def ok(x):
        if x["id"] in ids or x["id"] in skipped:
            return False
        if too_similar(x, chosen, thr):
            skipped.add(x["id"]); return False
        return True
    for x in papers[:top_n]:
        take(x, True)
    # each topic's minimum: first the best paper of each sub-area, taking the topic's groups in config order (= priority, e.g.
    # ketamine, stimulants, cannabis), if it scores at least variety_min_fraction of the topic's best; then the best remaining papers
    generic = set(cfg["scoring"].get("generic_groups", []))
    frac = cfg["scoring"].get("variety_min_fraction", 0.6)
    def sub(x):
        own = [g for g in x.get("matched") or [] if g not in generic and cfg["_byname"].get(g, {}).get("stream") == x["stream"]]
        return own[0] if own else ((x.get("matched") or [""])[0])
    for s, (lo, hi) in quota.items():
        cands = [x for x in papers if x["stream"] == s]
        if not cands:
            continue
        floor, used = frac * cands[0]["score"], {sub(x) for x in chosen if x["stream"] == s}
        for g in (g["name"] for g in cfg["groups"] if g["stream"] == s and g["name"] not in generic):
            if per.get(s, 0) >= lo:
                break
            if g in used:
                continue
            for x in cands:
                if x["score"] < floor:
                    break
                if sub(x) == g and ok(x):
                    take(x); used.add(g)
                    break
        for x in cands:
            if per.get(s, 0) >= lo:
                break
            if ok(x):
                take(x)
    for x in papers:
        if len(chosen) >= total:
            break
        if per.get(x["stream"], 0) < quota.get(x["stream"], (0, 0))[1] and ok(x):
            take(x)
    # Tools & methods: papers ABOUT a tool or method (scoring.is_tool_paper), from the tools stream or from any topic when that
    # paper was not picked as a normal paper (e.g. a TMS-EEG toolbox that missed the neuroimaging quota). Reviews, editorials,
    # comments and letters are left out.
    tools = []
    def tool_ok(x):
        return (x.get("tool") and not x.get("is_review") and not (x.get("score_parts") or {}).get("low_type")
                and not re.match(r"(editorial|comment|commentary|letter)\b", x["title"], re.I))
    pool = (x for x in scored if tool_ok(x) and x["score"] >= min_score and x["id"] not in ids)
    for x in sorted(pool, key=lambda x: -x["score"]):
        if len(tools) < settings.get("tool_papers_max", 4) and not too_similar(x, tools, thr):
            tools.append(x)
    wider = [x for x in scored if x["stream"] == "wider" and x["id"] not in ids]
    return chosen, tools, wider, len(skipped)

GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)")

def why_picked(cfg, x, rec, prof):
    """One scripted line explaining the pick, e.g. 'psilocybin · SV2A · PET · close to your paper “Synaptic density…”'."""
    from .scoring import matched_terms, rec_text
    bits = matched_terms(cfg, rec, [g for g in x["matched"] if cfg["_byname"][g].get("keywords")])
    if x.get("modality") and x["modality"].lower() not in {b.lower() for b in bits}:
        bits.append(x["modality"])
    if x.get("named"):
        bits.append("by " + ", ".join(x["named"][:2]))
    sim, title = prof.nearest(rec_text(rec))
    if sim >= cfg["scoring"].get("why_near_min_sim", 0.12) and title:
        t = title if len(title) <= 60 else title[:57].rsplit(" ", 1)[0] + "…"
        bits.append(f"close to your paper “{t}”")
    elif x.get("liked_sim", 0) >= 0.2:
        bits.append("like papers you 👍")
    return " · ".join(bits)

def run(start, end, settings, user_state=None, want=None, outdir=None, reuse_raw=False):
    """Returns dict(papers=[...], tool_papers=[...], stats={...}). Each paper is a card-ready dict (kind='paper')."""
    cfg = load_config()
    t0 = time.time()
    cache = os.path.join(outdir, "raw.json") if outdir else None
    if cache and reuse_raw and os.path.exists(cache):   # tuning: re-score last fetch without hitting the APIs again
        with open(cache, encoding="utf-8") as f:
            c = json.load(f)
        raw, src_stats = c["raw"], c["stats"]
        for r in raw:
            r["groups"] = set(r["groups"])
        log(f"papers: reusing {len(raw)} cached raw records")
    else:
        raw, src_stats = fetch_all(cfg, start, end, want or ALL_SOURCES)
        if cache:
            os.makedirs(outdir, exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"raw": [{**r, "groups": sorted(r["groups"])} for r in raw], "stats": src_stats}, f, ensure_ascii=False)
    merged = merge(raw)
    ex_dois, ex_titles = shown_keys(settings.get("_week"))
    us = user_state or {}
    kept, drops = [], {"already_shown": 0, "errata": 0, "outside_window": 0, "bulk_below_threshold": 0}
    for r in merged:
        if (r.get("doi") and norm_doi(r["doi"]) in ex_dois) or norm_title(r["title"]) in ex_titles:
            drops["already_shown"] += 1; continue
        if is_dropped(r):
            drops["errata"] += 1; continue
        if r["source"] in ("biorxiv", "medrxiv", "arxiv") and not daterange_ok(r.get("date", ""), start, end):
            drops["outside_window"] += 1; continue
        kept.append(r)
    prof = Profile([rec_text(r) for r in kept], us.get("liked_texts", []), us.get("disliked_texts", []))
    ttf = Tfidf([r["title"] for r in kept])      # title-only vectors for the variety rule
    bulk_min = cfg["bulk_sources"]["min_keyword_score"]
    scored, recs = [], {}
    for r in kept:
        s = score_record(cfg, r, prof, start)
        if r["source"] in ("biorxiv", "medrxiv", "arxiv") and not set(r["sources"]) & {"pubmed", "europepmc", "openalex", "europepmc-preprint"}:
            if s["parts"]["keywords"] + s["parts"]["modality"] < bulk_min or not s["stream"]:
                drops["bulk_below_threshold"] += 1; continue
        doi = norm_doi(r.get("doi"))
        pre = r["source"] in PREPRINT_SOURCES or "preprint" in (r.get("pubtypes") or [])
        scored.append({"id": item_id("paper", doi or norm_title(r["title"])), "kind": "paper", "stream": s["stream"],
                       "title": r["title"], "authors": r.get("authors") or [], "venue": r.get("venue") or "", "date": r.get("date") or "",
                       "doi": doi, "url": r.get("url"), "abstract": r.get("abstract") or "", "affiliations": r.get("affiliations", ""),
                       "is_review": is_review(r), "is_preprint": pre, "modality": s["modality"], "named": s["named"],
                       "score": s["score"], "score_parts": s["parts"], "matched": s["groups"], "profile_sim": s["profile_sim"],
                       "liked_sim": s["liked_sim"], "tool": s["tool"], "sources": r["sources"], "pubtypes": r.get("pubtypes") or [],
                       "_tv": ttf.vec(r["title"]), "_av": prof.tf.vec(rec_text(r))})
        recs[scored[-1]["id"]] = r
    papers, tool_papers, wider, n_similar = select(cfg, scored, settings)
    for x in papers + tool_papers:
        x["why_picked"] = why_picked(cfg, x, recs[x["id"]], prof)
    for x in tool_papers:
        m = GITHUB_RE.search(recs[x["id"]].get("abstract") or "")
        if m:
            x["repo"] = m.group(1).rstrip(".")
        x["reason"] = "SOFTWARE" if "tools_papers" in x["matched"] else "NEW METHOD"
    for x in scored:
        x.pop("_tv", None); x.pop("_av", None)
    stats = {"window": [str(start), str(end)], "sources": src_stats, "raw_hits": len(raw), "after_merge": len(merged),
             "dropped": drops, "scored_pool": len(scored), "selected": len(papers), "tool_papers": len(tool_papers),
             "skipped_too_similar": n_similar, "wider_candidates": len(wider),
             "per_stream": {s: sum(1 for p in papers if p["stream"] == s) for s in cfg["streams"]},
             "seconds": round(time.time() - t0, 1)}
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "pool.jsonl"), "w", encoding="utf-8") as f:   # every scored item, for auditing/tuning
            for x in sorted(scored, key=lambda x: -x["score"]):
                f.write(json.dumps({k: x[k] for k in ("score", "stream", "tool", "title", "venue", "date", "doi", "matched", "score_parts", "profile_sim")},
                                   ensure_ascii=False) + "\n")
    log(f"papers: {len(raw)} raw -> {len(merged)} merged -> {len(scored)} scored -> {len(papers)} selected + {len(tool_papers)} tool papers "
        f"({n_similar} skipped as too similar; {len(wider)} wider-science candidates)")
    return {"papers": papers, "tool_papers": tool_papers, "wider": wider, "stats": stats, "cfg_streams": cfg["streams"]}

if __name__ == "__main__":
    import argparse
    from .common import last_full_week, write_json
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--sources"); ap.add_argument("--reuse", action="store_true")
    a = ap.parse_args()
    s, e = last_full_week()
    start = dt.date.fromisoformat(a.start) if a.start else s
    end = dt.date.fromisoformat(a.end) if a.end else e
    od = os.path.join(OUT, str(end))
    res = run(start, end, {"papers_total": 25}, want=a.sources.split(",") if a.sources else None, outdir=od, reuse_raw=a.reuse)
    write_json(os.path.join(od, "papers.json"), res)
    for p in res["papers"]:
        print(f"{'★' if p['top'] else ' '} [{p['score']:5.1f}] {p['stream']:16} {p['title'][:90]} | {p['venue'][:30]}")
    print("tools:")
    for p in res["tool_papers"]:
        print(f"  [{p['score']:5.1f}] {p['title'][:90]} | {p['venue'][:30]}")
    print(json.dumps(res["stats"], indent=1)[:2500])
