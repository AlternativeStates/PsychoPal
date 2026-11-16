"""Preference learning with a fake Claude client: monthly taste digest (gates, validation), taste-term bonus (caps, generic terms),
weekly re-rank (bonus values, mapping, all-or-nothing) and its effect on selection. No network."""
import datetime as dt, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from psychopal import prefs, common, papers, scoring

def _resp(obj, stop="end_turn"):
    return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=json.dumps(obj))],
                                 usage=types.SimpleNamespace(input_tokens=1000, output_tokens=200))

class Fake:
    def __init__(self, answer=None, exc=None):
        self.calls, self.answer, self.exc = [], answer, exc
        self.messages = self
    def create(self, **p):
        self.calls.append(p)
        if self.exc:
            raise self.exc
        return self.answer(p) if callable(self.answer) else self.answer

SETTINGS = {"_llm_on": True, "llm": {"monthly_budget_usd": 1.0}, "min_paper_score": 4.0,
            "taste": {"enabled": True, "model": "claude-haiku-5-5", "effort": "medium", "thinking": "adaptive", "min_marks": 3,
                      "max_boost": 3, "max_penalise": 2, "term_weight": 0.75, "max_bonus": 1.5},
            "rerank": {"enabled": True, "model": "claude-haiku-5-5", "effort": "low", "thinking": "off", "shortlist": 80, "max_bonus": 2.5}}

@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "STATE", str(tmp_path))
    monkeypatch.setattr(prefs, "STATE", str(tmp_path))
    monkeypatch.setattr(prefs, "TASTE", str(tmp_path / "taste.json"))
    rows = [{"id": f"pa-{i}", "kind": "paper", "stream": "psychedelics", "title": f"Psilocybin PET study {i}", "week": "2026-W46",
             "snippet": "SV2A PET in humans."} for i in range(4)]
    common.append_jsonl(str(tmp_path / "items.jsonl"), rows)
    return tmp_path

US = {"liked": {"pa-0", "pa-1"}, "starred": {"pa-2"}, "disliked": {"pa-3"}}
TODAY = dt.date(2026, 12, 7)

def test_digest_waits_for_enough_marks(state):
    stats = {}
    out = prefs.monthly_digest({"liked": {"pa-0"}, "starred": set(), "disliked": set()}, SETTINGS, stats, today=TODAY, client=Fake())
    assert out is None and "needs 3" in stats["taste"]["skipped"]
    assert prefs.public(None, SETTINGS, {"liked": {"pa-0"}, "starred": set(), "disliked": set()}) == {"waiting": "1 of 3 marked cards"}

def test_digest_writes_validated_terms_and_then_waits(state):
    fake = Fake(_resp({"profile": "You favour human PET studies of psychedelics.",
                       "boost": ["SV2A", "tms-eeg", "brain", "x", "5-ht2a", "aperiodic", "extra term"],
                       "penalise": ["rodent", "sv2a", "machine learning", "spinal cord"]}))
    stats = {}
    t = prefs.monthly_digest(US, SETTINGS, stats, today=TODAY, client=fake)
    assert t["boost"] == ["sv2a", "tms-eeg", "5-ht2a"]                 # lowercased, generic/short dropped, cut to max_boost
    assert t["penalise"] == ["rodent", "machine learning"]             # overlap with boost removed, cut to max_penalise
    assert t["marks"]["total"] == 4 and json.load(open(prefs.TASTE, encoding="utf-8"))["month"] == "2026-12"
    assert "thinking" not in fake.calls[0]                             # adaptive thinking for the digest
    assert "Psilocybin PET study 0" in fake.calls[0]["messages"][0]["content"]
    stats = {}
    prefs.monthly_digest(US, SETTINGS, stats, today=TODAY, client=fake)
    assert stats["taste"]["skipped"] == "already done this month" and len(fake.calls) == 1
    stats = {}
    prefs.monthly_digest(US, SETTINGS, stats, today=dt.date(2027, 1, 4), client=fake)
    assert stats["taste"]["skipped"] == "no new marks since the last digest" and len(fake.calls) == 1

def test_digest_failure_keeps_previous_taste(state):
    stats = {}
    assert prefs.monthly_digest(US, SETTINGS, stats, today=TODAY, client=Fake(exc=RuntimeError("down"))) is None
    assert "down" in stats["taste"]["error"]

