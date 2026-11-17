"""Claude plumbing without any API call: thinking setting per model, cost accounting, monthly budget, the once-a-month sweep rule,
the key check, the monthly sweep (fake client), the run summary line and the alerts."""
import datetime as dt, json, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from psychopal import llm, common, monthly_sweep, check_claude, run_weekly, opportunities

def test_thinking_param_per_model():
    assert llm.thinking_param("claude-haiku-5-5", "off", "low") == {"type": "disabled"}
    assert llm.thinking_param("claude-haiku-5-5", False, "low") == {"type": "disabled"}     # YAML reads bare `off` as False
    assert llm.thinking_param("claude-sonnet-5-5", "off", "low") == {"type": "between_tools"}
    assert llm.thinking_param("claude-opus-5-5", "off", "low") is None                   # cannot be switched off
    assert llm.thinking_param("claude-haiku-5-5", "adaptive", "medium") is None          # adaptive = default: leave it out
    assert llm.thinking_param("claude-haiku-5-5", "off", "max") is None

def test_cost_usd():
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert llm.cost_usd("claude-haiku-5-5", u) == pytest.approx(0.60)
    assert llm.cost_usd("claude-haiku-5-5", u, batch=True) == pytest.approx(0.30)
    assert llm.cost_usd("claude-sonnet-5-5", {"web_searches": 6}) == pytest.approx(0.06)
    assert llm.cost_usd("claude-sonnet-5-5", {"cache_read": 1_000_000}) == pytest.approx(0.20)
    assert llm.cost_usd("some-new-model", u) == llm.cost_usd("claude-sonnet-5-5", u)      # unknown: priced high

def test_usage_of_reads_cache_and_search_fields():
    msg = types.SimpleNamespace(usage=types.SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=None,
                                                            cache_read_input_tokens=7,
                                                            server_tool_use=types.SimpleNamespace(web_search_requests=3)))
    assert llm.usage_of(msg) == {"input_tokens": 10, "output_tokens": 5, "cache_write": 0, "cache_read": 7, "web_searches": 3}

def test_month_spend_and_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "STATE", str(tmp_path))
    common.append_jsonl(str(tmp_path / "runs.jsonl"), [{"finished": "2026-11-09T07:00:00", "claude": {"cost_usd": 0.01}},
                                                      {"finished": "2026-10-30T07:00:00", "claude": {"cost_usd": 5.0}},   # last month
                                                      {"finished": "2026-11-16T07:00:00", "llm": {"off": True}}])
    common.append_jsonl(str(tmp_path / "sweeps.jsonl"), [{"date": "2026-11-02", "cost_usd": 0.25}])
    today = dt.date(2026, 11, 20)
    assert llm.month_spend(today) == pytest.approx(0.26)
    assert llm.budget_left(1.0, 0.04, today) == pytest.approx(0.70)

@pytest.mark.parametrize("rows,due", [
    ([], True),
    ([{"date": "2026-11-02", "ok": True, "cost_usd": 0.2}], False),
    ([{"date": "2026-11-02", "ok": False, "cost_usd": 0.0, "error": "APIConnectionError"}], True),    # failed for free: retry
    ([{"date": "2026-11-02", "ok": False, "cost_usd": 0.1, "error": "JSONDecodeError"}], False),     # paid: don't pay twice
    ([{"date": "2026-11-02", "ok": True, "dry_run": True, "cost_usd": 0.2}], True),                  # tests never count
    ([{"date": "2026-10-05", "ok": True}], True),                                                    # last month
    ([{"date": "2026-11-02", "proposed": 4}], False),                                                # old-format row
])
def test_due_this_month(rows, due):
    assert llm.due_this_month(rows, dt.date(2026, 11, 17)) is due

def test_key_shape_never_shows_the_key():
    s = check_claude.key_shape("sk-ant-api03-SECRETSECRET\n")
    assert "SECRET" not in s and "sk-ant-api" in s and "stripped" in s
    assert "ADMIN" in check_claude.key_shape("sk-ant-admin01-x")

def test_check_claude_off_without_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(check_claude, "load_yaml", lambda n: {"llm": {"enabled": False}})
    assert check_claude.main(["--force"]) == 0 and "OFF" in capsys.readouterr().out

class _KeyCheckClient:
    def __init__(self, exc=None):
        def create(**p):
            if exc:
                raise exc
            return types.SimpleNamespace(headers={"anthropic-workspace-id": "wrkspc_PRIVATEWS"},
                                         parse=lambda: types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="ready")]))
        self.messages = types.SimpleNamespace(with_raw_response=types.SimpleNamespace(create=create))
        self.models = types.SimpleNamespace(retrieve=lambda m: None)

@pytest.mark.parametrize("fail", [False, True])
def test_check_claude_saves_only_the_verdict(tmp_path, monkeypatch, capsys, fail):
    import anthropic
    exc = None
    if fail:                                     # built without an HTTP response object, so the test doesn't depend on SDK internals
        exc = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
        exc.message = "invalid x-api-key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SECRETSECRET")
    monkeypatch.setattr(check_claude, "load_yaml", lambda n: {"llm": {"enabled": False, "model": "claude-haiku-5-5"},
                                                              "sweep": {"enabled": True, "model": "claude-sonnet-5-5"}})
    monkeypatch.setattr(check_claude, "anthropic_client", lambda: _KeyCheckClient(exc))
    out = tmp_path / "claude_check.json"
    assert check_claude.main(["--force", "--save", str(out)]) == (1 if fail else 0)
    printed, saved = capsys.readouterr().out, json.load(open(out, encoding="utf-8"))
    assert "characters" in printed and "SECRET" not in printed                  # the log shows the key's shape only
    assert saved["status"] == ("fail" if fail else "ok") and "key tested anyway" in saved["result"]
    assert "rejected" in saved["result"] if fail else "claude-sonnet-5-5 available" in saved["result"]
    assert not any(s in json.dumps(saved) for s in ("SECRET", "sk-ant", "characters", "wrkspc"))   # public file: verdict only

