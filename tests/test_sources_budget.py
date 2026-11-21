"""Europe PMC time budget (it took 18 min instead of 1-3 on 21 Nov 2026): after the budget the remaining, lower-priority searches
are skipped and listed, what was fetched is kept, and the edition gets a run note. No network: fake HTTP and a fake clock."""
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from psychopal import sources, scoring

def test_europepmc_stops_after_its_budget(monkeypatch):
    calls = []
    def fake_get(url, params=None, **kw):
        calls.append(params["query"])
        n = len(calls)
        return {"hitCount": 1, "resultList": {"result": [{"id": f"E{n}", "title": f"Paper {n}", "source": "MED",
                                                          "firstPublicationDate": "2026-11-17"}]}}
    monkeypatch.setattr(sources, "get", fake_get)
    ticks = iter(range(0, 10_000, 100))                       # every clock reading is 100 s later than the last
    recs, info = sources.europepmc(scoring.load_config(), dt.date(2026, 11, 16), dt.date(2026, 11, 22), lambda m: None,
                                   budget_s=300, clock=lambda: next(ticks))
    assert len(calls) == 3 and len(recs) == 3                   # three searches fitted in the budget; their papers are kept
    assert info["skipped_slow"] and info["skipped_slow"][0] not in info["per_group"]
    assert len(info["per_group"]) + len(info["skipped_slow"]) >= 30          # every group was either searched or listed

def test_no_budget_problem_on_a_normal_day(monkeypatch):
    monkeypatch.setattr(sources, "get", lambda url, params=None, **kw: {"hitCount": 0, "resultList": {"result": []}})
    recs, info = sources.europepmc(scoring.load_config(), dt.date(2026, 11, 16), dt.date(2026, 11, 22), lambda m: None,
                                   clock=lambda: 0.0)
    assert "skipped_slow" not in info and info["queries"] >= 30
