"""Extras (no LLM): newly registered psychedelic trials, Catch-up classics, and Bluesky/Hacker News buzz."""
import datetime as dt, math, os, re
from . import sources, chatter
from .common import STATE, get, load_jsonl, norm_doi, norm_title, item_id, clean, MAILTO, log
from .scoring import load_config, kw_hit, score_record, Profile, rec_text

def _shown(skip_week=None):
    ids, dois, titles = set(), set(), set()
    for r in load_jsonl(os.path.join(STATE, "items.jsonl")):
        if skip_week and r.get("week") == skip_week:
            continue
        ids.add(r["id"])
        if r.get("doi"):
            dois.add(norm_doi(r["doi"]))
        if r.get("title"):
            titles.add(norm_title(r["title"]))
    return ids, dois, titles

# ---------------- trials ----------------
def trials(start, end, settings, week):
    cfg = load_config()
    rows, info = sources.clinicaltrials(cfg, start, end, log)
    psy = cfg["_byname"]["psychedelic_any"]
    shown, _, _ = _shown(week)
    cards = []
    for t in rows:
        text = " ".join([t.get("title") or "", t.get("official_title") or "", " ".join(t.get("interventions") or [])])
        if not kw_hit(cfg, psy, text, t.get("summary") or ""):
            continue          # CT.gov term search is fuzzy: keep only trials that actually name a psychedelic
        cid = item_id("trial", t["nct"])
        if cid in shown:
            continue
        phase = "/".join(p.replace("PHASE", "Phase ").replace("EARLY_", "Early ") for p in (t.get("phase") or [])) or (t.get("study_type") or "").title()
        desc = (f"{t.get('summary') or ''} Conditions: {', '.join(t.get('conditions') or [])}. Interventions: {', '.join(t.get('interventions') or [])}. "
                f"Sponsor: {t.get('sponsor')}. Planned enrolment: {t.get('enrollment')}. Countries: {', '.join(t.get('countries') or [])}. "
                f"Primary outcomes: {'; '.join(t.get('primary_outcomes') or [])}.")
        cards.append({"id": cid, "kind": "trial", "stream": "psychedelics", "title": t.get("title") or t["nct"], "url": t["url"],
                      "venue": f"ClinicalTrials.gov {t['nct']}", "date": t.get("first_posted") or "", "phase": phase,
                      "funder": t.get("sponsor") or "", "description": desc, "status": t.get("status") or ""})
    cards.sort(key=lambda c: (c["phase"] or "") + c["date"], reverse=True)
    log(f"trials: {len(rows)} registered in window, {len(cards)} psychedelic")
    return cards[: settings.get("trials_max", 5)], {"registered": len(rows), "psychedelic": len(cards)}

# ---------------- catch-up classics ----------------
DEFAULT_CATCHUP = ["psilocybin depression", "psychedelic neuroplasticity", "serotonin release PET", "5-HT2A receptor psychedelic",
                   "ketamine antidepressant mechanism", "EEG complexity consciousness", "heartbeat evoked potential", "whole-brain model serotonin",
                   "entropic brain psychedelics", "DMT EEG", "LSD fMRI connectivity", "TMS-EEG cortical excitability", "PET kinetic modelling",
                   "interoception brain", "predictive processing psychedelics", "integrated information decomposition brain",
                   "aperiodic EEG excitation inhibition", "cortical travelling waves", "synaptic density SV2A PET", "MDMA-assisted therapy PTSD",
                   "dynamic causal modelling EEG", "brain criticality", "ayahuasca brain", "psychedelic mystical experience"]

def catchup(settings, week, prof_texts=None):
    cfg = load_config()
    topics = cfg.get("catchup_topics") or DEFAULT_CATCHUP
    wk = int(week.split("W")[1]) + 53 * int(week[:4])
    picks = [topics[(wk * 3 + i) % len(topics)] for i in range(3)]     # rotate 3 topics per week
    until = dt.date.today() - dt.timedelta(days=730)
    shown, sdois, stitles = _shown(week)
    pool = []
    for q in picks:
        params = {"filter": f"title_and_abstract.search:{q},from_publication_date:2008-01-01,to_publication_date:{until},cited_by_count:>80,type:article|review",
                  "sort": "cited_by_count:desc", "per-page": 25, "mailto": MAILTO,
                  "select": "id,doi,display_name,publication_date,primary_location,authorships,abstract_inverted_index,cited_by_count"}
        if os.environ.get("OPENALEX_API_KEY"):
            params["api_key"] = os.environ["OPENALEX_API_KEY"]
        try:
            r = get("https://api.openalex.org/works", params=params, timeout=40)
        except Exception as e:
            log(f"catchup: {q!r} failed: {e}"); continue
        for w in r.get("results", []):
            doi = norm_doi(w.get("doi"))
            t = clean(w.get("display_name"))
            if not t or doi in sdois or norm_title(t) in stitles:
                continue
            loc = (w.get("primary_location") or {}).get("source") or {}
            pool.append({"title": t, "doi": doi, "abstract": sources._inv(w.get("abstract_inverted_index")),
                         "authors": [a["author"]["display_name"] for a in w.get("authorships", [])], "venue": loc.get("display_name") or "",
                         "date": w.get("publication_date") or "", "citations": w.get("cited_by_count") or 0, "topic": q,
                         "url": f"https://doi.org/{doi}" if doi else w["id"], "source": "openalex", "groups": set(), "pubtypes": []})
    if not pool:
        return [], {"topics": picks, "pool": 0}
    prof = Profile([rec_text(r) for r in pool], prof_texts or [])
    for r in pool:
        s = score_record(cfg, r, prof)
        r["stream"] = s["stream"] or "psychedelics"
        r["rank"] = s["parts"]["profile_sim"] + s["parts"]["keywords"] * 0.3 + math.log10(max(10, r["citations"]))
    pool.sort(key=lambda r: -r["rank"])
    out, seen = [], set()
    for r in pool:
        if len(out) >= settings.get("catchup_per_week", 2):
            break
        key = r["doi"] or norm_title(r["title"])
        if key in seen or len(r["abstract"]) < 200:
            continue
        seen.add(key)
        out.append({"id": item_id("paper", key), "kind": "catchup", "stream": r["stream"], "title": r["title"], "url": r["url"], "doi": r["doi"],
                    "venue": r["venue"], "date": r["date"], "authors": r["authors"], "abstract": r["abstract"], "citations": r["citations"],
                    "reason": "CATCH-UP"})
    log(f"catchup: topics {picks} -> {len(pool)} candidates -> {len(out)} picked")
    return out, {"topics": picks, "pool": len(pool)}

