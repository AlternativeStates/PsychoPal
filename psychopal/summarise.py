"""Card text writer: one Claude Message Batch per week (Haiku 5.5), switched off unless llm.enabled is true in config/settings.yaml.
- Papers get a structured Aim / Methods / Results / Conclusion summary (`sections`) + "why it matters for you" + tags; other cards
  (trials, tools, funding, conferences, courses) get a short post. Opportunity pages also get status / dates / relevance.
- Without Claude (off, no key, budget used up, refusal, timeout, bad JSON) papers get the scripted summary of their abstract
  (psychopal/abstracts.py), recomputed every run and never cached; failed items are retried the next week.
- Only items with no cached Claude text (state/summaries.jsonl, current PROMPT_VERSION) are sent; items too thin to summarise
  (body under 200 characters) are never sent. 10 items per request, all requests in one batch (50% cheaper)."""
import json, os, re, time, datetime as dt
from . import llm
from .common import CONFIG, STATE, load_jsonl, append_jsonl, log, anthropic_client
from .abstracts import structure, as_text, sentences, trim, tidy, LIMITS

CACHE = os.path.join(STATE, "summaries.jsonl")
PROMPT_VERSION = 2          # bump when STYLE/SCHEMA change: cached rows of older versions are rewritten (this week's items only)
PAPER_KINDS = ("paper", "catchup", "tool_paper")
PARTS = (("Aim", "aim"), ("Methods", "methods"), ("Results", "results"), ("Conclusion", "conclusion"))
MIN_BODY = 200

STYLE = """You write the text for cards in PsychoPal, a weekly feed for one researcher whose profile is below. Return one object per
item, using only the facts given in the item. UK English; neutral and precise; no hype, no exclamation marks, no emojis, no hashtags;
never repeat the title. Use "" for every field that does not apply.

Papers (type paper, catchup or tool_paper): summarise the abstract in four parts, each in plain full sentences:
- aim: at most 25 words: the question or goal.
- methods: at most 30 words: design, sample (n, species or population), drug and dose, modality, main analysis.
- results: at most 45 words and 2 sentences: the main findings, keeping the reported numbers (n, effect sizes, p-values).
- conclusion: at most 25 words: what the authors conclude, with any caveat they state.
For reviews and meta-analyses, methods = scope and number of studies, results = the pooled or main findings. post = "" for papers.

All other types (trial, tool_release, tool_repo, funding, conference, training): post = 1-2 sentences, at most 40 words: what it is
or offers, for whom, and when. tool_repo items are brand-new GitHub repositories: say what the software does, for which data or
modality, and how mature it looks. aim, methods, results and conclusion = "".

Every item:
- why_you: one sentence, at most 25 words, on why it matters for THIS reader (link it to his work or plans). Concrete, never
  generic; if it is only loosely relevant, say so. Items marked "section: wider science" are deliberately outside his fields: say in
  at most 20 words why a neuroscientist might find it interesting, without forcing a link to his work.
- tags: 1-3 short lowercase topic tags (e.g. "psilocybin", "pet", "eeg complexity", "fellowship").

Funding, conference and training items (from their web page text; today's date is given):
- status: open | upcoming | closed | unknown.
- deadline: the next application or abstract deadline as YYYY-MM-DD, else "". event_dates: e.g. "12-15 Jun 2027", else "".
- If there is a deadline, end the post with it in words, e.g. "Next deadline: 5 March 2027."
- relevant: "no" only for a funding call he clearly cannot apply for (studentships, industry-only, wrong career stage or country)
  or one outside neuroscience, psychiatry and mental health; otherwise "yes".
Other types: status, deadline and event_dates = "", relevant = "yes"."""

_S = {"type": "string"}
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": _S, "aim": _S, "methods": _S, "results": _S, "conclusion": _S, "post": _S, "why_you": _S,
                       "tags": {"type": "array", "items": _S},
                       "status": {"type": "string", "enum": ["", "open", "upcoming", "closed", "unknown"]},
                       "deadline": _S, "event_dates": _S, "relevant": {"type": "string", "enum": ["yes", "no"]}},
        "required": ["id", "aim", "methods", "results", "conclusion", "post", "why_you", "tags", "status", "deadline", "event_dates",
                     "relevant"],
        "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}

