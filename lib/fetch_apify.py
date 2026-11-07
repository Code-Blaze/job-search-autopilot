#!/usr/bin/env python3
"""Run the enabled Apify actor runs in config/searches.json and save raw items.

Usage: fetch_apify.py <YYYY-MM-DD>
Writes output/raw/<date>_<site>_<label>.json and output/.fetch_status_<date>.json
(failures, budget skips, token usage — read by run_daily.py for the Telegram caption).
Exits non-zero only if zero runs succeeded.
Tokens come from APIFY_TOKEN, APIFY_TOKEN_2, ... (stops at first missing/REPLACE).

Budget pacing (BUDGET_PACING=0 to disable): each day may spend roughly
  sum over tokens of (credit left / days until that token's monthly reset).
Platforms with "_always_run": true (Naukri, <$0.01 per search) always run and count
toward that. The others (~$0.10 per search) run in order of "longest since last
fetched" while today's spend stays within the allowance (+ half a search, so a
$0.10/day allowance gives about one paid search a day; when credit is tighter they
alternate days). Estimated costs come from each platform's "_cost_usd".

Region alternation (profile.json locations.alternate_regions; ALTERNATE_REGIONS=0 to
disable): paid searches tied to one of those regions run only on that region's days,
in turn by day of the month (with two regions: even days the first, odd days the second).
Remote / country-wide searches can run any day. A run's region comes from its "location"
(or an explicit "_region": "<region id>" | "any"). "_always_run" platforms are unaffected.

DRY_RUN=1 prints today's plan without calling Apify (no tokens needed, costs nothing).
"""
import datetime as dt
import glob
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C  # noqa: E402

BASE = "https://api.apify.com/v2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES = ("linkedin", "indeed", "naukri", "wellfound")
SYNC_TIMEOUT = 290          # seconds Apify waits for a sync run
POLL_EVERY = 15
POLL_MAX = 8 * 60           # wall-clock limit when waiting on an async run
RETRY_WAIT = 30             # seconds before the single retry of a transient failure
DEFAULT_COST = {"linkedin": 0.10, "indeed": 0.10, "naukri": 0.006, "wellfound": 0.05}

QUOTA_WORDS = re.compile(r"quota|limit|credit|usage|exceed|insufficient", re.I)
NETWORK_ERRORS = (OSError, http.client.HTTPException)   # incl. URLError, timeouts, conn reset


class TokenExhausted(Exception):
    pass


class Transient(Exception):
    """Network blip, Apify 5xx, or the actor run itself failed — worth one retry."""


def load_tokens():
    tokens = []
    i = 1
    while True:
        name = "APIFY_TOKEN" if i == 1 else "APIFY_TOKEN_%d" % i
        val = os.environ.get(name, "").strip()
        if not val or val.startswith("REPLACE"):
            break
        tokens.append(val)
        i += 1
    return tokens


TOKENS = load_tokens()


def redact(text):
    text = str(text)
    for t in TOKENS:
        text = text.replace(t, "<REDACTED>")
    return re.sub(r"token=[^&\s\"']+", "token=<REDACTED>", text)


def log(msg):
    print("[fetch] " + redact(msg), flush=True)


