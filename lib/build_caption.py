#!/usr/bin/env python3
"""Telegram caption for the day's tracker: counts, top 3 picks, and warnings
(failed searches, budget skips, exhausted / low Apify credit).

Usage: build_caption.py <YYYY-MM-DD>   -> prints the caption (<= 1024 chars, Telegram's limit)
Reads output/.index_<date>.json, output/jobs_<date>.json, output/.fetch_status_<date>.json.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT = 1024
LOW_CREDIT_USD = 1.0
TOP_N = 3


def load(name, default):
    try:
        with open(os.path.join(ROOT, "output", name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def pick_line(i, r):
    where = ", ".join(x for x in (r.get("location", "").split(",")[0], r.get("work_arrangement", ""))
                      if x and x != "Not Specified")
    head = "%d. %s — %s" % (i, r.get("job_title", "?"), r.get("company") or "?")
    if where:
        head += " (%s)" % where
    return head, r.get("apply_link", "")


def warnings(st):
    out = []
    if st.get("failed"):
        out.append("⚠️ %d search(es) failed: %s" % (len(st["failed"]), ", ".join(st["failed"])))
    toks = [t for t in st.get("tokens", []) if "remaining" in t]
    for t in toks:
        if t["remaining"] < 0.005:
            out.append("⚠️ Apify token #%d is at its limit (resets %s)" % (t["n"], t["resets"]))
    if toks:
        left = sum(t["remaining"] for t in toks)
        line = "💳 Apify credit left: $%.2f" % left
        if st.get("est_spent_usd"):
            line += " (today ~$%.2f)" % st["est_spent_usd"]
        if left < LOW_CREDIT_USD:
            line = "⚠️ " + line + " — LOW, add a backup token"
        out.append(line)
    elif st.get("tokens"):
        out.append("⚠️ Apify credit unknown (usage check failed)")
    if st.get("region_name"):
        out.append("📍 Paid searches today: %s + remote (%s next)"
                   % (st["region_name"], st.get("region_next", "?")))
    if st.get("budget_skipped"):
        out.append("⏭ Skipped to save credit (rotates daily): %s" % ", ".join(st["budget_skipped"]))
    return out


def main():
    if len(sys.argv) != 2:
        print("usage: build_caption.py <YYYY-MM-DD>", file=sys.stderr)
        return 2
    date = sys.argv[1]
    idx = load(".index_%s.json" % date, {})
    rows = load("jobs_%s.json" % date, [])
    st = load(".fetch_status_%s.json" % date, {})

    head = "📋 Job tracker %s — %s roles, %s new since last run" % (
        date, idx.get("count", len(rows)), idx.get("new_since_last", "?"))
    tail = warnings(st)

    # Add picks one at a time (link optional) while the caption still fits.
    picks = []
    for i, r in enumerate(rows[:TOP_N], 1):
        line, link = pick_line(i, r)
        for cand in ([line, link] if link else [line]), [line]:
            trial = "\n".join([head, "", "Top picks:"] + picks + cand + [""] + tail)
            if len(trial) <= LIMIT:
                picks += cand
                break
    body = [head]
    if picks:
        body += ["", "Top picks:"] + picks
    if tail:
        body += [""] + tail
    print("\n".join(body)[:LIMIT])
    return 0


if __name__ == "__main__":
    sys.exit(main())
