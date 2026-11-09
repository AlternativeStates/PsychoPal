"""Claude-free mode: structured abstract summaries, Claude switched off, opportunities without deadlines."""
import datetime as dt, json, os, sys
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from psychopal import abstracts, summarise, opportunities, run_weekly, sources
from psychopal.common import clean

LABELLED = ("Background: Psilocybin produces rapid antidepressant effects, but its mechanism in humans remains unclear. "
            "Objective: To test whether psilocybin increases synaptic density measured with PET in major depressive disorder. "
            "Methods: Thirty-five participants with moderate-to-severe depression were randomized to 25 mg psilocybin or niacin and scanned "
            "with [11C]UCB-J PET at baseline and one week later. "
            "Results: Depression scores fell more after psilocybin than niacin (p < 0.01). SV2A binding did not increase in the hippocampus "
            "or frontal cortex (all p > 0.3). Higher baseline binding predicted a larger clinical response (r = 0.52, p = 0.004). "
            "Conclusions: Antidepressant effects of psilocybin can occur without a detectable increase in synaptic density. "
            "Trial registration: NCT01234567.")

UNSTRUCTURED = ("Ketamine has rapid antidepressant effects in treatment-resistant depression, but its effect on working memory circuits "
                "remains unclear. Here we tested whether a single ketamine infusion changes working memory performance and brain activity. "
                "We recruited 22 patients and 19 healthy volunteers in a randomized, double-blind, placebo-controlled crossover trial with "
                "3T fMRI during an N-back task. Ketamine significantly reduced depression scores compared with placebo (p < 0.01). "
                "Task performance did not differ between conditions, but parietal activation decreased after ketamine (p = 0.01). "
                "These findings suggest that ketamine's antidepressant effects occur without working memory changes. "
                "Interpretation is limited by the modest sample size.")

def test_labelled_abstract_maps_to_four_parts():
    parts = dict(abstracts.structure(LABELLED))
    assert list(parts) == ["Aim", "Methods", "Results", "Conclusion"]
    assert parts["Aim"].startswith("To test whether psilocybin")
    assert "r = 0.52" in parts["Results"] and "NCT" not in parts["Conclusion"]

def test_openalex_style_headings_without_colons():
    text = LABELLED.replace("Background:", "Background").replace("Methods:", "Methods").replace("Results:", "Results") \
                   .replace("Conclusions:", "Conclusions").replace("Objective:", "Objective")
    assert [k for k, _ in abstracts.structure("Abstract " + text)] == ["Aim", "Methods", "Results", "Conclusion"]

def test_unstructured_abstract_gets_parts_in_order_and_skips_limitations():
    parts = dict(abstracts.structure(UNSTRUCTURED))
    assert list(parts) == ["Aim", "Methods", "Results", "Conclusion"]
    assert parts["Aim"].startswith("Here we tested")
    assert parts["Conclusion"].startswith("These findings suggest")
    assert "limited" not in " ".join(parts.values())

def test_short_abstract_gives_no_structure():
    assert abstracts.structure("We show that psilocybin is interesting. It does things.") == []

def test_long_sentence_is_trimmed_with_ellipsis():
    out = abstracts.trim(["word " * 80], 30)
    assert out.endswith("…") and len(out.split()) <= 31

def test_source_parsers_keep_section_labels():
    x = ET.fromstring('<AbstractText Label="METHODS" NlmCategory="METHODS">We scanned <i>n</i> = 20 people.</AbstractText>')
    assert sources._labelled_part(x) == "Methods: We scanned n = 20 people."
    assert clean(sources.headings("<h4>Background</h4>Text. <h4>Results</h4>More.")) == "Background: Text. Results: More."

def test_summarise_off_gives_sections_and_never_calls_or_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(summarise, "CACHE", str(tmp_path / "summaries.jsonl"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(summarise, "call_claude", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Claude must not be called")))
    items = [{"id": "pa-1", "kind": "paper", "title": "T", "abstract": UNSTRUCTURED},
             {"id": "fu-1", "kind": "funding", "title": "F", "notes": "Fellowship for postdocs.", "page_text": "x"}]
    stats = {}
    summarise.summarise(items, {"enabled": False}, stats)
    assert stats["llm"] == {"off": True}
    assert [k for k, _ in items[0]["sections"]] == ["Aim", "Methods", "Results", "Conclusion"]
    assert items[0]["post"].startswith("Aim: ") and items[0]["ai"] is False
    assert items[1]["post"] == "Fellowship for postdocs." and "sections" not in items[1]
    assert not os.path.exists(tmp_path / "summaries.jsonl")

def test_no_alerts_when_claude_off():
    s = {"llm": {"enabled": False, "key_expires": "2026-11-20"}}
    assert run_weekly.claude_alerts({"off": True}, s, today=dt.date(2026, 11, 9)) == []

def _opp(key, h, notes="Note"):
    return {"kind": "funding", "entry": key, "title": key, "url": "https://x", "id": f"fu-{key}-{h}", "notes": notes, "post": notes,
            "deadline": "2026-12-01", "_extract": True, "reason": "NEW ON YOUR RADAR", "_hash": h, "status": ""}

def test_opportunities_without_claude_or_deadlines(tmp_path, monkeypatch):
    monkeypatch.setattr(opportunities, "STATE_FILE", str(tmp_path / "opportunities.json"))
    settings = {"deadlines": False, "_llm_on": False}
    st = {"entries": {}, "first_run_done": False}
    ctx = {"state": st, "today": dt.date(2026, 11, 9), "week": "2026-W46"}
    # first run: everything is new -> nothing shown (no flood), all marked as read, no deadline strip
    cards, strip = opportunities.finalise([_opp("a", "h1"), _opp("b", "h1")], ctx, settings, set())
    assert cards == [] and strip == []
    assert st["first_run_done"] and st["entries"]["a"]["extracted"]
    # later week: a genuinely new entry is shown, without any deadline
    ctx = {"state": st, "today": dt.date(2026, 11, 16), "week": "2026-W47"}
    cards, strip = opportunities.finalise([_opp("c", "h1")], ctx, settings, set())
    assert [c["entry"] for c in cards] == ["c"] and not cards[0]["deadline"] and strip == []
