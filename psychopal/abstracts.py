"""Scripted, structured card summary from an abstract (no LLM): Aim · Methods · Results · Conclusion.

1. Structured abstracts (PubMed labels, Europe PMC <h4> headings, "Background: ..." / "METHODS ..." in OpenAlex text): the
   labelled sections are mapped onto the four standard parts.
2. Unstructured abstracts: each sentence gets a score for each part from cue phrases ("here we", "we recruited", "p < 0.05",
   "these findings suggest") and its position; the best labelling that keeps the parts in reading order is chosen (small DP).
Each part is trimmed to one or two sentences, so a card reads in about 20 seconds. Returns [] when the text is too thin."""
import re

PARTS = ("Aim", "Methods", "Results", "Conclusion")
LIMITS = {"Aim": 32, "Background": 32, "Methods": 34, "Results": 55, "Conclusion": 34}   # words per part

# ---- 1. labelled sections -------------------------------------------------------------------------------------------
_LAB = {   # canonical part: label phrases (longest first, matched case-insensitively)
    "aim": ["background and purpose", "background and aims?", "background and objectives?", "aims? and objectives?", "aims?/hypothesis",
            "objectives?", "aims?", "purpose", "rationale", "importance", "context", "hypothes[ie]s", "research question", "question",
            "introduction", "background", "problem", "motivation"],
    "methods": ["design, setting,? and participants", "design, setting,? participants", "materials? and methods", "methods and materials",
                "patients and methods", "subjects and methods", "participants and methods", "experimental approach", "experimental design",
                "study design", "design and methods", "setting and participants", "main outcomes? and measures?", "outcome measures?",
                "interventions?", "exposures?", "participants", "patients", "setting", "design", "measurements?", "methodology",
                "methods?", "approach", "procedures?", "data sources", "study selection", "data extraction", "search methods",
                "selection criteria", "eligibility criteria", "data collection and analysis", "evidence acquisition", "analysis"],
    "results": ["methods and results", "methods and findings", "main results", "key results", "key findings", "data synthesis",
                "evidence synthesis", "results?", "findings", "outcomes?", "observations"],
    "conclusion": ["conclusions? and relevance", "conclusions? and implications", "conclusions? and significance", "authors' conclusions",
                   "interpretations?", "conclusions?", "implications", "discussion", "clinical relevance", "summary", "significance"],
    "drop": ["trial registration", "clinical trial registration", "registration", "funding", "keywords?", "highlights", "limitations",
             "significance statement", "level of evidence", "systematic review registration", "prospero registration", "graphical abstract"],
}
_ALL = sorted(((p, k) for k, ps in _LAB.items() for p in ps), key=lambda x: -len(x[0]))
LABEL_RE = re.compile(r"(?<![\w-])(" + "|".join(p for p, _ in _ALL) + r")\s*(:|\.|-|–|—)?\s+(?=\S)", re.I)
_KIND = [(re.compile(rf"^(?:{p})$", re.I), k) for p, k in _ALL]

def _kind(label):
    for rx, k in _KIND:
        if rx.match(label):
            return k
    return None

AIM_LABEL = re.compile(r"^(objectives?|aims?|purpose|aims?/hypothesis|hypothes[ie]s|research question|question|background and (purpose|aims?|objectives?)|aims? and objectives?)$", re.I)

