"""PsychoPal weekly run: papers + extras + opportunities + tools -> card text -> static site + archive.
Claude is optional (llm.enabled in config/settings.yaml). Switched on: a monthly taste digest from your marks, a capped re-rank of
the paper shortlist, and card text for new items only; switched off (or failing): scripted card text and script-only ranking.
Every stage is isolated: a failure is recorded as a run note and the edition still publishes.

  python -m psychopal.run_weekly                        # previous Monday-Sunday
  python -m psychopal.run_weekly --week-end 2026-11-08  # a specific week (re-running a week is idempotent)
  python -m psychopal.run_weekly --end 2026-11-21       # this week so far (Monday to the 21st): the app's refresh button
  python -m psychopal.run_weekly --start 2026-11-02 --end 2026-11-21   # a longer period (at most 4 weeks)
  python -m psychopal.run_weekly --no-llm               # extractive card text only (zero tokens)
  python -m psychopal.run_weekly --llm-limit 5          # cap Claude to 5 items (testing)
  python -m psychopal.run_weekly --reuse-papers         # re-score cached paper search results (tuning)

Editions: one per ISO week, filed under the week of the period's LAST day. A refresh in the middle of the week builds that week's
edition "so far"; the Monday run completes it. Re-running a week keeps the earliest start it already had (a longer refresh period
is never cut back) unless --start is given. Cards shown in other editions are never repeated. Per-week amounts (papers, tools,
trials...) are scaled to the period's length.
"""
import argparse, datetime as dt, json, os, time, traceback
from . import papers, extras, opportunities, tools_watch, build_site, build_archive, wider, prefs, llm
from .summarise import summarise
from .common import STATE, OUT, SITE, load_jsonl, append_jsonl, load_json, write_json, load_yaml, week_id, last_full_week, log

MAX_DAYS = 28       # longest period one edition may cover (a refresh after a holiday)

def edition_window(today, week_end=None, start=None, end=None, existing=None, max_days=MAX_DAYS):
    """(start, end, week id) of the edition to build. `existing` = this week's entry in site/data/index.json, if any."""
    if week_end:
        end = dt.date.fromisoformat(week_end)
        default_start = end - dt.timedelta(days=6)
    elif end or start:
        end = min(dt.date.fromisoformat(end), today) if end else today
        default_start = end - dt.timedelta(days=end.weekday())          # Monday of that week
    else:
        default_start, end = last_full_week(today)
    week = week_id(end)
    if start:
        s = dt.date.fromisoformat(start)
    else:
        s = default_start
        if existing and existing.get("start"):
            s = min(s, dt.date.fromisoformat(existing["start"]))         # keep a longer period an earlier refresh chose
    s = max(min(s, end), end - dt.timedelta(days=max_days - 1))
    return s, end, week

def edition_label(week, end):
    """'Week 47, 2026', or 'Week 47, 2026 (so far)' when the period stops before that week's Sunday."""
    sunday = end + dt.timedelta(days=6 - end.weekday())
    return f"Week {int(week.split('W')[1])}, {week[:4]}" + (" (so far)" if end < sunday else "")

def scale_for_period(settings, days):
    """Per-week amounts scaled to the period: a 3-day refresh shows fewer cards, a 3-week catch-up more. 7 days = unchanged."""
    if days == 7:
        return settings
    f = min(max(days / 7, 0.3), MAX_DAYS / 7)
    sc = lambda v, lo=1: max(lo, round(v * f))
    s = dict(settings)
    s["papers_total"] = sc(settings.get("papers_total", 25), 5)
    s["top_for_you"] = min(settings.get("top_for_you", 5), s["papers_total"])
    quota = {}
    for k, v in (settings.get("stream_quota") or {}).items():
        lo, hi = (v, v) if isinstance(v, int) else (v[0], v[1])
        quota[k] = [sc(lo, 0), sc(hi)]
    s["stream_quota"] = quota
    s["min_per_stream"] = sc(settings.get("min_per_stream", 2), 0)
    for k in ("max_per_stream", "tool_papers_max", "trials_max", "ukri_max", "tool_releases_max", "buzz_discover"):
        if k in settings:
            s[k] = sc(settings[k], 0 if k == "buzz_discover" else 1)
    if (settings.get("wider") or {}).get("cards"):
        s["wider"] = {**settings["wider"], "cards": sc(settings["wider"]["cards"], 0)}
    s["_scale"] = round(f, 2)
    return s

