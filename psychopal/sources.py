"""One function per scholarly source (ported verbatim from the Neurodynamics Digest pipeline). Each returns (records, info) and raises on total failure.
A record: dict(title, authors[list of 'Given Surname'], venue, date, doi, url, abstract, source, pubtypes[list], groups[set])."""
import os, re, time, datetime as dt, xml.etree.ElementTree as ET
from .common import get, post, clean, norm_doi, MAILTO, FetchError

def _fill(q, cfg):
    """Expand {name} placeholders with the matching top-level strings in queries.json (e.g. {psychedelic_terms})."""
    for k, v in cfg.items():
        if isinstance(v, str) and not k.startswith("_"):
            q = q.replace("{" + k + "}", v)
    return q

def group_query(g, cfg, source):
    if isinstance(g.get(source), str):
        return _fill(g[source], cfg)
    if source == "openalex" and "openalex_query" in g:
        return _fill(g["openalex_query"], cfg)
    if "query" in g:
        return _fill(g["query"], cfg)
    return None

# ---------------- PubMed ----------------
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
def _ncbi_params(extra):
    p = {"tool": "psychopal", "email": MAILTO, **extra}
    if os.environ.get("NCBI_API_KEY"):
        p["api_key"] = os.environ["NCBI_API_KEY"]
    return p

def _labelled_part(x):
    """One <AbstractText>, keeping its section label ("Methods: ...") so card summaries can use the abstract's own structure."""
    t = clean("".join(x.itertext()))
    lab = (x.get("Label") or "").strip()
    if not lab or not t or lab.upper() in ("UNLABELLED", "UNASSIGNED"):
        return t
    return f"{lab.capitalize() if lab.isupper() else lab}: {t}"

def headings(html):
    """Europe PMC marks abstract sections with <h4>Methods</h4>: turn them into 'Methods: ' before the tags are stripped."""
    return re.sub(r"<(h[1-6]|b|strong)>\s*([^<]{2,60}?)\s*:?\s*</\1>", r" \2: ", html or "")

