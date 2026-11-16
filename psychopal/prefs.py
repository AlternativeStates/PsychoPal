"""Learning Claudio's preferences with Claude. Both parts are optional, capped and fail-safe (any problem = no effect on ranking).

- Monthly taste digest (`monthly_digest`): once a month, when at least taste.min_marks cards carry 👍 / 👎 / ★ (synced from the phone)
  and the marks changed, Claude (Haiku) reads those cards and writes a short taste profile plus a few terms to rank higher or lower.
  Saved in state/taste.json and shown at the top of the feed.
- Weekly adjustment (`adjust`, called by papers.run before selection):
  * taste terms (no API call): +-term_weight per matching term, capped at +-taste.max_bonus;
  * re-rank: ONE Claude (Haiku) call rates the ~80-paper shortlist 1-5 against profile.md and the taste profile;
    bonus = max_bonus x (rating - 3) / 2, i.e. one of -2.5, -1.25, 0, +1.25, +2.5. All or nothing: no bonus if the call fails.
  Claude can therefore move a paper by at most about +-4 points; the edition cut-off is around 17, so an off-topic paper can't get in.

  python -m psychopal.prefs --digest --force --dry-run    # print the taste digest Claude would write now (~$0.003)
"""
import argparse, datetime as dt, hashlib, json, os, re
from . import llm
from .common import CONFIG, STATE, load_json, write_json, load_jsonl, log, anthropic_client
from .abstracts import structure

TASTE = os.path.join(STATE, "taste.json")
TERM_RE = re.compile(r"^[a-z0-9][a-z0-9 \-]{2,38}$")
GENERIC = {"brain", "study", "studies", "patients", "patient", "effect", "effects", "research", "paper", "papers", "human", "humans",
           "neuroscience", "data", "analysis", "results", "method", "methods", "model", "models", "review", "health", "clinical",
           "treatment", "disease", "disorder", "disorders", "cognition", "behaviour", "behavior", "trial", "trials"}

DIGEST_SYSTEM = """You help a researcher's weekly reading feed learn his taste. Below are cards he marked as liked or saved, and cards
he marked "not for me", plus his own profile. Based ONLY on these marks:
- profile: at most 100 words, second person ("You favour ..."), describing the topics, methods, species and study types he
  prefers and avoids. Be specific; do not just restate his profile.
- boost: up to 8 specific terms (1-3 words, lowercase, as they would appear in a paper's title or abstract) typical of the liked cards.
- penalise: up to 6 such terms typical of the "not for me" cards.
Never use generic words (brain, study, patients, effect, research, analysis, clinical). Leave a list empty if the marks show no pattern."""

DIGEST_SCHEMA = {"type": "object", "properties": {"profile": {"type": "string"},
                                                  "boost": {"type": "array", "items": {"type": "string"}},
                                                  "penalise": {"type": "array", "items": {"type": "string"}}},
                 "required": ["profile", "boost", "penalise"], "additionalProperties": False}

RERANK_SYSTEM = """You help rank papers for one researcher's weekly reading list. His profile and, if available, a summary of what
he has liked and disliked are below. Rate EVERY numbered paper once, for how much HE would want to read it:
5 = core to his current work or plans; 4 = clearly relevant; 3 = related, worth a look; 2 = marginal; 1 = off-target for him.
Judge relevance to him only, not journal prestige or general importance."""

RERANK_SCHEMA = {"type": "object", "properties": {"ratings": {"type": "array", "items": {
    "type": "object", "properties": {"n": {"type": "integer"}, "r": {"type": "integer"}},
    "required": ["n", "r"], "additionalProperties": False}}}, "required": ["ratings"], "additionalProperties": False}

def load():
    return load_json(TASTE, default=None)

def _profile():
    with open(os.path.join(CONFIG, "profile.md"), encoding="utf-8") as f:
        return f.read()

def _text(msg):
    if getattr(msg, "stop_reason", None) in ("refusal", "max_tokens"):
        raise ValueError(f"stop_reason={msg.stop_reason}")
    return next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")

