"""Exercises summarise.py against a fake Anthropic client (no network, no key): batch and direct paths, 400-retry, refusals, caching,
prompt versions, thin items, the monthly budget and the cost accounting."""
import json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anthropic
import pytest
from psychopal import summarise, llm, common
from psychopal.abstracts import as_text

ABSTRACT = ("Psychedelics may promote synaptic plasticity, but direct evidence in living humans remains limited and indirect. "
            "Here we tested whether a single dose changes synaptic density. We measured SV2A density with [11C]UCB-J PET in 20 "
            "healthy volunteers before and 7 days after 25 mg psilocybin. Synaptic density did not change overall, although "
            "hippocampal binding increased by 6% in a subgroup of responders. These findings suggest that psilocybin does not "
            "robustly change synaptic density one week after dosing.")

def _answer(it_id):
    if it_id.startswith("pa-"):
        return {"id": it_id, "aim": "To test whether psilocybin changes synaptic density in humans.",
                "methods": "SV2A PET in 20 healthy volunteers before and 7 days after 25 mg psilocybin.",
                "results": "No overall change in synaptic density; hippocampal binding rose in a subgroup.",
                "conclusion": "Psilocybin does not robustly change synaptic density at one week.",
                "post": "", "why_you": "Directly tests the plasticity claim with your PET method.", "tags": ["psilocybin", "pet"],
                "status": "", "deadline": "", "event_dates": "", "relevant": "yes"}
    return {"id": it_id, "aim": "", "methods": "", "results": "", "conclusion": "", "post": f"Post for {it_id}.",
            "why_you": "Relevant to your PET work.", "tags": ["fellowship"], "status": "open", "deadline": "2027-03-05",
            "event_dates": "", "relevant": "no" if it_id.startswith("uk-") else "yes"}

def _msg(ids, stop="end_turn"):
    text = json.dumps({"items": [_answer(i) for i in ids]})
    return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=text)],
                                 usage=types.SimpleNamespace(input_tokens=100, output_tokens=50))

def _ids(params):
    return [l.split(": ", 1)[1] for l in params["messages"][0]["content"].splitlines() if l.startswith("id: ")]

class FakeMessages:
    def __init__(self, reject_thinking=False, refuse_multi=False):
        self.calls, self.reject_thinking, self.refuse_multi = [], reject_thinking, refuse_multi
        self.batches = FakeBatches(self)
    def respond(self, params):
        ids = _ids(params)
        if self.refuse_multi and len(ids) > 1:
            return _msg([], stop="refusal")
        return _msg(ids)
    def create(self, **params):
        self.calls.append(params)
        if self.reject_thinking and "thinking" in params:
            e = anthropic.BadRequestError.__new__(anthropic.BadRequestError)   # skip __init__ (needs a real HTTP response)
            e.args, e.message = ("thinking type not supported",), "thinking type not supported"
            raise e
        return self.respond(params)

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
                out.append(types.SimpleNamespace(custom_id=r["custom_id"],
                                                 result=types.SimpleNamespace(type="succeeded", message=self.parent.respond(r["params"]))))
        return out
    def cancel(self, bid):
        pass

@pytest.fixture
def fake(monkeypatch, tmp_path):
    holder = {}
    def factory(reject=False, refuse_multi=False):
        fm = FakeMessages(reject, refuse_multi)
        monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: types.SimpleNamespace(messages=fm))
        holder["m"] = fm
        return fm
    monkeypatch.setattr(summarise, "CACHE", str(tmp_path / "summaries.jsonl"))
    monkeypatch.setattr(common, "STATE", str(tmp_path))          # budget: empty runs.jsonl / sweeps.jsonl
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return factory

ITEMS = lambda n: [{"id": f"pa-{i}", "kind": "paper", "title": f"Paper {i}", "abstract": ABSTRACT} for i in range(n)]
CFG = {"enabled": True, "items_per_request": 10, "model": "claude-haiku-5-5", "effort": "low", "thinking": "off"}