def load_cache():
    return {r["id"]: r for r in load_jsonl(CACHE)}

def _body(it):
    return it.get("abstract") or it.get("summary") or it.get("description") or it.get("page_text") or it.get("notes") or ""

def sendable(it):
    """Watched pages and UKRI calls always go to Claude (dates, relevance); anything else needs a real abstract/description."""
    return it.get("page_text") is not None or bool(it.get("_ukri")) or len(_body(it).strip()) >= MIN_BODY

def item_text(it, cfg=None):
    """Compact, model-facing description of one item."""
    k = it["kind"]
    lines = [f"id: {it['id']}", f"type: {k}", f"title: {it.get('title', '')}"]
    if it.get("stream") == "wider":
        lines.append("section: wider science")
    for key, label in (("venue", "venue"), ("date", "date"), ("organiser", "organiser"), ("funder", "funder"),
                       ("location", "location"), ("event_dates", "dates"), ("deadline", "deadline"), ("amount", "amount"),
                       ("version", "version"), ("repo", "repository")):
        if it.get(key) and not (key == "date" and k in PAPER_KINDS):
            lines.append(f"{label}: {it[key]}")
    if it.get("authors"):
        au = it["authors"]
        lines.append("authors: " + (", ".join(au[:3]) + (f" … {au[-1]}" if len(au) > 3 else "")))
    body = _body(it)
    body = tidy(body) if k in PAPER_KINDS or k == "trial" else re.sub(r"\s+", " ", body).strip()
    lines.append("text: " + body[: int((cfg or {}).get("text_chars", 2400))])
    return "\n".join(lines)

def extractive(it):
    """No-LLM card text: a structured summary of a paper's abstract, else the first sentences (~55 words) of the description."""
    if it.get("page_text") is not None:   # watched pages: raw page text reads badly, use the curated note instead
        note = (it.get("notes") or "").strip()
        return {"id": it["id"], "post": note or "Check the page for current dates and details.", "why_you": "", "tags": [], "ai": False}
    if it.get("kind") in PAPER_KINDS and it.get("abstract"):
        parts = structure(it["abstract"])
        if parts:
            return {"id": it["id"], "post": as_text(parts), "sections": parts, "why_you": "", "tags": [], "ai": False}
    body = re.sub(r"\s+", " ", it.get("abstract") or it.get("summary") or it.get("description") or it.get("notes") or "").strip()
    body = re.sub(r"^(abstract|background|introduction|objectives?|aims?)[:.\s]+", "", body, flags=re.I)
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z])", body)
    out = ""
    for s in sents:
        if len((out + " " + s).split()) > 55 and out:
            break
        out = (out + " " + s).strip()
    words = out.split()
    if len(words) > 70:
        out = " ".join(words[:60]) + "…"
    return {"id": it["id"], "post": out, "why_you": "", "tags": [], "ai": False}

def _params(cfg, profile, chunk, today):
    model, effort = cfg.get("model", "claude-haiku-5-5"), cfg.get("effort", "low")
    user = f"Today is {today}. Write the cards for these items:\n\n" + "\n\n---\n\n".join(item_text(it, cfg) for it in chunk)
    p = {"model": model, "max_tokens": 2000 + 400 * len(chunk),
         "system": STYLE + "\n\nREADER PROFILE:\n" + profile,
         "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
         "messages": [{"role": "user", "content": user}]}
    th = llm.thinking_param(model, cfg.get("thinking", "off"), effort)
    if th:
        p["thinking"] = th
    return p

def _parse(msg):
    if getattr(msg, "stop_reason", None) in ("refusal", "max_tokens"):
        raise ValueError(f"stop_reason={msg.stop_reason}")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text)["items"]