def _params(cfg, system, user, schema, max_tokens):
    model, effort = cfg.get("model", "claude-haiku-5-5"), cfg.get("effort", "low")
    p = {"model": model, "max_tokens": max_tokens, "system": system,
         "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
         "messages": [{"role": "user", "content": user}]}
    th = llm.thinking_param(model, cfg.get("thinking", "off"), effort)
    if th:
        p["thinking"] = th
    return p

def _client(client):
    c = client or anthropic_client()
    return c.with_options(timeout=90, max_retries=2) if hasattr(c, "with_options") else c

# ---------------- monthly taste digest ----------------
def marked(us):
    """(liked or starred, disliked) cards that can be found in state/items.jsonl, newest week first."""
    items = {r["id"]: r for r in load_jsonl(os.path.join(STATE, "items.jsonl"))}
    wk = lambda r: r.get("week") or ""
    liked = sorted((items[i] for i in (us.get("liked", set()) | us.get("starred", set())) if i in items), key=wk, reverse=True)
    disliked = sorted((items[i] for i in us.get("disliked", set()) if i in items), key=wk, reverse=True)
    return liked, disliked

def marks_hash(liked, disliked):
    s = "|".join(sorted(r["id"] for r in liked)) + "#" + "|".join(sorted(r["id"] for r in disliked))
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]

def clean_terms(xs, k, exclude=()):
    out = []
    for t in xs or []:
        t = re.sub(r"\s+", " ", str(t).lower()).strip()
        if TERM_RE.match(t) and t not in GENERIC and t not in out and t not in exclude:
            out.append(t)
    return out[:k]

def monthly_digest(us, settings, stats, today=None, force=False, dry=False, client=None, run_spent=0.0):
    """Writes state/taste.json when due; returns the current taste (new or previous) or None."""
    cfg, old = settings.get("taste") or {}, load()
    if not (settings.get("_llm_on") and cfg.get("enabled", True)):
        return old
    today = today or dt.date.today()
    liked, disliked = marked(us)
    n, need, h = len(liked) + len(disliked), int(cfg.get("min_marks", 10)), marks_hash(liked, disliked)
    skip = None
    if n < need:
        skip = f"{n} marked cards (needs {need})"
    elif old and old.get("month") == today.strftime("%Y-%m"):
        skip = "already done this month"
    elif old and (old.get("marks") or {}).get("hash") == h:
        skip = "no new marks since the last digest"
    elif not (client or os.environ.get("ANTHROPIC_API_KEY")):
        skip = "no ANTHROPIC_API_KEY"
    elif llm.budget_left((settings.get("llm") or {}).get("monthly_budget_usd", 1.0), run_spent, today) <= 0:
        skip = "monthly budget used up"
    if skip and not force:
        stats["taste"] = {"skipped": skip}
        return old
    line = lambda r: f"- [{r.get('stream') or r.get('kind')}] {r.get('title', '')}. {(r.get('snippet') or r.get('post') or '')[:200]}"
    user = (f"Cards he liked or saved ({len(liked[:60])}):\n" + ("\n".join(map(line, liked[:60])) or "- none") +
            f"\n\nCards he marked 'not for me' ({len(disliked[:40])}):\n" + ("\n".join(map(line, disliked[:40])) or "- none"))
    params = _params(cfg, DIGEST_SYSTEM + "\n\nHIS PROFILE (written by him):\n" + _profile(), user, DIGEST_SCHEMA, 6000)
    usage = {**llm.new_usage(), "cost_usd": 0.0}
    st = {"model": params["model"], "marks": n}
    try:
        msg = _client(client).messages.create(**params)
        llm.account(usage, msg, params["model"])
        d = json.loads(_text(msg))
    except Exception as e:
        stats["taste"] = {**st, "error": f"{type(e).__name__}: {str(e)[:200]}", "cost_usd": usage["cost_usd"]}
        log("taste: FAILED " + stats["taste"]["error"])
        return old
    boost = clean_terms(d.get("boost"), int(cfg.get("max_boost", 8)))
    taste = {"version": 1, "month": today.strftime("%Y-%m"), "written": str(today), "model": params["model"],
             "marks": {"liked": len(liked), "disliked": len(disliked), "total": n, "hash": h},
             "profile": re.sub(r"\s+", " ", d.get("profile") or "").strip()[:900],
             "boost": boost, "penalise": clean_terms(d.get("penalise"), int(cfg.get("max_penalise", 6)), exclude=boost),
             "cost_usd": usage["cost_usd"]}
    stats["taste"] = {**st, "written": not dry, "cost_usd": usage["cost_usd"]}
    if not dry:
        write_json(TASTE, taste)
    log(f"taste: digest from {n} marks: boost {taste['boost']}, lower {taste['penalise']} (${usage['cost_usd']:.4f})")
    return taste

