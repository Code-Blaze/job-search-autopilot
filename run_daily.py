#!/usr/bin/env python3
"""Daily job search: fetch -> filter -> tailored resumes -> spreadsheet -> Telegram.

  python3 run_daily.py             run today's search (Windows: py run_daily.py)
  python3 run_daily.py --dry-run   only show which searches would run; costs nothing
  python3 run_daily.py --force     re-run a day that already ran (reuses paid searches saved today)

Everything is logged to logs/run_<date>.log. Works on macOS, Linux and Windows.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB)
DATE = dt.date.today().isoformat()
LOG_PATH = os.path.join(ROOT, "logs", "run_%s.log" % DATE)
SECRET_PATTERNS = [re.compile(r"bot[0-9]+:[A-Za-z0-9_-]+"), re.compile(r"token=[^&\s\"']+"),
                   re.compile(r"sk-ant-[A-Za-z0-9_-]+"), re.compile(r"apify_api_[A-Za-z0-9]+")]
_log = None
INTERACTIVE = sys.stdout is not None   # None under pythonw (Windows scheduled run)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)    # Windows: no console pop-ups


def redact(text):
    for rx in SECRET_PATTERNS:
        text = rx.sub("<REDACTED>", text)
    for k, v in os.environ.items():
        if v and len(v) > 12 and ("TOKEN" in k or "KEY" in k) and v in text:
            text = text.replace(v, "<REDACTED>")
    return text


def log(line=""):
    line = redact(str(line))
    _log.write(line + "\n")
    _log.flush()
    if INTERACTIVE:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode(), flush=True)


def load_secrets():
    """Read KEY=value / export KEY="value" lines from secrets.env into the environment."""
    path = os.path.join(ROOT, "secrets.env")
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", raw)
            if not m or raw.lstrip().startswith("#"):
                continue
            val = m.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            os.environ[m.group(1)] = val
    return True


def step(script, *args):
    """Run lib/<script> with this Python, log its output, return the exit code."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(LIB, script)] + list(args), cwd=ROOT,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=NO_WINDOW)
    out = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        out.append(line)
        log(line)
    proc.wait()
    step.last_output = "\n".join(out)
    return proc.returncode


def telegram_text(msg):
    import notify_telegram as T
    rc, info = T.send_text(msg)
    log("[telegram] %s" % ("not configured - skipping" if rc == 1 else info))
    return rc


def telegram_file(path, caption):
    import notify_telegram as T
    rc, info = T.send_file(path, caption)
    log("[telegram] %s" % ("not configured - skipping" if rc == 1 else info if rc == 0 else "send FAILED: " + info))
    return rc


def fail(msg):
    log("FATAL: " + msg)
    telegram_text("⚠️ Job tracker %s FAILED — %s (see logs/run_%s.log)" % (DATE, msg, DATE))
    log("===== run finished %s =====" % dt.datetime.now().strftime("%F %T"))
    sys.exit(1)


def apify_tokens():
    out, i = [], 1
    while True:
        name = "APIFY_TOKEN" if i == 1 else "APIFY_TOKEN_%d" % i
        v = os.environ.get(name, "").strip()
        if not v or v.startswith("REPLACE"):
            return out
        out.append((name, v))
        i += 1


def token_ok(tok):
    req = urllib.request.Request("https://api.apify.com/v2/users/me", headers={"Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def main():
    global _log
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "output", "raw"), exist_ok=True)
    _log = open(LOG_PATH, "a", encoding="utf-8")
    log("===== run started %s =====" % dt.datetime.now().strftime("%F %T"))
    if "--dry-run" in sys.argv:
        os.environ["DRY_RUN"] = "1"
    if "--force" in sys.argv:
        os.environ["FORCE"] = "1"
    dry = os.environ.get("DRY_RUN") == "1"

    if not load_secrets() and not dry:
        fail("secrets.env missing (run setup first)")
    if step("config.py", "check") != 0:
        fail("fix the settings above (see docs/CUSTOMIZE.md)")
    import config as C
    xlsx = C.tracker_path(DATE)

    if dry:
        step("fetch_apify.py", DATE)
        log("===== dry run finished =====")
        return 0

    if os.path.exists(xlsx) and os.environ.get("FORCE") != "1":
        log("skip: tracker for %s already exists (use --force to run anyway)" % DATE)
        log("===== run finished %s =====" % dt.datetime.now().strftime("%F %T"))
        return 0

    # ---- preflight: at least one working Apify token (credit is checked by the fetcher)
    valid = 0
    for name, tok in apify_tokens():
        code = token_ok(tok)
        log("preflight: %s -> HTTP %s" % (name, code))
        valid += code == 200
    if not valid:
        fail("no valid Apify token in secrets.env")

    # ---- pipeline
    if step("fetch_apify.py", DATE) != 0:
        import build_caption as B
        warn = "; ".join(B.warnings(B.load(".fetch_status_%s.json" % DATE, {})))
        fail("fetch: zero Apify runs succeeded" + (" — " + warn if warn else ""))
    if step("classify_from_raw.py", DATE) != 0:
        fail("classify step failed")
    if step("tailor_resume.py", DATE) != 0:
        log("WARNING: some tailored resumes were not written (see [tailor] above)")
    if step("ai_tailor.py", DATE) != 0:
        log("WARNING: AI resume step failed; rule-based resumes kept")
    if step("write_tracker.py", os.path.join("output", "jobs_%s.json" % DATE)) != 0 or not os.path.exists(xlsx):
        fail("spreadsheet writer failed")
    log("spreadsheet: %s" % xlsx)

    # ---- Telegram: spreadsheet with a caption (counts, top picks, warnings), then resumes as a zip
    step("build_caption.py", DATE)
    caption = step.last_output.strip() or "📋 Job tracker %s" % DATE
    rc = telegram_file(xlsx, caption)
    res_dir = os.path.join(ROOT, "output", "resumes", DATE)
    docs = sorted(f for f in os.listdir(res_dir) if f.endswith(".docx")) if os.path.isdir(res_dir) else []
    if rc == 0 and docs:
        zpath = os.path.join(ROOT, "output", "resumes", "Tailored_Resumes_%s.zip" % DATE)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in docs:
                z.write(os.path.join(res_dir, f), f)
        if telegram_file(zpath, "📄 Tailored resumes %s (numbered as in the tracker)" % DATE) != 0:
            log("WARNING: could not send tailored resumes to Telegram")
    elif rc == 2:
        log("WARNING: Telegram send failed; spreadsheet saved locally")
    log("===== run finished %s =====" % dt.datetime.now().strftime("%F %T"))
    if not INTERACTIVE:
        return 0
    print("\nDone. Open: %s" % xlsx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
