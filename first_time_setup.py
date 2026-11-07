#!/usr/bin/env python3
"""First-time setup.   python3 first_time_setup.py [example]      (Windows: py first_time_setup.py)

Copies an example's settings into config/ (never overwrites yours), creates secrets.env,
and checks everything. Then edit the files in config/ and secrets.env (see README.md).
"""
import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FILES = ("profile.json", "searches.json", "resume.json")


def main():
    for stream in (sys.stdout, sys.stderr):   # old Windows consoles can't print ✓ / ₹
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if sys.version_info < (3, 8):
        print("Python 3.8 or newer is needed (you have %s)." % platform.python_version())
        return 1
    os.chdir(ROOT)
    examples = sorted(d for d in os.listdir("examples") if os.path.isdir(os.path.join("examples", d)))
    ex = sys.argv[1] if len(sys.argv) > 1 else ""
    if not ex:
        print("Pick a starting example (you will edit it to match you):")
        for i, d in enumerate(examples, 1):
            print("  %d. %s" % (i, d))
        ex = input("Number or name: ").strip()
    if ex.isdigit() and 1 <= int(ex) <= len(examples):
        ex = examples[int(ex) - 1]
    if ex not in examples:
        print("No such example: %s" % ex)
        return 1

    for d in ("config", "logs", os.path.join("output", "raw")):
        os.makedirs(d, exist_ok=True)
    for f in FILES:
        dst = os.path.join("config", f)
        if os.path.exists(dst):
            print("keep:    config/%s (already exists)" % f)
        else:
            shutil.copy(os.path.join("examples", ex, f), dst)
            print("copied:  examples/%s/%s -> config/%s" % (ex, f, f))
    if not os.path.exists("secrets.env"):
        shutil.copy("secrets.env.example", "secrets.env")
        try:
            os.chmod("secrets.env", 0o600)
        except OSError:
            pass
        print("created: secrets.env (private; put your Apify token in it)")

    print(flush=True)
    subprocess.call([sys.executable, os.path.join("lib", "config.py"), "check"])
    py = "py" if platform.system() == "Windows" else "python3"
    print("""
Next steps (details in README.md):
  1. Edit config/profile.json  - your name, roles, cities, skills
  2. Edit config/resume.json   - your real resume
  3. Edit config/searches.json - what to search for and where
  4. Put your Apify token in secrets.env (Telegram is optional)
  5. Check:      {py} lib/config.py check
     Free test:  {py} run_daily.py --dry-run
     Real run:   {py} run_daily.py
     Every day:  {py} schedule.py 9 0
""".format(py=py))
    return 0


if __name__ == "__main__":
    sys.exit(main())
