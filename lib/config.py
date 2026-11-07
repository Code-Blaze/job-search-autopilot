#!/usr/bin/env python3
"""Load your settings from config/ (copied from an examples/ folder by first_time_setup.py).

  config/profile.json   who you are and which jobs you want (roles, cities, skills...)
  config/searches.json  what to search on LinkedIn / Indeed / Naukri / Wellfound via Apify
  config/resume.json    your real resume, used to make a tailored copy for every job

Keyword lists are plain words or phrases, matched as whole words, case-insensitive:
  "incident response"   also matches "Incident  Response"
  "pen-test"            also matches "pen test", "pentest", "pen - test"
  "forensic*"           * at the end matches any ending: forensic, forensics
  "re:\\bsoc\\b"        anything starting with re: is a raw Python regular expression

Command line:
  config.py check                  validate the three files and print a summary
  config.py tracker-path <date>    path of that day's spreadsheet (used by the shell scripts)
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.environ.get("JSA_CONFIG_DIR") or os.path.join(ROOT, "config")
NEVER = re.compile(r"(?!x)x")    # matches nothing (for empty keyword lists)


class ConfigError(Exception):
    pass


def load_json(name, required=True):
    path = os.path.join(CONFIG_DIR, name)
    if not os.path.exists(path):
        if required:
            raise ConfigError("%s not found. Run first_time_setup.py first (or copy a file from examples/)."
                              % os.path.relpath(path, ROOT))
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        raise ConfigError("%s is not valid JSON: %s" % (os.path.relpath(path, ROOT), e))


def phrase_pattern(term):
    """One keyword/phrase -> regex source (see module docstring)."""
    term = str(term).strip()
    if term.startswith("re:"):
        return term[3:]
    star = term.endswith("*")
    core = term.rstrip("*").strip()
    parts = []
    for word in core.split():
        pieces = [re.escape(p) for p in word.split("-")]
        parts.append(r"\s*-?\s*".join(pieces))     # "pen-test" ~ "pen test" ~ "pentest"
    pat = r"\s+".join(parts) + (r"\w*" if star else "")
    # whole-word match that also works for terms like "c++", ".net", "node.js"
    return r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % pat if not star else \
           r"(?<![A-Za-z0-9])(?:%s)" % pat


def keywords_rx(terms, case_sensitive=False):
    """Compile a list of keywords into one regex (matches nothing if the list is empty)."""
    terms = [t for t in (terms or []) if str(t).strip()]
    if not terms:
        return NEVER
    return re.compile("|".join("(?:%s)" % phrase_pattern(t) for t in terms),
                      0 if case_sensitive else re.I)


def named_matchers(entries):
    """Skills / certs: "Python" or {"name": "SIEM", "match": ["SIEM"], "exact_case": true}
    -> [(display name, compiled regex)]."""
    out = []
    for e in entries or []:
        if isinstance(e, str):
            out.append((e, keywords_rx([e])))
        else:
            out.append((e["name"], keywords_rx(e.get("match") or [e["name"]], e.get("exact_case", False))))
    return out


_cache = {}


def profile():
    if "profile" not in _cache:
        _cache["profile"] = load_json("profile.json")
    return _cache["profile"]


def searches():
    return load_json("searches.json")


def resume():
    return load_json("resume.json")


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")


def tracker_prefix():
    p = profile()
    name = slug(p.get("name", ""))
    if not name or name.lower() in ("your_name", "yourname"):
        return "Job_Tracker"
    return name + "_Job_Tracker"


def tracker_path(date):
    return os.path.join(ROOT, "output", "%s_%s.xlsx" % (tracker_prefix(), date))


def regions():
    """[{"id", "name", "rx"}] from profile.locations.alternate_regions (may be empty)."""
    out = []
    for r in profile().get("locations", {}).get("alternate_regions", []) or []:
        out.append({"id": r["id"], "name": r.get("name", r["id"]), "rx": keywords_rx(r.get("match", []))})
    return out


# ---------------------------------------------------------------- check
def check():
    problems, notes = [], []
    try:
        p = profile()
    except ConfigError as e:
        print("✗ " + str(e))
        return 1
    if not p.get("name") or p["name"].lower() == "your name":
        problems.append("profile.json: set \"name\" to your name (used for file names and resumes)")
    roles = p.get("roles", {})
    if not roles.get("title_keywords"):
        problems.append("profile.json: roles.title_keywords is empty, so no job would be kept")
    loc = p.get("locations", {})
    if loc.get("remote", "country") not in ("country", "anywhere", "none"):
        problems.append('profile.json: locations.remote must be "country", "anywhere" or "none"')
    if not (loc.get("onsite_cities") or loc.get("hybrid_cities")) and loc.get("remote") == "none":
        problems.append("profile.json: no onsite/hybrid cities and remote is \"none\", so nothing can match")
    # compile every keyword list so a bad re: pattern is caught now, not at 9 am
    for section, key in (("roles", "title_keywords"), ("roles", "title_exclude"),
                         ("roles", "other_field_titles"), ("roles", "generic_titles"),
                         ("roles", "description_keywords"), ("locations", "country_names"),
                         ("locations", "country_places"), ("locations", "onsite_cities"),
                         ("locations", "hybrid_cities"), ("locations", "priority_cities")):
        try:
            keywords_rx(p.get(section, {}).get(key))
        except re.error as e:
            problems.append("profile.json: %s.%s has a bad pattern: %s" % (section, key, e))
    try:
        sk = named_matchers(p.get("skills"))
        ce = named_matchers(p.get("certs"))
        notes.append("%d skills, %d certifications to look for" % (len(sk), len(ce)))
    except (re.error, KeyError, TypeError) as e:
        problems.append("profile.json: skills/certs entry is malformed: %s" % e)
    regs = p.get("locations", {}).get("alternate_regions") or []
    if regs:
        notes.append("paid searches alternate by day between: " + ", ".join(r.get("name", r["id"]) for r in regs))

    try:
        s = searches()
        active = []
        for site, block in s.items():
            if isinstance(block, dict) and "runs" in block and block.get("_enabled", True):
                active += ["%s/%s" % (site, r.get("_label", "?")) for r in block["runs"] if r.get("_enabled", True)]
        notes.append("%d searches enabled: %s" % (len(active), ", ".join(active)))
        if not active:
            problems.append("searches.json: no search is enabled")
    except ConfigError as e:
        problems.append(str(e))

    try:
        r = resume()
        tracks = [t["id"] for t in p.get("resume_tracks", [])]
        if "default" not in r.get("headlines", {}):
            problems.append('resume.json: "headlines" needs a "default" entry')
        missing = [t for t in tracks if t not in r.get("headlines", {})]
        if missing:
            notes.append("resume.json has no headline for track(s) %s; the default one is used" % ", ".join(missing))
        if r.get("name", "").lower() in ("", "your name"):
            problems.append("resume.json: fill in your own resume (name, experience, skills...)")
    except ConfigError as e:
        problems.append(str(e))

    print("Settings in %s" % os.path.relpath(CONFIG_DIR, ROOT))
    print("  tracker file: output/%s_<date>.xlsx" % tracker_prefix())
    for n in notes:
        print("  " + n)
    for pr in problems:
        print("✗ " + pr)
    print("✓ looks good" if not problems else "%d problem(s) to fix" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):   # old Windows consoles can't print ✓ / ₹
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(sys.argv) >= 2 and sys.argv[1] == "check":
        sys.exit(check())
    if len(sys.argv) == 3 and sys.argv[1] == "tracker-path":
        try:
            print(tracker_path(sys.argv[2]))
        except ConfigError as e:
            print(e, file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
