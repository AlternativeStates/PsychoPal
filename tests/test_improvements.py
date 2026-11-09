"""Offline tests for the Nov 2026 improvements: ranking penalties, variety rule, 'why picked', Wider science, new tool sources, alerts."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from psychopal import scoring, papers, extras, tools_watch, chatter, wider, run_weekly

@pytest.fixture(scope="module")
def cfg():
    return scoring.load_config()

def rec(title, abstract="", venue="", groups=(), pubtypes=()):
    return {"title": title, "abstract": abstract, "venue": venue, "groups": set(groups), "source": "pubmed", "authors": [],
            "pubtypes": list(pubtypes), "date": "2026-11-03", "sources": ["pubmed"]}

KET = ("Ketamine produced rapid antidepressant effects in treatment-resistant depression; patients were randomised and depressive "
       "symptoms fell within 24 hours in this trial of intravenous ketamine.")

# ---- ranking penalties ----
def test_case_report_penalised(cfg):
    prof = scoring.Profile(["x"])
    normal = scoring.score_record(cfg, rec("Ketamine for treatment-resistant depression: a randomised trial", KET), prof)
    case = scoring.score_record(cfg, rec("Ketamine for treatment-resistant depression: a case report", KET), prof)
    assert "case_report" in case["parts"] and case["score"] < normal["score"]

def test_narrative_review_penalised_only_outside_strong_journals(cfg):
    prof = scoring.Profile(["x"])
    abst = "In this review we summarise ketamine and esketamine for depression. " + KET
    weak = scoring.score_record(cfg, rec("Ketamine in depression: a narrative review", abst, venue="Surgical neurology international"), prof)
    strong = scoring.score_record(cfg, rec("Ketamine in depression: a narrative review", abst, venue="Nature reviews neuroscience"), prof)
    sysrev = scoring.score_record(cfg, rec("Ketamine in depression: a systematic review and meta-analysis", abst, venue="Frontiers in psychiatry"), prof)
    assert "weak_review" in weak["parts"]
    assert "weak_review" not in strong["parts"] and "weak_review" not in sysrev["parts"]

def test_nonhuman_toolbox_penalised(cfg):
    prof = scoring.Profile(["x"])
    worm = scoring.score_record(cfg, rec("An open-source toolbox for C. elegans connectome imaging",
                                         "We present an open-source Python toolbox for calcium imaging and MRI-style connectome analysis in C. elegans."), prof)
    human = scoring.score_record(cfg, rec("An open-source toolbox for EEG preprocessing",
                                          "We present an open-source Python toolbox for EEG preprocessing in human participants and patients."), prof)
    if worm["stream"] == "tools":
        assert "nonhuman_tool" in worm["parts"]
    assert "nonhuman_tool" not in human["parts"] and "not_neuro_tool" not in human["parts"]
    prostate = scoring.score_record(cfg, rec("An open-source toolbox for prostate MRI lesion localisation",
                                             "We present an open-source Python toolbox and analysis pipeline for prostate MRI in patients."), prof)
    if prostate["stream"] == "tools":
        assert "not_neuro_tool" in prostate["parts"]

# ---- Wider science routing ----
def test_wider_group_only_decides_stream_when_nothing_else_matches(cfg):
    prof = scoring.Profile(["x"])
    bio = scoring.score_record(cfg, rec("A bacterial enzyme rewires plant root symbiosis", "Root nodules and nitrogen fixation in legumes.",
                                        venue="Nature", groups={"wider_journals"}), prof)
    psy = scoring.score_record(cfg, rec("Psilocybin and cortical plasticity in humans", "Psilocybin increased synaptic plasticity; psychedelic effects.",
                                        venue="Nature", groups={"wider_journals"}), prof)
    assert bio["stream"] == "wider"
    assert psy["stream"] == "psychedelics" and "wider_journals" not in psy["groups"]

def test_no_neuroscience_stream_left(cfg):
    assert "neuroscience" not in cfg["streams"] and "wider" in cfg["streams"]

# ---- variety rule ----
def _scored(i, title, abstract, stream="neuropsychopharm", score=10.0):
    return {"id": f"pa-{i}", "title": title, "abstract": abstract, "stream": stream, "score": score, "matched": [], "modality": "", "named": []}

def test_select_skips_near_duplicates(cfg):
    a = _scored(1, "Theta burst stimulation extends ketamine response in resistant depression", KET, score=12)
    b = _scored(2, "Theta burst stimulation extends ketamine response in resistant depression: replication", KET, score=11)
    c = _scored(3, "Serotonin transporter PET in healthy volunteers", "We measured serotonin transporter binding with PET.", score=9)
    pool = [a, b, c]
    tf = scoring.Tfidf([scoring.rec_text(x) for x in pool] + ["unrelated filler text about cats", "more filler on rivers and lakes"])
    ttf = scoring.Tfidf([x["title"] for x in pool] + ["cats", "rivers"])
    for x in pool:
        x["_av"], x["_tv"] = tf.vec(scoring.rec_text(x)), ttf.vec(x["title"])
    chosen, tools, wide, skipped = papers.select(cfg, pool, {"papers_total": 3, "top_for_you": 0, "min_per_stream": 0})
    ids = [x["id"] for x in chosen]
    assert "pa-1" in ids and "pa-3" in ids and "pa-2" not in ids and skipped == 1

def test_why_picked_names_terms(cfg):
    prof = scoring.Profile(["x"])
    r = rec("Psilocybin and SV2A synaptic density measured with PET", "Psilocybin; [11C]UCB-J positron emission tomography; synaptic density.")
    s = scoring.score_record(cfg, r, prof)
    x = {"matched": s["groups"], "modality": s["modality"], "named": s["named"], "liked_sim": 0}
    line = papers.why_picked(cfg, x, r, prof)
    assert "psilocybin" in line.lower() and "PET" in line

# ---- link -> DOI ----
@pytest.mark.parametrize("link,doi", [
    ("https://www.nature.com/articles/s41586-026-11213-z", "10.1038/s41586-026-11213-z"),
    ("https://www.nature.com/articles/d41586-026-03540-y", None),
    ("https://www.biorxiv.org/content/10.1101/2026.11.01.123456v2.full", "10.1101/2026.11.01.123456"),
    ("https://elifesciences.org/reviewed-preprints/101234", "10.7554/elife.101234"),
    ("https://arxiv.org/abs/2611.01234", "10.48550/arxiv.2611.01234"),
    ("https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.3002999", "10.1371/journal.pbio.3002999"),
    ("https://example.com/blog", None)])
def test_link_doi(link, doi):
    assert chatter.link_doi(link) == doi

# ---- Wider science picking ----
def test_wider_pick_ranks_by_tier_brain_and_attention(monkeypatch, tmp_path):
    monkeypatch.setattr(extras, "_shown", lambda week=None: (set(), set(), set()))
    monkeypatch.setattr(chatter, "discover", lambda since, errors, queries=(), domains=None: (
        [{"doi": "10.1101/2026.11.03.000001", "posters": 25, "link": "x"}], 5))
    monkeypatch.setattr(wider, "_openalex", lambda doi: {
        "title": "A gut microbe that shapes mouse social behaviour", "doi": doi, "abstract": "Gut microbes changed social behaviour and brain gene expression. " * 10,
        "authors": ["A B"], "venue": "bioRxiv", "date": "2026-11-03", "url": f"https://doi.org/{doi}", "type": "preprint", "pubtypes": [], "is_preprint": True})
    long = lambda s: (s + " ") * 12
    cands = [
        {"id": "pa-a", "title": "Ancient DNA reveals the spread of farming", "abstract": long("Genomes from ancient farmers."), "venue": "Nature", "doi": "10.1038/a"},
        {"id": "pa-b", "title": "Hippocampal replay during sleep", "abstract": long("Neurons in the hippocampus replay during sleep and memory."), "venue": "Science", "doi": "10.1126/b"},
        {"id": "pa-c", "title": "Soil carbon in boreal forests", "abstract": long("Carbon storage in soil."), "venue": "Science advances", "doi": "10.1126/c"},
        {"id": "pa-d", "title": "Short", "abstract": "too short", "venue": "Nature", "doi": "10.1038/d"},
    ]
    out, st = wider.pick(cands, [], {"wider": {"cards": 3}}, dt.date(2026, 11, 2), "2026-W45")
    titles = [c["title"] for c in out]
    assert titles[0] == "A gut microbe that shapes mouse social behaviour"   # preprint, but 25 people posted about it
    assert titles.index("Hippocampal replay during sleep") < titles.index("Ancient DNA reveals the spread of farming")   # brain bonus
    assert all(c["stream"] == "wider" and c["kind"] == "paper" and not c.get("top") for c in out)
    assert "Short" not in titles and len(out) == 3

# ---- tools ----
def test_one_release_per_repo(monkeypatch):
    rel = [{"tag_name": "v26.1.0", "published_at": "2026-11-05T10:00:00Z", "html_url": "u1", "body": "new"},
           {"tag_name": "v26.0.0", "published_at": "2026-11-03T10:00:00Z", "html_url": "u2", "body": "old"}]
    monkeypatch.setattr(tools_watch, "get", lambda *a, **k: rel)
    out, err = tools_watch.releases(dt.date(2026, 11, 2), dt.date(2026, 11, 8), {"repos": ["nipreps/mriqc"]})
    assert [c["version"] for c in out] == ["v26.1.0"]

def test_new_repos(monkeypatch):
    def fake_get(url, **k):
        if "search/repositories" in url:
            return {"items": [
                {"full_name": "lab/awesome-eeg", "name": "awesome-eeg", "description": "list", "stargazers_count": 500},
                {"full_name": "lab/fastpet", "name": "fastpet", "description": "Fast PET kinetic modelling", "stargazers_count": 40,
                 "html_url": "https://github.com/lab/fastpet", "created_at": "2026-10-20T00:00:00Z", "topics": ["pet-imaging"], "language": "Python"},
                {"full_name": "lab/tiny", "name": "tiny", "description": "tiny", "stargazers_count": 2},
                {"full_name": "x/FlyDrones", "name": "FlyDrones", "description": "A fruit fly connectome as a drone pilot", "stargazers_count": 300},
                {"full_name": "y/anima", "name": "anima", "description": "A Claude Code skill for biology analysis", "stargazers_count": 250}]}
        return "# fastpet\nFits compartment models."
    monkeypatch.setattr(tools_watch, "get", fake_get)
    out, err = tools_watch.new_repos(dt.date(2026, 11, 2), dt.date(2026, 11, 8), {"new_repo_topics": ["pet-imaging"]}, set())
    assert [c["repo"] for c in out] == ["lab/fastpet"]
    assert out[0]["kind"] == "tool_repo" and "compartment" in out[0]["description"]

# ---- catch-up: one per topic ----
def test_catchup_one_per_topic(monkeypatch):
    monkeypatch.setattr(extras, "_shown", lambda week=None: (set(), set(), set()))
    def fake_get(url, params=None, **k):
        topic = params["filter"].split("search:")[1].split(",")[0]
        return {"results": [{"doi": f"https://doi.org/10.1/{topic.replace(' ', '')}{i}", "display_name": f"{topic} classic paper {i}",
                             "publication_date": "2015-01-01", "cited_by_count": 500 - i, "authorships": [],
                             "abstract_inverted_index": {f"psilocybinword{j}": [j] for j in range(40)},
                             "primary_location": {"source": {"display_name": "J"}}} for i in range(3)]}
    monkeypatch.setattr(extras, "get", fake_get)
    out, st = extras.catchup({"catchup_per_week": 2}, "2026-W45")
    assert len(out) == 2 and len({c["title"].split(" classic")[0] for c in out}) == 2

# ---- alerts ----
def test_claude_alerts():
    s = {"llm": {"enabled": True}}
    assert "rejected" in run_weekly.claude_alerts({"error": "AuthenticationError: Error code: 401"}, s)[0]
    assert run_weekly.claude_alerts({"skipped": "no key"}, s, no_llm=True) == []
    exp = {"llm": {"key_expires": "2026-11-20"}}
    assert "expires in 11 days" in run_weekly.claude_alerts({}, exp, today=dt.date(2026, 11, 9))[0]
    assert run_weekly.claude_alerts({}, {"llm": {"key_expires": "2027-11-09"}}, today=dt.date(2026, 11, 9)) == []
