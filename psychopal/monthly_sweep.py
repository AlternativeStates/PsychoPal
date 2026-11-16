"""Monthly discovery sweep: ONE Claude call (Sonnet 5.5 with web search, at most 6 searches) proposes funding calls, conferences and
courses that are not yet on the watchlists. Proposals are checked (the URL must load, the name/URL must be new) and appended to
config/*.yaml with `source: sweep`; they then appear in the feed with a "NEW ON YOUR RADAR" badge. 👎 on such a card hides it for good.
Runs on the first weekly run of each month that actually sweeps (state/sweeps.jsonl), so backup or manual runs are covered too.

  python -m psychopal.monthly_sweep                     # sweeps if none ran this month (and Claude is on)
  python -m psychopal.monthly_sweep --force --dry-run   # test: sweep now, print proposals, change nothing (does not count)
Cost: 6 web searches ($0.06) + ~40-80k tokens on Sonnet 5.5 (~$0.15-0.25) per month."""
import argparse, datetime as dt, json, os, re
from . import llm
from .common import CONFIG, STATE, load_yaml, load_jsonl, append_jsonl, log, anthropic_client
from .opportunities import page_text, KIND_FILES

SWEEPS = os.path.join(STATE, "sweeps.jsonl")

ASK = """You are helping a researcher keep a watchlist of opportunities. His profile:

{profile}

He already tracks these (do NOT propose them again):
{tracked}

Search the web for opportunities he is NOT tracking yet that are open, upcoming, or announced for the next 12 months (today is {today}):
1. funding: postdoctoral / early-career fellowships and project grants he could apply for from the UK (UK, European, international,
   mental-health, neuroscience and psychedelic-research funders; Imperial College London internal schemes),
2. conference: scientific meetings in psychedelic research, neuropsychopharmacology, PET/neuroimaging, EEG/TMS-EEG, consciousness,
   computational neuroscience,
3. training: courses, workshops or summer schools on neuroimaging analysis, PET kinetic modelling, TMS-EEG, fMRI, EEG/MEG,
   computational neuroscience or psychiatry.
You have at most {searches} searches, so make each one count. Prefer official pages (funder/organiser websites), not aggregators.
Propose at most 10 items in total, only ones you are confident exist.

End your answer with a JSON array in a ```json fenced block, one object per item:
{{"kind": "funding|conference|training", "name": "...", "url": "https://official page", "organiser": "...", "why": "one sentence on relevance",
  "deadline": "YYYY-MM-DD or empty", "event_dates": "e.g. 12-15 Jun 2027 or empty", "location": "city, country or online or empty"}}"""

def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def parse_proposals(text):
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S) or re.search(r"(\[\s*\{.*\}\s*\])", text, re.S)
    if not m:
        return []
    props = json.loads(m.group(1))
    return [p for p in props if isinstance(p, dict)]

def ask_claude(client, cfg, prompt, usage):
    """The search conversation. Server-side searches can pause the turn (pause_turn): resume at most once, and never once the
    search cap is reached (max_uses is not reliably enforced across resumes)."""
    model, cap = cfg.get("model", "claude-sonnet-5-5"), int(cfg.get("max_searches", 6))
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": cap}]
    messages, msg = [{"role": "user", "content": prompt}], None
    for _ in range(2):
        params = {"model": model, "max_tokens": 12000, "tools": tools, "output_config": {"effort": cfg.get("effort", "medium")},
                  "messages": messages}
        th = llm.thinking_param(model, cfg.get("thinking", "adaptive"), cfg.get("effort", "medium"))
        if th:
            params["thinking"] = th
        msg = client.messages.create(**params)
        llm.account(usage, msg, model)
        if msg.stop_reason != "pause_turn" or usage.get("web_searches", 0) >= cap:
            break
        messages = [messages[0], {"role": "assistant", "content": msg.content}]
    if getattr(msg, "stop_reason", None) == "refusal":
        raise ValueError("refusal")
    # citations split the answer into several text blocks, sometimes inside the JSON: join them without separators
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

