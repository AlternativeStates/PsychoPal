"""Monthly discovery sweep: ONE Claude call with web search proposes funding calls, conferences and courses that are not yet on the
watchlists. Proposals are checked (the URL must load, the name/URL must be new) and appended to config/*.yaml with `source: sweep`;
they then appear in the feed with a "NEW ON YOUR RADAR" badge. 👎 on such a card hides it for good.

  python -m psychopal.monthly_sweep            # runs only on the first Monday of the month unless --force
  python -m psychopal.monthly_sweep --force --dry-run
Cost: ~8 web searches ($0.08) + ~60-100k tokens on Sonnet 5.5 (~$0.20-0.30) per month."""
import argparse, datetime as dt, json, os, re
from .common import CONFIG, STATE, load_yaml, append_jsonl, log
from .opportunities import load_watchlist, page_text, KIND_FILES

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
Prefer official pages (funder/organiser websites), not aggregators. Propose at most 12 items in total, only ones you are confident exist.

End your answer with a JSON array in a ```json fenced block, one object per item:
{{"kind": "funding|conference|training", "name": "...", "url": "https://official page", "organiser": "...", "why": "one sentence on relevance",
  "deadline": "YYYY-MM-DD or empty", "event_dates": "e.g. 12-15 Jun 2027 or empty", "location": "city, country or online or empty"}}"""

def first_monday(today):
    return today.weekday() == 0 and today.day <= 7

def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def run_sweep(cfg, dry=False):
    import anthropic
    today = dt.date.today()
    wl = []
    for kind, fn in KIND_FILES.items():   # include disabled entries so they are never re-proposed
        for e in (load_yaml(fn).get("items") or []):
            wl.append({**e, "kind": kind})
    tracked = "\n".join(f"- [{e['kind']}] {e['name']} ({e.get('url', '')})" for e in wl)
    with open(os.path.join(CONFIG, "profile.md"), encoding="utf-8") as f:
        profile = f.read()
    client = anthropic.Anthropic()
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": cfg.get("max_searches", 8)}]
    messages = [{"role": "user", "content": ASK.format(profile=profile, tracked=tracked, today=today)}]
    usage = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0}
    msg = None
    for _ in range(4):   # resume server-side tool loops (pause_turn) a few times at most
        msg = client.messages.create(model=cfg.get("model", "claude-sonnet-5-5"), max_tokens=12000, tools=tools,
                                     output_config={"effort": "medium"}, messages=messages)
        u = msg.usage
        usage["input_tokens"] += u.input_tokens or 0
        usage["output_tokens"] += u.output_tokens or 0
        stu = getattr(u, "server_tool_use", None)
        usage["web_searches"] += getattr(stu, "web_search_requests", 0) or 0 if stu else 0
        if msg.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": msg.content}]
    text = "\n".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S) or re.search(r"(\[\s*\{.*\}\s*\])", text, re.S)
    props = json.loads(m.group(1)) if m else []
    known_names = {norm(e["name"]) for e in wl}
    known_urls = {norm(re.sub(r"^https?://(www\.)?", "", e.get("url", "")).rstrip("/")) for e in wl}
    added, rejected = [], []
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
    stats = {"date": str(today), "proposed": len(props), "added": [a["name"] for a in added], "rejected": rejected, "usage": usage}
    append_jsonl(os.path.join(STATE, "sweeps.jsonl"), [stats])
    log(f"sweep: {len(props)} proposed, {len(added)} added, {len(rejected)} rejected; usage {usage}")
    return stats

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
    ap.add_argument("--force", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = (load_yaml("settings.yaml").get("sweep") or {})
    if not cfg.get("enabled", True):
        log("sweep disabled in settings.yaml"); return
    if not (a.force or first_monday(dt.date.today())):
        log("not the first Monday of the month; skipping (use --force)"); return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("no ANTHROPIC_API_KEY; skipping sweep"); return
    print(json.dumps(run_sweep(cfg, a.dry_run), indent=1, ensure_ascii=False))

if __name__ == "__main__":
    main()
