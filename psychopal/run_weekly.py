"""PsychoPal weekly run: papers + extras + opportunities + tools -> card text -> static site + archive.
Card text is scripted (structured abstract summaries) while Claude is switched off (llm.enabled: false in config/settings.yaml);
switched on, Claude writes it for new items only.
Every stage is isolated: a failure is recorded as a run note and the edition still publishes.

  python -m psychopal.run_weekly                        # previous Monday-Sunday
  python -m psychopal.run_weekly --week-end 2026-11-08  # a specific week (re-running a week is idempotent)
  python -m psychopal.run_weekly --no-llm               # extractive card text only (zero tokens)
  python -m psychopal.run_weekly --llm-limit 5          # cap Claude to 5 items (testing)
  python -m psychopal.run_weekly --reuse-papers         # re-score cached paper search results (tuning)
"""
import argparse, datetime as dt, json, os, time, traceback
from . import papers, extras, opportunities, tools_watch, build_site, build_archive, wider
from .summarise import summarise
from .common import STATE, OUT, load_jsonl, append_jsonl, load_json, write_json, load_yaml, week_id, last_full_week, log

def read_user_state():
    """Likes / dislikes / saves synced from the phone (sync.js -> user-state branch -> state/user_state.json, fetched by the workflow)."""
    raw = load_json(os.path.join(STATE, "user_state.json"), default=None)
    out = {"liked": set(), "disliked": set(), "starred": set(), "search_saved": []}
    if not raw:
        return out
    saved = {}   # pp-search-sent: {doi: {title, abstract, ...}} flattened by sync.js into "doi\tfield" leaves
    for k, e in ((raw.get("stores") or {}).get("pp-search-sent") or {}).items():
        if e.get("d") or "\t" not in k:
            continue
        a, b = k.split("\t", 1)
        try:
            saved.setdefault(a, {})[b] = json.loads(e.get("v") or "null")
        except ValueError:
            pass
    out["search_saved"] = [f"{v.get('title', '')} {v.get('abstract', '')}" for v in saved.values() if isinstance(v, dict) and v.get("title")]
    leaves = (raw.get("stores") or {}).get("pp-marks") or {}
    for k, e in leaves.items():
        if e.get("d") or "\t" not in k:
            continue
        kind, iid = k.split("\t", 1)
        if kind in ("like", "more"):
            out["liked"].add(iid)
        elif kind in ("dislike", "ni"):
            out["disliked"].add(iid)
        elif kind == "star":
            out["starred"].add(iid)
    return out

def preference_texts(us):
    items = {r["id"]: r for r in load_jsonl(os.path.join(STATE, "items.jsonl"))}
    txt = lambda r: f"{r.get('title', '')} {r.get('post', '')} {r.get('snippet', '')}"
    liked = [txt(items[i]) for i in (us["liked"] | us["starred"]) if i in items and items[i].get("kind") in ("paper", "catchup", "trial")]
    disliked = [txt(items[i]) for i in us["disliked"] if i in items and items[i].get("kind") in ("paper", "catchup")]
    return liked + us.get("search_saved", []), disliked