@pytest.mark.parametrize("use_batch", [True, False])
def test_writes_structured_summaries_and_caches(fake, use_batch):
    fm = fake()
    stats = {}
    items = summarise.summarise(ITEMS(23), {**CFG, "use_batch": use_batch}, stats)
    assert all(it["ai"] for it in items)
    secs = items[0]["sections"]
    assert [k for k, _ in secs] == ["Aim", "Methods", "Results", "Conclusion"]
    assert items[0]["post"] == as_text(secs) and items[0]["why_you"].startswith("Directly")
    assert stats["llm"]["requests"] == 3 and stats["llm"]["output_tokens"] == 150 and stats["llm"]["written"] == 23
    # Haiku 5.5: $0.10 / $0.50 per MTok, halved in batch mode
    assert stats["llm"]["cost_usd"] == pytest.approx(llm.cost_usd("claude-haiku-5-5", {"input_tokens": 300, "output_tokens": 150},
                                                                  batch=use_batch))
    sent = [r["params"] for r in fm.batches.reqs] if use_batch else fm.calls
    assert sent[0]["thinking"] == {"type": "disabled"} and sent[0]["model"] == "claude-haiku-5-5"
    assert sent[0]["messages"][0]["content"].startswith("Today is ")
    n_calls = len(fm.calls) + len(fm.batches.reqs)
    again = summarise.summarise(ITEMS(23), {**CFG, "use_batch": use_batch}, {})   # second run: all cached, no new calls
    assert len(fm.calls) + len(fm.batches.reqs) == n_calls and again[0]["sections"] == secs

def test_other_cards_get_a_post_and_opportunity_fields(fake):
    fake()
    items = [{"id": "fu-1", "kind": "funding", "title": "Fellowship", "page_text": "Apply by 5 March 2027.", "notes": "x"},
             {"id": "uk-1", "kind": "funding", "title": "UKRI studentship", "description": "PhD studentships.", "_ukri": True}]
    summarise.summarise(items, {**CFG, "use_batch": False}, {})
    assert items[0]["post"] == "Post for fu-1." and "sections" not in items[0]
    assert items[0]["status"] == "open" and items[0]["deadline"] == "2027-03-05"
    assert items[1]["relevant"] == "no"

def test_thin_items_are_not_sent(fake):
    fm = fake()
    items = [{"id": "pa-thin", "kind": "paper", "title": "T", "abstract": "Too short to summarise."}] + ITEMS(1)
    stats = {}
    summarise.summarise(items, {**CFG, "use_batch": False}, stats)
    assert _ids(fm.calls[0]) == ["pa-0"] and stats["llm"]["skipped_thin"] == 1
    assert not items[0]["ai"]

def test_old_prompt_version_is_rewritten(fake, tmp_path):
    fm = fake()
    with open(summarise.CACHE, "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "pa-0", "post": "old v1 text", "ai": True}) + "\n")              # v1 row (no "v")
        f.write(json.dumps({"id": "pa-1", "post": "current", "ai": True, "v": summarise.PROMPT_VERSION}) + "\n")
    items = summarise.summarise(ITEMS(2), {**CFG, "use_batch": False}, {})
    assert _ids(fm.calls[0]) == ["pa-0"]
    assert items[0]["sections"] and items[1]["post"] == "current"

def test_long_parts_are_trimmed(fake, monkeypatch):
    fake()
    long = " ".join(["word"] * 120) + "."
    monkeypatch.setattr(sys.modules[__name__], "_answer", lambda i: {**_ANSWER(i), "results": long})
    items = summarise.summarise(ITEMS(1), {**CFG, "use_batch": False}, {})
    res = dict(items[0]["sections"])["Results"]
    assert len(res.split()) <= summarise.LIMITS["Results"] + 9

_ANSWER = _answer

def test_refusal_retries_items_one_by_one(fake):
    fm = fake(refuse_multi=True)
    stats = {}
    items = summarise.summarise(ITEMS(3), {**CFG, "use_batch": True}, stats)
    assert all(it["ai"] for it in items)
    assert stats["llm"]["refusals"] == 1 and len(fm.calls) == 3          # 3 single-item retries

def test_rejected_param_retries_without_thinking(fake):
    fm = fake(reject=True)
    items = summarise.summarise(ITEMS(5), {**CFG, "use_batch": True}, {})
    assert all(it["ai"] for it in items)                 # recovered via direct retry
    assert "thinking" not in fm.calls[-1]

def test_budget_used_up_falls_back(fake, monkeypatch):
    fm = fake()
    monkeypatch.setattr(llm, "month_spend", lambda today=None: 5.0)
    stats = {}
    items = summarise.summarise(ITEMS(2), {**CFG, "monthly_budget_usd": 1.0}, stats)
    assert "budget_stop" in stats["llm"] and not fm.calls and not fm.batches.reqs
    assert not items[0]["ai"] and items[0]["sections"]               # scripted summary instead

def test_no_key_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(summarise, "CACHE", str(tmp_path / "s.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    stats = {}
    items = summarise.summarise(ITEMS(2), {"enabled": True}, stats)
    assert not items[0]["ai"] and items[0]["sections"]
    assert "skipped" in stats["llm"]