def test_nothing_happens_while_claude_is_off(state):
    fake = Fake(_resp({}))
    off = {**SETTINGS, "_llm_on": False}
    assert prefs.monthly_digest(US, off, {}, today=TODAY, client=fake) is None
    pool = [_paper(1, 10)]
    prefs.adjust(pool, off, {"boost": ["paper"]}, {}, client=fake)
    assert not fake.calls and "claude" not in pool[0]["score_parts"] and "taste" not in pool[0]["score_parts"]
    assert prefs.public(None, off, US) is None

def _paper(i, score, stream="psychedelics", title=None, abstract=""):
    return {"id": f"x-{i}", "title": title or f"paper {i}", "abstract": abstract, "stream": stream, "score": score,
            "score_parts": {"keywords": score}, "matched": [], "modality": "", "named": [], "_av": {f"w{i}": 1.0},
            "_tv": {f"w{i}": 1.0}}

def test_taste_bonus_is_capped_and_ignores_generic_terms():
    pool = [_paper(0, 10, title="SV2A TMS-EEG aperiodic study of (R)-ketamine")] + \
           [_paper(i, 10, title=f"common word study {i}") for i in range(1, 6)] + [_paper(9, 10, title="rodent model")]
    taste = {"boost": ["sv2a", "tms-eeg", "aperiodic", "common word", "(r)-ketamine"], "penalise": ["rodent"]}
    stats = {}
    tb = prefs.taste_bonus(pool, taste, SETTINGS["taste"], stats)
    assert tb["x-0"][0] == 1.5                                          # 4 hits x 0.75 capped at 1.5
    assert tb["x-9"][0] == -0.75
    assert "common word" in stats["taste_terms"]["ignored_generic"]     # matches > 25% of the pool
    assert "x-1" not in tb

def _rating_answer(rate):
    def answer(p):
        lines = [l for l in p["messages"][0]["content"].splitlines() if " | " in l]
        return _resp({"ratings": [{"n": int(l.split(" | ")[0]), "r": rate(l)} for l in lines]})
    return answer

def test_rerank_bonuses_map_to_the_right_papers():
    pool = [_paper(i, 20 - i, title=f"paper {i}") for i in range(10)]
    fake = Fake(_rating_answer(lambda l: 5 if "paper 7" in l else 1 if "paper 0" in l else 3))
    stats = {}
    prefs.adjust(pool, SETTINGS, None, stats, client=fake)
    by = {x["id"]: x for x in pool}
    assert by["x-7"]["score_parts"]["claude"] == 2.5 and by["x-7"]["score"] == 13 + 2.5
    assert by["x-0"]["score_parts"]["claude"] == -2.5 and by["x-0"]["score"] == 17.5
    assert "claude" not in by["x-3"]["score_parts"] and by["x-3"]["claude_rating"] == 3
    assert stats["rerank"]["rated"] == 10 and fake.calls[0]["thinking"] == {"type": "disabled"}

def test_rerank_all_or_nothing():
    pool = [_paper(i, 20 - i) for i in range(10)]
    half = Fake(lambda p: _resp({"ratings": [{"n": 1, "r": 5}, {"n": 2, "r": 5}]}))   # 2 of 10 rated
    stats = {}
    prefs.adjust(pool, SETTINGS, None, stats, client=half)
    assert all("claude" not in x["score_parts"] for x in pool) and "not used" in stats["rerank"]["error"]
    stats = {}
    prefs.adjust(pool, SETTINGS, None, stats, client=Fake(exc=RuntimeError("timeout")))
    assert all(x["score"] == 20 - i for i, x in enumerate(pool)) and "timeout" in stats["rerank"]["error"]
    stats = {}
    prefs.adjust(pool, SETTINGS, None, stats, client=Fake(_resp({}, stop="refusal")))
    assert "refusal" in stats["rerank"]["error"]

def test_rerank_moves_a_paper_into_the_edition():
    cfg = scoring.load_config()
    pool = [_paper(i, 30 - i, stream="neuroimaging") for i in range(6)]
    settings = {**SETTINGS, "papers_total": 3, "top_for_you": 0, "stream_quota": {"neuroimaging": [0, 3]}, "min_per_stream": 0}
    before = [x["id"] for x in papers.select(cfg, [dict(x) for x in pool], settings)[0]]
    assert before == ["x-0", "x-1", "x-2"]
    prefs.adjust(pool, settings, None, {}, client=Fake(_rating_answer(lambda l: 5 if l.startswith("4 ") else 3)))
    after = [x["id"] for x in papers.select(cfg, pool, settings)[0]]
    assert "x-3" in after and "x-2" not in after                       # 27 + 2.5 = 29.5 overtakes 28