# ---------------- buzz ----------------
def buzz(papers, start, settings):
    """Annotate the week's papers with Bluesky/HN chatter (in place). Returns stats."""
    errors, checked, hits = [], 0, 0
    import time
    chatter.DEADLINE = time.time() + settings.get("buzz_max_seconds", 240)
    for c in papers[: settings.get("buzz_top", 30)]:
        if time.time() > chatter.DEADLINE:
            break
        try:
            r = chatter.check(c, str(start), errors)
        except Exception as e:
            errors.append(str(e)); continue
        checked += 1
        if r["bsky_human_posters"] or r["hn"]:
            hits += 1
            top = next((p for p in r["bsky_posts"] if not p["bot"]), None)
            c["buzz"] = {"score": r["buzz"], "posts": r["bsky_human_posters"], "hn": sum(h["points"] for h in r["hn"]),
                         "note": f"{r['bsky_human_posters']} people posted on Bluesky" + (f"; top post by @{top['handle']}" if top else "")}
    log(f"buzz: {hits}/{checked} papers have chatter ({len(errors)} errors)")
    return {"checked": checked, "with_chatter": hits, "errors": len(errors)}

DOI_IN_URL = re.compile(r"(10\.\d{4,9}/[^\s?#]+)")
def buzz_discover(start, settings, week, chosen_dois):
    """Papers being discussed on Bluesky that the database search did not pick, kept only if they match the profile."""
    n = settings.get("buzz_discover", 3)
    if not n:
        return [], {}
    errors = []
    import time
    chatter.DEADLINE = time.time() + settings.get("buzz_discover_max_seconds", 120)
    rows, calls = chatter.discover(str(start), errors)
    cfg = load_config()
    shown, sdois, _ = _shown(week)
    cands = []
    for row in [r for r in rows if r["neuro_text"] and r["posters"] >= 3][:25]:
        m = DOI_IN_URL.search(row["link"])
        if not m:
            continue
        doi = norm_doi(re.sub(r"(v\d+)$", "", m.group(1)))
        if doi in sdois or doi in chosen_dois:
            continue
        try:
            w = get(f"https://api.openalex.org/works/doi:{doi}", params={"mailto": MAILTO}, timeout=25, retries=1)
        except Exception:
            continue
        loc = (w.get("primary_location") or {}).get("source") or {}
        cands.append({"title": clean(w.get("display_name")), "doi": doi, "abstract": sources._inv(w.get("abstract_inverted_index")),
                      "authors": [a["author"]["display_name"] for a in w.get("authorships", [])], "venue": loc.get("display_name") or "",
                      "date": w.get("publication_date") or "", "url": f"https://doi.org/{doi}", "source": "openalex", "groups": set(),
                      "pubtypes": [], "_row": row})
    if not cands:
        return [], {"calls": calls, "candidates": 0}
    prof = Profile([rec_text(r) for r in cands])
    out = []
    for r in cands:
        s = score_record(cfg, r, prof)
        if s["stream"] and s["stream"] != "tools" and s["score"] >= settings.get("min_paper_score", 4.0):
            row = r["_row"]
            out.append({"id": item_id("paper", r["doi"]), "kind": "paper", "stream": s["stream"], "title": r["title"], "url": r["url"],
                        "doi": r["doi"], "venue": r["venue"], "date": r["date"], "authors": r["authors"], "abstract": r["abstract"],
                        "score": s["score"], "reason": "BEING DISCUSSED",
                        "buzz": {"score": row["buzz"], "posts": row["posters"], "note": f"{row['posters']} people posted on Bluesky"}})
    out.sort(key=lambda x: -x["score"])
    log(f"buzz-discover: {len(rows)} links, {len(cands)} resolved, {len(out)} on-profile")
    return out[:n], {"calls": calls, "candidates": len(cands), "kept": len(out[:n])}