def _refused(msg, usage, tag):
    if getattr(msg, "stop_reason", None) != "refusal":
        return False
    cat = getattr(getattr(msg, "stop_details", None), "category", None)
    usage["refusals"] = usage.get("refusals", 0) + 1
    usage.setdefault("errors", []).append(f"{tag}: refusal" + (f" ({cat})" if cat else ""))
    return True

def _singles(client, cfg, profile, chunk, tag, out, usage, today):
    """A refused request: re-send its items one by one (at most 2 chunks per run), so only the item that trips the filter loses out."""
    if len(chunk) < 2 or usage.get("split_chunks", 0) >= 2:
        return
    usage["split_chunks"] = usage.get("split_chunks", 0) + 1
    for j, it in enumerate(chunk):
        _direct(client, cfg, profile, [it], f"{tag}.{j}", out, usage, today, split=False)

def _direct(client, cfg, profile, chunk, tag, out, usage, today, split=True):
    """Synchronous call; if the request is rejected (400), retry once without the optional thinking setting."""
    import anthropic
    params = _params(cfg, profile, chunk, today)
    for attempt in (0, 1):
        try:
            msg = client.messages.create(**params)
            llm.account(usage, msg, params["model"], batch=False)
            if _refused(msg, usage, tag):
                if split:
                    _singles(client, cfg, profile, chunk, tag, out, usage, today)
                return
            for r in _parse(msg):
                out[r["id"]] = r
            return
        except anthropic.BadRequestError as e:
            usage.setdefault("errors", []).append(f"{tag}: BadRequest {str(e)[:200]}")
            if attempt == 0 and "thinking" in params:
                params.pop("thinking")
                continue
            return
        except Exception as e:
            usage.setdefault("errors", []).append(f"{tag}: {type(e).__name__}: {str(e)[:200]}")
            return

def call_claude(items, cfg, stats):
    client = anthropic_client()
    with open(os.path.join(CONFIG, "profile.md"), encoding="utf-8") as f:
        profile = f.read()
    today = str(dt.date.today())
    n = cfg.get("items_per_request", 10)
    chunks = [items[i:i + n] for i in range(0, len(items), n)]
    model = cfg.get("model", "claude-haiku-5-5")
    out, usage = {}, {**llm.new_usage(), "model": model, "items": len(items), "requests": len(chunks),
                      "batch": bool(cfg.get("use_batch", True)), "cost_usd": 0.0}
    stats["llm"] = usage
    if cfg.get("use_batch", True):
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        batch = client.messages.batches.create(requests=[
            Request(custom_id=f"c{i}", params=MessageCreateParamsNonStreaming(**_params(cfg, profile, ch, today)))
            for i, ch in enumerate(chunks)])
        log(f"llm: batch {batch.id} with {len(chunks)} requests ({len(items)} items)")
        deadline = time.time() + 60 * cfg.get("batch_max_wait_min", 60)
        while True:
            batch = client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            if time.time() > deadline:
                client.messages.batches.cancel(batch.id)
                usage["error"] = "batch timed out; cancelled (items retried next week)"
                log("llm: " + usage["error"])
                return out
            time.sleep(20)
        retry, refused = [], []
        for res in client.messages.batches.results(batch.id):
            i = int(res.custom_id[1:])
            if res.result.type != "succeeded":
                err = getattr(getattr(res.result, "error", None), "error", None) or getattr(res.result, "error", None)
                usage.setdefault("errors", []).append(f"{res.custom_id}: {res.result.type} {str(err)[:160]}")
                retry.append(i)
                continue
            msg = res.result.message
            llm.account(usage, msg, model, batch=True)
            if _refused(msg, usage, res.custom_id):
                refused.append(i)
                continue
            try:
                for r in _parse(msg):
                    out[r["id"]] = r
            except Exception as e:
                usage.setdefault("errors", []).append(f"{res.custom_id}: {e}")
        for i in retry:   # e.g. a parameter rejected for this model: one direct retry per chunk, without the thinking setting
            _direct(client, cfg, profile, chunks[i], f"c{i}", out, usage, today)
        for i in refused:
            _singles(client, cfg, profile, chunks[i], f"c{i}", out, usage, today)
    else:
        for i, ch in enumerate(chunks):
            _direct(client, cfg, profile, ch, f"c{i}", out, usage, today)
    log(f"llm: {len(out)}/{len(items)} items written; tokens in={usage['input_tokens']} out={usage['output_tokens']} "
        f"cost ${usage['cost_usd']:.4f}")
    return out

