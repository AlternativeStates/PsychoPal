"""Laptop nudge: the dependable half of the automatic Monday run (GitHub's own timer fires late or not at all for this repo).

A Windows scheduled task on Claudio's laptop runs this ONCE a week: Monday 07:30, or as soon as the laptop is on again if it was
off or asleep then. It reads the live site; if no edition covers last Monday-Sunday yet, it pushes one small commit to the
`weekly-nudge` branch with the GitHub login git already has. `.github/workflows/nudge.yml` then starts the weekly workflow on main
(only_if_missing=true: it re-checks the site and stops at once if the edition appeared in the meantime). If the site or GitHub
can't be reached, it tries again every 10 minutes for an hour, then gives up until next Monday (the app's ⟳ button still works).

It uploads nothing from the local working copy: the commit reuses origin/main's files exactly. It never touches the local
branches or files.

  python -m psychopal.nudge             # nudge if last week's edition is missing (what the scheduled task runs)
  python -m psychopal.nudge --check     # only say whether a nudge is needed
  python -m psychopal.nudge --force     # nudge even if the edition is up (test: the run then stops after ~10 s)
  python -m psychopal.nudge --install   # (re)create the Windows scheduled task "PsychoPal weekly nudge"
  python -m psychopal.nudge --uninstall # remove it

Log: %LOCALAPPDATA%\\PsychoPal\\nudge.log (last 200 lines; written only when it nudges or something fails)."""
import datetime as dt, os, subprocess, sys, tempfile, time
import requests
from .common import load_yaml, last_full_week, week_id

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PsychoPal")
LOG = os.path.join(HOME, "nudge.log")
BRANCH, TASK = "weekly-nudge", "PsychoPal weekly nudge"
TRIES, PAUSE_S = 7, 600          # offline at 07:30? try again every 10 min for an hour

def log(msg):
    os.makedirs(HOME, exist_ok=True)
    lines = open(LOG, encoding="utf-8").read().splitlines()[-199:] if os.path.exists(LOG) else []
    lines.append(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {msg}")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(msg)

def covered(weeks, monday, sunday):
    """Is last week done? True when one edition's period runs from that Monday (or earlier) to that Sunday (or later). A
    refresh from the app that stopped mid-week does not count, so the Monday run completes it. Same rule as weekly.yml's check."""
    return any(w.get("start", "9999") <= str(monday) and w.get("end", "") >= str(sunday) for w in weeks)

def site_weeks(site_url, get=requests.get):
    r = get(site_url.rstrip("/") + "/data/index.json", timeout=30, headers={"User-Agent": "psychopal-nudge", "Cache-Control": "no-cache"})
    r.raise_for_status()
    return r.json().get("weeks", [])

def git(*args):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, check=True, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()

def push_nudge(week, run=git):
    """One new commit on top of origin/main with origin/main's own files, force-pushed to the weekly-nudge branch."""
    run("fetch", "-q", "origin", "main")
    tree = run("rev-parse", "origin/main^{tree}")
    sha = run("commit-tree", tree, "-p", "origin/main", "-m", f"Nudge: build edition {week}")
    run("push", "-q", "-f", "origin", f"{sha}:refs/heads/{BRANCH}")
    return sha

def attempt(args, today, get, run):
    """One try. Returns 0 (done / nothing to do), or raises on a network or git problem."""
    monday, sunday = last_full_week(today)
    week = week_id(monday)
    done = covered(site_weeks(load_yaml("settings.yaml").get("site_url"), get), monday, sunday)
    if "--check" in args:
        print(f"{week}: {'published' if done else 'missing, a nudge is needed'}")
        return 0
    if done and "--force" not in args:
        return 0                                                   # the normal case: one web request, nothing logged
    sha = push_nudge(week, run)
    log(f"nudged GitHub to build {week} (commit {sha[:7]} on {BRANCH})" + (" [forced]" if done else ""))
    return 0

def main(argv=None, today=None, get=requests.get, run=git, sleep=time.sleep):
    args = argv if argv is not None else sys.argv[1:]
    if "--install" in args or "--uninstall" in args:
        return install(remove="--uninstall" in args)
    tries = 1 if ("--check" in args or "--force" in args) else TRIES
    for i in range(tries):
        try:
            return attempt(args, today, get, run)
        except subprocess.CalledProcessError as e:
            why = f"git {e.cmd[3] if len(e.cmd) > 3 else ''}: {(e.stderr or '').strip()[:300]}"
        except Exception as e:
            why = f"could not read the site ({type(e).__name__})"
        if i + 1 < tries:
            sleep(PAUSE_S)
    log(f"nudge FAILED after {tries} tries ({why}); next try next Monday, or use the app's refresh button")
    return 1

# ---------------- Windows scheduled task: once a week ----------------
def task_xml(user, python, root, start="2026-11-16T07:30:00"):
    x = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>PsychoPal: on Monday morning (or when the laptop is next on), asks GitHub to build last week's edition if it isn't on the site yet (python -m psychopal.nudge). Log: %LOCALAPPDATA%\\PsychoPal\\nudge.log</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
      <ScheduleByWeek><DaysOfWeek><Monday /></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>
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
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
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