def read_user_state():
    """Likes / dislikes / saves synced from the phone (sync.js -> user-state branch -> state/user_state.json, fetched by the workflow)."""
    try:
        raw = load_json(os.path.join(STATE, "user_state.json"), default=None)
    except ValueError:                       # empty or damaged file: run without marks rather than fail the edition
        raw = None
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
    if llm.get("budget_stop") and not no_llm:
        b = (settings.get("llm") or {}).get("monthly_budget_usd", 1.0)
        out.append(f"Claude's monthly budget (${float(b):.2f}) is used up, so cards show scripted summaries until the 1st. "
                   "Raise llm.monthly_budget_usd in config/settings.yaml to change this.")
    elif err and ("401" in err or "Authentication" in err):
        out.append("Claude rejected the API key, so cards show scripted summaries this week. Replace the PSYCHOPAL_2027 secret on GitHub, "
                   "then re-run the workflow for this week.")
    elif err and ("credit" in err.lower() or "billing" in err.lower()):
        out.append("The Claude account has run out of credits, so cards show scripted summaries this week.")
    elif err:
        out.append("Claude was unavailable, so some cards show scripted summaries this week. They are rewritten next week.")
    elif llm.get("skipped") and not no_llm and (settings.get("llm") or {}).get("enabled", True):
        out.append("No Claude API key is set, so cards show scripted summaries. Add the PSYCHOPAL_2027 secret on GitHub.")
    exp = (settings.get("llm") or {}).get("key_expires")
    if exp:
        try:
            days = (dt.date.fromisoformat(str(exp)[:10]) - today).days
        except ValueError:
            days = None
        if days is not None and days < 0:
            out.append(f"The Claude API key expired on {exp}. Create a new one and update the PSYCHOPAL_2027 secret on GitHub "
                       "(and llm.key_expires in config/settings.yaml).")
        elif days is not None and days <= 21:
            out.append(f"The Claude API key expires in {days} days ({exp}). Create a new one and update the PSYCHOPAL_2027 secret on "
                       "GitHub, and llm.key_expires in config/settings.yaml.")
    return out