def pubmed(cfg, start, end, log):
    info = {"queries": 0, "query_errors": []}
    pmid_groups = {}
    queries = []
    for g in cfg["groups"]:
        q = group_query(g, cfg, "pubmed")
        if q:
            queries.append((g["name"], q))
    people = cfg.get("named_researchers", {}).get("people", [])
    if people:
        q = " OR ".join(f'"{p["surname"]} {p["first"]}"[fau]' for p in people)
        queries.append(("named_researchers", q))
    for name, q in queries:
        try:
            r = get(EUTILS + "esearch.fcgi", params=_ncbi_params({"db": "pubmed", "term": q, "datetype": "edat",
                    "mindate": start.strftime("%Y/%m/%d"), "maxdate": end.strftime("%Y/%m/%d"), "retmode": "json", "retmax": 400}))
            ids = r["esearchresult"].get("idlist", [])
            info["queries"] += 1
            info.setdefault("per_group", {})[name] = int(r["esearchresult"].get("count", 0))
            for i in ids:
                pmid_groups.setdefault(i, set()).add(name)
        except Exception as e:
            info["query_errors"].append(f"{name}: {e}")
        time.sleep(0.35)
    if not pmid_groups and info["query_errors"]:
        raise FetchError("; ".join(info["query_errors"][:3]))
    recs = []
    ids = list(pmid_groups)
    for k in range(0, len(ids), 200):
        batch = ids[k:k + 200]
        try:
            xml = post(EUTILS + "efetch.fcgi", data=_ncbi_params({"db": "pubmed", "id": ",".join(batch), "retmode": "xml"}), expect="text", timeout=60)
        except Exception as e:
            info["query_errors"].append(f"efetch: {e}"); continue
        root = ET.fromstring(xml)
        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//MedlineCitation/PMID")
            a = art.find(".//Article")
            if a is None:
                continue
            title = clean("".join(a.find("ArticleTitle").itertext()) if a.find("ArticleTitle") is not None else "")
            abst = " ".join(_labelled_part(x) for x in a.findall(".//Abstract/AbstractText")).strip()
            authors = []
            for au in a.findall(".//AuthorList/Author"):
                ln, fn = au.findtext("LastName"), au.findtext("ForeName") or au.findtext("Initials") or ""
                if ln:
                    authors.append(f"{fn} {ln}".strip())
                elif au.findtext("CollectiveName"):
                    authors.append(au.findtext("CollectiveName"))
            venue = a.findtext(".//Journal/Title") or ""
            doi = ""
            for aid in art.findall(".//PubmedData/ArticleIdList/ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text or ""
            if not doi:
                for el in a.findall("ELocationID"):
                    if el.get("EIdType") == "doi":
                        doi = el.text or ""
            ad = a.find("ArticleDate")
            if ad is not None:
                date = f'{ad.findtext("Year")}-{int(ad.findtext("Month") or 1):02d}-{int(ad.findtext("Day") or 1):02d}'
            else:
                pd = a.find(".//Journal/JournalIssue/PubDate")
                y = pd.findtext("Year") if pd is not None else None
                date = y or ""
            pts = [p.text for p in a.findall(".//PublicationTypeList/PublicationType") if p.text]
            affs = " | ".join(clean(x.text) for x in a.findall(".//AffiliationInfo/Affiliation") if x.text)[:600]
            recs.append({"title": title, "authors": authors, "venue": venue, "date": date, "doi": norm_doi(doi),
                         "url": f"https://doi.org/{norm_doi(doi)}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                         "abstract": abst, "source": "pubmed", "pmid": pmid, "pubtypes": pts, "affiliations": affs,
                         "groups": set(pmid_groups.get(pmid, set()))})
        time.sleep(0.35)
    info["records"] = len(recs)
    return recs, info

# ---------------- Europe PMC ----------------
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
def europepmc(cfg, start, end, log):
    info = {"queries": 0, "query_errors": [], "per_group": {}}
    out = {}
    queries = []
    for g in cfg["groups"]:
        if "pubmed" in g and "europepmc" not in g and "query" not in g:
            continue
        q = group_query(g, cfg, "europepmc")
        if q:
            queries.append((g["name"], q))
    people = [p for p in cfg.get("named_researchers", {}).get("people", []) if not p.get("strict")]
    if people:
        queries.append(("named_researchers", " OR ".join(f'AUTH:"{p["surname"]} {p["first"][0]}"' for p in people)))
    for name, q in queries:
        full = f"({q}) AND (FIRST_PDATE:[{start} TO {end}])"
        cursor, pages = "*", 0
        try:
            while pages < 3:
                r = get(EPMC, params={"query": full, "format": "json", "resultType": "core", "pageSize": 200, "cursorMark": cursor}, timeout=45)
                info["queries"] += 1; pages += 1
                if pages == 1:
                    info["per_group"][name] = r.get("hitCount", 0)
                for x in r.get("resultList", {}).get("result", []):
                    key = x.get("id")
                    if key in out:
                        out[key]["groups"].add(name); continue
                    pts = (x.get("pubTypeList") or {}).get("pubType", [])
                    venue = ((x.get("journalInfo") or {}).get("journal") or {}).get("title") or (x.get("bookOrReportDetails") or {}).get("publisher") or ""
                    if x.get("source") == "PPR" and not venue:
                        venue = "Preprint"
                    authors = [clean(f'{a.get("firstName","")} {a.get("lastName","")}') if a.get("lastName") else a.get("fullName", "")
                               for a in (x.get("authorList") or {}).get("author", [])]
                    doi = norm_doi(x.get("doi"))
                    affs = " | ".join(a.get("affiliation", "") for a in (x.get("authorList") or {}).get("author", []) if a.get("affiliation"))[:600]
                    out[key] = {"title": clean(x.get("title")), "authors": authors, "venue": venue,
                                "date": x.get("firstPublicationDate", ""), "doi": doi,
                                "url": f"https://doi.org/{doi}" if doi else f'https://europepmc.org/article/{x.get("source")}/{x.get("id")}',
                                "abstract": clean(headings(x.get("abstractText"))), "source": "europepmc" + ("-preprint" if x.get("source") == "PPR" else ""),
                                "pmid": x.get("pmid"), "pubtypes": pts, "affiliations": affs, "groups": {name}}
                nxt = r.get("nextCursorMark")
                if not nxt or nxt == cursor or len(r.get("resultList", {}).get("result", [])) < 200:
                    break
                cursor = nxt
        except Exception as e:
            info["query_errors"].append(f"{name}: {e}")
    if not out and info["query_errors"]:
        raise FetchError("; ".join(info["query_errors"][:3]))
    info["records"] = len(out)
    return list(out.values()), info

# ---------------- OpenAlex ----------------
OA = "https://api.openalex.org/works"
def _inv(ii):
    if not ii:
        return ""
    pos = {}
    for w, ps in ii.items():
        for p in ps:
            pos[p] = w
    return " ".join(pos[i] for i in sorted(pos))

def openalex(cfg, start, end, log):
    """Only groups flagged openalex=true (shared free budget ~$0.10/day per IP without a key, ~$0.001 per call)."""
    info = {"queries": 0, "query_errors": [], "per_group": {}, "cost_usd": 0.0}
    out = {}
    for g in cfg["groups"]:
        if not g.get("openalex"):
            continue
        q = group_query(g, cfg, "openalex")
        q = q.replace("*", "")
        params = {"filter": f"title_and_abstract.search:{q},from_publication_date:{start},to_publication_date:{end}",
                  "per-page": 100, "mailto": MAILTO,
                  "select": "id,doi,display_name,publication_date,type,primary_location,authorships,abstract_inverted_index"}
        if os.environ.get("OPENALEX_API_KEY"):
            params["api_key"] = os.environ["OPENALEX_API_KEY"]
        try:
            r = get(OA, params=params, timeout=45)
            info["queries"] += 1
            info["per_group"][g["name"]] = r.get("meta", {}).get("count", 0)
            info["cost_usd"] += float(r.get("meta", {}).get("cost_usd") or 0)
            for w in r.get("results", []):
                key = w["id"]
                if key in out:
                    out[key]["groups"].add(g["name"]); continue
                loc = w.get("primary_location") or {}
                src = (loc.get("source") or {})
                doi = norm_doi(w.get("doi"))
                out[key] = {"title": clean(w.get("display_name")), "authors": [a["author"]["display_name"] for a in w.get("authorships", [])],
                            "venue": src.get("display_name") or "", "date": w.get("publication_date", ""), "doi": doi,
                            "url": f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or key),
                            "abstract": _inv(w.get("abstract_inverted_index")), "source": "openalex",
                            "pubtypes": [w.get("type") or ""], "groups": {g["name"]}}
        except Exception as e:
            info["query_errors"].append(f'{g["name"]}: {e}')
        time.sleep(0.2)
    if not out and info["query_errors"]:
        raise FetchError("; ".join(info["query_errors"][:3]))
    info["records"] = len(out)
    return list(out.values()), info

