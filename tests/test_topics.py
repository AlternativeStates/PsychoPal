"""Ranked topics: stream order, routing of typical papers, psychedelics title claim, per-topic quotas."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from psychopal import scoring, papers

RANKED = ["psychedelics", "neuroimaging", "neuropsychopharm", "bio_psychiatry", "neurodynamics", "brain_body"]

@pytest.fixture(scope="module")
def cfg():
    return scoring.load_config()

@pytest.fixture(scope="module")
def prof():
    return scoring.Profile(["x"])

def rec(title, abstract=""):
    return {"title": title, "abstract": abstract, "venue": "", "groups": set(), "source": "pubmed", "authors": [], "pubtypes": [],
            "date": "2026-11-03", "sources": ["pubmed"]}

def test_streams_in_order_of_importance(cfg):
    assert [s for s in cfg["streams"] if s not in ("wider", "tools")] == RANKED
    assert "comp_neuro" not in cfg["streams"]

@pytest.mark.parametrize("title,abstract,stream", [
    ("Mystical experience predicts wellbeing in naturalistic psilocybin users: a survey",
     "In a naturalistic survey of 900 psilocybin users, mystical experience scores predicted wellbeing.", "psychedelics"),
    ("Ketamine versus psilocybin for treatment-resistant depression",
     "A randomized trial comparing ketamine and psilocybin.", "psychedelics"),
    ("Ketamine reduces suicidal ideation within hours",
     "Ketamine is a rapid-acting antidepressant; like psychedelics it may act on plasticity. Patients received ketamine.", "neuropsychopharm"),
    ("Cannabidiol modulates striatal dopamine release measured with PET",
     "Cannabidiol reduced [11C]raclopride binding in the striatum of healthy volunteers.", "neuropsychopharm"),
    ("Chronic social defeat stress induces anhedonia via ventral hippocampal circuits in mice",
     "Mice exposed to chronic social defeat developed anhedonia; optogenetic silencing restored reward behaviour.", "bio_psychiatry"),
    ("Electric field modelling and neuronavigation improve TMS targeting",
     "We modelled the transcranial magnetic stimulation electric field in 40 participants to optimise cortical targeting.", "neuroimaging"),
    ("Optically pumped magnetometer MEG: source reconstruction accuracy in children",
     "Wearable OPM-MEG source localisation was compared with conventional MEG.", "neuroimaging"),
    ("Dynamic causal modelling of propofol effects on EEG",
     "Spectral DCM of high-density EEG with a canonical microcircuit model during propofol sedation.", "neurodynamics"),
])
def test_typical_papers_route_to_their_topic(cfg, prof, title, abstract, stream):
    assert scoring.score_record(cfg, rec(title, abstract), prof)["stream"] == stream

def test_topic_priority_bonus(cfg, prof):
    s = scoring.score_record(cfg, rec("Psilocybin alters EEG complexity", "Psilocybin increased EEG Lempel-Ziv complexity."), prof)
    assert s["parts"]["topic_priority"] == cfg["scoring"]["stream_bonus"]["psychedelics"]

def test_quotas_follow_settings_order(cfg):
    q = papers.quotas(cfg, {"stream_quota": {"psychedelics": [7, 10], "brain_body": [2, 4]}, "min_per_stream": 1, "max_per_stream": 3})
    assert list(q)[:2] == ["psychedelics", "brain_body"] and q["psychedelics"] == (7, 10) and q["neuroimaging"] == (1, 3)

def test_select_fills_minimums_and_respects_maximums(cfg):
    pool = []
    for s in RANKED:                       # 12 candidates per topic; brain-body papers score highest, psychedelics lowest
        base = {"brain_body": 30, "psychedelics": 5}.get(s, 10)
        for i in range(12):
            pool.append({"id": f"{s}-{i}", "title": f"{s} paper {i}", "abstract": "", "stream": s, "score": base + i * 0.1,
                         "matched": [], "modality": "", "named": [], "_av": {f"{s}{i}": 1.0}, "_tv": {f"{s}{i}": 1.0}})
    settings = {"papers_total": 25, "top_for_you": 5, "min_paper_score": 4.0,
                "stream_quota": {"psychedelics": [7, 10], "neuroimaging": [4, 7], "neuropsychopharm": [3, 6], "bio_psychiatry": [3, 5],
                                 "neurodynamics": [2, 5], "brain_body": [2, 4]}}
    chosen, *_ = papers.select(cfg, pool, settings)
    n = {s: sum(1 for x in chosen if x["stream"] == s) for s in RANKED}
    assert len(chosen) == 25
    assert n["psychedelics"] >= 7                       # minimum met even though they score lowest
    assert n["brain_body"] == 5                         # Top 5 are all brain-body (highest scores) ...
    assert all(n[s] <= settings["stream_quota"][s][1] for s in RANKED if s != "brain_body")

def test_used_up_quota_fails_fast_and_blocks_host(monkeypatch):
    from psychopal import common
    calls = []
    class R:
        status_code, headers, text = 429, {"Retry-After": "553"}, ""
    class S:
        def request(self, *a, **k):
            calls.append(1); return R()
    monkeypatch.setattr(common, "session", lambda: S())
    monkeypatch.setattr(common, "_QUOTA_HIT", {})
    monkeypatch.setattr(common.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not wait")))
    for _ in range(3):
        with pytest.raises(common.FetchError, match="quota used up"):
            common.get("https://api.openalex.org/works", params={"x": 1})
    assert len(calls) == 1          # later calls do not touch the network

def test_topic_minimum_takes_sub_areas_in_priority_order(cfg):
    def p(i, group, score):
        return {"id": f"np-{i}", "title": f"paper {i}", "abstract": "", "stream": "neuropsychopharm", "score": score,
                "matched": [group], "modality": "", "named": [], "_av": {str(i): 1.0}, "_tv": {str(i): 1.0}}
    pool = [p(1, "ketamine_rapid", 20), p(2, "ketamine_rapid", 19), p(3, "plasticity", 18), p(4, "ketamine_rapid", 17),
            p(5, "stimulants", 14), p(6, "cannabis", 13.5), p(7, "cannabis", 5)]
    settings = {"papers_total": 3, "top_for_you": 0, "stream_quota": {"neuropsychopharm": [3, 6]}, "min_per_stream": 0}
    chosen, *_ = papers.select(cfg, pool, settings)
    assert [x["id"] for x in chosen] == ["np-1", "np-5", "np-6"]      # ketamine, then stimulants, then cannabis (all >= 60% of best)

# --- Tools & methods: only papers ABOUT a tool or method, from any topic when not already picked as a paper ---

@pytest.mark.parametrize("title,abstract,expected", [
    ("Resting-state EEG graph analysis reveals enhanced alpha-band integration in high-IQ individuals",
     "We used an open-source analysis pipeline to compute graph metrics.", False),
    ("The association of MRI-derived tissue bridge morphometrics with recovery after spinal cord injury",
     "Images were processed with an automated analysis pipeline.", False),
    ("Respiratory phase orchestrates human olfactory cortical dynamics", "We provide an open-source olfactometer.", False),
    ("Characterising cortical perturbation-recovery dynamics using TMS-EEG: the Brain Dynamic Toolbox", "", True),
    ("Counteracting effect-size inflation in brain-wide association studies using cross-validation", "", True),
    ("Next generation Human Neocortical Neurosolver model", "Code is available at github.com/example/hnn-core.", True),
])
def test_tool_paper_needs_tool_title_or_code_link(title, abstract, expected):
    assert scoring.is_tool_paper({"title": title, "abstract": abstract}) is expected

def test_findings_paper_loses_tool_keywords(cfg, prof):
    s = scoring.score_record(cfg, rec("Resting-state EEG graph analysis reveals enhanced alpha-band integration in high-IQ adults",
        "We recorded resting-state EEG in 200 healthy adults and used an open-source analysis pipeline to compute graph metrics."), prof)
    assert "tools_papers" not in s["groups"] and not s["tool"] and s["stream"] != "tools"

def test_on_topic_toolbox_is_a_tool_candidate(cfg, prof):
    s = scoring.score_record(cfg, rec("Characterising cortical perturbation-recovery dynamics using TMS-EEG: the Brain Dynamic Toolbox",
        "We present an open-source toolbox for TMS-EEG in 30 healthy participants, quantifying cortical perturbation and recovery."), prof)
    assert s["tool"] and s["stream"] == "neuroimaging"

def test_tools_cards_come_from_any_topic_but_not_editorials(cfg):
    def p(i, stream, score, tool=False, title=None, low=False):
        return {"id": f"x-{i}", "title": title or f"paper {i}", "abstract": "", "stream": stream, "score": score, "tool": tool,
                "is_review": False, "score_parts": {"low_type": -2.0} if low else {}, "matched": [], "modality": "", "named": [],
                "_av": {f"w{i}": 1.0}, "_tv": {f"w{i}": 1.0}}
    pool = [p(1, "neuroimaging", 20, tool=True),                     # picked as a paper: not repeated as a tool card
            p(2, "neuroimaging", 15, tool=True),                     # missed the quota: becomes a Tools card
            p(3, "neurodynamics", 14),                               # not a tool paper
            p(4, "neuroimaging", 13, tool=True, title="Editorial: methods in brain imaging", low=True),
            p(5, "tools", 9, tool=True), p(6, "tools", 8)]
    settings = {"papers_total": 1, "top_for_you": 0, "stream_quota": {"neuroimaging": [1, 1]}, "min_per_stream": 0, "max_per_stream": 0}
    chosen, tools, *_ = papers.select(cfg, pool, settings)
    assert [x["id"] for x in chosen] == ["x-1"]
    assert [x["id"] for x in tools] == ["x-2", "x-5"]