# ---------------- monthly sweep ----------------
class FakeSweepClient:
    """First answer pauses the turn after using up all searches; the JSON is split across two text blocks (citations)."""
    def __init__(self, searches=6):
        self.calls, self.searches = [], searches
        self.messages = self
    def create(self, **params):
        self.calls.append(params)
        props = [{"kind": "funding", "name": "Brand New Psychedelic Fellowship", "url": "https://example.org/fellowship",
                  "organiser": "Example Trust", "why": "Fits your PET work.", "deadline": "", "event_dates": "", "location": "UK"},
                 {"kind": "nonsense", "name": "Bad", "url": "ftp://x"}]
        text = "Here is what I found.\n```json\n" + json.dumps(props) + "\n```"
        cut = text.index("Example Trust")
        blocks = [types.SimpleNamespace(type="text", text=text[:cut]), types.SimpleNamespace(type="web_search_tool_result"),
                  types.SimpleNamespace(type="text", text=text[cut:])]
        u = types.SimpleNamespace(input_tokens=30000, output_tokens=2000,
                                  server_tool_use=types.SimpleNamespace(web_search_requests=self.searches))
        return types.SimpleNamespace(stop_reason="pause_turn", content=blocks, usage=u)

def test_sweep_stops_at_search_cap_and_parses_split_json(tmp_path, monkeypatch):
    monkeypatch.setattr(monthly_sweep, "SWEEPS", str(tmp_path / "sweeps.jsonl"))
    monkeypatch.setattr(monthly_sweep, "page_text", lambda url: "ok")
    written = []
    monkeypatch.setattr(monthly_sweep, "write_entry", lambda p, today: written.append(p))
    fc = FakeSweepClient(searches=6)
    row = monthly_sweep.run_sweep({"model": "claude-sonnet-5-5", "max_searches": 6}, dry=True, client=fc)
    assert len(fc.calls) == 1                                   # cap reached: no resume
    assert "thinking" not in fc.calls[0]                         # sweep keeps adaptive thinking
    assert fc.calls[0]["tools"][0]["max_uses"] == 6
    assert row["ok"] and row["dry_run"] and row["added"] == ["Brand New Psychedelic Fellowship"] and written == []
    assert row["rejected"][0][1] == "bad fields"
    assert row["cost_usd"] == pytest.approx(llm.cost_usd("claude-sonnet-5-5", {"input_tokens": 30000, "output_tokens": 2000,
                                                                               "web_searches": 6}))
    saved = common.load_jsonl(str(tmp_path / "sweeps.jsonl"))[-1]
    assert saved["dry_run"] and llm.due_this_month([saved])     # a dry run doesn't use up the month

def test_sweep_failure_is_logged_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(monthly_sweep, "SWEEPS", str(tmp_path / "sweeps.jsonl"))
    bad = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **p: (_ for _ in ()).throw(RuntimeError("down"))))
    row = monthly_sweep.run_sweep({"model": "claude-sonnet-5-5"}, client=bad)
    assert not row["ok"] and "down" in row["error"] and row["cost_usd"] == 0
    assert llm.due_this_month([row])                             # free failure: the month is still due

# ---------------- run summary, alerts, opportunities ----------------
def test_claude_summary_line(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "STATE", str(tmp_path))
    stats = {"llm": {"written": 40, "cost_usd": 0.006}, "rerank": {"rated": 80, "cost_usd": 0.002},
             "taste": {"skipped": "6 marked cards (needs 10)"}, "papers": {"claude_moves": {"in": ["a", "b"], "out": ["c", "d"]}}}
    s = run_weekly.claude_summary(stats, {"monthly_budget_usd": 1.0}, True)
    assert s["cost_usd"] == pytest.approx(0.008) and s["month_to_date_usd"] == pytest.approx(0.008)
    assert "cards 40 new" in s["line"] and "2 in / 2 out" in s["line"] and "needs 10" in s["line"]
    assert run_weekly.claude_summary({}, {}, False)["cost_usd"] == 0.0

def test_budget_alert():
    s = {"llm": {"enabled": True, "monthly_budget_usd": 1.0}}
    assert "budget ($1.00) is used up" in run_weekly.claude_alerts({"budget_stop": "x"}, s)[0]

def test_finalise_hides_irrelevant_ukri_and_closed_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(opportunities, "STATE_FILE", str(tmp_path / "opportunities.json"))
    st = {"entries": {}, "first_run_done": True}
    ctx = {"state": st, "today": dt.date(2026, 11, 16), "week": "2026-W47", "stats": {}}
    items = [{"id": "u1", "kind": "funding", "title": "Studentship", "_ukri": True, "relevant": "no"},
             {"id": "u2", "kind": "funding", "title": "MRC fellowship", "_ukri": True, "relevant": "yes"},
             {"id": "p1", "kind": "conference", "entry": "conference:x", "title": "Closed meeting", "_extract": True, "status": "closed",
              "ai": True},
             {"id": "p2", "kind": "conference", "entry": "conference:y", "title": "Open meeting", "_extract": True, "status": "open",
              "ai": True}]
    cards, _ = opportunities.finalise(items, ctx, {"deadlines": False, "_llm_on": True}, set())
    assert sorted(c["id"] for c in cards) == ["p2", "u2"] and ctx["stats"]["ukri_hidden"] == 1
