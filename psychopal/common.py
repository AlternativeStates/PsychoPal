"""Shared helpers for PsychoPal (HTTP with retries, normalisation, I/O). Ported from the Neurodynamics Digest pipeline."""
import json, os, re, sys, time, random, hashlib, unicodedata, datetime as dt
import requests

for _s in (sys.stdout, sys.stderr):   # Windows consoles default to cp1252; never crash on ★ or Greek letters
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config")
STATE = os.path.join(ROOT, "state")
SITE = os.path.join(ROOT, "site")
OUT = os.path.join(ROOT, "out")
# Contact address for the "polite pool" of scholarly APIs (NCBI, OpenAlex, Crossref). Set PSYCHOPAL_MAILTO in GitHub variables.
MAILTO = os.environ.get("PSYCHOPAL_MAILTO", "psychopal@example.invalid")
UA = f"PsychoPal/1.0 (weekly literature digest; mailto:{MAILTO})"

_session = None
def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA})
    return _session

class FetchError(Exception):
    pass

def http(method, url, *, params=None, data=None, timeout=30, retries=4, backoff=1.5, expect="json", headers=None, log=None):
    """GET/POST with retries + exponential backoff on timeouts, 429 and 5xx. Raises FetchError after the last try."""
    last = None
    for attempt in range(retries + 1):
        try:
            r = session().request(method, url, params=params, data=data, timeout=timeout, headers=headers)
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else backoff * (2 ** attempt) + random.random()
                last = f"HTTP {r.status_code}"
                if attempt < retries:
                    time.sleep(min(wait, 60)); continue
                raise FetchError(last)
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code}: {r.text[:200]}")
            if expect == "json":
                return r.json()
            return r.text
        except (requests.Timeout, requests.ConnectionError, ValueError) as e:
            last = f"{type(e).__name__}: {str(e)[:150]}"
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt) + random.random()); continue
            raise FetchError(last)
    raise FetchError(last or "unknown error")

def get(url, **kw):
    return http("GET", url, **kw)

def post(url, **kw):
    return http("POST", url, **kw)

def norm_doi(d):
    if not d:
        return ""
    d = d.strip().lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.rstrip(".")

def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))

def norm_title(t):
    t = strip_accents(re.sub(r"<[^>]+>", " ", t or "")).lower()
    return re.sub(r"[^a-z0-9]+", "", t)[:150]

def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()

def load_jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return out

def append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def write_json(path, obj, indent=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)

def load_yaml(name):
    import yaml
    with open(os.path.join(CONFIG, name), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def daterange_ok(d, start, end):
    try:
        x = dt.date.fromisoformat(d[:10])
    except Exception:
        return True  # unknown date: keep (API already filtered by window)
    return start <= x <= end

def parse_date(s):
    """Best-effort ISO date (YYYY, YYYY-MM or YYYY-MM-DD) -> date, or None."""
    if not s:
        return None
    s = str(s)[:10]
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return dt.datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None

def week_id(d):
    """ISO week label for the Monday-Sunday window containing d, e.g. 2026-W45."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"

def last_full_week(today=None):
    """(monday, sunday) of the most recent complete Monday-Sunday week before today."""
    today = today or dt.date.today()
    monday_this = today - dt.timedelta(days=today.weekday())
    start = monday_this - dt.timedelta(days=7)
    return start, start + dt.timedelta(days=6)

def item_id(kind, key):
    """Stable short id for any card: kind prefix + hash of its natural key (DOI, URL, NCT, scheme id...)."""
    h = hashlib.sha1(f"{kind}|{(key or '').lower().strip()}".encode("utf-8")).hexdigest()[:10]
    return f"{kind[:2]}-{h}"

def anthropic_client():
    """Claude API client. Reads ANTHROPIC_API_KEY; if the key is not scoped to a Console workspace, set ANTHROPIC_WORKSPACE_ID
    (wrkspc_...) and it is sent on every request as the anthropic-workspace-id header."""
    import anthropic
    ws = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws}) if ws else anthropic.Anthropic()

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)
