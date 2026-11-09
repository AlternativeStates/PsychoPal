"""Funding, conferences and training (no LLM here; text and dates are written/extracted by summarise.py).
Sources: config/funding.yaml, conferences.yaml, training.yaml (curated watchlists, extended by the monthly sweep) + the UKRI funding RSS feed.
Each watched page is fetched weekly; the deadline-relevant text is hashed. A NEW entry or a CHANGED page becomes an item for Claude
(extracts deadline / dates / status + writes the card); the extracted facts are kept in state/opportunities.json.
Cards (no spam): new entry, page updated, or 8/4/2 weeks before a deadline. Everything with a known deadline feeds the deadline strip."""
import datetime as dt, hashlib, html, os, re, xml.etree.ElementTree as ET
from .common import STATE, CONFIG, get, load_json, write_json, load_yaml, item_id, clean, log, parse_date

KIND_FILES = {"funding": "funding.yaml", "conference": "conferences.yaml", "training": "training.yaml"}
STATE_FILE = os.path.join(STATE, "opportunities.json")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 PsychoPal/1.0"
KEY_RE = re.compile(r"deadline|closing date|closes|close on|apply by|applications? (open|close|due)|submission|abstracts?|registration|"
                    r"call (opens|closes)|open for applications|dates?|\b20\d\d\b", re.I)
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"

def load_watchlist():
    out = []
    for kind, fn in KIND_FILES.items():
        path = os.path.join(CONFIG, fn)
        if not os.path.exists(path):
            continue
        for e in (load_yaml(fn).get("items") or []):
            if e.get("disabled"):
                continue
            e = dict(e)
            e["kind"] = kind
            e["key"] = f"{kind}:{e['id']}"
            out.append(e)
    return out

# ---------------- eligibility ----------------
def eligibility(e, profile, today):
    """(show?, note). Hides schemes that are clearly closed to Claudio; otherwise a short note when facts are missing."""
    el = e.get("eligibility") or {}
    if not el:
        return True, ""
    phd = parse_date(profile.get("phd_awarded"))
    mx = el.get("max_years_post_phd")
    if mx:
        if phd:
            yrs = (today - phd).days / 365.25
            if yrs > mx + 0.25:
                return False, ""
        else:
            note = f"≤{mx} yrs post-PhD"
            return True, note
    if el.get("requires_moving_country") and profile.get("open_to_moving_abroad") is False:
        return False, ""
    if el.get("requires_moving_country"):
        return True, "requires moving country"
    return True, ""

# ---------------- page fetch + change detection ----------------
def page_text(url):
    h = get(url, expect="text", timeout=25, retries=1, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml"})
    h = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form|iframe)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?is)<!--.*?-->", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|tr|div)>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    lines = [re.sub(r"[ \t\r\f\v]+", " ", l).strip() for l in t.split("\n")]
    return "\n".join(l for l in lines if len(l) > 2)

def relevant(text, limit=1600):
    """Lines most likely to carry dates/deadlines (keeps tokens and hash noise low)."""
    lines = text.split("\n")
    keep, n = [], 0
    for i, l in enumerate(lines):
        if KEY_RE.search(l) or re.search(rf"\b\d{{1,2}}(st|nd|rd|th)? ({MONTHS})|({MONTHS})[a-z]* \d{{1,2}}\b", l, re.I):
            chunk = " ".join(lines[max(0, i - 1): i + 2])
            if chunk not in keep:
                keep.append(chunk); n += len(chunk)
        if n > limit:
            break
    head = " ".join(lines[:6])[:300]
    return (head + "\n" + "\n".join(keep))[: limit + 300]

def digest(s):
    s = re.sub(r"\s+", " ", s.lower())
    s = re.sub(r"(cookie|accept all|privacy|©|copyright)[^.]{0,80}", "", s)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]

# ---------------- UKRI feed ----------------
UKRI_FEED = "https://www.ukri.org/opportunity/feed/"
UKRI_KEEP = re.compile(r"\b(MRC|Medical Research Council)\b|fellowship|early[- ]career|new investigator|career development|mental health|"
                       r"neuro|psychiatr|psycholog|brain|pharmacolog|imaging|biomedical", re.I)
UKRI_DROP = re.compile(r"\b(studentship|doctoral|PhD students?|CDT|centre for doctoral|innovation loans?|business|SME|procurement|"
                       r"Innovate UK|STFC|EPSRC only|AHRC|NERC|agri|crop|livestock|net zero|quantum|space)\b", re.I)

