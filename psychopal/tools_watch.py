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

def releases(start, end, cfg):
    out, errors = [], []
    for repo in cfg.get("repos", []):
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
    return out, errors

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

def run(start, end, settings):
    cfg = load_yaml("tools.yaml")
    rel, errors = releases(start, end, cfg)
    try:
        jo = joss(start, end, cfg)
    except Exception as e:
        jo = []; errors.append(f"joss: {str(e)[:80]}")
    rel.sort(key=lambda c: c["date"], reverse=True)
    log(f"tools: {len(rel)} releases, {len(jo)} JOSS papers ({len(errors)} errors)")
    return rel[: settings.get("tool_releases_max", 6)] + jo[:3], {"releases": len(rel), "joss": len(jo), "errors": errors[:10]}
