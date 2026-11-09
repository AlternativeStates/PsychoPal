"""Cheap, deterministic relevance scoring (ported from the Neurodynamics Digest fetch_candidates.py and generalised).
No LLM: keyword groups from queries.json + modality/venue/named-researcher bonuses + TF-IDF similarity to Claudio's profile and likes."""
import json, math, os, re
from collections import Counter
from .common import CONFIG, strip_accents, load_json

REVIEW_PT = {"Review", "Systematic Review", "Meta-Analysis", "Scoping Review", "review", "systematic-review", "meta-analysis",
             "review-article", "Review article", "Practice Guideline"}
REVIEW_TITLE_RE = re.compile(r"\b(review|meta-analys[ie]s|meta-analytic|systematic review|scoping review|umbrella review|perspective|primer|"
                             r"state of the art|roadmap|tutorial|narrative synthesis|overview)\b", re.I)
REVIEW_ABS_RE = re.compile(r"\b(this review|we review|in this perspective|this perspective|we systematically reviewed|systematic search of|"
                           r"meta-analysis of|we searched (pubmed|medline|embase|web of science))\b", re.I)
DROP_RE = re.compile(r"^(abstracts? (of|from)|proceedings of|abstract book|correction|corrigendum|erratum|retraction|retracted|expression of concern|"
                     r"withdrawn|notice of|author correction|publisher correction|elife assessment|reviewer #?\d|author response|"
                     r"peer review|decision letter|response to reviewers)", re.I)
DROP_PT = {"Congress", "Published Erratum", "Retraction of Publication", "Retracted Publication", "Expression of Concern", "erratum", "retraction", "paratext"}
LOW_PT = {"Editorial", "Comment", "Letter", "News", "editorial", "letter", "comment", "Interview", "Biography"}
CASE_RE = re.compile(r"\bcase (report|series)\b|\ba case of\b|:\s*a case\b|\bcase study of an?\b|\bin an? \d+[- ]year[- ]old\b", re.I)
CASE_PT = {"Case Reports", "case-report"}
SYSTEMATIC_RE = re.compile(r"systematic|meta-analy|umbrella review|pooled analysis", re.I)
# tools/methods: worm, fly, fish and rodent-only toolboxes are off-target for a human EEG/MEG/fMRI/PET reader
NONHUMAN_RE = re.compile(r"c\. elegans|caenorhabditis|drosophila|zebrafish|\bmice\b|\bmouse\b|\brats?\b|rodent|murine|macaque|marmoset|"
                         r"organoid|cell culture|in vitro|calcium imaging|two-photon", re.I)
NEURO_TOOL_RE = re.compile(r"\bbrains?\b|\bneur|\bcortex\b|\bcortical\b|\beeg\b|\bmeg\b|\bfmri\b|\bcereb|\bcogniti|\bpsychiatr|\bhippocamp|"
                           r"\bconnectom|\belectrophysiolog|\bmagnetoencephalograph|\belectroencephalograph|\bintracranial|\bwhite matter\b|"
                           r"\bgr[ae]y matter\b|\btractograph|\bspinal cord\b", re.I)
HUMAN_RE = re.compile(r"\bhuman|participants|patients|volunteers|\bpeople\b|healthy (adults|controls)|clinical", re.I)
ADDICTION_RE = re.compile(r"addict|substance use|gambling|alcohol|nicotine|smok|cocaine|opioid|methamphetamine|craving|tobacco", re.I)