def claude_alerts(llm, settings, no_llm=False, today=None):
    """Problems worth a banner at the top of the feed (minor ones stay in the 'Run notes' at the bottom)."""
    out, today = [], today or dt.date.today()
    if not (settings.get("llm") or {}).get("enabled", True):
        return out                      # Claude switched off: no key in use, nothing to warn about
    err = llm.get("error") or ""
    if err and ("401" in err or "Authentication" in err):
        out.append("Claude rejected the API key, so cards show abstract excerpts this week. Replace the ANTHROPIC_API_KEY secret on GitHub, "
                   "then re-run the workflow for this week.")
    elif err and ("credit" in err.lower() or "billing" in err.lower()):
        out.append("The Claude account has run out of credits, so cards show abstract excerpts this week.")
    elif err:
        out.append("Claude was unavailable, so some cards show abstract excerpts this week. They are rewritten next week.")
    elif llm.get("skipped") and not no_llm and (settings.get("llm") or {}).get("enabled", True):
        out.append("No Claude API key is set, so cards show abstract excerpts. Add the ANTHROPIC_API_KEY secret on GitHub.")
    exp = (settings.get("llm") or {}).get("key_expires")
    if exp:
        try:
            days = (dt.date.fromisoformat(str(exp)[:10]) - today).days
        except ValueError:
            days = None
        if days is not None and days < 0:
            out.append(f"The Claude API key expired on {exp}. Create a new one and update the ANTHROPIC_API_KEY secret on GitHub "
                       "(and llm.key_expires in config/settings.yaml).")
        elif days is not None and days <= 21:
            out.append(f"The Claude API key expires in {days} days ({exp}). Create a new one and update the ANTHROPIC_API_KEY secret on "
                       "GitHub, and llm.key_expires in config/settings.yaml.")
    return out