def labelled(text):
    """[(kind, label, text)] for a structured abstract, or [] if it does not look structured (needs >=2 labelled parts).
    A heading must be capitalised; without a colon it must start the text or follow a sentence end and be followed by a capital
    (OpenAlex style "Background Psilocybin ... Methods We ..."). Headings after the first must use the same style."""
    found = []
    for m in LABEL_RE.finditer(text):
        lab, sep, i = m.group(1), m.group(2), m.start()
        if not lab[:1].isupper():
            continue
        prev = text[:i].rstrip()[-1:]
        nxt = text[m.end():m.end() + 1]
        colon = sep == ":" or (sep in ("-", "–", "—") and lab.isupper())
        if found and found[0][3] and not colon:
            continue                                   # colon style: a bare capitalised word is just a word
        if not colon:
            if not ((prev == "" or prev in ".!?)]") and (nxt.isupper() or nxt.isdigit() or nxt in "([\"'")):
                continue
            if not (lab.isupper() or prev == "" or found):
                continue                               # a bare Title-case word mid-text only counts inside a structured abstract
        found.append((i, m.end(), lab, colon))
    found = [(i, j, lab, _kind(lab)) for i, j, lab, _ in found]
    if len({k for *_, k in found if k and k != "drop"}) < 2:
        return []
    out = []
    for n, (i, j, lab, kind) in enumerate(found):
        end = found[n + 1][0] if n + 1 < len(found) else len(text)
        body = text[j:end].strip()
        if body and kind:
            out.append((kind, lab, body))
    return out

# ---- 2. sentences and cue scores ------------------------------------------------------------------------------------
ABBR = re.compile(r"(\b(e\.g|i\.e|et al|vs|Fig|Figs|approx|ca|cf|Dr|Eq|No|resp|Ref|al|incl|Inc|Ltd|St|Jr|Sr|Ph\.D|U\.S|U\.K)\.|\b[A-Z]\.)$")

def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'])", text.strip())
    out = []
    for p in parts:
        if out and (ABBR.search(out[-1]) or re.search(r"\d\.$", out[-1]) and p[:1].isdigit()):
            out[-1] += " " + p
        else:
            out.append(p)
    return [s.strip() for s in out if len(s.split()) >= 3]

C = lambda p: re.compile(p, re.I)
AIM = C(r"^(here(in)?\b|in (this|the present|the current) (\w+ ){0,2}(study|work|paper|article|review|report|trial|analysis)|"
        r"this (\w+[- ]?){0,2}(study|work|paper|article|review|report|analysis|perspective) "
        r"(aim|aims|aimed|sought|investigat|examin|explor|assess|test|evaluat|report|describ|present|propos|introduc|address|synthesi[sz]|"
        r"summari[sz]|discuss|outlin|review|integrat|consider|argu)|"
        r"(we|the authors) (aim|aimed|sought|set out|ask|asked|hypothesi[sz]ed|predicted|test(ed)? whether|investigated? whether|examined whether|"
        r"explored? whether|wanted)|we (report|present|describe) (a|an|the|data|results|findings|first)\b|we (introduce|propose|develop) |"
        r"to (test|investigate|examine|assess|determine|address|evaluate|explore|characteri[sz]e|identify|understand|clarify|"
        r"compare|quantify|establish) )|\b(the|our) (aim|objective|purpose|goal)s? (of|was|is)\b")
GAP = C(r"\b(remains? (unclear|unknown|poorly|elusive|controversial|debated|to be|incompletely|largely|limited)|little is known|"
        r"(is|are) (not|poorly|incompletely) (understood|known|characteri[sz]ed)|it is (unclear|unknown)|(have|has) not (been|yet)|"
        r"(is|are) lacking|lack of|understudied|unexplored|open question|gap)\b")
BACKGROUND = C(r"\b(however\b|has (been|recently) (shown|proposed|suggested|implicated|associated)|have been (shown|proposed|suggested|implicated)|"
               r"previous (studies|work|research|findings)|growing (body|evidence)|it is thought|is thought to|"
               r"leading cause|is (a|an) (common|major|key|promising|important|novel)|are (increasingly|widely|commonly)|"
               r"(promising|emerging) (treatment|therapy|therapeutic|candidate))\b")