def ukri(start, end, seen):
    xml = get(UKRI_FEED, expect="text", timeout=30, retries=2)
    root = ET.fromstring(xml.encode("utf-8"))
    out = []
    for it in root.iter("item"):
        title = clean(it.findtext("title")); link = (it.findtext("link") or "").strip()
        desc = clean(html.unescape(it.findtext("description") or ""))
        pub = it.findtext("pubDate") or ""
        try:
            d = dt.datetime.strptime(pub[:16], "%a, %d %b %Y").date()
        except ValueError:
            d = None
        if not link or (d and not (start - dt.timedelta(days=7) <= d <= end)):
            continue
        text = f"{title} {desc}"
        if not UKRI_KEEP.search(text) or UKRI_DROP.search(text):
            continue
        cid = item_id("funding", link)
        if cid in seen:
            continue
        out.append({"id": cid, "kind": "funding", "title": title, "url": link, "funder": "UKRI", "date": str(d or ""),
                    "description": desc, "reason": "NEW CALL · UKRI", "_ukri": True})
    return out

# ---------------- main ----------------
def collect(start, end, settings, week, shown_ids, disliked):
    """Returns (items_for_llm_and_cards, ctx). Call finalise(items, ctx) after summarise()."""
    today = dt.date.today()
    st = load_json(STATE_FILE, default={"entries": {}, "first_run_done": False})
    profile = load_yaml("profile.yaml") if os.path.exists(os.path.join(CONFIG, "profile.yaml")) else {}
    items, stats = [], {"watched": 0, "fetched": 0, "fetch_failed": [], "changed": 0, "new": 0, "hidden_ineligible": 0}
    for e in load_watchlist():
        stats["watched"] += 1
        show, note = eligibility(e, profile, today)
        if not show:
            stats["hidden_ineligible"] += 1; continue
        rec = st["entries"].get(e["key"], {})
        if rec.get("card_id") in disliked:      # 👎 on this entry's card = stop showing it
            continue
        snippet, h = None, None
        if e.get("url") and not e.get("no_fetch"):     # no_fetch: the site blocks automated checks; dates come from the YAML
            try:
                snippet = relevant(page_text(e["url"]))
                h = digest(snippet)
                stats["fetched"] += 1
            except Exception as ex:
                stats["fetch_failed"].append(f"{e['id']}: {str(ex)[:80]}")
        is_new = not rec.get("extracted")          # never successfully read by Claude yet (also retries after an LLM outage)
        changed = (not is_new) and h is not None and rec.get("hash") and h != rec.get("hash") and rec.get("hash_week") != week
        if is_new:
            stats["new"] += 1
        if changed:
            stats["changed"] += 1
        version = (h or rec.get("hash") or "nohash")
        base = {"kind": e["kind"], "entry": e["key"], "title": e["name"], "url": e.get("url", ""), "funder": e.get("funder") or e.get("organiser", ""),
                "organiser": e.get("organiser", ""), "location": e.get("location", ""), "eligibility_note": note,
                "notes": e.get("notes", ""), "deadline": e.get("deadline") or rec.get("deadline", ""),
                "event_dates": e.get("event_dates") or rec.get("event_dates", ""), "status": rec.get("status", "")}
        if (is_new or changed) and snippet is not None:
            # this version of the page gets (re)read by Claude: dates + card text. Card id changes with the page version.
            base.update({"id": item_id(e["kind"], f"{e['key']}|{version}"), "description": (e.get("notes", "") + "\n" + snippet).strip(),
                         "page_text": snippet, "reason": "NEW ON YOUR RADAR" if is_new else "PAGE UPDATED", "_extract": True,
                         "_hash": h, "deadline": e.get("deadline") or regex_deadline(snippet, today),
                         "event_dates": e.get("event_dates", ""), "status": ""})
            items.append(base)
        else:
            base.update({"id": rec.get("card_id") or item_id(e["kind"], f"{e['key']}|{version}"), "description": e.get("notes", ""),
                         "_hash": h})
            items.append(base)
        if h is not None:
            rec_new = {**rec, "hash": h, "hash_week": week if (is_new or changed) else rec.get("hash_week", week), "seen": str(today)}
            st["entries"][e["key"]] = rec_new
    try:
        u = ukri(start, end, shown_ids)
        items += u[: settings.get("ukri_max", 6)]
        stats["ukri"] = len(u)
    except Exception as ex:
        stats["ukri_error"] = f"{type(ex).__name__}: {str(ex)[:120]}"
    items = [i for i in items if i["id"] not in disliked and i.get("entry", "") not in disliked]
    log(f"opportunities: {stats['watched']} watched, {stats['fetched']} fetched, {stats['new']} new, {stats['changed']} changed, "
        f"{len(stats['fetch_failed'])} fetch failures, ukri {stats.get('ukri', stats.get('ukri_error'))}")
    return items, {"state": st, "stats": stats, "week": week, "today": today}

