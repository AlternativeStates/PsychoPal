"""New tools (no LLM): GitHub releases of watched repos + new JOSS papers in the neuro space."""
import datetime as dt, os, re, xml.etree.ElementTree as ET
from .common import get, item_id, clean, load_yaml, log

def _gh_headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h

def _is_patch(tag):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", tag or "")
    return bool(m and m.group(3) and int(m.group(3)) > 0)

def releases(start, end, cfg, repos=None):
    out, errors = [], []
    for repo in repos if repos is not None else cfg.get("repos", []):
        try:
            rs = get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 5}, headers=_gh_headers(), timeout=20, retries=1)
        except Exception as e:
            errors.append(f"{repo}: {str(e)[:60]}"); continue
        for r in rs:
            if r.get("draft") or r.get("prerelease"):
                continue
            d = (r.get("published_at") or "")[:10]
            try:
                if not (start <= dt.date.fromisoformat(d) <= end):
                    continue
            except ValueError:
                continue
            tag = r.get("tag_name") or r.get("name") or ""
            if cfg.get("major_only", True) and _is_patch(tag):
                continue
            name = repo.split("/")[1]
            notes = re.sub(r"[#*`>\[\]]", " ", r.get("body") or "")
            out.append({"id": item_id("tool", f"{repo}@{tag}"), "kind": "tool_release", "stream": "tools",
                        "title": f"{name} {tag} released", "url": r.get("html_url"), "repo": repo, "version": tag, "date": d,
                        "venue": f"GitHub · {repo}", "description": clean(notes)[:1400]})
            break                                 # releases come newest first: one card per tool per week
    return out, errors

SKIP_REPO_RE = re.compile(r"awesome|course|tutorial|homework|assignment|lecture|workshop|bootcamp|notes|cheatsheet|thesis|dotfiles|\.github\.io", re.I)
# a new repo must look like data-analysis software for brain data, not a demo, an AI-agent plug-in or a robotics toy
ANALYSIS_RE = re.compile(r"\beeg\b|\bmeg\b|\bfmri\b|\bmri\b|\bpet\b|\bbids\b|neuroimag|electrophysiolog|\becog\b|\bieeg\b|\btms\b|connectiv|"
                         r"preprocess|pipeline|toolbox|toolkit|analys[ie]s|signal processing|time[- ]series|spectral|source (locali[sz]ation|imaging)|"
                         r"segmentation|registration|kinetic|tractograph|diffusion|spike sorting|decoding|modell?ing|simulat", re.I)
NOT_TOOL_RE = re.compile(r"\bclaude\b|\bllm\b|chatgpt|\bgpt\b|\bagents?\b|\bskill\b|\bprompt|\bdrone|\brobot|\bgame\b|fruit fly|drosophila|"
                         r"c\. elegans|zebrafish|crypto|trading", re.I)

def new_repos(start, end, cfg, shown_ids):
    """Brand-new open-source neuro tools: repositories created in the last ~2 months under neuro topics, ranked by stars."""
    since = start - dt.timedelta(days=cfg.get("new_repo_days", 60))
    seen, cands, errors = set(cfg.get("repos", [])), [], []
    for topic in cfg.get("new_repo_topics", []):
        try:
            r = get("https://api.github.com/search/repositories", headers=_gh_headers(), timeout=20, retries=1,
                    params={"q": f"topic:{topic} created:>={since} fork:false archived:false", "sort": "stars", "order": "desc", "per_page": 10})
        except Exception as e:
            errors.append(f"search {topic}: {str(e)[:60]}"); continue
        for x in r.get("items", []):
            full = x.get("full_name") or ""
            if full in seen or SKIP_REPO_RE.search(full) or not x.get("description") or x.get("stargazers_count", 0) < cfg.get("new_repo_min_stars", 10):
                continue
            blurb = f"{full} {x['description']} {' '.join(x.get('topics') or [])}"
            if NOT_TOOL_RE.search(blurb) or not ANALYSIS_RE.search(blurb):
                continue
            seen.add(full)
            cands.append(x)
    cands.sort(key=lambda x: -x.get("stargazers_count", 0))
    out = []
    for x in cands:
        cid = item_id("tool", f"repo:{x['full_name']}")
        if cid in shown_ids:
            continue
        readme = ""
        try:
            readme = get(f"https://api.github.com/repos/{x['full_name']}/readme", headers={**_gh_headers(), "Accept": "application/vnd.github.raw"},
                         expect="text", timeout=20, retries=1)
            readme = re.sub(r"!\[[^\]]*\]\([^)]*\)|<[^>]+>|[#*`>|]", " ", readme)     # drop badges, html and markdown symbols
        except Exception:
            pass
        stars, created = x.get("stargazers_count", 0), (x.get("created_at") or "")[:10]
        out.append({"id": cid, "kind": "tool_repo", "stream": "tools", "title": f"{x['name']}: {clean(x['description'])[:110]}",
                    "url": x.get("html_url"), "repo": x["full_name"], "date": created, "stars": stars,
                    "venue": f"GitHub · new · ★ {stars}", "why_picked": f"★ {stars} on GitHub since {created}" + (f" · {x['language']}" if x.get("language") else ""),
                    "description": clean(f"{x['description']}. Topics: {', '.join(x.get('topics') or [])}. Language: {x.get('language')}. {readme}")[:1400]})
        if len(out) >= cfg.get("new_repos_max", 2):
            break
    return out, errors

