"""Laptop nudge: starts the weekly build, because GitHub's own `schedule` trigger never fires for this repo (tested Nov 2026).

A Windows scheduled task on Claudio's laptop runs this at log-on, at unlock and on Monday-Wednesday mornings (a missed run starts
as soon as the laptop is on again). It reads the live site; if last week's edition is not there yet, it pushes one small commit to
the `weekly-nudge` branch with the GitHub login git already has. `.github/workflows/nudge.yml` then starts the weekly workflow on
main (only_if_missing=true, so a second nudge can never build twice). Pushes are reliable, unlike GitHub's timer.

It uploads nothing from the local working copy: the commit reuses origin/main's files exactly. It never touches the local
branches or files. When the edition is already up, it does nothing but one web request.

  python -m psychopal.nudge             # nudge if last week's edition is missing (what the scheduled task runs)
  python -m psychopal.nudge --check     # only say whether a nudge is needed
  python -m psychopal.nudge --force     # nudge even if the edition is up (test: the run then stops after ~10 s)
  python -m psychopal.nudge --install   # (re)create the Windows scheduled task "PsychoPal weekly nudge"
  python -m psychopal.nudge --uninstall # remove it

Log: %LOCALAPPDATA%\\PsychoPal\\nudge.log (last 200 lines kept)."""
import datetime as dt, json, os, subprocess, sys, tempfile
import requests
from .common import load_yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PsychoPal")
LOG, STAMP = os.path.join(HOME, "nudge.log"), os.path.join(HOME, "nudge.json")
BRANCH, TASK = "weekly-nudge", "PsychoPal weekly nudge"
WAIT_HOURS = 3          # after a nudge, wait this long before nudging again (a build takes ~10 min; failed builds are retried)

def log(msg):
    os.makedirs(HOME, exist_ok=True)
    lines = []
    if os.path.exists(LOG):
        lines = open(LOG, encoding="utf-8").read().splitlines()[-199:]
    lines.append(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {msg}")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(msg)

def last_week(today=None):
    """The edition due now: the last full ISO week (Mon-Sun) before today's week, same rule as the workflow's check job."""
    t = today or dt.date.today()
    y, w, _ = (t - dt.timedelta(days=t.weekday() + 7)).isocalendar()
    return f"{y}-W{w:02d}"

def published(site_url, week, get=requests.get):
    r = get(site_url.rstrip("/") + "/data/index.json", timeout=30, headers={"User-Agent": "psychopal-nudge",
                                                                              "Cache-Control": "no-cache"})
    r.raise_for_status()
    return any(x.get("week") == week for x in r.json().get("weeks", []))

def recently_nudged(week, now=None):
    try:
        s = json.load(open(STAMP, encoding="utf-8"))
    except Exception:
        return False
    when = dt.datetime.fromisoformat(s.get("at", "2000-01-01T00:00"))
    return s.get("week") == week and (now or dt.datetime.now()) - when < dt.timedelta(hours=WAIT_HOURS)

def git(*args):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, check=True, env=env,
                          creationflags=flags).stdout.strip()

def push_nudge(week, run=git):
    """One new commit on top of origin/main with origin/main's own files, force-pushed to the weekly-nudge branch."""
    run("fetch", "-q", "origin", "main")
    tree = run("rev-parse", "origin/main^{tree}")
    sha = run("commit-tree", tree, "-p", "origin/main", "-m", f"Nudge: build edition {week}")
    run("push", "-q", "-f", "origin", f"{sha}:refs/heads/{BRANCH}")
    return sha

def main(argv=None, today=None, get=requests.get, run=git):
    args = argv if argv is not None else sys.argv[1:]
    if "--install" in args or "--uninstall" in args:
        return install(remove="--uninstall" in args)
    week = last_week(today)
    site = load_yaml("settings.yaml").get("site_url")
    try:
        up = published(site, week, get)
    except Exception as e:
        log(f"could not read the site ({type(e).__name__}); will try again at the next log-on/unlock")
        return 1
    if "--check" in args:
        print(f"{week}: {'published' if up else 'missing, a nudge is needed'}")
        return 0
    if up and "--force" not in args:
        return 0                                     # the normal case: nothing to do, nothing logged
    if recently_nudged(week) and "--force" not in args:
        log(f"{week} still missing, but a nudge was sent less than {WAIT_HOURS} h ago; waiting")
        return 0
    try:
        sha = push_nudge(week, run)
    except subprocess.CalledProcessError as e:
        log(f"nudge for {week} FAILED: git {e.cmd[3] if len(e.cmd) > 3 else ''}: {(e.stderr or '').strip()[:300]}")
        return 1
    os.makedirs(HOME, exist_ok=True)
    with open(STAMP, "w", encoding="utf-8") as f:
        json.dump({"week": week, "at": dt.datetime.now().isoformat(timespec="minutes"), "commit": sha}, f)
    log(f"nudged GitHub to build {week} (commit {sha[:7]} on {BRANCH})" + (" [forced]" if up else ""))
    return 0

# ---------------- Windows scheduled task ----------------
def task_xml(user, python, root, start="2026-11-16T07:30:00"):
    x = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>PsychoPal: if last week's edition is not on the site yet, asks GitHub to build it (python -m psychopal.nudge). Log: %LOCALAPPDATA%\\PsychoPal\\nudge.log</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{x(user)}</UserId><Delay>PT2M</Delay></LogonTrigger>
    <SessionStateChangeTrigger><Enabled>true</Enabled><StateChange>SessionUnlock</StateChange><UserId>{x(user)}</UserId><Delay>PT1M</Delay></SessionStateChangeTrigger>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
      <ScheduleByWeek><DaysOfWeek><Monday /><Tuesday /><Wednesday /></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{x(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{x(python)}</Command><Arguments>-m psychopal.nudge</Arguments><WorkingDirectory>{x(root)}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""

def install(remove=False):
    if os.name != "nt":
        print("The scheduled task is for Windows only.")
        return 1
    if remove:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, text=True)
        print((r.stdout or r.stderr).strip())
        return r.returncode
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")      # no console window
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    path = os.path.join(tempfile.gettempdir(), "psychopal-nudge-task.xml")
    with open(path, "w", encoding="utf-16") as f:
        f.write(task_xml(user, exe if os.path.exists(exe) else sys.executable, ROOT))
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", path, "/F"], capture_output=True, text=True)
    os.remove(path)
    print((r.stdout or r.stderr).strip())
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