def stage(name, health, stats, fn, *a, **kw):
    t0 = time.time()
    try:
        res = fn(*a, **kw)
        stats.setdefault("stage_seconds", {})[name] = round(time.time() - t0, 1)
        return res
    except Exception as e:
        log(f"{name}: FAILED {type(e).__name__}: {e}")
        traceback.print_exc()
        health.append(f"{name} unavailable this week ({type(e).__name__})")
        stats.setdefault("failures", {})[name] = f"{type(e).__name__}: {str(e)[:300]}"
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week-end", help="Sunday that ends the week (default: last full week)")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm-limit", type=int)
    ap.add_argument("--reuse-papers", action="store_true")
    ap.add_argument("--skip", default="", help="comma list of stages to skip: papers,trials,catchup,buzz,opportunities,tools")
    a = ap.parse_args()
    settings = load_yaml("settings.yaml")
    if a.week_end:
        end = dt.date.fromisoformat(a.week_end); start = end - dt.timedelta(days=6)
    else:
        start, end = last_full_week()
    week = week_id(start)
    skip = set(filter(None, a.skip.split(",")))
    settings["_week"] = week
    outdir = os.path.join(OUT, str(end))
    log(f"=== PsychoPal {week}: {start} to {end} ===")
    stats, health = {"week": week, "window": [str(start), str(end)], "started": dt.datetime.now().isoformat(timespec="seconds")}, []

    us = read_user_state()
    liked_t, disliked_t = preference_texts(us)
    stats["preferences"] = {"liked": len(us["liked"]), "disliked": len(us["disliked"]), "starred": len(us["starred"])}
    prev = load_jsonl(os.path.join(STATE, "items.jsonl"))
    shown_before = {r["id"] for r in prev if r.get("week") != week}

    cards, alerts, res = [], [], None
    if "papers" not in skip:
        res = stage("papers", health, stats, papers.run, start, end, settings,
                    {"liked_texts": liked_t, "disliked_texts": disliked_t}, outdir=outdir, reuse_raw=a.reuse_papers)
        if res:
            stats["papers"] = res["stats"]
            cards += res["papers"]
            cards += [{**p, "kind": "tool_paper", "stream": "tools"} for p in res["tool_papers"]]
            failed = [k for k, v in res["stats"]["sources"].items() if v.get("error")]
            if failed:
                health.append("paper sources unavailable: " + ", ".join(failed))
            if len(failed) >= 3 or not res["papers"]:
                alerts.append("Few or no papers this week: the literature databases could not be reached (" + (", ".join(failed) or "no results") + ").")
        else:
            alerts.append("The paper search failed this week, so there are no papers in this edition.")
    chosen_dois = {c.get("doi") for c in cards if c.get("doi")}
    if "buzz" not in skip:
        b = stage("buzz", health, stats, extras.buzz, [c for c in cards if c["kind"] == "paper"], start, settings)
        if b:
            stats["buzz"] = b
        d = stage("buzz_discover", health, stats, extras.buzz_discover, start, settings, week, chosen_dois)
        if d:
            cards += d[0]; stats["buzz_discover"] = d[1]
    if "wider" not in skip and res:
        wd = stage("wider", health, stats, wider.pick, res.get("wider", []), cards, settings, start, week)
        if wd:
            cards += wd[0]; stats["wider"] = wd[1]
    if "trials" not in skip:
        t = stage("trials", health, stats, extras.trials, start, end, settings, week)
        if t:
            cards += t[0]; stats["trials"] = t[1]
    if "catchup" not in skip:
        c = stage("catchup", health, stats, extras.catchup, settings, week, liked_t)
        if c:
            cards += c[0]; stats["catchup"] = c[1]
    if "tools" not in skip:
        t = stage("tools", health, stats, tools_watch.run, start, end, settings, us["liked"] | us["starred"], shown_before)
        if t:
            cards += t[0]; stats["tools"] = t[1]
    opp_items, opp_ctx = [], None
    if "opportunities" not in skip:
        r = stage("opportunities", health, stats, opportunities.collect, start, end, settings, week, shown_before, us["disliked"])
        if r:
            opp_items, opp_ctx = r
            stats["opportunities"] = opp_ctx["stats"]

    # drop anything hidden with 👎 and de-duplicate ids
    seen, uniq = set(), []
    for c in cards:
        if c["id"] in seen or c["id"] in us["disliked"]:
            continue
        seen.add(c["id"]); uniq.append(c)
    cards = uniq

    # Claude: card text for new items only (opportunity pages: only new/changed versions)
    llm_cfg = dict(settings.get("llm") or {})
    if a.no_llm:
        llm_cfg["enabled"] = False
    settings["_llm_on"] = bool(llm_cfg.get("enabled", True))
    to_write = cards + [i for i in opp_items if i.get("_extract") or i.get("_ukri")]
    stage("summarise", health, stats, summarise, to_write, llm_cfg, stats, limit=a.llm_limit)
    if stats.get("llm", {}).get("error"):
        health.append("card text fell back to abstracts for some items (Claude unavailable)")
    alerts += claude_alerts(stats.get("llm", {}), settings, a.no_llm)

    deadlines = []
    if opp_ctx:
        r = stage("opportunities_finalise", health, stats, opportunities.finalise, opp_items, opp_ctx, settings, shown_before)
        if r:
            opp_cards, deadlines = r
            cards = opp_cards + cards
    edition = {"week": week, "label": f"Week {int(week.split('W')[1])}, {start.year}", "start": str(start), "end": str(end),
               "generated": dt.datetime.now().isoformat(timespec="seconds"), "cards": cards, "deadlines": deadlines, "health": health,
               "alerts": alerts}
    n = build_site.write_edition(edition, settings)

    # archive state: one row per card per week (idempotent on re-runs of the same week)
    rows = [r for r in prev if r.get("week") != week]
    for c in cards:
        rows.append({k: c.get(k) for k in build_site.CARD_FIELDS if c.get(k) not in (None, "", [], False)}
                    | {"week": week, "snippet": (c.get("abstract") or c.get("description") or "")[:500]})
    os.makedirs(STATE, exist_ok=True)
    tmp = os.path.join(STATE, "items.jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, os.path.join(STATE, "items.jsonl"))
    stage("archive", health, stats, build_archive.build, settings)

    stats["cards"] = {k: sum(1 for c in cards if c["kind"] == k) for k in sorted({c["kind"] for c in cards})}
    stats["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    stats["health"] = health
    stats["alerts"] = alerts
    write_json(os.path.join(STATE, "stats.json"), stats)
    append_jsonl(os.path.join(STATE, "runs.jsonl"), [{"week": week, "finished": stats["finished"], "cards": stats["cards"],
                                                     "llm": stats.get("llm", {}), "failures": stats.get("failures", {})}])
    log(f"=== done: {n} cards -> site/data/weeks/{week}.json · {stats['cards']} · llm {stats.get('llm', {})} ===")

if __name__ == "__main__":
    main()
