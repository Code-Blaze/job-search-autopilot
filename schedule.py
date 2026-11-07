#!/usr/bin/env python3
"""Run the job search automatically every day.

  python3 schedule.py 9 0        every day at 09:00 (Windows: py schedule.py 9 0)
  python3 schedule.py --remove   stop the daily run

macOS: a launchd job. Linux: a crontab line. Windows: a Task Scheduler task.
The computer must be on (and you logged in) at that time. A missed day is picked up on
the next run: searches look back up to 3 days.
"""
import os
import platform
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(ROOT, "run_daily.py")
NAME = "JobSearchAutopilot"
LABEL = "io.github.job-search-autopilot"
CRON_TAG = "# job-search-autopilot"


def windows(hour, minute, remove):
    if remove:
        subprocess.call(["schtasks", "/Delete", "/TN", NAME, "/F"])
        return 0
    # pythonw.exe runs without a console window popping up every morning
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(w):
        exe = w
    cmd = '"%s" "%s"' % (exe, RUNNER)
    rc = subprocess.call(["schtasks", "/Create", "/SC", "DAILY", "/TN", NAME, "/TR", cmd,
                          "/ST", "%02d:%02d" % (hour, minute), "/F"])
    if rc == 0:
        print("Installed Task Scheduler task '%s' at %02d:%02d daily." % (NAME, hour, minute))
        print("Run now:    schtasks /Run /TN %s" % NAME)
        print("Tip: in Task Scheduler > %s > Settings, tick 'Run task as soon as possible after a"
              " scheduled start is missed' if your PC is often off at that time." % NAME)
    return rc


def mac(hour, minute, remove):
    plist = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)
    uid = str(os.getuid())
    subprocess.call(["launchctl", "bootout", "gui/" + uid, plist], stderr=subprocess.DEVNULL)
    if remove:
        if os.path.exists(plist):
            os.remove(plist)
        print("Removed the daily run.")
        return 0
    os.makedirs(os.path.dirname(plist), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(plist, "w", encoding="utf-8") as f:
        f.write("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array><string>{py}</string><string>{runner}</string></array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>
  <key>StandardOutPath</key><string>{root}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>{root}/logs/launchd.err.log</string>
</dict>
</plist>
""".format(label=LABEL, py=sys.executable, runner=RUNNER, root=ROOT, h=hour, m=minute))
    rc = subprocess.call(["launchctl", "bootstrap", "gui/" + uid, plist])
    if rc == 0:
        print("Installed daily run at %02d:%02d (launchd %s)." % (hour, minute, LABEL))
        print("Run now:    launchctl kickstart -k gui/%s/%s" % (uid, LABEL))
    return rc


def linux(hour, minute, remove):
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        print("crontab not found: install cron, or schedule '%s %s' yourself." % (sys.executable, RUNNER))
        return 1
    lines = [ln for ln in cur.splitlines() if not ln.endswith(CRON_TAG)]
    if not remove:
        lines.append('%d %d * * * cd "%s" && "%s" "%s" %s' % (minute, hour, ROOT, sys.executable, RUNNER, CRON_TAG))
    rc = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True).returncode
    if rc == 0:
        print("Removed the daily run." if remove else "Installed cron job at %02d:%02d daily." % (hour, minute))
    return rc


def main():
    args = sys.argv[1:]
    remove = "--remove" in args
    nums = [a for a in args if a.isdigit()]
    hour = int(nums[0]) if nums else 9
    minute = int(nums[1]) if len(nums) > 1 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        print(__doc__)
        return 2
    system = platform.system()
    if system == "Windows":
        return windows(hour, minute, remove)
    if system == "Darwin":
        return mac(hour, minute, remove)
    return linux(hour, minute, remove)


if __name__ == "__main__":
    sys.exit(main())