def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def _row(r, it, model, today):
    """A cache row from Claude's answer, or None if it is unusable (the item then keeps the scripted text and is retried)."""
    secs = []
    for label, key in PARTS:
        t = _clean(r.get(key))
        if t:
            secs.append([label, trim(sentences(t), LIMITS[label] + 8)])   # catch over-long output
    if len(secs) < 2:
        secs = []
    post = _clean(r.get("post")) or (as_text(secs) if secs else "")
    if not post:
        return None
    row = {"id": it["id"], "post": post, "why_you": _clean(r.get("why_you")), "tags": [_clean(t) for t in r.get("tags", [])][:3],
           "deadline": r.get("deadline", ""), "event_dates": r.get("event_dates", ""), "status": r.get("status", ""),
           "relevant": r.get("relevant", ""), "ai": True, "v": PROMPT_VERSION, "model": model, "written": today}
    if secs:
        row["sections"] = secs
    return row

def fresh_enough(c):
    return bool(c and c.get("ai") and c.get("v", 1) >= PROMPT_VERSION)

def summarise(items, cfg, stats, limit=None, run_spent=0.0):
    """Attach post / sections / why_you / tags (and dates, status, relevance for opportunities) to every item, in place."""
    cache = load_cache()
    on = bool(cfg.get("enabled", True))
    todo = [it for it in items if on and not fresh_enough(cache.get(it["id"])) and sendable(it)]
    thin = sum(1 for it in items if on and not fresh_enough(cache.get(it["id"])) and not sendable(it))
    todo = todo[: min(limit or 10 ** 9, cfg.get("max_items_to_llm", 80))]
    fresh = {}
    if not on:
        stats["llm"] = {"off": True}
    elif todo and os.environ.get("ANTHROPIC_API_KEY"):
        budget = cfg.get("monthly_budget_usd", 1.0)
        left = llm.budget_left(budget, run_spent)
        if left <= 0:
            stats["llm"] = {"budget_stop": f"${budget:.2f} monthly budget used up", "items": len(todo)}
            log("llm: " + stats["llm"]["budget_stop"] + "; scripted text this week")
        else:
            try:
                fresh = call_claude(todo, cfg, stats)
            except Exception as e:
                stats["llm"] = {**stats.get("llm", {}), "error": f"{type(e).__name__}: {str(e)[:300]}"}
                log("llm: FAILED " + stats["llm"]["error"])
    elif todo:
        stats["llm"] = {"skipped": "no ANTHROPIC_API_KEY", "items": len(todo)}
    else:
        stats["llm"] = {**llm.new_usage(), "items": 0, "requests": 0, "cost_usd": 0.0}
    if on and thin:
        stats["llm"]["skipped_thin"] = thin
    today, model = str(dt.date.today()), cfg.get("model", "claude-haiku-5-5")
    new_rows = []
    for it in items:
        if it["id"] in fresh:
            row = _row(fresh[it["id"]], it, model, today)
            if row:
                new_rows.append(row); cache[it["id"]] = row
        c = cache.get(it["id"])
        if not fresh_enough(c):
            c = extractive(it)            # scripted text: recomputed every run, never cached
        it["post"], it["why_you"], it["ai_tags"], it["ai"] = c.get("post", ""), c.get("why_you", ""), c.get("tags", []), c.get("ai", False)
        if c.get("sections"):
            it["sections"] = c["sections"]
        for k in ("deadline", "event_dates", "status", "relevant"):
            if c.get(k) and not it.get(k):
                it[k] = c[k]
    if new_rows:
        append_jsonl(CACHE, new_rows)
    if fresh:
        stats["llm"]["written"] = len(new_rows)
    return items