# ---------------- bioRxiv / medRxiv ----------------
def rxiv(server, cats, start, end, log):
    info = {"per_category": {}, "versions_skipped": 0, "query_errors": []}
    recs = []
    for cat in cats:
        cursor, total, n = 0, None, 0
        try:
            while total is None or cursor < total:
                r = get(f"https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}", params={"category": cat}, timeout=45)
                m = (r.get("messages") or [{}])[0]
                total = int(m.get("total", 0) or 0)
                col = r.get("collection", []) or []
                if not col:
                    break
                for x in col:
                    if str(x.get("version")) != "1":
                        info["versions_skipped"] += 1; continue
                    authors = []
                    for a in (x.get("authors") or "").split(";"):
                        a = a.strip()
                        if "," in a:
                            ln, fn = a.split(",", 1); authors.append(f"{fn.strip()} {ln.strip()}")
                        elif a:
                            authors.append(a)
                    doi = norm_doi(x.get("doi"))
                    recs.append({"title": clean(x.get("title")), "authors": authors, "venue": f"{server} ({x.get('category')})",
                                 "date": x.get("date", ""), "doi": doi, "url": f"https://www.{server}.org/content/{doi}v1",
                                 "abstract": clean(x.get("abstract")), "source": server, "pubtypes": [x.get("type", "")],
                                 "affiliations": x.get("author_corresponding_institution", ""), "groups": set()})
                    n += 1
                cursor += len(col)
                time.sleep(0.2)
            info["per_category"][cat] = n
        except Exception as e:
            info["query_errors"].append(f"{cat}: {e}")
    if not recs and info["query_errors"]:
        raise FetchError("; ".join(info["query_errors"][:3]))
    info["records"] = len(recs)
    return recs, info

