#!/usr/bin/env python3
"""Buzz: how much online chatter each paper is getting, from free no-key sources (ported from the Neurodynamics Digest chatter.py).
  * Bluesky public AppView (api.bsky.app app.bsky.feed.searchPosts, no auth): posts linking the paper's DOI / publisher URL,
    plus posts quoting the title. Bot accounts (preprint feeds) are excluded.
  * Hacker News (Algolia API): stories linking the DOI/URL or quoting the title.
  * --discover: Bluesky posts linking biorxiv/medrxiv/nature/etc. since the window start (sort=top), aggregated by link,
    to surface papers being talked about that the database search did not shortlist.
Not used (see README): Altmetric (key required since Nov 2025), Crossref Event Data (API sunset Apr 2026),
Reddit (blocks the box; OAuth needed), X (use the agent's own X tool instead).

Used by extras.py: check(card, since, errors) per paper, discover(since, errors) for papers being discussed that we did not pick."""
import re, time
from collections import defaultdict
from .common import get

BSKY = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
HN = "https://hn.algolia.com/api/v1/search"
BOT_RE = re.compile(r"bot\b|bot\.|biorxiv|medrxiv|arxiv|preprint|rridrobot|papers?feed|journal-?feed|newpapers|scholar-?alert|\.feed\b|pubmed", re.I)
PAUSE = 0.25

DEADLINE = None   # set by extras.py: stop calling out once the time budget is spent (keeps a slow API from stalling the run)

def bsky(params, errors):
    if DEADLINE and time.time() > DEADLINE:
        return []
    time.sleep(PAUSE)
    try:
        return get(BSKY, params={**params, "limit": 100}, timeout=15, retries=1).get("posts", [])
    except Exception as e:
        errors.append(f"bsky {params.get('q')!r} {params.get('url', '')}: {e}")
        return []

def url_variants(c):
    doi, url = (c.get("doi") or "").lower(), c.get("url") or ""
    v = []
    if doi:
        v += [f"https://doi.org/{doi}"]
        if doi.startswith(("10.1101/", "10.64898/")):
            server = "medrxiv" if "medrxiv" in (c.get("venue") or "").lower() or "medrxiv" in c.get("source", "") else "biorxiv"
            base = f"https://www.{server}.org/content/{doi}"
            v += [base + "v1", base + "v1.full", base + "v2", base]
        m = re.match(r"10\.1038/(.+)", doi)
        if m:
            v.append(f"https://www.nature.com/articles/{m.group(1)}")
        m = re.match(r"10\.1126/(.+)", doi)
        if m:
            v.append(f"https://www.science.org/doi/{doi}")
        m = re.match(r"10\.1016/(.+)", doi)
        if m and ("cell" in (c.get("venue") or "").lower() or "neuron" in (c.get("venue") or "").lower()):
            v.append(f"https://www.cell.com/action/showPdf?pii=")  # placeholder never matches; Cell URLs use PII, skipped
            v.pop()
        m = re.match(r"10\.48550/arxiv\.(.+)", doi)
        if m:
            v += [f"https://arxiv.org/abs/{m.group(1)}", f"https://arxiv.org/pdf/{m.group(1)}"]
    if url and url not in v and "europepmc" not in url:
        v.append(url)
    return v

def title_query(t):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", t or "")
    if len(words) < 5:
        return None
    return '"' + " ".join(words[:9]) + '"'

def post_rec(p):
    a = p.get("author", {})
    ext = ((p.get("embed") or {}).get("external") or {}).get("uri") or (((p.get("embed") or {}).get("media") or {}).get("external") or {}).get("uri")
    rk = p.get("uri", "").split("/")[-1]
    return {"uri": p.get("uri"), "url": f"https://bsky.app/profile/{a.get('handle')}/post/{rk}", "handle": a.get("handle"),
            "name": a.get("displayName"), "text": (p.get("record") or {}).get("text", "")[:300], "created": (p.get("record") or {}).get("createdAt"),
            "likes": p.get("likeCount", 0), "reposts": p.get("repostCount", 0), "quotes": p.get("quoteCount", 0), "replies": p.get("replyCount", 0),
            "link": ext}

def is_bot(p):
    return bool(BOT_RE.search(p.get("handle") or "") or BOT_RE.search(p.get("name") or ""))

def hn(c, errors):
    out = {}
    if DEADLINE and time.time() > DEADLINE:
        return []
    qs = [c.get("doi")] if c.get("doi") else []
    tq = title_query(c.get("title"))
    if tq:
        qs.append(tq.strip('"'))
    for q in qs:
        time.sleep(PAUSE)
        try:
            r = get(HN, params={"query": q, "tags": "story", "restrictSearchableAttributes": "url,title" if q == c.get("doi") else "title"}, timeout=15, retries=1)
        except Exception as e:
            errors.append(f"hn {q!r}: {e}"); continue
        for h in r.get("hits", []):
            out[h["objectID"]] = {"url": f"https://news.ycombinator.com/item?id={h['objectID']}", "title": h.get("title"),
                                  "points": h.get("points") or 0, "comments": h.get("num_comments") or 0, "created": h.get("created_at")}
    return list(out.values())

