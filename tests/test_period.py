"""Editions for any period (the app's ⟳ button): which week an edition is filed under, its period, the "(so far)" label, keeping a
longer period on re-runs, the 4-week cap, per-week amounts scaled to the period, and the feed index order. No network."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from psychopal import run_weekly as rw, build_site, common

SAT = dt.date(2026, 11, 21)          # Saturday of W47 (16-22 Nov)
MON = dt.date(2026, 11, 23)          # Monday of W48
D = dt.date.fromisoformat

@pytest.mark.parametrize("today,kw,existing,expect", [
    (MON, {}, None, ("2026-11-16", "2026-11-22", "2026-W47")),                                   # automatic: last full week
    (SAT, {"end": "2026-11-21"}, None, ("2026-11-16", "2026-11-21", "2026-W47")),               # this week so far
    (SAT, {"end": "2026-11-30"}, None, ("2026-11-16", "2026-11-21", "2026-W47")),               # never later than today
    (SAT, {"start": "2026-11-02", "end": "2026-11-21"}, None, ("2026-11-02", "2026-11-21", "2026-W47")),   # from a date
    (SAT, {"start": "2026-09-01", "end": "2026-11-21"}, None, ("2026-10-25", "2026-11-21", "2026-W47")),   # capped at 28 days
    (MON, {}, {"start": "2026-11-02"}, ("2026-11-02", "2026-11-22", "2026-W47")),               # Monday run keeps the longer period
    (MON, {"end": "2026-11-22"}, None, ("2026-11-16", "2026-11-22", "2026-W47")),               # app: "last week"
    (MON, {"week_end": "2026-11-08"}, None, ("2026-11-02", "2026-11-08", "2026-W45")),          # GitHub button: rebuild a week
    (MON, {"start": "2026-11-20", "end": "2026-11-23"}, {"start": "2026-11-16"},
          ("2026-11-20", "2026-11-23", "2026-W48")),                                             # an explicit start wins
])
def test_edition_window(today, kw, existing, expect):
    s, e, w = rw.edition_window(today, kw.get("week_end"), kw.get("start"), kw.get("end"), existing)
    assert (str(s), str(e), w) == expect

def test_label():
    assert rw.edition_label("2026-W47", D("2026-11-22")) == "Week 47, 2026"
    assert rw.edition_label("2026-W47", D("2026-11-21")) == "Week 47, 2026 (so far)"
    assert rw.edition_label("2027-W01", D("2027-01-10")) == "Week 1, 2027"

def test_scale_for_period():
    s = common.load_yaml("settings.yaml")
    assert rw.scale_for_period(s, 7) is s                                    # a normal week: untouched
    short = rw.scale_for_period(s, 3)
    assert short["papers_total"] == round(s["papers_total"] * 3 / 7) and short["papers_total"] < s["papers_total"]
    assert short["stream_quota"]["psychedelics"] == [3, 4] and short["top_for_you"] == 5
    assert rw.scale_for_period(s, 1)["papers_total"] == round(s["papers_total"] * 0.3)       # floor for one day
    long = rw.scale_for_period(s, 21)
    assert long["papers_total"] == s["papers_total"] * 3 and long["stream_quota"]["psychedelics"] == [21, 30]
    assert long["wider"]["cards"] == s["wider"]["cards"] * 3 and long["wider"]["max_seconds"] == s["wider"]["max_seconds"]
    assert s["papers_total"] == 25                                           # the original settings are not modified

def test_index_newest_period_first(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site, "SITE", str(tmp_path))
    def ed(week, start, end):
        return {"week": week, "start": start, "end": end, "generated": "x", "cards": []}
    build_site.write_edition(ed("2026-W46", "2026-11-09", "2026-11-15"), {})
    build_site.write_edition(ed("2026-W47", "2026-11-02", "2026-11-21"), {})   # refresh from 2 Nov: starts before W46
    idx = json.load(open(tmp_path / "data" / "index.json", encoding="utf-8"))
    assert [w["week"] for w in idx["weeks"]] == ["2026-W47", "2026-W46"] and idx["latest"] == "2026-W47"