METHODS = C(r"\b(we (used|use|recruited|conducted|performed|analy[sz]ed|analy[sz]e|applied|apply|developed|develop|measured|assessed|collected|"
            r"combined|enrolled|randomi[sz]ed|scanned|recorded|trained|propose|introduce|present|leverage|leveraged|employed|acquired|"
            r"administered|compared|simulated|built|designed|included|followed|tested|examined|investigated|quantified|estimated|modelled|modeled|"
            r"systematically searched|searched|pooled|fitted)|participants|patients (were|received|underwent)|subjects|healthy (volunteers|adults|controls)|"
            r"randomi[sz]ed|double[- ]blind|placebo[- ]controlled|cross[- ]?over|open[- ]label|cohort|cross[- ]sectional|longitudinal|"
            r"sample of|\bn\s*=|data from|dataset|were (recruited|enrolled|randomi[sz]ed|scanned|assigned|included|collected|analy[sz]ed|measured|acquired)|"
            r"was (acquired|measured|assessed|recorded)|positron emission|\bpet\b|\bfmri\b|\beeg\b|\bmeg\b|\bmri\b|questionnaire|interviews?|"
            r"meta[- ]analysis|systematic review|mixed[- ]effects|regression|simulation|in vitro|in vivo|mice|rats)\b")
RESULTS = C(r"\b(we (found|find|show|showed|shown|observed|observe|identified|identify|report|demonstrate|demonstrated|reveal|revealed|detected)|"
            r"(results|analyses|findings) (showed|revealed|indicated|demonstrated)|revealed|showed|shown|resulted in|led to|was (associated|"
            r"correlated|linked|related|higher|lower|greater|reduced|increased|decreased|significant|not)|were (associated|correlated|higher|lower|"
            r"greater|reduced|increased|decreased|significant|not)|increase[ds]?|decrease[ds]?|reduc(ed|tion)|improv(ed|ement)|"
            r"significant(ly)?|no (significant )?(difference|effect|change|association)|did not|compared (with|to)|relative to|"
            r"outperform|achiev|accuracy|predicted|predicts|correlat|odds ratio|hazard ratio|effect size|cohen|95\s*%|\bci\b|"
            r"activated|inhibited|elicited|enhanced|attenuated|abolished|blocked|exhibited|displayed|emerged|persisted|normali[sz]ed|"
            r"mediated|explained|\bp\s*[<=>≤]|\br\s*=|\bd\s*=|\bOR\s*=|\bβ|response rate|remission)\b")
CONCL_START = C(r"^(in conclusion|in summary|to conclude|overall|together|taken together|collectively|altogether|thus|hence|therefore|"
                r"these (\w+ )?(findings|results|data|observations|outcomes)|our (\w+ )?(findings|results|data|study|work|observations)|"
                r"this (study|work|finding|evidence|framework|approach|model) (suggests?|shows?|demonstrates?|provides?|highlights?|indicates?|"
                r"supports?|establishes|offers|opens|identifies|reveals?|underscores?)|we conclude|we propose that|this suggests|such (findings|results))")
CONCL_ANY = C(r"\b(suggest(s|ing)? that|indicat(e|es|ing) that|support(s|ing)? (the|a) (role|notion|hypothesis|use)|highlight(s|ing)? the|"
              r"implications? for|warrant(s|ed)?|future (studies|research|trials|work)|may (help|inform|guide|offer|provide|represent|"
              r"be useful)|potential (target|biomarker|treatment|therapeutic)|pave the way|open new avenues)\b")
LIMITATION = C(r"\b(limited by|limitations?\b|small sample|modest sample|should be interpreted with caution|interpret(ed)? with caution|"
               r"preliminary nature|underpowered)\b")
CONNECTIVE = C(r"^(in contrast|by contrast|however|moreover|additionally|furthermore|similarly|conversely|notably|importantly|interestingly|"
               r"likewise|nevertheless|nonetheless|also|finally|second(ly)?|third(ly)?|further|meanwhile|instead|accordingly)\b")
DANGLING = C(r"^\W*(\w+\W+){0,2}(them|they|these|this|those|such|its|their)\b")
NUMBERS = re.compile(r"\d+(\.\d+)?\s*%|\bp\s*[<=≤]\s*0?\.\d|\b\d+(\.\d+)?\s*(±|\[|\()|\bn\s*=\s*\d|\b\d{2,}\b")