def learned_repos(liked_ids):
    """Repos of tool cards you 👍 or saved: their future releases are watched too (stored nowhere else; derived each week)."""
    from .common import STATE, load_jsonl
    out = []
    for r in load_jsonl(os.path.join(STATE, "items.jsonl")):
        if r.get("id") in liked_ids and r.get("repo") and r.get("kind") in ("tool_repo", "tool_paper", "tool_release"):
            out.append(r["repo"])
    return out

JOSS = "https://joss.theoj.org/papers/published.atom"
def joss(start, end, cfg):
    """JOSS publishes ~20 papers per feed page (a few days), so page back until we pass the window start (max 6 pages)."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    kw = re.compile(cfg.get("joss_keywords", "neuro|eeg|fmri|brain"), re.I)
    out = []
    for page in range(1, 7):
        xml = get(JOSS, params={"page": page}, expect="text", timeout=30, retries=1)
        root = ET.fromstring(xml.encode("utf-8"))
        entries = root.findall("a:entry", ns)
        oldest = None
        for e in entries:
            title = clean(e.findtext("a:title", "", ns))
            d = (e.findtext("a:published", "", ns) or e.findtext("a:updated", "", ns) or "")[:10]
            link = next((l.get("href") for l in e.findall("a:link", ns) if l.get("href")), "")
            content = e.find("a:content", ns)
            summ = clean(" ".join(content.itertext())) if content is not None else ""
            try:
                day = dt.date.fromisoformat(d)
            except ValueError:
                continue
            oldest = day if oldest is None or day < oldest else oldest
            if not (start <= day <= end) or not kw.search(title + " " + summ):
                continue
            out.append({"id": item_id("tool", link), "kind": "tool_paper", "stream": "tools", "title": title, "url": link, "date": d,
                        "venue": "Journal of Open Source Software", "description": f"{title}. {summ}"[:1400]})
        if not entries or (oldest and oldest < start):
            break
    return out

def run(start, end, settings, liked_ids=(), shown_ids=()):
    cfg = load_yaml("tools.yaml")
    learned = [r for r in dict.fromkeys(learned_repos(set(liked_ids))) if r not in cfg.get("repos", [])]
    rel, errors = releases(start, end, cfg, list(cfg.get("repos", [])) + learned)
    try:
        jo = joss(start, end, cfg)
    except Exception as e:
        jo = []; errors.append(f"joss: {str(e)[:80]}")
    try:
        nr, e2 = new_repos(start, end, cfg, set(shown_ids))
        errors += e2
    except Exception as e:
        nr = []; errors.append(f"new repos: {str(e)[:80]}")
    rel.sort(key=lambda c: c["date"], reverse=True)
    log(f"tools: {len(rel)} releases ({len(learned)} repos learned from your 👍), {len(jo)} JOSS papers, {len(nr)} new repos ({len(errors)} errors)")
    return (rel[: settings.get("tool_releases_max", 6)] + jo[:3] + nr,
            {"releases": len(rel), "learned_repos": learned, "joss": len(jo), "new_repos": len(nr), "errors": errors[:10]})