def buzz_score(posts, hn_hits):
    human = [p for p in posts if not p["bot"]]
    eng = sum(p["likes"] + 2 * p["reposts"] + 2 * p["quotes"] + p["replies"] for p in human)
    authors = len({p["handle"] for p in human})
    return round(3 * authors + eng ** 0.5 * 2 + sum(h["points"] ** 0.5 + h["comments"] ** 0.5 for h in hn_hits), 2), authors, eng

def check(c, since, errors):
    posts = {}
    for u in url_variants(c):
        for p in bsky({"q": "*", "url": u, "sort": "top"}, errors):
            posts[p["uri"]] = post_rec(p)
    tq = title_query(c.get("title"))
    if tq:
        for p in bsky({"q": tq, "sort": "top"}, errors):
            posts.setdefault(p["uri"], post_rec(p))
    for p in posts.values():
        p["bot"] = is_bot(p)
    hits = hn(c, errors)
    score, authors, eng = buzz_score(list(posts.values()), hits)
    plist = sorted(posts.values(), key=lambda p: -(p["likes"] + 2 * p["reposts"]))
    recent = [p for p in posts.values() if (p.get("created") or "")[:10] >= since]
    w_score, w_authors, _ = buzz_score(recent, [])
    return {"title": c["title"], "doi": c.get("doi"), "url": c.get("url"), "venue": c.get("venue"), "candidate_score": c.get("score"),
            "kind": c.get("_kind"), "buzz": score, "bsky_human_posters": authors, "bsky_engagement": eng,
            "buzz_in_window": w_score, "bsky_human_posters_in_window": w_authors, "bsky_posts_total": len(plist), "bsky_bot_posts": sum(p["bot"] for p in plist), "bsky_posts": plist[:8], "hn": hits}

DISCOVER_DOMAINS = ["biorxiv.org", "medrxiv.org", "nature.com", "science.org", "cell.com", "pnas.org", "elifesciences.org", "arxiv.org"]
NEURO_RE = re.compile(r"neuro|brain|cortex|cortical|eeg|fmri|psychedel|psilocyb|neuron|cogniti|conscious|addict|synap|hippocamp|mental|psychiat", re.I)

DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s?#&]+)")
def link_doi(link):
    """DOI behind a publisher / preprint link, or None (news pieces, editorials and unknown sites).
    nature.com/articles/s41586-026-01234-5 -> 10.1038/s41586-026-01234-5; elifesciences.org/articles/101234 -> 10.7554/eLife.101234;
    arxiv.org/abs/2611.01234 -> 10.48550/arXiv.2611.01234; anything containing a DOI (bioRxiv, science.org, pnas.org, plos.org...) -> that DOI."""
    link = (link or "").strip()
    m = re.search(r"nature\.com/articles/(s\d{5}-\d{3}-\d{4,6}-\w)", link, re.I)
    if m:
        return "10.1038/" + m.group(1).lower()
    if re.search(r"nature\.com/articles/d\d{5}", link, re.I):
        return None                              # Nature news / comment, not a research paper
    m = re.search(r"elifesciences\.org/(?:reviewed-preprints|articles)/(\d{5,7})", link, re.I)
    if m:
        return f"10.7554/elife.{m.group(1)}"
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", link, re.I)
    if m:
        return f"10.48550/arxiv.{m.group(1)}"
    m = DOI_RE.search(link)
    if not m:
        return None
    doi = re.sub(r"(\.full(-text)?|\.abstract|\.pdf|\.supplementary-material)$", "", m.group(1).rstrip("/.").lower())
    doi = re.sub(r"(?<=\d)v\d+$", "", doi)          # bioRxiv/medRxiv version suffix
    return doi

def discover(since, errors, queries=("neuroscience", "brain", "psychedelic", "EEG", "neurons"), domains=None):
    agg = defaultdict(lambda: {"posts": {}, "link": None})
    calls = 0
    for dom in domains or DISCOVER_DOMAINS:
        for q in queries:
            calls += 1
            for p in bsky({"q": q, "domain": dom, "since": f"{since}T00:00:00Z", "sort": "top"}, errors):
                r = post_rec(p)
                if not r["link"] or is_bot(r):
                    continue
                key = re.sub(r"(v\d+)?(\.full(-text)?|\.abstract|\.pdf)?([?#].*)?$", "", r["link"].rstrip("/")).lower()
                key = key.replace("http://", "https://").replace("//nature.com", "//www.nature.com")
                agg[key]["posts"][r["uri"]] = r
                agg[key]["link"] = r["link"]
    rows = []
    for key, v in agg.items():
        ps = list(v["posts"].values())
        for p in ps:
            p["bot"] = False
        score, authors, eng = buzz_score(ps, [])
        text = " ".join(p["text"] for p in ps)
        rows.append({"link": key, "raw_link": v["link"], "doi": link_doi(v["link"]) or link_doi(key),
                     "buzz": score, "posters": authors, "engagement": eng, "neuro_text": bool(NEURO_RE.search(text) or NEURO_RE.search(key)),
                     "sample_post": max(ps, key=lambda p: p["likes"])["url"], "sample_text": max(ps, key=lambda p: p["likes"])["text"][:200]})
    rows.sort(key=lambda r: -r["buzz"])
    return rows, calls