def public(taste, settings, us):
    """What the feed shows about learned taste (None = nothing to show)."""
    cfg = settings.get("taste") or {}
    if not (settings.get("_llm_on") and cfg.get("enabled", True)):
        return None
    if taste:
        return {"month": taste.get("month"), "marks": (taste.get("marks") or {}).get("total", 0), "profile": taste.get("profile", ""),
                "boost": taste.get("boost", []), "penalise": taste.get("penalise", [])}
    liked, disliked = marked(us)
    return {"waiting": f"{len(liked) + len(disliked)} of {int(cfg.get('min_marks', 10))} marked cards"}

# ---------------- weekly adjustment ----------------
def _paper_text(x):
    return (x.get("title") or "") + " " + (x.get("abstract") or "")[:1500]

def taste_bonus(scored, taste, cfg, stats):
    terms = [(t, 1) for t in taste.get("boost", [])] + [(t, -1) for t in taste.get("penalise", [])]
    if not terms or not scored:
        return {}
    texts = [_paper_text(x) for x in scored]
    rx, ignored = {}, []
    for t, sign in terms:
        r = re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", re.I)   # escaped: terms are never run as regex
        if sum(1 for tx in texts if r.search(tx)) > 0.25 * len(texts):
            ignored.append(t); continue                                             # too generic for this week's pool
        rx[t] = (r, sign)
    w, cap = float(cfg.get("term_weight", 0.75)), float(cfg.get("max_bonus", 1.5))
    out = {}
    for x, tx in zip(scored, texts):
        up = [t for t, (r, s) in rx.items() if s > 0 and r.search(tx)]
        down = [t for t, (r, s) in rx.items() if s < 0 and r.search(tx)]
        b = max(-cap, min(cap, w * (len(up) - len(down))))
        if b:
            out[x["id"]] = (round(b, 2), up[:2])
    stats["taste_terms"] = {"papers": len(out), "ignored_generic": ignored}
    return out

def shortlist(scored, settings, size):
    min_score = settings.get("min_paper_score", 4.0)
    ok = sorted((x for x in scored if x["score"] >= min_score and ((x.get("stream") and x["stream"] != "wider") or x.get("tool"))),
                key=lambda x: -x["score"])
    pick, ids = [], set()
    def add(xs):
        for x in xs:
            if x["id"] not in ids:
                ids.add(x["id"]); pick.append(x)
    add(ok[:40])
    for s in sorted({x["stream"] for x in ok if x.get("stream")}):
        add([x for x in ok if x["stream"] == s][:8])
    add([x for x in ok if x.get("tool")][:8])
    pick.sort(key=lambda x: -x["score"])
    return pick[:size]

def brief(x):
    parts = dict(structure(x.get("abstract") or ""))
    s = " ".join(v for v in (parts.get("Aim") or parts.get("Background"), parts.get("Conclusion")) if v)
    return (s or re.sub(r"\s+", " ", x.get("abstract") or "")[:300])[:450]

