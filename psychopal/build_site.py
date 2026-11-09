"""Writes the static feed data: site/data/weeks/<week>.json, site/data/index.json and site/config.js.
The feed UI itself (site/index.html, app.js, styles.css) is hand-written and never generated."""
import json, os
from .common import SITE, load_json, write_json

# fields copied to the public card (everything else, e.g. full abstracts and score internals, stays in state/)
CARD_FIELDS = ("id", "kind", "stream", "title", "url", "venue", "date", "authors", "post", "why_you", "ai_tags", "ai", "top",
               "is_preprint", "is_review", "modality", "buzz", "phase", "status", "citations", "deadline", "event_dates", "location",
               "funder", "reason", "eligibility_note", "doi", "repo", "version")

def public_card(c):
    out = {k: c[k] for k in CARD_FIELDS if c.get(k) not in (None, "", [], False)}
    if out.get("authors") and len(out["authors"]) > 8:
        out["authors"] = out["authors"][:6] + out["authors"][-1:]
    return out

def write_edition(ed, settings):
    data = os.path.join(SITE, "data")
    week = ed["week"]
    cards = [public_card(c) for c in ed["cards"]]
    write_json(os.path.join(data, "weeks", f"{week}.json"),
               {"week": week, "label": ed.get("label") or week, "start": ed["start"], "end": ed["end"], "generated": ed["generated"],
                "cards": cards, "deadlines": ed.get("deadlines", []), "health": ed.get("health", [])}, indent=None)
    idx = load_json(os.path.join(data, "index.json"), default={"weeks": []})
    weeks = [w for w in idx["weeks"] if w["week"] != week]
    weeks.append({"week": week, "label": ed.get("label") or week, "start": ed["start"], "end": ed["end"], "n": len(cards)})
    weeks.sort(key=lambda w: w["start"], reverse=True)
    write_json(os.path.join(data, "index.json"), {"latest": weeks[0]["week"], "weeks": weeks})
    cfg = {"repo": settings.get("github_repo") or "", "title": settings.get("site_title") or "PsychoPal", "site_url": settings.get("site_url") or ""}
    with open(os.path.join(SITE, "config.js"), "w", encoding="utf-8") as f:
        f.write("window.PP_CONFIG=" + json.dumps(cfg) + ";\n")
    return len(cards)
