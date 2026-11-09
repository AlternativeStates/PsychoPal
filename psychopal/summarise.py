"""Card text writer. The ONLY weekly Claude call.
- Sends only items that have no cached text in state/summaries.jsonl (so recurring funding/conference cards cost 0 after the first week).
- Groups items 10 per request and submits them as one Message Batch (50% cheaper); falls back to direct calls if batching is off.
- Structured JSON output (one object per item): post, why_you, tags (+ deadline/status/event_dates for opportunity pages).
- Any failure (no key, refusal, timeout, bad JSON) -> extractive fallback from the abstract/description, retried next week."""
import json, os, re, time, datetime as dt
from .common import CONFIG, STATE, load_jsonl, append_jsonl, log, anthropic_client

CACHE = os.path.join(STATE, "summaries.jsonl")

STYLE = """You write the text for cards in PsychoPal, a weekly scrolling feed (like LinkedIn posts) for one researcher whose profile is below.
For each item return:
- post: 2-3 plain sentences, at most 60 words. Say what was done and what was found (papers) or what it offers, for whom and by when
  (funding, conferences, courses, software). Include concrete numbers if the source gives them (n, design, effect sizes, amounts, dates).
  Neutral and accurate; no hype, no exclamation marks, no emojis, no hashtags. UK English. Do not repeat the title.
- why_you: one sentence, at most 25 words, on why this matters for THIS reader specifically (link it to his work or plans). Be concrete,
  never generic. If it is only loosely relevant, say so honestly.
- tags: 1-3 short lowercase topic tags (e.g. "psilocybin", "pet", "eeg complexity", "fellowship").
- deadline / event_dates / status: only for funding, conference and training items; use "" for anything else or if not stated.
  deadline = next application/abstract deadline as YYYY-MM-DD; event_dates = e.g. "12-15 Jun 2027"; status = open|upcoming|closed|unknown.
Use only the information given in the item. If the text is too thin to say anything specific, write a short, factual post anyway."""

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "post": {"type": "string"}, "why_you": {"type": "string"},
                       "tags": {"type": "array", "items": {"type": "string"}},
                       "deadline": {"type": "string"}, "event_dates": {"type": "string"}, "status": {"type": "string"}},
        "required": ["id", "post", "why_you", "tags", "deadline", "event_dates", "status"],
        "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}

def load_cache():
    return {r["id"]: r for r in load_jsonl(CACHE)}

def item_text(it):
    """Compact, model-facing description of one item (trimmed to keep tokens low)."""
    k = it["kind"]
    lines = [f"id: {it['id']}", f"type: {k}", f"title: {it.get('title', '')}"]
    for key, label in (("venue", "venue"), ("date", "date"), ("organiser", "organiser"), ("funder", "funder"),
                       ("location", "location"), ("event_dates", "dates"), ("deadline", "deadline"), ("amount", "amount"),
                       ("version", "version"), ("repo", "repository")):
        if it.get(key):
            lines.append(f"{label}: {it[key]}")
    if it.get("authors"):
        au = it["authors"]
        lines.append("authors: " + (", ".join(au[:3]) + (f" … {au[-1]}" if len(au) > 3 else "")))
    body = it.get("abstract") or it.get("summary") or it.get("description") or it.get("page_text") or it.get("notes") or ""
    lines.append("text: " + re.sub(r"\s+", " ", body)[:1500])
    return "\n".join(lines)

def extractive(it):
    """No-LLM fallback: first sentences of the abstract/description, ~55 words."""
    if it.get("page_text") is not None:   # watched pages: raw page text reads badly, use the curated note instead
        note = (it.get("notes") or "").strip()
        return {"id": it["id"], "post": note or "Check the page for current dates and details.", "why_you": "", "tags": [], "ai": False}
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

def _params(model, effort, profile, chunk):
    user = "Write the cards for these items:\n\n" + "\n\n---\n\n".join(item_text(it) for it in chunk)
    return {"model": model, "max_tokens": 4000 + 300 * len(chunk),
            "system": STYLE + "\n\nREADER PROFILE:\n" + profile,
            "thinking": {"type": "between_tools"},   # Sonnet 5.5: no reasoning needed to summarise; keeps output tokens low
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": user}]}

def _parse(msg):
    if getattr(msg, "stop_reason", None) in ("refusal", "max_tokens"):
        raise ValueError(f"stop_reason={msg.stop_reason}")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text)["items"]

def _usage(acc, msg):
    u = getattr(msg, "usage", None)
    if u:
        acc["input_tokens"] += u.input_tokens or 0
        acc["output_tokens"] += u.output_tokens or 0