def feats(s, i, n):
    co_start, co_any = bool(CONCL_START.search(s)), bool(CONCL_ANY.search(s))
    return {"aim": bool(AIM.search(s)), "gap": bool(GAP.search(s)), "bg": bool(BACKGROUND.search(s)), "methods": bool(METHODS.search(s)),
            "res": len(RESULTS.findall(s)), "nums": len(NUMBERS.findall(s)), "co": 2 * co_start + co_any,
            "limit": bool(LIMITATION.search(s)), "conn": bool(CONNECTIVE.search(s)), "dangling": bool(DANGLING.search(s)),
            "first": i == 0, "last": i == n - 1, "p": i / max(1, n - 1)}

def _score(f):
    p, aim, re_, nums, co = f["p"], f["aim"], min(f["res"], 3), min(f["nums"], 3), min(f["co"], 3)
    s0 = 1.2 * (p < 0.3) + 0.4 * (p < 0.5) + 1.5 * f["first"] + 2.0 * aim + 1.5 * (f["bg"] or f["gap"]) - 0.8 * min(re_, 2) - 0.5 * min(nums, 2)
    s1 = 2.0 * f["methods"] + 0.8 * (0.1 < p < 0.7) + 0.6 * aim + 0.3 * min(nums, 2) - 1.0 * (co > 0) - 0.5 * f["first"]
    s2 = 1.0 * re_ + 0.4 * nums + 0.8 * (0.3 < p < 0.95) - 1.5 * aim - 0.8 * f["first"]
    s3 = 1.6 * co + 1.2 * f["last"] + 0.6 * (p > 0.75) - 0.5 * min(nums, 2) - 2.0 * f["first"] - 1.0 * aim - 2.5 * f["limit"]
    return [s0, s1, s2, s3]

def classify(sents):
    """Best non-decreasing labelling Background/Aim(0) <= Methods(1) <= Results(2) <= Conclusion(3) (Viterbi-style DP)."""
    n = len(sents)
    fs = [feats(s, i, n) for i, s in enumerate(sents)]
    sc = [_score(f) for f in fs]
    best = [[float("-inf")] * 4 for _ in range(n)]
    back = [[0] * 4 for _ in range(n)]
    best[0] = list(sc[0])
    for i in range(1, n):
        for k in range(4):
            j = max(range(k + 1), key=lambda j: best[i - 1][j])
            best[i][k], back[i][k] = best[i - 1][j] + sc[i][k], j
    k = max(range(4), key=lambda k: best[n - 1][k])
    labels = [0] * n
    for i in range(n - 1, -1, -1):
        labels[i] = k
        k = back[i][k]
    return labels, fs

# ---- 3. assemble ----------------------------------------------------------------------------------------------------
JUNK = re.compile(r"(©|\(c\)\s*\d{4}|copyright\b|\bpublished by\b|all rights reserved|this article is protected|\(PsycInfo Database Record"
                  r"|creative commons|the author\(s\)\s*\d{4}|\bdoi:\s*10\.|trial registration|clinicaltrials\.gov identifier|prospero\b).*$", re.I)

def tidy(text):
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"^(abstract|summary)\b[:.\s]*", "", t, flags=re.I)
    t = JUNK.sub("", t).strip()
    return t

def trim(sents, limit):
    """Whole sentences up to `limit` words; a single long sentence is cut at a word boundary with an ellipsis."""
    out, n = [], 0
    for s in sents:
        w = len(s.split())
        if out and n + w > limit:
            break
        if not out and w > limit:
            words = s.split()[:limit]
            while words and re.fullmatch(r"(and|or|of|the|a|an|in|to|with|for|by|on|at|from|that|which|as|than|whereas|while|but|including)[,;:]?",
                                         words[-1], re.I):
                words.pop()
            return " ".join(words).rstrip(",;:") + " …"
        out.append(s); n += w
    return " ".join(out)

def _pick(idx, key, k):
    """Top-k of the sentence indices `idx` by key(i), returned in reading order."""
    return sorted(sorted(idx, key=lambda i: (-key(i), i))[:k])

def _background(idx, fs):
    """The sentence that states the question: an explicit aim, else the (last) gap statement, else the opening sentence."""
    aims = [i for i in idx if fs[i]["aim"]]
    if aims:
        return "Aim", aims[0]
    gaps = [i for i in idx if fs[i]["gap"]]
    if gaps:
        return "Background", gaps[-1]
    ok = [i for i in idx if not fs[i]["dangling"]]
    return "Background", (ok or idx)[0]