MONTH_N = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
DATE_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTHS})[a-z]*\.?,?\s+(20\d\d)\b|\b({MONTHS})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I)
DEADLINE_CTX = re.compile(r"(deadline|closing date|closes|close on|apply by|submissions? (deadline|close)|abstracts? due)[^\n]{0,80}", re.I)

def regex_deadline(text, today):
    """Free fallback when Claude is unavailable: first future date that follows a deadline-like word."""
    for m in DEADLINE_CTX.finditer(text or ""):
        d = DATE_RE.search(m.group(0))
        if not d:
            continue
        try:
            if d.group(1):
                day, mon, yr = int(d.group(1)), MONTH_N[d.group(2)[:3].lower()], int(d.group(3))
            else:
                day, mon, yr = int(d.group(5)), MONTH_N[d.group(4)[:3].lower()], int(d.group(6))
            x = dt.date(yr, mon, day)
        except (ValueError, KeyError):
            continue
        if x >= today:
            return str(x)
    return ""

def _days(s, today):
    d = parse_date(s) if s and len(str(s)) >= 10 else None
    return (d - today).days if d else None

def finalise(items, ctx, settings, shown_ids):
    """After summarise(): store extracted dates, decide which items become cards, build the deadline strip."""
    from .summarise import load_cache
    cache = load_cache()
    st, today, week = ctx["state"], ctx["today"], ctx["week"]
    first_run = not st.get("first_run_done")
    any_ai = False
    cd = sorted(settings.get("countdown_weeks", [8, 4, 2]), reverse=True)
    cards, strip = [], []
    for it in items:
        key = it.get("entry")
        if key:
            rec = st["entries"].setdefault(key, {})
            if it.get("_extract"):
                for k in ("deadline", "event_dates", "status"):
                    if it.get(k):
                        rec[k] = it[k]
                rec["card_id"] = it["id"]
                if it.get("ai"):
                    rec["extracted"] = True
                    any_ai = True
            it["deadline"] = it.get("deadline") or rec.get("deadline", "")
            it["event_dates"] = it.get("event_dates") or rec.get("event_dates", "")
        days = _days(it.get("deadline"), today)
        if days is not None and 0 <= days <= settings.get("deadline_strip_days", 90):
            strip.append({"id": it["id"], "name": it["title"], "kind": it["kind"], "deadline": it["deadline"][:10], "url": it.get("url", ""),
                          "label": "abstracts" if it["kind"] == "conference" else "deadline"})
        show = False
        if it.get("_ukri"):
            show = True
        elif it.get("_extract"):
            # first run: everything is "new", so only surface what is actionable soon (the rest waits for its countdown)
            show = (not first_run) or (days is not None and 0 <= days <= 120) or it.get("status") == "open"
        if not show and days is not None and days >= 0 and key:
            for w in cd:
                if abs(days - 7 * w) <= 3:
                    cdid = item_id(it["kind"], f"{key}|countdown|{w}|{it['deadline'][:10]}")
                    if cdid not in shown_ids:
                        prev = cache.get(rec.get("card_id") or "", {})   # reuse the text written for this entry's page version
                        it = {**it, "id": cdid, "reason": f"DEADLINE IN {w} WEEKS", "post": it.get("post") or prev.get("post") or it.get("notes", ""),
                              "why_you": it.get("why_you") or prev.get("why_you", ""), "ai_tags": it.get("ai_tags") or prev.get("tags", [])}
                        show = True
                    break
        if days is not None and days < 0 and not it.get("_ukri"):
            show = False      # already closed
        if show:
            cards.append(it)
    if any_ai:
        st["first_run_done"] = True
    write_json(STATE_FILE, st)
    order = {"funding": 0, "conference": 1, "training": 2}
    cards.sort(key=lambda c: (order.get(c["kind"], 9), _days(c.get("deadline"), today) if _days(c.get("deadline"), today) is not None else 999))
    caps = {"funding": 8, "conference": 5, "training": 5}
    out, n = [], {}
    for c in cards:
        n[c["kind"]] = n.get(c["kind"], 0) + 1
        if n[c["kind"]] <= caps.get(c["kind"], 5):
            out.append(c)
    strip.sort(key=lambda d: d["deadline"])
    return out, strip

if __name__ == "__main__":
    # python -m psychopal.opportunities --check : verify every watchlist URL loads and show what would be hashed
    import sys
    if "--check" in sys.argv:
        for e in load_watchlist():
            if e.get("no_fetch"):
                print(f"SKIP {e['kind']:10} {e['id']:28} (no_fetch: dates entered by hand, deadline {e.get('deadline') or '-'})"); continue
            try:
                t = relevant(page_text(e["url"]))
                print(f"OK   {e['kind']:10} {e['id']:28} {len(t):5} chars  {t[:90]!r}")
            except Exception as ex:
                print(f"FAIL {e['kind']:10} {e['id']:28} {e['url']}  {str(ex)[:80]}")