def _direct(client, model, effort, profile, chunk, i, out, usage):
    """Synchronous call; if the request is rejected (400), retry once without the optional thinking setting."""
    import anthropic
    params = _params(model, effort, profile, chunk)
    for attempt in (0, 1):
        try:
            msg = client.messages.create(**params)
            _usage(usage, msg)
            for r in _parse(msg):
                out[r["id"]] = r
            return
        except anthropic.BadRequestError as e:
            usage.setdefault("errors", []).append(f"c{i}: BadRequest {str(e)[:200]}")
            if attempt == 0 and "thinking" in params:
                params.pop("thinking")
                continue
            return
        except Exception as e:
            usage.setdefault("errors", []).append(f"c{i}: {type(e).__name__}: {str(e)[:200]}")
            return

def call_claude(items, cfg, stats):
    import anthropic
    client = anthropic_client()
    with open(os.path.join(CONFIG, "profile.md"), encoding="utf-8") as f:
        profile = f.read()
    n = cfg.get("items_per_request", 10)
    chunks = [items[i:i + n] for i in range(0, len(items), n)]
    model, effort = cfg.get("model", "claude-sonnet-5-5"), cfg.get("effort", "low")
    out, usage = {}, {"input_tokens": 0, "output_tokens": 0, "requests": len(chunks), "batch": bool(cfg.get("use_batch", True))}
    stats["llm"] = usage
    if cfg.get("use_batch", True):
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        batch = client.messages.batches.create(requests=[
            Request(custom_id=f"c{i}", params=MessageCreateParamsNonStreaming(**_params(model, effort, profile, ch)))
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
        retry = []
        for res in client.messages.batches.results(batch.id):
            i = int(res.custom_id[1:])
            if res.result.type != "succeeded":
                err = getattr(getattr(res.result, "error", None), "error", None) or getattr(res.result, "error", None)
                usage.setdefault("errors", []).append(f"{res.custom_id}: {res.result.type} {str(err)[:160]}")
                retry.append(i)
                continue
            msg = res.result.message
            _usage(usage, msg)
            try:
                for r in _parse(msg):
                    out[r["id"]] = r
            except Exception as e:
                usage.setdefault("errors", []).append(f"{res.custom_id}: {e}")
        for i in retry:   # e.g. a parameter rejected for this model: one direct retry per chunk, without the thinking setting
            _direct(client, model, effort, profile, chunks[i], i, out, usage)
    else:
        for i, ch in enumerate(chunks):
            _direct(client, model, effort, profile, ch, i, out, usage)
    log(f"llm: {len(out)}/{len(items)} items written; tokens in={usage['input_tokens']} out={usage['output_tokens']}")
    return out

def summarise(items, cfg, stats, limit=None):
    """Attach post / why_you / tags to every item (in place). Returns the list."""
    cache = load_cache()
    todo = [it for it in items if it["id"] not in cache or (not cache[it["id"]].get("ai") and cfg.get("enabled", True))]
    todo = todo[: min(limit or 10 ** 9, cfg.get("max_items_to_llm", 80))]
    fresh = {}
    if todo and cfg.get("enabled", True) and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            fresh = call_claude(todo, cfg, stats)
        except Exception as e:
            stats["llm"] = {**stats.get("llm", {}), "error": f"{type(e).__name__}: {str(e)[:300]}"}
            log("llm: FAILED " + stats["llm"]["error"])
    elif todo:
        stats["llm"] = {"skipped": "no ANTHROPIC_API_KEY or llm disabled", "items": len(todo)}
    today, model = str(dt.date.today()), cfg.get("model")
    new_rows = []
    for it in items:
        if it["id"] in fresh:
            r = fresh[it["id"]]
            row = {"id": it["id"], "post": r["post"].strip(), "why_you": r["why_you"].strip(), "tags": r.get("tags", [])[:3],
                   "deadline": r.get("deadline", ""), "event_dates": r.get("event_dates", ""), "status": r.get("status", ""),
                   "ai": True, "model": model, "written": today}
            new_rows.append(row); cache[it["id"]] = row
        elif it["id"] not in cache or (it.get("refresh_text") and not cache[it["id"]].get("ai")):
            row = {**extractive(it), "written": today}
            if it["id"] not in cache:
                new_rows.append(row)
            cache[it["id"]] = row
        c = cache[it["id"]]
        it["post"], it["why_you"], it["ai_tags"], it["ai"] = c.get("post", ""), c.get("why_you", ""), c.get("tags", []), c.get("ai", False)
        for k in ("deadline", "event_dates", "status"):
            if c.get(k) and not it.get(k):
                it[k] = c[k]
    if new_rows:
        append_jsonl(CACHE, new_rows)
    return items