# ---------------- arXiv ----------------
def arxiv(cats, start, end, log):
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    recs, info = [], {"per_category": {}}
    for cat in cats:
        q = f"cat:{cat} AND submittedDate:[{start:%Y%m%d}0000 TO {end:%Y%m%d}2359]"
        startn, n = 0, 0
        while True:
            xml = get("https://export.arxiv.org/api/query", params={"search_query": q, "start": startn, "max_results": 200,
                      "sortBy": "submittedDate", "sortOrder": "descending"}, expect="text", timeout=60)
            root = ET.fromstring(xml)
            ents = root.findall("a:entry", ns)
            for e in ents:
                aid = (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
                base = re.sub(r"v\d+$", "", aid)
                doi = norm_doi(e.findtext("arxiv:doi", "", ns)) or f"10.48550/arxiv.{base}".lower()
                recs.append({"title": clean(e.findtext("a:title", "", ns)), "authors": [clean(a.findtext("a:name", "", ns)) for a in e.findall("a:author", ns)],
                             "venue": f"arXiv ({cat})", "date": (e.findtext("a:published", "", ns) or "")[:10], "doi": doi,
                             "url": f"https://arxiv.org/abs/{base}", "abstract": clean(e.findtext("a:summary", "", ns)),
                             "source": "arxiv", "pubtypes": ["preprint"], "groups": set()})
                n += 1
            if len(ents) < 200:
                break
            startn += 200; time.sleep(3)
        info["per_category"][cat] = n
        time.sleep(3)
    info["records"] = len(recs)
    return recs, info

# ---------------- ClinicalTrials.gov ----------------
def clinicaltrials(cfg, start, end, log):
    terms = cfg.get("clinicaltrials_terms", [])
    q = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
    params = {"query.term": q, "filter.advanced": f"AREA[StudyFirstPostDate]RANGE[{start},{end}]", "pageSize": 100, "countTotal": "true",
              "fields": "NCTId,BriefTitle,OfficialTitle,Condition,InterventionName,Phase,OverallStatus,LeadSponsorName,StudyFirstPostDate,BriefSummary,EnrollmentCount,LocationCountry,PrimaryOutcomeMeasure,StudyType"}
    r = get("https://clinicaltrials.gov/api/v2/studies", params=params, timeout=45)
    out = []
    for s in r.get("studies", []):
        p = s.get("protocolSection", {})
        idm, st, cond = p.get("identificationModule", {}), p.get("statusModule", {}), p.get("conditionsModule", {})
        arms, design, desc = p.get("armsInterventionsModule", {}), p.get("designModule", {}), p.get("descriptionModule", {})
        spons, outc, locs = p.get("sponsorCollaboratorsModule", {}), p.get("outcomesModule", {}), p.get("contactsLocationsModule", {})
        out.append({"nct": idm.get("nctId"), "title": idm.get("briefTitle"), "official_title": idm.get("officialTitle"),
                    "conditions": cond.get("conditions", []), "interventions": [i.get("name") for i in arms.get("interventions", [])],
                    "phase": design.get("phases", []), "study_type": design.get("studyType"), "status": st.get("overallStatus"),
                    "first_posted": (st.get("studyFirstPostDateStruct") or {}).get("date"),
                    "sponsor": (spons.get("leadSponsor") or {}).get("name"), "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
                    "countries": sorted({l.get("country") for l in locs.get("locations", []) if l.get("country")}),
                    "primary_outcomes": [o.get("measure") for o in outc.get("primaryOutcomes", [])][:4],
                    "summary": clean(desc.get("briefSummary"))[:600],
                    "url": f'https://clinicaltrials.gov/study/{idm.get("nctId")}'})
    return out, {"records": len(out), "total": r.get("totalCount")}