def load_config(path=None):
    with open(path or os.path.join(CONFIG, "queries.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    compile_cfg(cfg)
    return cfg

def compile_cfg(cfg):
    cfg["_neg"] = {re.compile(k, re.I).pattern: re.compile(v, re.I)
                   for k, v in cfg.get("keyword_negations", {}).items() if not k.startswith("_")}
    for g in cfg["groups"]:
        g["_kw"] = [re.compile(k, re.I) for k in g.get("keywords", [])]
        g["_also"] = re.compile(g["also_requires_re"], re.I) if g.get("also_requires_re") else None
    cfg["_mods"] = [(m["label"], re.compile(m["re"], re.I), m["bonus"]) for m in cfg["modalities"]]
    cfg["_pet"] = next(rx for lab, rx, _ in cfg["_mods"] if lab == "PET")
    v = cfg["venues"]
    cfg["_t1"], cfg["_t2"] = {x.lower() for x in v["tier1"]}, {x.lower() for x in v["tier2"]}
    cfg["_anchor"] = {x.lower() for x in cfg.get("anchor_venues", [])}
    cfg["_byname"] = {g["name"]: g for g in cfg["groups"]}
    eng = cfg["scoring"].get("engineering_re")
    cfg["_eng"] = re.compile(eng, re.I) if eng else None

def _hit(cfg, k, s, full):
    if not k.search(s):
        return False
    neg = cfg["_neg"].get(k.pattern)
    return not (neg and neg.search(full))

def kw_hit(cfg, g, title, abst):
    full = title + " " + abst
    if any(_hit(cfg, k, title, full) for k in g["_kw"]):
        return 1.0
    return 0.6 if any(_hit(cfg, k, abst, full) for k in g["_kw"]) else 0.0

def imaging_hit(cfg, text):
    return any(rx.search(text) for _, rx, _ in cfg["_mods"])

def _special(cfg, req, text, raw, name):
    if req == "@imaging":
        return 1.0 if imaging_hit(cfg, text) else 0.0
    if req == "@pet":
        return 1.0 if cfg["_pet"].search(text) else 0.0
    if req == "@addiction":
        return 1.0 if ADDICTION_RE.search(text) else 0.0
    if req == "@self":
        return raw[name]
    return raw.get(req, 0.0)

def group_scores(cfg, rec):
    """{group_name: weighted hit} for every group the record matches."""
    title, abst = rec.get("title") or "", rec.get("abstract") or ""
    text = title + " " + abst
    raw = {g["name"]: kw_hit(cfg, g, title, abst) for g in cfg["groups"]}
    api_groups = rec.get("groups") or set()
    out = {}
    for g in cfg["groups"]:
        n, s = g["name"], raw[g["name"]]
        if g.get("all_of"):
            parts = [_special(cfg, req, text, raw, n) for req in g["all_of"]]
            s = min(parts) if parts else 0.0
            named_groups = [r for r in g["all_of"] if not r.startswith("@")]
            if s and named_groups:
                s = max(s, raw.get(named_groups[0], 0.0))
        if s and g.get("require_imaging") and not imaging_hit(cfg, text):
            s = 0.0
        if s and g["_also"] is not None and not g["_also"].search(text):
            s = 0.0
        if n in api_groups and not g.get("keywords") and not g.get("all_of"):
            s = max(s, 1.0)      # affiliation / journal groups: the API match is the signal
        elif n in api_groups and not abst and rec.get("source") == "pubmed" and not s:
            s = 0.4              # PubMed matched title/MeSH but we have no abstract to re-check
        if s:
            out[n] = round(g["weight"] * s, 2)
    return out

def modality(cfg, title, text):
    f = cfg.get("modality_abstract_factor", 0.6)
    for label, rx, bonus in cfg["_mods"]:
        if rx.search(title):
            return label, bonus
    for label, rx, bonus in cfg["_mods"]:
        if len(rx.findall(text)) >= 2:   # abstract-only: need >= 2 mentions so a passing reference doesn't count
            return label, round(bonus * f, 2)
    return "", 0.0

def named_hits(cfg, authors):
    hits = []
    for p in cfg.get("named_researchers", {}).get("people", []):
        sns = [p["surname"]] + p.get("alt_surnames", [])
        for a in authors or []:
            a0 = strip_accents(a).lower()
            for sn in sns:
                sn0 = strip_accents(sn).lower()
                if re.search(r"(^|\s)" + re.escape(sn0) + r"$", a0):
                    given = a0[: -len(sn0)].strip()
                    f0 = strip_accents(p["first"]).lower()
                    if (given.startswith(f0) if p.get("strict") else given[:1] == f0[:1]):
                        hits.append(p["name"])
    return sorted(set(hits))

def clean_venue(venue):
    return re.sub(r"\s*\(.*\)$", "", (venue or "").lower().strip())

def venue_bonus(cfg, venue):
    v = clean_venue(venue)
    if v in cfg["_t1"]:
        return cfg["venues"]["tier1_bonus"]
    if v in cfg["_t2"]:
        return cfg["venues"]["tier2_bonus"]
    return 0.0

def is_review(rec):
    if set(rec.get("pubtypes") or []) & REVIEW_PT:
        return True
    if REVIEW_TITLE_RE.search(rec.get("title") or ""):
        return True
    return bool(REVIEW_ABS_RE.search((rec.get("abstract") or "")[:600]))

def is_case_report(rec):
    return bool(CASE_RE.search(rec.get("title") or "") or set(rec.get("pubtypes") or []) & CASE_PT)

def is_narrative_review(rec):
    """A review that is not systematic / meta-analytic (those keep their value wherever they are published)."""
    return is_review(rec) and not SYSTEMATIC_RE.search((rec.get("title") or "") + " " + " ".join(rec.get("pubtypes") or []))

def is_dropped(rec):
    return bool(DROP_RE.search(rec.get("title") or "") or (set(rec.get("pubtypes") or []) & DROP_PT))

WORD_RE = re.compile(r"[\w\-]+", re.U)
def matched_terms(cfg, rec, groups, n=2):
    """The words that made a record match its strongest keyword groups (title first), e.g. ['psilocybin', 'SV2A']."""
    title, abst = rec.get("title") or "", rec.get("abstract") or ""
    out = []
    for g in groups:
        for text in (title, abst):
            for k in cfg["_byname"][g].get("_kw", []):
                m = k.search(text)
                if not m:
                    continue
                a, b = m.start(), m.end()          # widen the match to whole words: 'psilocyb' -> 'psilocybin'
                while a > 0 and (text[a - 1].isalnum() or text[a - 1] == "-"):
                    a -= 1
                while b < len(text) and (text[b].isalnum() or text[b] == "-"):
                    b += 1
                w = text[a:b].strip(" -")
                if len(w) > 32 or len(w) < 3:
                    continue
                w = w if (w.isupper() or re.search(r"[A-Z].*[A-Z]|\d", w)) else w.lower()
                if w.lower() not in {x.lower() for x in out}:
                    out.append(w)
                break
            else:
                continue
            break
        if len(out) >= n:
            break
    return out

# ---------------- TF-IDF profile model ----------------
STOP = set("the a an of and or in on for to with by from at as is are was were be been this that these those we our their its into than not no "
           "using use used based study studies via between within across during after before over under both more less can may also which "
           "it they there here such data results result effects effect show shows showed new two one".split())

def toks(s):
    return [w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", strip_accents(s).lower()) if w not in STOP]

class Tfidf:
    def __init__(self, docs):
        self.df = Counter()
        for d in docs:
            self.df.update(set(toks(d)))
        self.n = max(1, len(docs))
    def vec(self, s):
        tf = Counter(toks(s))
        v = {w: (1 + math.log(c)) * math.log((self.n + 1) / (self.df.get(w, 0) + 1)) for w, c in tf.items()}
        nrm = math.sqrt(sum(x * x for x in v.values())) or 1
        return {w: x / nrm for w, x in v.items()}
    @staticmethod
    def cos(a, b):
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(w, 0) for w, x in a.items())

def rec_text(r):
    return (r.get("title") or "") + " " + (r.get("abstract") or "")[:1500]

class Profile:
    """Similarity of a text to Claudio's own papers (config/profile_papers.json) and to liked / disliked items."""
    def __init__(self, corpus_texts, liked_texts=(), disliked_texts=()):
        prof = load_json(os.path.join(CONFIG, "profile_papers.json"), default={"papers": []})["papers"]
        self.profile_texts = [(p.get("title") or "") + " " + (p.get("abstract") or "") for p in prof]
        self.profile_titles = [(p.get("title") or "") if not p.get("manual") else "" for p in prof]   # manual interest seeds are not papers
        self.tf = Tfidf(self.profile_texts + list(liked_texts) + list(disliked_texts) + list(corpus_texts))
        self.pv = [self.tf.vec(t) for t in self.profile_texts]
        self.lv = [self.tf.vec(t) for t in liked_texts]
        self.dv = [self.tf.vec(t) for t in disliked_texts]
    def nearest(self, text):
        """(similarity, title) of the closest of Claudio's own papers."""
        v = self.tf.vec(text)
        best = max(((Tfidf.cos(v, x), i) for i, x in enumerate(self.pv) if self.profile_titles[i]), default=(0.0, -1))
        return (best[0], self.profile_titles[best[1]]) if best[1] >= 0 else (0.0, "")
    def sims(self, text):
        v = self.tf.vec(text)
        mx = lambda vs: max((Tfidf.cos(v, x) for x in vs), default=0.0)
        return mx(self.pv), mx(self.lv), mx(self.dv)

def score_record(cfg, r, prof, start=None):
    """Full score for one merged record -> dict(score, parts, groups, stream, modality, named, profile_sim)."""
    import datetime as dt
    sc = cfg["scoring"]
    text = rec_text(r)
    gs = group_scores(cfg, r)
    mod, mbonus = modality(cfg, r.get("title") or "", text)
    decay = sc.get("group_decay", [1.0])
    kw = sum(v * (decay[i] if i < len(decay) else decay[-1]) for i, v in enumerate(sorted(gs.values(), reverse=True)))
    named = named_hits(cfg, r.get("authors") or [])
    psim, lsim, dsim = prof.sims(text)
    cac = cfg.get("current_analyses", {})
    ca_hits = [gs[g] / cfg["_byname"][g]["weight"] for g in cac.get("groups", []) if gs.get(g)]
    parts = {"keywords": round(kw, 2), "modality": mbonus,
             "named": cfg["named_researchers"]["weight"] if named else 0.0,
             "venue": venue_bonus(cfg, r.get("venue")),
             "current_work": round(cac.get("bonus", 0) * max(ca_hits), 2) if ca_hits else 0.0,
             "profile_sim": round(sc["profile_weight"] * psim, 2),
             "liked": round(sc["liked_weight"] * lsim, 2),
             "disliked": round(sc["disliked_weight"] * dsim, 2)}
    if len(r.get("abstract") or "") < 100:
        parts["no_abstract"] = sc["no_abstract_penalty"]
    if set(r.get("pubtypes") or []) & LOW_PT:
        parts["low_type"] = sc["low_type_penalty"]
    if cfg["_eng"] and cfg["_eng"].search(text) and not any(g.startswith("psychedelic") for g in gs):
        parts["engineering"] = sc["engineering_penalty"]
    if any(x in (r.get("venue") or "").lower() for x in sc.get("repository_venues", [])):
        parts["repository"] = sc["repository_penalty"]
    if start is not None:
        try:
            if (start - dt.date.fromisoformat((r.get("date") or "")[:10])).days > sc.get("stale_pubdate_days", 60):
                parts["stale"] = sc["stale_penalty"]
        except ValueError:
            pass
    # profile anchoring: groups flagged 'anchored' only count for top venues AND papers close enough to the profile
    anchored = [g for g in gs if cfg["_byname"][g].get("anchored")]
    if anchored and not (clean_venue(r.get("venue")) in cfg["_anchor"] and max(psim, lsim) >= sc.get("anchored_min_profile_sim", 0.06)):
        for g in anchored:
            gs.pop(g)
    # the broad-journal ("Wider science") group only decides the stream when nothing else matched; a broad-journal paper that is
    # also on one of the reader's topics competes as a normal paper (its journal already earns the venue bonus)
    wider = [g for g in gs if cfg["_byname"][g].get("stream") == "wider"]
    if wider and len(wider) < len(gs):
        for g in wider:
            gs.pop(g)
    kw = sum(v * (decay[i] if i < len(decay) else decay[-1]) for i, v in enumerate(sorted(gs.values(), reverse=True)))
    parts["keywords"] = round(kw, 2)
    if is_case_report(r):
        parts["case_report"] = sc.get("case_report_penalty", -4.0)
    elif is_narrative_review(r) and not venue_bonus(cfg, r.get("venue")):
        parts["weak_review"] = sc.get("weak_review_penalty", -2.5)    # narrative reviews outside strong journals
    low = sc.get("low_quality_venues", [])
    if any(x in (r.get("venue") or "").lower() for x in low):
        parts["low_quality_venue"] = sc.get("low_quality_penalty", -3.0)
    if not gs and not named:
        parts["off_topic"] = sc["off_topic_penalty"]
    stream = None
    if gs:
        top = max(gs, key=lambda k: gs[k])
        stream = cfg["_byname"][top]["stream"]
        if stream == "tools":   # a methods paper that is ALSO strongly on-topic stays a paper
            rest = {k: v for k, v in gs.items() if cfg["_byname"][k]["stream"] != "tools"}
            if rest and max(rest.values()) >= gs[top]:
                stream = cfg["_byname"][max(rest, key=lambda k: rest[k])]["stream"]
    if stream == "tools" and NONHUMAN_RE.search(text) and not HUMAN_RE.search(text):
        parts["nonhuman_tool"] = sc.get("nonhuman_tool_penalty", -4.0)
    if stream == "tools" and not NEURO_TOOL_RE.search(text):
        parts["not_neuro_tool"] = sc.get("not_neuro_tool_penalty", -6.0)   # e.g. prostate or cardiac MRI software
    return {"score": round(sum(parts.values()), 2), "parts": parts, "groups": sorted(gs, key=lambda k: -gs[k]),
            "stream": stream, "modality": mod, "named": named, "profile_sim": round(psim, 3), "liked_sim": round(lsim, 3)}
