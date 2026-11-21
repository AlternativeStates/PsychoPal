"""Laptop nudge (psychopal/nudge.py) without network or git: when last week counts as done (an app refresh that stopped mid-week
does not), nothing happens when it is done, the push sequence when it is missing, retries when offline, failures logged, and the
once-a-week scheduled task."""
import datetime as dt, os, subprocess, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from psychopal import nudge

MON = dt.date(2026, 11, 23)                              # last week = 16-22 Nov 2026 (W47)
W = lambda week, start, end: {"week": week, "start": start, "end": end}

@pytest.mark.parametrize("weeks,done", [
    ([W("2026-W46", "2026-11-09", "2026-11-15")], False),                  # last week not built
    ([W("2026-W47", "2026-11-16", "2026-11-22")], True),                   # built normally
    ([W("2026-W47", "2026-11-16", "2026-11-21")], False),                  # app refresh "so far" on Saturday: complete it
    ([W("2026-W47", "2026-11-18", "2026-11-22")], False),                  # started mid-week: rebuild from Monday
    ([W("2026-W48", "2026-11-16", "2026-11-23")], True),                   # app refresh "from 16 Nov to today" covers it
])
def test_covered(weeks, done):
    assert nudge.covered(weeks, dt.date(2026, 11, 16), dt.date(2026, 11, 22)) is done

def _site(*weeks):
    def get(url, **kw):
        assert url == "https://alternativestates.github.io/PsychoPal/data/index.json"
        return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"weeks": list(weeks)})
    return get

class Git:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail
    def __call__(self, *args):
        self.calls.append(args)
        if self.fail == args[0]:
            raise subprocess.CalledProcessError(1, ["git", "-C", "x", *args], stderr="fatal: Authentication failed")
        return {"rev-parse": "tree123", "commit-tree": "abc1234def"}.get(args[0], "")

@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(nudge, "HOME", str(tmp_path))
    monkeypatch.setattr(nudge, "LOG", str(tmp_path / "nudge.log"))
    return tmp_path

DONE = W("2026-W47", "2026-11-16", "2026-11-22")
OLD = W("2026-W46", "2026-11-09", "2026-11-15")

def test_nothing_happens_when_last_week_is_done():
    git = Git()
    assert nudge.main([], today=MON, get=_site(DONE, OLD), run=git, sleep=lambda s: None) == 0
    assert git.calls == [] and not os.path.exists(nudge.LOG)

def test_missing_week_pushes_one_commit_on_top_of_origin_main():
    git = Git()
    assert nudge.main([], today=MON, get=_site(OLD), run=git, sleep=lambda s: None) == 0
    assert [c[0] for c in git.calls] == ["fetch", "rev-parse", "commit-tree", "push"]
    assert git.calls[1] == ("rev-parse", "origin/main^{tree}")                        # origin's files, not the local copy
    assert git.calls[2][:4] == ("commit-tree", "tree123", "-p", "origin/main")
    assert git.calls[3] == ("push", "-q", "-f", "origin", "abc1234def:refs/heads/weekly-nudge")
    assert "nudged GitHub to build 2026-W47" in open(nudge.LOG).read()

def test_offline_retries_then_gives_up_quietly():
    naps = []
    def offline(url, **kw):
        raise ConnectionError("no network")
    assert nudge.main([], today=MON, get=offline, run=Git(), sleep=naps.append) == 1
    assert len(naps) == nudge.TRIES - 1 and set(naps) == {nudge.PAUSE_S}
    assert "FAILED after 7 tries (could not read the site" in open(nudge.LOG).read()

def test_comes_back_online_on_a_later_try():
    calls = {"n": 0}
    def flaky(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("no network yet")
        return _site(OLD)(url)
    git = Git()
    assert nudge.main([], today=MON, get=flaky, run=git, sleep=lambda s: None) == 0
    assert git.calls[-1][0] == "push" and calls["n"] == 3

def test_git_failure_is_logged():
    assert nudge.main([], today=MON, get=_site(OLD), run=Git(fail="push"), sleep=lambda s: None) == 1
    assert "git push: fatal: Authentication failed" in open(nudge.LOG).read()

def test_check_and_force(capsys):
    git = Git()
    nudge.main(["--check"], today=MON, get=_site(OLD), run=git)
    assert "2026-W47: missing" in capsys.readouterr().out and git.calls == []
    nudge.main(["--force"], today=MON, get=_site(DONE), run=git)                    # test nudge although it's done
    assert git.calls[-1][0] == "push" and "[forced]" in open(nudge.LOG).read()

def test_task_runs_once_a_week():
    x = nudge.task_xml("DOM\\me", r"C:\p\.venv\Scripts\pythonw.exe", r"C:\p")
    assert x.count("Trigger>") == 2 and "<CalendarTrigger>" in x and "<Monday />" in x       # one trigger only
    assert "LogonTrigger" not in x and "SessionUnlock" not in x and "<Tuesday />" not in x
    for s in ("<StartWhenAvailable>true", "InteractiveToken", "<Arguments>-m psychopal.nudge</Arguments>", "pythonw.exe",
              "<WorkingDirectory>C:\\p<", "<ExecutionTimeLimit>PT2H"):
        assert s in x
