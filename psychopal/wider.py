"""Wider science (no LLM): a few low-priority cards from beyond Claudio's own fields, so he keeps an eye on big results in neuroscience
and biology generally.

Candidates
  * every research paper of the week in broad journals (the 'wider_journals' PubMed group in queries.json; papers that also match one
    of his topics are NOT here, they compete as normal papers);
  * papers and preprints from any field that many people are posting about on Bluesky (links to nature.com, science.org, biorxiv.org…).
Ranking = journal tier + Bluesky attention + a small bonus for brain/mind topics. Deliberately NOT similarity to his own work,
otherwise this section would just repeat his streams. Never part of 'Top 5 for you'.
Settings: settings.yaml > wider (cards, max_seconds); queries.json > wider (tiers, keywords, Bluesky queries/domains)."""
import datetime as dt, math, re, time
from . import chatter, sources
from .common import get, norm_doi, norm_title, item_id, clean, MAILTO, log
from .scoring import load_config, clean_venue, is_review, Tfidf, rec_text
from .extras import _shown

PREPRINT_PREFIXES = ("10.1101/", "10.64898/", "10.48550/")

def attention(posters):
    return min(4.0, 1.5 * math.log2(1 + posters)) if posters else 0.0

def _openalex(doi):
    w = get(f"https://api.openalex.org/works/doi:{doi}", params={"mailto": MAILTO}, timeout=25, retries=1)
    loc = (w.get("primary_location") or {}).get("source") or {}
    return {"title": clean(w.get("display_name")), "doi": norm_doi(doi), "abstract": sources._inv(w.get("abstract_inverted_index")),
            "authors": [a["author"]["display_name"] for a in w.get("authorships", [])], "venue": loc.get("display_name") or "",
            "date": w.get("publication_date") or "", "url": f"https://doi.org/{doi}", "type": w.get("type") or "",
            "pubtypes": [], "is_preprint": doi.startswith(PREPRINT_PREFIXES) or w.get("type") == "preprint"}

def pick(cands, chosen, settings, start, week):
    """cands: scored records whose only match was the broad-journal group (papers.run()['wider']); chosen: this week's other cards."""
    ws = settings.get("wider") or {}
    n = ws.get("cards", 4)
    if not n:
        return [], {"off": True}
    cfg = load_config()
    w = cfg.get("wider") or {}
    tiers = {name: float(t) for t, names in (w.get("tiers") or {}).items() for name in names}
    brain = re.compile(w.get("brain_re") or r"\bbrain|\bneuro", re.I)
    life = re.compile(w["life_re"], re.I) if w.get("life_re") else None    # life sciences only: no geology, astronomy, materials…
    _, sdois, stitles = _shown(week)
    taken = {c.get("doi") for c in chosen if c.get("doi")} | {norm_title(c.get("title")) for c in chosen}
    stats = {"journal_candidates": 0, "discussed_links": 0, "resolved": 0}

    # 1. attention: who is posting about which paper this week
    errors = []
    chatter.DEADLINE = time.time() + ws.get("max_seconds", 150)
    rows, calls = chatter.discover(str(start), errors, queries=w.get("bsky_queries") or ("new", "study"), domains=w.get("bsky_domains"))
    att = {}
    for r in rows:
        if r.get("doi") and r["posters"] > att.get(r["doi"], {}).get("posters", 0):
            att[r["doi"]] = r
    stats.update(bsky_calls=calls, bsky_errors=len(errors), discussed_links=len(att))

    # 2. pool = broad-journal papers of the week + much-discussed papers we did not have
    pool = {}
    for c in cands:
        if len(c.get("abstract") or "") < 300 or c.get("is_review"):
            continue
        t = tiers.get(clean_venue(c.get("venue")))
        if t:
            pool[c.get("doi") or c["id"]] = {**c, "_tier": t}
    stats["journal_candidates"] = len(pool)
    min_date = str(start - dt.timedelta(days=45))
    for doi, r in sorted(att.items(), key=lambda kv: -kv[1]["posters"]):
        if stats["resolved"] >= ws.get("max_resolve", 12) or time.time() > chatter.DEADLINE + 60:
            break
        pre = doi.startswith(PREPRINT_PREFIXES)
        if doi in pool or doi in sdois or doi in taken or r["posters"] < (w.get("min_posters_preprint", 4) if pre else 3):
            continue
        try:
            rec = _openalex(doi)
        except Exception:
            continue
        stats["resolved"] += 1
        if not rec["title"] or len(rec["abstract"]) < 300 or (rec["date"] or "") < min_date or rec["type"] in ("editorial", "letter", "erratum", "paratext"):
            continue
        if is_review(rec) or norm_title(rec["title"]) in stitles or norm_title(rec["title"]) in taken:
            continue
        t = w.get("preprint_tier", 1.0) if rec["is_preprint"] else tiers.get(clean_venue(rec["venue"]), 0.5)
        pool[doi] = {**rec, "id": item_id("paper", doi), "kind": "paper", "stream": "wider", "_tier": t}

    # 3. rank and pick with variety (no two near-identical topics, at most 2 per journal)
    items = list(pool.values())
    if life:
        items = [x for x in items if life.search((x.get("title") or "") + " " + (x.get("abstract") or "")[:1500])]
    stats["life_science_candidates"] = len(items)
    if not items:
        return [], stats
    tf = Tfidf([rec_text(x) for x in items] + [rec_text(c) for c in chosen if c.get("title")])
    vec = lambda x: tf.vec(rec_text(x))
    for x in items:
        posters = att.get(x.get("doi") or "", {}).get("posters", 0)
        x["_posters"] = posters
        x["_brain"] = bool(brain.search((x.get("title") or "") + " " + (x.get("abstract") or "")[:1500]))
        x["_wscore"] = x["_tier"] + (w.get("brain_bonus", 1.5) if x["_brain"] else 0) + attention(posters)
    items.sort(key=lambda x: (-x["_wscore"], x.get("title") or ""))
    out, per_venue, chosen_vecs = [], {}, [vec(c) for c in chosen if c.get("title")]
    thr = settings.get("max_similarity", 0.22)
    for x in items:
        if len(out) >= n:
            break
        v = vec(x)
        if any(Tfidf.cos(v, u) >= thr for u in chosen_vecs):
            continue
        venue = clean_venue(x.get("venue")) if not x.get("is_preprint") else "preprint"
        if per_venue.get(venue, 0) >= 2:
            continue
        per_venue[venue] = per_venue.get(venue, 0) + 1
        chosen_vecs.append(v)
        why = [("preprint" if x.get("is_preprint") else x.get("venue") or "")]
        if x["_posters"] >= 3:
            why.append(f"{x['_posters']} people posted about it on Bluesky")
        if x["_brain"]:
            why.append("brain, mind or behaviour")
        card = {k: x.get(k) for k in ("id", "title", "authors", "venue", "date", "doi", "url", "abstract", "is_preprint")}
        card.update({"kind": "paper", "stream": "wider", "why_picked": " · ".join(b for b in why if b), "score": round(x["_wscore"], 2)})
        if x["_posters"] >= 3:
            card["buzz"] = {"score": x["_posters"], "posts": x["_posters"], "note": f"{x['_posters']} people posted on Bluesky"}
        out.append(card)
    stats["picked"] = len(out)
    log(f"wider: {stats['journal_candidates']} journal papers + {stats['resolved']} discussed papers resolved -> {len(out)} picked")
    return out, stats