def claude_summary(stats, llm_cfg, on):
    """Claude's cost this run + month to date, and one readable line for the Actions run page."""
    if not on:
        return {"on": False, "cost_usd": 0.0, "line": "switched off (scripted card text, script-only ranking)"}
    parts = {k: stats.get(k) or {} for k in ("llm", "rerank", "taste")}
    cost = round(sum(float(v.get("cost_usd") or 0) for v in parts.values()), 4)
    budget = float(llm_cfg.get("monthly_budget_usd", 1.0))
    mtd = round(llm.month_spend() + cost, 4)        # this run is not in runs.jsonl yet
    c, r, t = parts["llm"], parts["rerank"], parts["taste"]
    bits = []
    if c.get("budget_stop") or c.get("skipped") or c.get("error"):
        bits.append("cards: " + (c.get("budget_stop") or c.get("skipped") or "error, scripted text"))
    else:
        bits.append(f"cards {c.get('written', 0)} new (${float(c.get('cost_usd') or 0):.3f})")
    if r:
        mv = (stats.get("papers") or {}).get("claude_moves") or {}
        bits.append(f"re-rank: {r.get('skipped') or r.get('error') or str(r.get('rated', 0)) + ' papers rated'}"
                    + (f", {len(mv.get('in', []))} in / {len(mv.get('out', []))} out" if mv and r.get("rated") else "")
                    + (f" (${float(r.get('cost_usd') or 0):.3f})" if r.get("cost_usd") else ""))
    if t:
        bits.append("taste: " + (t.get("skipped") or t.get("error") or f"new digest from {t.get('marks')} marks"))
    bits.append(f"this run ${cost:.3f} · month ${mtd:.2f} of ${budget:.2f}")
    return {"on": True, "cost_usd": cost, "month_to_date_usd": mtd, "budget_usd": budget,
            "parts": {k: v.get("cost_usd", 0) for k, v in parts.items()}, "line": " · ".join(bits)}

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
    ap.add_argument("--start", help="first day of the period (with --end; default: that week's Monday)")
    ap.add_argument("--end", help="last day of the period (default: last Sunday; never later than today)")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm-limit", type=int)
    ap.add_argument("--reuse-papers", action="store_true")
    ap.add_argument("--skip", default="", help="comma list of stages to skip: papers,trials,catchup,buzz,opportunities,tools")
    a = ap.parse_args()
    settings = load_yaml("settings.yaml")
    today = dt.date.today()
    index = load_json(os.path.join(SITE, "data", "index.json"), default={"weeks": []}) or {"weeks": []}
    week = edition_window(today, a.week_end, a.start, a.end)[2]                  # the week depends only on the end date
    existing = next((w for w in index.get("weeks", []) if w.get("week") == week), None)
    start, end, week = edition_window(today, a.week_end, a.start, a.end, existing)
    days = (end - start).days + 1
    settings = scale_for_period(settings, days)
    skip = set(filter(None, a.skip.split(",")))
    settings["_week"] = week
    outdir = os.path.join(OUT, str(end))
    log(f"=== PsychoPal {week}: {start} to {end} ({days} days) ===")
    stats, health = {"week": week, "window": [str(start), str(end)], "started": dt.datetime.now().isoformat(timespec="seconds")}, []
    if days != 7:
        stats["period_scale"] = settings.get("_scale")

    # Claude master switch (llm.enabled; --no-llm turns it off for this run)
    llm_cfg = dict(settings.get("llm") or {})
    if a.no_llm:
        llm_cfg["enabled"] = False
    settings["_llm_on"] = bool(llm_cfg.get("enabled", True))

    us = read_user_state()
    liked_t, disliked_t = preference_texts(us)
    stats["preferences"] = {"liked": len(us["liked"]), "disliked": len(us["disliked"]), "starred": len(us["starred"]),
                            "synced": os.path.exists(os.path.join(STATE, "user_state.json"))}
    prev = load_jsonl(os.path.join(STATE, "items.jsonl"))
    shown_before = {r["id"] for r in prev if r.get("week") != week}

    # preferences: monthly taste digest (Claude), then a capped re-rank + taste bonus applied before paper selection
    taste = None
    if settings["_llm_on"]:
        taste = stage("taste", health, stats, prefs.monthly_digest, us, settings, stats)
        taste = taste or prefs.load()
    spent = lambda: sum(float((stats.get(k) or {}).get("cost_usd") or 0) for k in ("taste", "rerank", "llm"))
    adjust = (lambda sc: prefs.adjust(sc, settings, taste, stats, run_spent=spent())) if settings["_llm_on"] else None

    cards, alerts, res = [], [], None
    if "papers" not in skip:
        res = stage("papers", health, stats, papers.run, start, end, settings,
                    {"liked_texts": liked_t, "disliked_texts": disliked_t}, outdir=outdir, reuse_raw=a.reuse_papers, adjust=adjust)
        if (stats.get("rerank") or {}).get("error"):
            health.append("Claude re-rank unavailable this week; script ranking only")
        if res:
            stats["papers"] = res["stats"]
            cards += res["papers"]
            cards += [{**p, "kind": "tool_paper", "stream": "tools"} for p in res["tool_papers"]]
            failed = [k for k, v in res["stats"]["sources"].items() if v.get("error")]
            if failed:
                health.append("paper sources unavailable: " + ", ".join(failed))
            for k, v in res["stats"]["sources"].items():
                if v.get("skipped_slow"):
                    health.append(f"{k} was slow, so {len(v['skipped_slow'])} lower-priority searches were skipped "
                                  "(PubMed and OpenAlex cover most of the same papers)")
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
    to_write = cards + [i for i in opp_items if i.get("_extract") or i.get("_ukri")]
    stage("summarise", health, stats, summarise, to_write, llm_cfg, stats, limit=a.llm_limit, run_spent=spent())
    if stats.get("llm", {}).get("error"):
        health.append("card text fell back to scripted summaries for some items (Claude unavailable)")
    alerts += claude_alerts(stats.get("llm", {}), settings, a.no_llm)

    deadlines = []
    if opp_ctx:
        r = stage("opportunities_finalise", health, stats, opportunities.finalise, opp_items, opp_ctx, settings, shown_before)
        if r:
            opp_cards, deadlines = r
            cards = opp_cards + cards
    edition = {"week": week, "label": edition_label(week, end), "start": str(start), "end": str(end),
               "generated": dt.datetime.now().isoformat(timespec="seconds"), "cards": cards, "deadlines": deadlines, "health": health,
               "alerts": alerts, "taste": prefs.public(taste, settings, us)}
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
    stats["claude"] = claude_summary(stats, llm_cfg, settings["_llm_on"])
    write_json(os.path.join(STATE, "stats.json"), stats)
    append_jsonl(os.path.join(STATE, "runs.jsonl"), [{"week": week, "finished": stats["finished"], "cards": stats["cards"],
                                                     "llm": stats.get("llm", {}), "claude": stats["claude"],
                                                     "failures": stats.get("failures", {})}])
    log(f"=== done: {n} cards -> site/data/weeks/{week}.json · {stats['cards']} · llm {stats.get('llm', {})} ===")

if __name__ == "__main__":
    main()
