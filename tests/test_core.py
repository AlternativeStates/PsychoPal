"""Offline tests (no network): scoring, dedupe, deadline parsing, eligibility, user-state parsing, card text fallback, site build."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from psychopal import scoring, papers, opportunities, summarise, build_site, run_weekly, common

@pytest.fixture(scope="module")
def cfg():
    return scoring.load_config()

def rec(title, abstract="", venue="", groups=(), source="pubmed", authors=()):
    return {"title": title, "abstract": abstract, "venue": venue, "groups": set(groups), "source": source, "authors": list(authors),
            "pubtypes": [], "date": "2026-11-03", "sources": [source]}

def test_config_streams_valid(cfg):
    streams = set(cfg["streams"])
    for g in cfg["groups"]:
        assert g["stream"] in streams, g["name"]
    for g in cfg.get("current_analyses", {}).get("groups", []):
        assert g in cfg["_byname"]

def test_psychedelic_pet_paper_scores_high_and_routes(cfg):
    prof = scoring.Profile(["x"])
    r = rec("Psilocybin alters 5-HT2A receptor binding measured with PET in healthy volunteers",
            "We used [11C]Cimbi-36 positron emission tomography to quantify serotonin 2A receptor occupancy after psilocybin. PET binding potential decreased.",
            venue="Biological psychiatry")
    s = scoring.score_record(cfg, r, prof)
    assert s["stream"] in ("psychedelics", "neuropsychopharm")
    assert s["score"] > 15
    assert s["modality"] == "PET"

def test_brain_body_and_neurodynamics_streams(cfg):
    prof = scoring.Profile(["x"])
    bb = scoring.score_record(cfg, rec("Heartbeat-evoked potentials in EEG track interoceptive attention",
                                       "EEG heartbeat evoked potential amplitude and cardiac cycle effects on cortical responses."), prof)
    nd = scoring.score_record(cfg, rec("Lempel-Ziv complexity of EEG signals and criticality during anaesthesia",
                                       "We computed Lempel-Ziv complexity and entropy of high-density EEG; criticality measures changed."), prof)
    assert bb["stream"] == "brain_body"
    assert nd["stream"] == "neurodynamics"

def test_negation_dmt_disease_modifying(cfg):
    g = cfg["_byname"]["psychedelic_any"]
    assert scoring.kw_hit(cfg, g, "DMT use in multiple sclerosis", "disease-modifying therapy (DMT) adherence") == 0.0
    assert scoring.kw_hit(cfg, g, "DMT alters EEG", "N,N-dimethyltryptamine was given") == 1.0

def test_anchored_stream_requires_top_venue(cfg):
    prof = scoring.Profile(["synaptic plasticity cortex neurons"])
    r = rec("Cortical neurons and synaptic plasticity", "Neurons in cortex show synaptic plasticity.", venue="Some Small Journal", groups=["neuro_top"])
    s = scoring.score_record(cfg, r, prof)
    assert s["stream"] is None

def test_elife_reviews_dropped():
    assert scoring.is_dropped({"title": "Reviewer #2 (Public review): Something"})
    assert scoring.is_dropped({"title": "eLife Assessment: Something"})
    assert not scoring.is_dropped({"title": "A real paper"})

def test_merge_dedupes_by_doi_and_title():
    a = rec("Same paper title that is long enough", "short", source="europepmc"); a["doi"] = "10.1/x"
    b = rec("Same paper title that is long enough", "a much longer abstract here", source="pubmed"); b["doi"] = "10.1/x"
    c = rec("Same paper title that is long enough", "", source="biorxiv"); c["doi"] = ""
    m = papers.merge([a, b, c])
    assert len(m) == 1 and m[0]["source"] == "pubmed" and "longer" in m[0]["abstract"]

def test_regex_deadline():
    t = dt.date(2026, 11, 9)
    assert opportunities.regex_deadline("Closing date: 17 February 2027 at 16:00", t) == "2027-02-17"
    assert opportunities.regex_deadline("The deadline is March 3, 2027.", t) == "2027-03-03"
    assert opportunities.regex_deadline("deadline 1 Jan 2020", t) == ""

def test_eligibility():
    today = dt.date(2026, 11, 9)
    e = {"eligibility": {"max_years_post_phd": 3}}
    assert opportunities.eligibility(e, {"phd_awarded": "2020-01-01"}, today)[0] is False
    assert opportunities.eligibility(e, {"phd_awarded": "2024-06-01"}, today)[0] is True
    show, note = opportunities.eligibility(e, {"phd_awarded": None}, today)
    assert show and "3" in note
    assert opportunities.eligibility({"eligibility": {"requires_moving_country": True}}, {"open_to_moving_abroad": False}, today)[0] is False

def test_extractive_fallback():
    it = {"id": "pa-1", "abstract": "Background: We did X in 40 people. We found Y. Then many more sentences follow here to pad the text out."}
    out = summarise.extractive(it)
    assert out["post"].startswith("We did X") and out["ai"] is False
    page = summarise.extractive({"id": "fu-1", "page_text": "Home Menu Login", "notes": "Curated note."})
    assert page["post"] == "Curated note."

def test_read_user_state(tmp_path, monkeypatch):
    raw = {"stores": {"pp-marks": {"like\tpa-1": {"v": "1", "t": 5}, "dislike\tpa-2": {"v": "1", "t": 5}, "star\tpa-3": {"v": "1", "t": 5},
                                   "like\tpa-9": {"v": None, "t": 9, "d": 1}},
                      "pp-search-sent": {"10.1/abc\ttitle": {"v": "\"Saved paper\"", "t": 1}, "10.1/abc\tabstract": {"v": "\"About PET\"", "t": 1}}}}
    (tmp_path / "user_state.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(run_weekly, "STATE", str(tmp_path))
    us = run_weekly.read_user_state()
    assert us["liked"] == {"pa-1"} and us["disliked"] == {"pa-2"} and us["starred"] == {"pa-3"}
    assert us["search_saved"] == ["Saved paper About PET"]

def test_public_card_strips_internals():
    c = {"id": "pa-1", "kind": "paper", "title": "T", "abstract": "long", "score_parts": {"a": 1}, "post": "p", "top": True}
    out = build_site.public_card(c)
    assert "abstract" not in out and "score_parts" not in out and out["top"] is True

def test_week_helpers():
    s, e = common.last_full_week(dt.date(2026, 11, 9))
    assert (s, e) == (dt.date(2026, 11, 2), dt.date(2026, 11, 8))
    assert common.week_id(s) == "2026-W45"
    assert common.item_id("paper", "10.1/X") == common.item_id("paper", "10.1/x")