def rerank(scored, settings, taste, stats, client=None, run_spent=0.0):
    """{paper id: rating 1-5} for the shortlist, or {} (no effect) if anything goes wrong."""
    cfg = settings.get("rerank") or {}
    short = shortlist(scored, settings, int(cfg.get("shortlist", 80)))
    if not short:
        return {}, short
    if not (client or os.environ.get("ANTHROPIC_API_KEY")):
        stats["rerank"] = {"skipped": "no ANTHROPIC_API_KEY"}; return {}, short
    if llm.budget_left((settings.get("llm") or {}).get("monthly_budget_usd", 1.0), run_spent) <= 0:
        stats["rerank"] = {"skipped": "monthly budget used up"}; return {}, short
    sys_txt = RERANK_SYSTEM + "\n\nHIS PROFILE:\n" + _profile() + "\n\nWHAT HIS LIKES AND DISLIKES SHOW:\n" + \
        ((taste or {}).get("profile") or "nothing yet")
    user = "Papers:\n" + "\n".join(f"{i} | {x.get('stream') or 'tools'} | {x['title']} | {brief(x)}" for i, x in enumerate(short, 1))
    params = _params(cfg, sys_txt, user, RERANK_SCHEMA, 3000)
    usage = {**llm.new_usage(), "cost_usd": 0.0}
    st = {"model": params["model"], "shortlist": len(short)}
    try:
        msg = _client(client).messages.create(**params)
        llm.account(usage, msg, params["model"])
        got = {}
        for r in json.loads(_text(msg)).get("ratings", []):
            n, v = int(r["n"]), int(r["r"])
            if 1 <= n <= len(short) and n not in got:
                got[n] = max(1, min(5, v))
    except Exception as e:
        stats["rerank"] = {**st, "error": f"{type(e).__name__}: {str(e)[:200]}", "cost_usd": usage["cost_usd"]}
        log("rerank: FAILED " + stats["rerank"]["error"]); return {}, short
    if len(got) < 0.5 * len(short):
        stats["rerank"] = {**st, "error": f"only {len(got)} of {len(short)} papers rated; not used", "cost_usd": usage["cost_usd"]}
        log("rerank: " + stats["rerank"]["error"]); return {}, short
    stats["rerank"] = {**st, "rated": len(got), "cost_usd": usage["cost_usd"]}
    log(f"rerank: {len(got)}/{len(short)} papers rated (${usage['cost_usd']:.4f})")
    return {short[n - 1]["id"]: v for n, v in got.items()}, short

def adjust(scored, settings, taste, stats, client=None, run_spent=0.0):
    """Adds score_parts 'taste' and 'claude' (both capped) and recomputes scores, in place. No-op while Claude is off."""
    if not settings.get("_llm_on"):
        return
    tcfg, rcfg = settings.get("taste") or {}, settings.get("rerank") or {}
    tb = taste_bonus(scored, taste, tcfg, stats) if taste and tcfg.get("enabled", True) else {}
    ratings = {}
    if rcfg.get("enabled", True):
        ratings, _ = rerank(scored, settings, taste, stats, client, run_spent)
    mb = float(rcfg.get("max_bonus", 2.5))
    for x in scored:
        if x["id"] in tb:
            x["score_parts"]["taste"], x["taste_terms"] = tb[x["id"]]
        if x["id"] in ratings:
            x["claude_rating"] = ratings[x["id"]]
            b = round(mb * (ratings[x["id"]] - 3) / 2, 2)
            if b:
                x["score_parts"]["claude"] = b
        if x["id"] in tb or x["id"] in ratings:
            x["score"] = round(sum(x["score_parts"].values()), 2)

def main():
    from .run_weekly import read_user_state
    from .common import load_yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    settings = load_yaml("settings.yaml")
    settings["_llm_on"] = True
    stats = {}
    if a.digest:
        t = monthly_digest(read_user_state(), settings, stats, force=a.force, dry=a.dry_run)
        print(json.dumps({"stats": stats, "taste": t}, indent=1, ensure_ascii=False))

if __name__ == "__main__":
    main()