def run_sweep(cfg, dry=False, client=None):
    today = dt.date.today()
    wl = []
    for kind, fn in KIND_FILES.items():   # include disabled entries so they are never re-proposed
        for e in (load_yaml(fn).get("items") or []):
            wl.append({**e, "kind": kind})
    tracked = "\n".join(f"- [{e['kind']}] {e['name']} ({e.get('url', '')})" for e in wl)
    with open(os.path.join(CONFIG, "profile.md"), encoding="utf-8") as f:
        profile = f.read()
    model = cfg.get("model", "claude-sonnet-5-5")
    usage = {**llm.new_usage(), "cost_usd": 0.0}
    row = {"date": str(today), "ok": False, "dry_run": bool(dry), "model": model}
    added, rejected, props = [], [], []
    try:
        text = ask_claude(client or anthropic_client(), cfg,
                          ASK.format(profile=profile, tracked=tracked, today=today, searches=cfg.get("max_searches", 6)), usage)
        props = parse_proposals(text)
        known_names = {norm(e["name"]) for e in wl}
        known_urls = {norm(re.sub(r"^https?://(www\.)?", "", e.get("url", "")).rstrip("/")) for e in wl}
        for p in props:
            kind = p.get("kind")
            url = (p.get("url") or "").strip()
            if kind not in KIND_FILES or not url.startswith("http") or not p.get("name"):
                rejected.append((p.get("name"), "bad fields")); continue
            if norm(p["name"]) in known_names or norm(re.sub(r"^https?://(www\.)?", "", url).rstrip("/")) in known_urls:
                rejected.append((p["name"], "already tracked")); continue
            try:
                page_text(url)
            except Exception as e:
                rejected.append((p["name"], f"url failed: {str(e)[:60]}")); continue
            added.append(p)
            known_names.add(norm(p["name"]))
        if not dry:
            for p in added:
                write_entry(p, today)
        row["ok"] = True
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        log("sweep: FAILED " + row["error"])
    cost = usage.pop("cost_usd")
    row.update({"proposed": len(props), "added": [a["name"] for a in added], "rejected": rejected, "usage": usage, "cost_usd": cost})
    append_jsonl(SWEEPS, [row])
    log(f"sweep: {len(props)} proposed, {len(added)} added, {len(rejected)} rejected; {usage['web_searches']} searches, ${cost:.3f}")
    return row

def write_entry(p, today):
    """Append a YAML block to the right watchlist (text append keeps the file's comments intact)."""
    fn = os.path.join(CONFIG, KIND_FILES[p["kind"]])
    slug = re.sub(r"[^a-z0-9]+", "-", p["name"].lower()).strip("-")[:40]
    q = lambda s: json.dumps(str(s), ensure_ascii=False)   # JSON strings are valid YAML scalars
    lines = [f"  - id: sweep-{slug}", f"    name: {q(p['name'])}", f"    url: {q(p['url'])}",
             f"    {'funder' if p['kind'] == 'funding' else 'organiser'}: {q(p.get('organiser', ''))}",
             f"    notes: {q(p.get('why', ''))}", f"    source: sweep", f"    added: {today}"]
    for k in ("deadline", "event_dates", "location"):
        if p.get(k):
            lines.append(f"    {k}: {q(p[k])}")
    with open(fn, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(lines) + "\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="sweep even if one ran this month or the budget is low")
    ap.add_argument("--dry-run", action="store_true", help="print proposals, change no watchlist (does not count as this month's sweep)")
    a = ap.parse_args()
    settings = load_yaml("settings.yaml")
    cfg = settings.get("sweep") or {}
    if not (settings.get("llm") or {}).get("enabled", True) or not cfg.get("enabled", True):
        log("sweep: Claude or the sweep is switched off in settings.yaml"); return
    if not (a.force or llm.due_this_month(load_jsonl(SWEEPS))):
        log("sweep: already done this month; skipping (use --force)"); return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("sweep: no ANTHROPIC_API_KEY; skipping"); return
    left = llm.budget_left((settings.get("llm") or {}).get("monthly_budget_usd", 1.0))
    if left < cfg.get("min_budget_left_usd", 0.40) and not a.force:
        log(f"sweep: only ${left:.2f} of this month's Claude budget left; skipping"); return
    print(json.dumps(run_sweep(cfg, a.dry_run), indent=1, ensure_ascii=False))

if __name__ == "__main__":
    main()