def choose(sents, labels, fs, have=()):
    """One or two sentences per part from a labelled sentence list (limitation sentences are never shown)."""
    by = {k: [i for i, l in enumerate(labels) if l == k and not fs[i]["limit"]] for k in range(4)}
    parts, used = {}, set()
    if not ({"Aim", "Background"} & set(have)) and by[0]:
        lab, i = _background(by[0], fs)
        parts[lab] = trim([sents[i]], LIMITS[lab]); used.add(i)
    if "Methods" not in have and by[1]:
        top = _pick(by[1], lambda i: 2 * fs[i]["methods"] + 0.3 * min(fs[i]["nums"], 2) - 0.3 * by[1].index(i), 2)
        parts["Methods"] = trim([sents[i] for i in top], LIMITS["Methods"]); used.update(top)
    if "Conclusion" not in have and by[3]:
        i = _pick(by[3], lambda i: fs[i]["co"] - 0.1 * by[3].index(i) - 0.5 * fs[i]["conn"], 1)[0]
        parts["Conclusion"] = trim([sents[i]], LIMITS["Conclusion"]); used.add(i)
    if "Results" not in have and by[2]:
        cand = [i for i in by[2] if i not in used]
        if not by[3] and "Conclusion" not in have and len(cand) >= 3 and not fs[cand[-1]]["nums"]:
            # no explicit conclusion: the closing sentence of the abstract is usually the take-home message
            i = cand.pop()
            parts["Conclusion"] = trim([sents[i]], LIMITS["Conclusion"])
        if cand:
            top = _pick(cand, lambda i: min(fs[i]["res"], 4) + 0.5 * min(fs[i]["nums"], 3) - 1.0 * fs[i]["conn"] - 0.1 * by[2].index(i), 2)
            parts["Results"] = trim([sents[i] for i in top], LIMITS["Results"])
    return parts

def structure(abstract):
    """[[label, text], ...] in the order Aim/Background, Methods, Results, Conclusion; [] if the abstract is too short."""
    text = tidy(abstract)
    if len(text.split()) < 60:
        return []
    parts = {}
    lab = labelled(text)
    if lab:
        groups = {}
        for kind, lab_txt, body in lab:
            if kind != "drop":
                groups.setdefault(kind, []).append((lab_txt, body))
        if groups.get("aim"):
            lab_txt, body = groups["aim"][-1]             # Importance/Background then Objective: the last one is the question
            ss = sentences(body) or [body]
            fs = [feats(x, i, len(ss)) for i, x in enumerate(ss)]
            name, i = ("Aim", 0) if AIM_LABEL.match(lab_txt) else _background(list(range(len(ss))), fs)
            parts[name] = trim([ss[i]], LIMITS[name])
        for kind, label, k in (("methods", "Methods", 1), ("results", "Results", 2), ("conclusion", "Conclusion", 3)):
            if groups.get(kind):
                body = " ".join(x for _, x in groups[kind])
                ss = sentences(body) or [body]
                fs = [feats(x, i, len(ss)) for i, x in enumerate(ss)]
                got = choose(ss, [k] * len(ss), fs, have=[p for p in PARTS + ("Background",) if p != label])
                if got.get(label):
                    parts[label] = got[label]
    if "Results" not in parts or "Conclusion" not in parts:
        sents = sentences(text if not lab else " ".join(b for k, _, b in lab if k != "drop"))
        if len(sents) >= 3:
            labels, fs = classify(sents)
            for k, v in choose(sents, labels, fs, have=list(parts)).items():
                parts.setdefault(k, v)
    seen, out = set(), []
    for k in ("Aim", "Background", "Methods", "Results", "Conclusion"):   # never show the same sentence twice
        v = parts.get(k)
        if v and v not in seen:
            seen.add(v); out.append([k, v])
    return out if len(out) >= 2 else []

def as_text(parts):
    return " ".join(f"{k}: {v}" for k, v in parts)
