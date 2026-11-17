"""Laptop nudge (psychopal/nudge.py) without network or git: which week is due, nothing happens when it is published, the push
sequence when it is missing, the wait after a nudge, failures logged, and the scheduled-task definition."""
import datetime as dt, json, os, subprocess, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from psychopal import nudge

@pytest.mark.parametrize("today,week", [
    (dt.date(2026, 11, 23), "2026-W47"),     # Monday: the week that just ended
    (dt.date(2026, 11, 22), "2026-W46"),     # Sunday: still the previous one
    (dt.date(2026, 11, 17), "2026-W46"),
    (dt.date(2027, 1, 4), "2026-W53"),       # across the new year
])
def test_last_week(today, week):
    assert nudge.last_week(today) == week

def _site(*weeks):
    def get(url, **kw):
        assert url == "https://alternativestates.github.io/PsychoPal/data/index.json"
        return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"weeks": [{"week": w} for w in weeks]})
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
    monkeypatch.setattr(nudge, "STAMP", str(tmp_path / "nudge.json"))
    return tmp_path

MON = dt.date(2026, 11, 23)

def test_nothing_happens_when_the_edition_is_up(home):
    git = Git()
    assert nudge.main([], today=MON, get=_site("2026-W46", "2026-W47"), run=git) == 0
    assert git.calls == [] and not os.path.exists(nudge.LOG)

def test_missing_edition_pushes_one_commit_on_top_of_origin_main(home):
    git = Git()
    assert nudge.main([], today=MON, get=_site("2026-W46"), run=git) == 0
    assert [c[0] for c in git.calls] == ["fetch", "rev-parse", "commit-tree", "push"]
    assert git.calls[1] == ("rev-parse", "origin/main^{tree}")                       # origin's files, not the local copy
    assert git.calls[2][:4] == ("commit-tree", "tree123", "-p", "origin/main")
    assert git.calls[3] == ("push", "-q", "-f", "origin", "abc1234def:refs/heads/weekly-nudge")
    assert json.load(open(nudge.STAMP))["week"] == "2026-W47" and "nudged GitHub to build 2026-W47" in open(nudge.LOG).read()
    git2 = Git()                                                                       # a second log-on soon after: wait
    nudge.main([], today=MON, get=_site("2026-W46"), run=git2)
    assert git2.calls == [] and "waiting" in open(nudge.LOG).read()

def test_nudges_again_after_the_wait(home):
    json.dump({"week": "2026-W47", "at": (dt.datetime.now() - dt.timedelta(hours=4)).isoformat()}, open(nudge.STAMP, "w"))
    git = Git()
    nudge.main([], today=MON, get=_site("2026-W46"), run=git)
    assert [c[0] for c in git.calls][-1] == "push"

def test_failures_are_logged_not_raised(home):
    assert nudge.main([], today=MON, get=_site("2026-W46"), run=Git(fail="push")) == 1
    assert "FAILED: git push: fatal: Authentication failed" in open(nudge.LOG).read() and not os.path.exists(nudge.STAMP)
    def offline(url, **kw):
        raise ConnectionError("no network")
    assert nudge.main([], today=MON, get=offline, run=Git()) == 1
    assert "could not read the site" in open(nudge.LOG).read()

def test_check_and_force(home, capsys):
    git = Git()
    nudge.main(["--check"], today=MON, get=_site("2026-W46"), run=git)
    assert "2026-W47: missing" in capsys.readouterr().out and git.calls == []
    nudge.main(["--force"], today=MON, get=_site("2026-W47"), run=git)                # test nudge although it's up
    assert git.calls[-1][0] == "push" and "[forced]" in open(nudge.LOG).read()

def test_task_xml():
    x = nudge.task_xml("DOM\\me", r"C:\p\.venv\Scripts\pythonw.exe", r"C:\p")
    for s in ("<LogonTrigger>", "SessionUnlock", "<Monday />", "<Wednesday />", "<StartWhenAvailable>true",
              "InteractiveToken", "<Arguments>-m psychopal.nudge</Arguments>", "pythonw.exe", "<WorkingDirectory>C:\\p<"):
        assert s in x
