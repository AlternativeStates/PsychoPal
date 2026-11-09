#!/usr/bin/env python3
"""Fetch Claudio's own papers from PubMed into config/profile_papers.json (the TF-IDF 'profile' that relevance is measured against).
Run once, and again whenever you publish something new:   python scripts/seed_profile.py
Optional: --query to change the author query, --since to drop older papers by a namesake, --add-doi to add extra papers you consider
'core' to your profile (e.g. key papers by others)."""
import argparse, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from psychopal import sources
from psychopal.common import CONFIG, write_json, log, load_json

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="Agnorelli C[au]")
    ap.add_argument("--since", default="2019-01-01", help="ignore papers entered in PubMed before this date (namesakes)")
    a = ap.parse_args()
    cfg = {"groups": [{"name": "me", "pubmed": a.query}]}
    recs, info = sources.pubmed(cfg, dt.date.fromisoformat(a.since), dt.date.today(), log)
    path = os.path.join(CONFIG, "profile_papers.json")
    old = load_json(path, default={"papers": []})
    extra = [p for p in old["papers"] if p.get("manual")]   # keep hand-added papers
    papers = [{"title": r["title"], "abstract": r["abstract"], "venue": r["venue"], "date": r["date"], "doi": r["doi"],
               "pmid": r.get("pmid"), "url": r["url"]} for r in recs if r.get("abstract")]
    papers.sort(key=lambda p: p["date"] or "", reverse=True)
    write_json(path, {"_about": f"PubMed '{a.query}' since {a.since}; regenerate with scripts/seed_profile.py. "
                                "Add entries with \"manual\": true to keep extra papers across regenerations.",
                      "updated": str(dt.date.today()), "papers": papers + extra})
    log(f"{len(papers)} papers with abstracts (+{len(extra)} manual) -> {path}")
    for p in papers:
        print("-", p["date"], p["title"][:100])

if __name__ == "__main__":
    main()