def strip_private(obj):
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def http(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def tq(token, **extra):
    return urllib.parse.urlencode(dict(token=token, **extra))


def is_exhausted(status, body):
    if status in (402, 429):
        return True
    if status in (401, 403) and (status == 401 or QUOTA_WORDS.search(body or "")):
        return True
    return False


def check_status(status, text, what):
    if is_exhausted(status, text):
        raise TokenExhausted(text[:300])
    if status >= 500 or '"run-failed"' in (text or ""):
        raise Transient("%s HTTP %s: %s" % (what, status, text[:300]))
    if not 200 <= status < 300:
        raise RuntimeError("%s HTTP %s: %s" % (what, status, text[:300]))


# ---------------------------------------------------------------- runs
def find_started_run(actor, token, since):
    """The run our timed-out sync call started (so we wait on it instead of paying twice)."""
    try:
        status, text = http("GET", "%s/acts/%s/runs/last?%s" % (BASE, actor, tq(token)))
        if not 200 <= status < 300:
            return None
        run = json.loads(text)["data"]
        started = dt.datetime.fromisoformat(run["startedAt"].replace("Z", "+00:00")).timestamp()
        return run if started >= since - 10 else None
    except (NETWORK_ERRORS, ValueError, KeyError):
        return None


def wait_for_run(run, token):
    run_id, dataset = run["id"], run["defaultDatasetId"]
    deadline = time.time() + POLL_MAX
    st = run.get("status")
    while st not in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
        if time.time() > deadline:
            raise RuntimeError("run %s still %s after %ss (left running, not retried)"
                               % (run_id, st, POLL_MAX))
        time.sleep(POLL_EVERY)
        try:
            status, text = http("GET", "%s/actor-runs/%s?%s" % (BASE, run_id, tq(token)), timeout=30)
            if 200 <= status < 300:
                st = json.loads(text)["data"]["status"]
        except (NETWORK_ERRORS, ValueError):
            continue   # poll blip; keep waiting until the deadline
    if st != "SUCCEEDED":
        raise Transient("run %s ended with status %s" % (run_id, st))
    status, text = http("GET", "%s/datasets/%s/items?%s" % (BASE, dataset, tq(token, format="json")),
                        timeout=120)
    check_status(status, text, "dataset fetch")
    return json.loads(text)


def run_async(actor, body, token):
    status, text = http("POST", "%s/acts/%s/runs?%s" % (BASE, actor, tq(token)), body)
    check_status(status, text, "async start")
    return wait_for_run(json.loads(text)["data"], token)


def run_once(actor, body, token):
    url = "%s/acts/%s/run-sync-get-dataset-items?%s" % (
        BASE, actor, tq(token, timeout=SYNC_TIMEOUT, format="json"))
    t0 = time.time()
    try:
        status, text = http("POST", url, body, timeout=SYNC_TIMEOUT + 30)
    except NETWORK_ERRORS as e:
        status, text = None, str(e)
    if status is None or status in (408, 504):
        # The run may already be going on Apify; wait on it rather than start (and pay for) another.
        run = find_started_run(actor, token, t0)
        if run:
            log("  sync call interrupted (%s) -> waiting on run %s" % (status or text[:80], run["id"]))
            return wait_for_run(run, token)
        if status is None:
            raise Transient("network error: %s" % text[:200])
        log("  sync HTTP %s and no run found -> starting async" % status)
        return run_async(actor, body, token)
    check_status(status, text, "run")
    items = json.loads(text)
    if isinstance(items, dict) and "error" in items:
        raise RuntimeError("actor error: %s" % str(items["error"])[:300])
    return items


# ---------------------------------------------------------------- windows & budget
def fetch_window_days(date):
    """Days since the last run that produced output/jobs_<d>.json, clamped to 1..3."""
    today = dt.date.fromisoformat(date)
    prev = []
    for p in glob.glob(os.path.join(ROOT, "output", "jobs_*.json")):
        try:
            d = dt.date.fromisoformat(os.path.basename(p)[5:15])
        except ValueError:
            continue
        if d < today:
            prev.append(d)
    if not prev:
        return 3
    return max(1, min(3, (today - max(prev)).days))


def days_since_fetched(date, site, label):
    """Days since this search last saved raw output (99 if never)."""
    today = dt.date.fromisoformat(date)
    best = None
    for p in glob.glob(os.path.join(ROOT, "output", "raw", "*_%s_%s.json" % (site, label))):
        try:
            d = dt.date.fromisoformat(os.path.basename(p)[:10])
        except ValueError:
            continue
        if d < today and (best is None or d > best):
            best = d
    return (today - best).days if best else 99


def apply_window(site, body, days):
    if site == "linkedin" and "timePostedRange" in body:
        body["timePostedRange"] = str(days * 86400)
    elif site == "indeed" and "fromDays" in body:
        body["fromDays"] = "1" if days == 1 else "3"   # actor accepts 1/3/7/14 only
    elif site == "naukri" and "freshness" in body:
        body["freshness"] = "1" if days == 1 else "3"  # actor accepts 1/3/7/15/30
    return body


REGIONS = C.regions()
REGION_NAMES = {r["id"]: r["name"] for r in REGIONS}


def run_region(run):
    """Region id, or None (remote / country-wide / no match: runs any day)."""
    r = run.get("_region")
    if r:
        return None if r == "any" else r
    loc = str(run.get("location", ""))
    for reg in REGIONS:
        if reg["rx"].search(loc):
            return reg["id"]
    return None


def region_for_day(today):
    return REGIONS[today.day % len(REGIONS)]["id"] if REGIONS else None


def token_budget(token, today):
    """{'used','limit','remaining','days_left','resets'} from /users/me/limits, or None."""
    try:
        status, text = http("GET", "%s/users/me/limits?%s" % (BASE, tq(token)), timeout=30)
        if not 200 <= status < 300:
            return None
        d = json.loads(text)["data"]
        used = float(d["current"]["monthlyUsageUsd"])
        limit = float(d["limits"]["maxMonthlyUsageUsd"])
        resets = dt.date.fromisoformat(d["monthlyUsageCycle"]["endAt"][:10])
    except (NETWORK_ERRORS, ValueError, KeyError, TypeError):
        return None
    return {"used": round(used, 2), "limit": round(limit, 2),
            "remaining": round(max(0.0, limit - used), 3),
            "days_left": max(1, (resets - today).days + 1), "resets": resets.isoformat()}


# ---------------------------------------------------------------- main
def main():
    if len(sys.argv) != 2:
        print("usage: fetch_apify.py <YYYY-MM-DD>", file=sys.stderr)
        return 2
    date = sys.argv[1]
    dry = os.environ.get("DRY_RUN", "0") == "1"
    if not TOKENS and not dry:
        log("no Apify tokens configured")
        return 1
    try:
        cfg = C.searches()
    except C.ConfigError as e:
        log(str(e))
        return 1
    raw_dir = os.path.join(ROOT, "output", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    today = dt.date.fromisoformat(date)
    status_out = {"date": date, "ok": [], "failed": [], "budget_skipped": [], "tokens": [],
                  "exhausted": [], "est_spent_usd": 0.0, "allowance_usd": None,
                  "region_today": None, "off_day": []}

    # --- token usage & today's allowance
    budgets = [] if dry else [token_budget(t, today) for t in TOKENS]
    usable = []
    for i, b in enumerate(budgets):
        if b:
            log("token #%d: $%.2f of $%.2f used, resets %s" % (i + 1, b["used"], b["limit"], b["resets"]))
            status_out["tokens"].append(dict(b, n=i + 1))
        else:
            log("token #%d: usage unknown" % (i + 1))
            status_out["tokens"].append({"n": i + 1})
        if b is None or b["remaining"] >= 0.005:
            usable.append(i)
    if dry:
        usable = [0]
    if not usable:
        log("all Apify tokens are at their monthly limit")
        status_out["exhausted"] = list(range(1, len(TOKENS) + 1))
        write_status(date, status_out)
        return 1
    pacing = not dry and os.environ.get("BUDGET_PACING", "1") != "0" and all(budgets[i] for i in usable)
    allowance = sum(budgets[i]["remaining"] / budgets[i]["days_left"] for i in usable) if pacing else None
    status_out["allowance_usd"] = round(allowance, 3) if allowance is not None else None
    if pacing:
        log("budget: today's allowance $%.2f" % allowance)
    else:
        log("budget pacing off (BUDGET_PACING=0 or a token's usage is unknown)")

    # --- plan: always-run platforms first, then paid searches, stalest first
    global_days = fetch_window_days(date)
    log("fetch window: last %d day(s) (since previous run)" % global_days)
    region = region_for_day(today) if os.environ.get("ALTERNATE_REGIONS", "1") != "0" else None
    status_out["region_today"] = region
    if region:
        status_out["region_name"] = REGION_NAMES[region]
        status_out["region_next"] = REGION_NAMES[region_for_day(today + dt.timedelta(days=1))]
        log("region: today is a %s day (paid searches for other regions wait their turn)"
            % REGION_NAMES[region])
    plan = []
    for si, site in enumerate(SITES):
        block = cfg.get(site)
        if not block or not block.get("_enabled", True):
            log("%s: disabled, skipping" % site)
            continue
        for ri, run in enumerate(block.get("runs", [])):
            if not run.get("_enabled", True):
                continue
            label = run.get("_label", "run")
            rr = run_region(run)
            if region and not block.get("_always_run") and rr and rr != region:
                log("%s/%s: %s search, runs on %s days" % (site, label, REGION_NAMES.get(rr, rr), REGION_NAMES.get(rr, rr)))
                status_out["off_day"].append("%s/%s" % (site, label))
                continue
            stale = days_since_fetched(date, site, label)
            plan.append({"site": site, "label": label, "actor": block["actor"], "run": run,
                         "always": bool(block.get("_always_run")), "stale": stale,
                         "cost": float(block.get("_cost_usd", DEFAULT_COST.get(site, 0.10))),
                         "order": (si, ri)})
    plan.sort(key=lambda p: (not p["always"], -min(p["stale"], 30), p["order"]))

    tok_pos = 0       # index into `usable`
    spent = 0.0       # estimated, all searches today
    for p in plan:
        site, label, name = p["site"], p["label"], "%s/%s" % (p["site"], p["label"])
        # Same-day re-run (FORCE=1): don't pay again for a search already saved today.
        if not p["always"] and os.path.exists(os.path.join(raw_dir, "%s_%s_%s.json" % (date, site, label))):
            log("%s: already fetched today, reusing saved results" % name)
            continue
        if dry:
            log("%s: would run (est. $%.3f, last fetched %s)"
                % (name, p["cost"], "never" if p["stale"] == 99 else "%dd ago" % p["stale"]))
            continue
        if not p["always"] and allowance is not None and spent + p["cost"] > allowance + p["cost"] / 2:
            log("%s: skipped to stay on budget (last fetched %s)"
                % (name, "never" if p["stale"] == 99 else "%dd ago" % p["stale"]))
            status_out["budget_skipped"].append(name)
            continue
        days = max(global_days, min(p["stale"], 3))
        body = apply_window(site, strip_private(p["run"]), days)
        log("%s: starting %s (window %dd)" % (name, p["actor"], days))
        t0 = time.time()
        items, err, retried = None, None, False
        while tok_pos < len(usable):
            tok = TOKENS[usable[tok_pos]]
            try:
                items = run_once(p["actor"], body, tok)
                break
            except TokenExhausted as e:
                log("  token #%d exhausted (%s) -> rotating" % (usable[tok_pos] + 1, str(e)[:150]))
                status_out["exhausted"].append(usable[tok_pos] + 1)
                tok_pos += 1
            except Transient as e:
                if retried:
                    err = str(e)
                    break
                log("  transient failure (%s) -> retrying in %ds" % (str(e)[:200], RETRY_WAIT))
                retried = True
                time.sleep(RETRY_WAIT)
            except Exception as e:
                err = str(e)
                break
        if tok_pos >= len(usable):
            err = err or "all Apify tokens exhausted"
            log("  all Apify tokens exhausted; stopping further runs")
        if items is None:
            log("  FAILED: %s" % (err or "unknown"))
            status_out["failed"].append(name)
            if tok_pos >= len(usable):
                break
            continue
        path = os.path.join(raw_dir, "%s_%s_%s.json" % (date, site, label))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        log("  saved %d items in %.0fs -> %s" % (len(items), time.time() - t0, os.path.relpath(path, ROOT)))
        status_out["ok"].append(name)
        spent += p["cost"]

    if dry:
        log("dry run: nothing was fetched or charged")
        return 0
    status_out["est_spent_usd"] = round(spent, 3)
    status_out["exhausted"] = sorted(set(status_out["exhausted"]))
    write_status(date, status_out)
    log("done: %d runs succeeded, %d failed, %d skipped for budget (est. $%.2f)"
        % (len(status_out["ok"]), len(status_out["failed"]), len(status_out["budget_skipped"]),
           status_out["est_spent_usd"]))
    return 0 if status_out["ok"] else 1


def write_status(date, data):
    with open(os.path.join(ROOT, "output", ".fetch_status_%s.json" % date), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
