"""Exercises summarise.py against a fake Anthropic client (no network, no key): batch path, direct path, 400-retry, caching."""
import json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anthropic
import pytest
from psychopal import summarise

def _msg(items, stop="end_turn"):
    text = json.dumps({"items": [{"id": it_id, "post": f"Post for {it_id}.", "why_you": "Relevant to your PET work.", "tags": ["pet"],
                                  "deadline": "", "event_dates": "", "status": ""} for it_id in items]})
    return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=text)],
                                 usage=types.SimpleNamespace(input_tokens=100, output_tokens=50))

def _ids(params):
    return [l.split(": ", 1)[1] for l in params["messages"][0]["content"].splitlines() if l.startswith("id: ")]

class FakeMessages:
    def __init__(self, reject_thinking=False):
        self.calls, self.reject_thinking = [], reject_thinking
        self.batches = FakeBatches(self)
    def create(self, **params):
        self.calls.append(params)
        if self.reject_thinking and "thinking" in params:
            e = anthropic.BadRequestError.__new__(anthropic.BadRequestError)   # skip __init__ (needs a real HTTP response)
            e.args, e.message = ("thinking type not supported",), "thinking type not supported"
            raise e
        return _msg(_ids(params))

class FakeBatches:
    def __init__(self, parent):
        self.parent, self.reqs = parent, []
    def create(self, requests):
        self.reqs = requests
        return types.SimpleNamespace(id="b1", processing_status="in_progress")
    def retrieve(self, bid):
        return types.SimpleNamespace(id=bid, processing_status="ended")
    def results(self, bid):
        out = []
        for r in self.reqs:
            if self.parent.reject_thinking:
                out.append(types.SimpleNamespace(custom_id=r["custom_id"], result=types.SimpleNamespace(type="errored", error="invalid_request")))
            else:
                out.append(types.SimpleNamespace(custom_id=r["custom_id"], result=types.SimpleNamespace(type="succeeded", message=_msg(_ids(r["params"])))))
        return out
    def cancel(self, bid):
        pass

@pytest.fixture
def fake(monkeypatch, tmp_path):
    holder = {}
    def factory(reject=False):
        fm = FakeMessages(reject)
        monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: types.SimpleNamespace(messages=fm))
        holder["m"] = fm
        return fm
    monkeypatch.setattr(summarise, "CACHE", str(tmp_path / "summaries.jsonl"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return factory

ITEMS = lambda n: [{"id": f"pa-{i}", "kind": "paper", "title": f"Paper {i}", "abstract": "We did things. We found things."} for i in range(n)]

@pytest.mark.parametrize("use_batch", [True, False])
def test_writes_and_caches(fake, use_batch):
    fm = fake()
    cfg = {"enabled": True, "use_batch": use_batch, "items_per_request": 10, "model": "claude-sonnet-5-5", "effort": "low"}
    stats = {}
    items = summarise.summarise(ITEMS(23), cfg, stats)
    assert all(it["ai"] and it["post"].startswith("Post for") for it in items)
    assert stats["llm"]["requests"] == 3 and stats["llm"]["output_tokens"] == 150
    n_calls = len(fm.calls) + len(fm.batches.reqs)
    summarise.summarise(ITEMS(23), cfg, {})            # second run: everything cached, no new calls
    assert len(fm.calls) + len(fm.batches.reqs) == n_calls

def test_rejected_param_retries_without_thinking(fake):
    fm = fake(reject=True)
    cfg = {"enabled": True, "use_batch": True, "items_per_request": 10}
    stats = {}
    items = summarise.summarise(ITEMS(5), cfg, stats)
    assert all(it["ai"] for it in items)                 # recovered via direct retry
    assert "thinking" not in fm.calls[-1]

def test_no_key_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(summarise, "CACHE", str(tmp_path / "s.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    stats = {}
    items = summarise.summarise(ITEMS(2), {"enabled": True}, stats)
    assert not items[0]["ai"] and items[0]["post"].startswith("We did things")
    assert "skipped" in stats["llm"]
