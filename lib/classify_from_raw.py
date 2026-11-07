#!/usr/bin/env python3
"""Normalise + filter + score + de-dupe + sort raw Apify output.

Usage: classify_from_raw.py <YYYY-MM-DD>
Reads  output/raw/<date>_*.json, writes output/jobs_<date>.json.
All rules come from config/profile.json (see docs/CUSTOMIZE.md).
"""
import datetime as dt
import glob
import html
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import config as C  # noqa: E402

# ---------------------------------------------------------------- your settings
P = C.profile()
ROLES = P.get("roles", {})
EXP = P.get("experience", {})
EMP = P.get("employment", {})
LOC = P.get("locations", {})
SAL = P.get("salary", {})

MAX_AGE_DAYS = int(P.get("max_posting_age_days", 3))
CURRENCY = (SAL.get("currency") or "USD").upper()
SALARY_FLOOR = float(SAL.get("minimum_per_year") or 0)   # only applied when a salary is shown
MAX_MIN_YEARS = EXP.get("max_years_required")           # None = no limit
DROP_SENIORITY = set(EXP.get("drop_levels", ["Senior", "Exec"]))
KEEP_INTERNSHIPS = bool(EMP.get("keep_internships", False))
KEEP_CONTRACT = bool(EMP.get("keep_contract", False))

# ---------------------------------------------------------------- relevance
TITLE_RX = C.keywords_rx(ROLES.get("title_keywords"))            # title alone is enough
HARD_NEG_TITLE = C.keywords_rx(ROLES.get("title_exclude"))       # always dropped
OTHER_FIELD_TITLE = C.keywords_rx(ROLES.get("other_field_titles"))  # dropped unless TITLE_RX hits
GENERIC_TITLE = C.keywords_rx(ROLES.get("generic_titles"))       # kept if the description fits
DESC_TERMS = [C.keywords_rx([t]) for t in ROLES.get("description_keywords", [])]
DESC_MIN = int(ROLES.get("description_min_matches", 4))

# ---------------------------------------------------------------- skills
SKILLS = C.named_matchers(P.get("skills"))
CERTS = C.named_matchers(P.get("certs"))

# ---------------------------------------------------------------- location
#   Onsite / Not Specified -> locations.onsite_cities
#   Hybrid                 -> locations.hybrid_cities
#   Remote                 -> locations.remote: "country" (country_names/places), "anywhere", "none"
REMOTE_MODE = LOC.get("remote", "country")
COUNTRY_NAMES = LOC.get("country_names", [])
COUNTRY = C.keywords_rx(COUNTRY_NAMES + LOC.get("country_places", []))
CHECK_COUNTRY = REMOTE_MODE != "anywhere" and bool(COUNTRY_NAMES or LOC.get("country_places"))
REMOTE_WORDS = LOC.get("remote_location_words", ["remote", "anywhere", "worldwide", "global"])
PLACELESS_START = re.compile(r"(?:%s)[\w\s,()-]*" % "|".join(C.phrase_pattern(w) for w in REMOTE_WORDS), re.I)
# country codes ("IN", "US") only count in the "no city named" check, never as a country match
NO_CITY = C.keywords_rx(["remote", "anywhere", "pan", "work from home", "wfh"] + COUNTRY_NAMES
                        + LOC.get("country_codes", []))
ONSITE_AREA = C.keywords_rx(LOC.get("onsite_cities"))
HYBRID_AREA = C.keywords_rx(LOC.get("hybrid_cities"))
PRIORITY = C.keywords_rx(LOC.get("priority_cities"))


def no_city(loc):
    """True for location strings that name no city, e.g. 'India', 'Remote, IN', ''."""
    return re.sub(r"[\s,()/-]", "", NO_CITY.sub("", loc or "")) == ""

# Rough exchange rates (value of 1 unit in INR, the internal unit). Only used to compare
# salaries with your minimum and show an estimate; update them if they drift a lot.
FX_TO_INR = {"INR": 1, "₹": 1, "RS": 1, "USD": 84, "$": 84, "EUR": 91, "€": 91,
             "GBP": 108, "£": 108, "SGD": 63, "AED": 23, "CAD": 61, "AUD": 55, "NZD": 50,
             "CHF": 95, "JPY": 0.55, "¥": 0.55, "MYR": 18, "PHP": 1.45, "IDR": 0.0052,
             "PKR": 0.30, "BDT": 0.70, "LKR": 0.28, "NGN": 0.055, "KES": 0.65, "ZAR": 4.6,
             "BRL": 15, "MXN": 4.6, "SAR": 22, "QAR": 23, "PLN": 21, "SEK": 7.9, "NOK": 7.8,
             "DKK": 12.2, "HKD": 10.8, "CNY": 11.7}
if CURRENCY not in FX_TO_INR:
    raise C.ConfigError("profile.json: salary.currency %r is not known; use one of %s"
                        % (CURRENCY, ", ".join(k for k in FX_TO_INR if k.isalpha() and len(k) == 3)))
SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
PERIOD_MULT = {"hour": 2080, "hourly": 2080, "day": 260, "daily": 260, "week": 52,
               "weekly": 52, "month": 12, "monthly": 12, "year": 1, "yearly": 1, "annual": 1,
               "annually": 1, "yr": 1, "mo": 12, "hr": 2080}

WA_ORDER = {"Onsite": 0, "Hybrid": 1, "Remote": 2, "Not Specified": 3}
ET_ORDER = {"Full-time": 0, "Part-time": 1, "Freelance": 1, "Not Specified": 2}
KEEP_ET = {"Full-time", "Part-time", "Freelance", "Not Specified"}

TODAY = dt.date.today()


# ================================================================ helpers
def s(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(s(x) for x in v if s(x))
    if isinstance(v, dict):
        for k in ("formattedAddressShort", "fullAddress", "text", "name", "label", "value"):
            if v.get(k):
                return s(v[k])
        return ", ".join(s(x) for x in v.values() if isinstance(x, str) and x)
    return re.sub(r"\s+", " ", html.unescape(str(v))).strip()  # "&amp;" -> "&"


def g(d, *keys):
    """First non-empty value among keys (dotted paths allowed)."""
    for k in keys:
        cur = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, "", [], {}):
            return cur
    return None


def parse_date(v):
    """Return a date from epoch s/ms, ISO, YYYY-MM-DD, or 'N days ago' text; else None."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        ts = v / 1000 if v > 1e11 else v
        try:
            return dt.datetime.utcfromtimestamp(ts).date()
        except (OverflowError, ValueError, OSError):
            return None
    t = str(v).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", t)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    low = t.lower()
    if re.search(r"just\s+posted|today|now|minute|hour|\bhr", low):
        return TODAY
    if "yesterday" in low:
        return TODAY - dt.timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*(day|d\b|week|wk|month|mo)", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n if unit.startswith("d") else n * 7 if unit.startswith("w") else n * 30
        return TODAY - dt.timedelta(days=days)
    return None


def recency_bucket(d):
    if d is None:
        return "Unknown", 99
    age = (TODAY - d).days
    if age <= 1:
        return "Last 24 Hours", age
    if age <= 3:
        return "1-3 Days Old", age
    if age <= 7:
        return "4-7 Days Old", age
    return "Older", age


def work_arrangement(flags_text, desc):
    t = flags_text.lower()
    if "hybrid" in t:
        return "Hybrid"
    if re.search(r"\bremote\b|work\s+from\s+home|\bwfh\b", t):
        return "Remote"
    if re.search(r"on-?\s?site|in-?office|office", t):
        return "Onsite"
    d = desc.lower()[:4000]
    if re.search(r"\bhybrid\b", d):
        return "Hybrid"
    if re.search(r"\b(fully|100%)\s+remote\b|\bremote\s+(role|position|job|work)\b|work\s+from\s+home", d):
        return "Remote"
    if re.search(r"\b(on-?site|work\s+from\s+office|wfo|in-?office)\b", d):
        return "Onsite"
    return "Not Specified"


def employment_types(raw, title):
    """Return set of normalised types from raw text + title."""
    t = (s(raw) + " " + title).lower()
    out = set()
    if re.search(r"\bintern(ship)?s?\b", t):
        out.add("Internship")
    if re.search(r"full[\s_-]?time|permanent|regular", t):
        out.add("Full-time")
    if re.search(r"part[\s_-]?time", t):
        out.add("Part-time")
    if re.search(r"freelance", t):
        out.add("Freelance")
    if re.search(r"contract|temporary|temp\b|fixed[\s-]term", t):
        out.add("Contract")
    return out


def pick_employment(types):
    for t in ("Full-time", "Part-time", "Freelance", "Contract", "Internship"):
        if t in types:
            return t
    return "Not Specified"


YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?\+?\s*(?:years?|yrs?)", re.I)


def min_years(text):
    """Smallest 'N years' requirement mentioned near experience wording; None if absent."""
    found = []
    for m in YEARS_RE.finditer(text):
        ctx = text[max(0, m.start() - 60): m.end() + 60].lower()
        if "experience" in ctx or "exp" in ctx:
            n = int(m.group(1))
            if n <= 20:
                found.append(n)
    return min(found) if found else None


def seniority(title, li_level, yrs):
    tl = title.lower().replace("_", " ")   # "IN_Senior Associate_GRC" -> words
    if re.search(r"\b(director|vp|vice\s+president|chief|ciso|cso|head\s+of|head)\b", tl):
        return "Exec"
    if re.search(r"\b(senior|sr\.?|lead|principal|staff|manager|architect|l3|level\s*3|iii)\b", tl):
        return "Senior"
    if re.search(r"\b(intern|trainee|graduate|fresher|junior|jr\.?|entry|l1|level\s*1|apprentice)\b", tl):
        return "Entry"
    lv = (li_level or "").lower()
    if "director" in lv or "executive" in lv:
        return "Exec"
    if "entry" in lv or "intern" in lv:
        return "Entry"
    if "associate" in lv:
        return "Associate"
    if yrs is not None:
        if yrs <= 1:
            return "Entry"
        if yrs <= 2:
            return "Associate"
        if yrs <= 4:
            return "Mid"
        return "Senior"
    if re.search(r"\b(l2|level\s*2|ii)\b", tl):
        return "Mid"
    if "mid" in lv:
        return "Mid"
    return "Not Specified"


def annual_inr(lo, hi, currency, period, text=""):
    """Return (lo, hi) annual INR, or (None, None). Sanity-checks Indeed's mis-parsed periods."""
    def num(x):
        if x in (None, ""):
            return None
        if isinstance(x, (int, float)):
            return float(x)
        x = re.sub(r"[^\d.]", "", str(x))
        try:
            return float(x) if x else None
        except ValueError:
            return None
    lo, hi = num(lo), num(hi)
    if lo is None and hi is None and text:
        nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
        nums = [n for n in nums if n >= 1]
        if nums:
            lo, hi = nums[0], nums[-1]
        low = text.lower()
        if not period:
            period = next((p for p in PERIOD_MULT if re.search(r"\b" + p + r"\b", low)), "")
        if not currency:
            currency = "INR" if "₹" in text or re.search(r"\b(rs|inr)\b", low) else \
                       "USD" if "$" in text else ""
        if re.search(r"\b(lpa|lakhs?|lac)\b", low):
            lo = lo * 100000 if lo and lo < 1000 else lo
            hi = hi * 100000 if hi and hi < 1000 else hi
            period = "year"
    if lo is None and hi is None:
        return None, None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    cur = (currency or CURRENCY).strip().upper()
    rate = FX_TO_INR.get(cur, FX_TO_INR.get(cur[:1], 1))
    mult = PERIOD_MULT.get((period or "").strip().lower(), None)
    if mult is None:
        # Guess: Indian salaries < 2L stated without period are monthly; tiny numbers are hourly.
        base = hi * rate
        mult = 2080 if base < 2000 else 12 if base < 200000 else 1
    lo_a, hi_a = lo * rate * mult, hi * rate * mult
    # Sanity: hourly read as yearly (e.g. 450 "yearly") or monthly read as yearly.
    if hi_a < 50000 and mult == 1:
        lo_a, hi_a = lo_a * 12, hi_a * 12
    return lo_a, hi_a


def fmt_salary(lo, hi):
    """Annual estimate in your currency: "₹6.0–8.0 L" for INR (lakhs), "$85k–120k" otherwise."""
    if lo is None:
        return ""
    sym = SYMBOL.get(CURRENCY, CURRENCY + " ")
    if CURRENCY == "INR":
        def f(x):
            return "%.1f" % (x / 100000.0)
        unit = " L"
    else:
        def f(x):
            return "%dk" % round(x / 1000.0) if x >= 1000 else "%d" % round(x)
        unit = ""
    return "%s%s%s" % (sym, f(lo), unit) if abs(hi - lo) < 1 else "%s%s–%s%s" % (sym, f(lo), f(hi), unit)


# ================================================================ normalisers
def base_row():
    return {k: "" for k in (
        "job_title", "company", "location", "work_arrangement", "employment_type", "seniority",
        "posted_date", "posted_recency", "skill_match", "source", "skills", "certs",
        "company_size", "company_industry", "apply_link", "salary", "salary_est", "notes", "status")}


def norm_linkedin(it):
    r = base_row()
    desc = s(g(it, "jobDescription", "description"))
    r["job_title"] = s(g(it, "jobTitle", "title"))
    r["company"] = s(g(it, "companyName", "company"))
    r["location"] = s(g(it, "location"))
    r["_et_raw"] = s(g(it, "employmentType", "contractType"))
    r["_level"] = s(g(it, "seniorityLevel", "inferredSeniorityLevel"))
    r["_wa_raw"] = s(g(it, "workplaceType", "workType")) + " " + r["location"]
    r["_date"] = g(it, "postedDate", "postedAt", "publishedAt") or g(it, "postedTime")
    sal = g(it, "salary") or {}
    if isinstance(sal, dict):
        r["_sal"] = (sal.get("min"), sal.get("max"), sal.get("currencySymbol") or sal.get("currency"),
                     sal.get("period"), s(sal.get("text")))
        r["salary"] = s(sal.get("text"))
    else:
        r["_sal"] = (None, None, None, None, s(sal))
        r["salary"] = s(sal)
    ey = g(it, "experienceYears")
    r["_yrs"] = ey.get("min") if isinstance(ey, dict) and isinstance(ey.get("min"), (int, float)) else None
    r["company_size"] = s(g(it, "companyEmployeeCount", "companySize"))
    r["company_industry"] = s(g(it, "industry", "companyIndustry", "industries"))
    r["apply_link"] = s(g(it, "jobUrl", "applyUrl", "url", "link"))
    r["_skills_raw"] = s(g(it, "skills"))
    r["_desc"] = desc
    r["source"] = "LinkedIn"
    return r


def norm_indeed(it):
    r = base_row()
    desc = s(g(it, "descriptionText", "description"))
    r["job_title"] = s(g(it, "title", "displayTitle", "normalizedTitle"))
    r["company"] = s(g(it, "companyName", "sourceEmployerName", "company"))
    loc = g(it, "location") or {}
    if isinstance(loc, dict):
        r["location"] = s(loc.get("formattedAddressShort") or loc.get("city") or loc.get("fullAddress")
                          or loc.get("country"))
    else:
        r["location"] = s(loc)
    r["_et_raw"] = s(g(it, "jobType")) + " " + s(g(it, "attributes"))
    r["_level"] = ""
    remote = g(it, "isRemote")
    r["_wa_raw"] = (("remote " if remote is True else "") + s(g(it, "workingSystem")) + " "
                    + s(g(it, "remoteWorkModel", "remote")) + " " + r["location"])
    r["_date"] = g(it, "datePublished", "pubDate", "dateOnIndeed") or g(it, "age")
    sal = g(it, "salary") or {}
    if isinstance(sal, dict):
        r["_sal"] = (sal.get("salaryMin"), sal.get("salaryMax"), sal.get("salaryCurrency"),
                     sal.get("salaryType"), s(sal.get("salaryText")))
        r["salary"] = s(sal.get("salaryText"))
    else:
        r["_sal"] = (None, None, None, None, s(sal))
        r["salary"] = s(sal)
    r["_yrs"] = None
    r["company_size"] = s(g(it, "companyNumEmployees"))
    r["company_industry"] = s(g(it, "companyIndustry", "companySector"))
    r["apply_link"] = s(g(it, "jobUrl", "applyUrl", "url"))
    r["_skills_raw"] = s(g(it, "requirements"))
    r["_desc"] = desc
    r["source"] = "Indeed"
    if not r["company"]:
        r["notes"] = "company missing on Indeed"
    return r


def norm_wellfound(it):
    r = base_row()
    desc = s(g(it, "description", "descriptionMarkdown"))
    r["job_title"] = s(g(it, "title"))
    r["company"] = s(g(it, "companyName"))
    r["location"] = s(g(it, "locationNames", "jobAddressLocality", "jobAddressCountry"))
    r["_et_raw"] = s(g(it, "jobType"))
    r["_level"] = ""
    rk = s(g(it, "remoteKind"))
    r["_wa_raw"] = ("remote " if g(it, "remote") is True else "") + rk + \
                   (" hybrid" if g(it, "wfhFlexible") is True else "")
    r["_date"] = g(it, "postedAt", "datePosted")
    r["_sal"] = (g(it, "salaryMin"), g(it, "salaryMax"), g(it, "salaryCurrency"),
                 g(it, "salaryPeriod"), s(g(it, "compensation")))
    r["salary"] = s(g(it, "compensation"))
    y = g(it, "yearsExperienceMin")
    r["_yrs"] = y if isinstance(y, (int, float)) else None
    r["company_size"] = s(g(it, "companySizeLabel", "companySize"))
    r["company_industry"] = s(g(it, "industry", "companyBusinessType"))
    r["apply_link"] = s(g(it, "detailUrl", "portalUrl", "url"))
    r["_skills_raw"] = s(g(it, "skills"))
    r["_desc"] = desc
    r["source"] = "Wellfound"
    return r


def norm_naukri(it):
    """muhammetakkurtt~naukri-job-scraper, basic mode (fetchDetails=false)."""
    r = base_row()
    desc = re.sub(r"<[^>]+>", " ", s(g(it, "jobDescription")))
    r["job_title"] = s(g(it, "title"))
    r["company"] = s(g(it, "companyName"))
    r["location"] = s(g(it, "location"))          # e.g. "Noida", "Hybrid - Gurugram, Bengaluru"
    r["_et_raw"] = s(g(it, "jobType", "employmentType"))
    r["_level"] = ""
    r["_wa_raw"] = r["location"]
    r["_date"] = g(it, "createdDate")
    sd = g(it, "salaryDetail") or {}
    lo = sd.get("minimumSalary") if isinstance(sd, dict) else None
    hi = sd.get("maximumSalary") if isinstance(sd, dict) else None
    if not lo or (isinstance(sd, dict) and sd.get("hideSalary")):
        r["_sal"] = (None, None, None, None, "")
    else:
        r["_sal"] = (lo, hi, sd.get("currency") or "INR", "year", "")   # Naukri CTC is annual
    sal = s(g(it, "salary"))
    r["salary"] = "" if sal.lower() == "not disclosed" else sal
    try:
        r["_yrs"] = int(g(it, "minimumExperience"))
    except (TypeError, ValueError):
        r["_yrs"] = None
    r["company_size"] = ""
    r["company_industry"] = ""
    r["apply_link"] = s(g(it, "jdURL"))
    r["_skills_raw"] = s(g(it, "tagsAndSkills"))
    r["_desc"] = desc
    r["source"] = "Naukri"
    if g(it, "walkinJob") is True:
        r["notes"] = "walk-in"
    return r


NORMALISERS = {"linkedin": norm_linkedin, "indeed": norm_indeed, "wellfound": norm_wellfound,
               "naukri": norm_naukri}


# ================================================================ classify
def relevance(title, desc):
    """Return (keep, reason, note)."""
    if HARD_NEG_TITLE.search(title):
        return False, "hard_negative_title", ""
    if TITLE_RX.search(title):
        return True, "", ""
    if OTHER_FIELD_TITLE.search(title):
        return False, "other_field_title", ""
    if DESC_TERMS and GENERIC_TITLE.search(title):
        hits = sum(1 for rx in DESC_TERMS if rx.search(desc))
        if hits >= DESC_MIN:
            return True, "", "relevance from description"
    return False, "not_relevant", ""


def classify(row):
    """Fill derived fields in place. Return drop reason or None."""
    title, desc = row["job_title"], row["_desc"]
    if not title:
        return "no_title"

    keep, reason, note = relevance(title, desc)
    if not keep:
        return reason
    if note:
        row["notes"] = (row["notes"] + "; " + note).strip("; ")

    # location: your country, or a location-less "Remote" / "Anywhere"
    loc = row["location"]
    if CHECK_COUNTRY and loc and not COUNTRY.search(loc) and not PLACELESS_START.fullmatch(loc):
        return "outside_country"

    # employment type
    types = employment_types(row["_et_raw"], title)
    if "Internship" in types and not KEEP_INTERNSHIPS:
        return "internship"
    et = pick_employment(types)
    if et == "Contract" and not KEEP_CONTRACT and not (types & KEEP_ET):
        return "contract"
    row["employment_type"] = et

    # seniority
    yrs = row["_yrs"]
    desc_yrs = min_years(desc)
    if yrs is None:
        yrs = desc_yrs
    sen = seniority(title, row["_level"], yrs)
    if sen in DROP_SENIORITY:
        return "seniority_%s" % sen.lower()
    if yrs is not None and MAX_MIN_YEARS is not None and yrs > MAX_MIN_YEARS:
        return "needs_%d+_years" % yrs
    row["seniority"] = sen
    if yrs:
        row["notes"] = (row["notes"] + "; asks %d+ yrs" % yrs).strip("; ")

    # recency
    d = parse_date(row["_date"])
    bucket, age = recency_bucket(d)
    if d is not None and age > MAX_AGE_DAYS:
        return "too_old"
    row["posted_date"] = d.isoformat() if d else ""
    row["posted_recency"] = bucket
    row["_age"] = age

    # work arrangement
    wa = work_arrangement(row["_wa_raw"], desc)
    # LinkedIn gives no workplace field. A row from a remote-only search with a city-less
    # location ("India") is taken as remote; a named city outside the area is not trusted.
    if wa == "Not Specified" and row.get("_remote_search") and row["source"] == "Naukri":
        wa = "Remote"   # Naukri's own workMode=remote filter is reliable
    if wa == "Not Specified" and row.get("_remote_search") and no_city(loc):
        wa = "Remote"
        row["notes"] = (row["notes"] + "; remote per search filter").strip("; ")
    row["work_arrangement"] = wa

    # location rules by arrangement
    if wa == "Remote":
        if REMOTE_MODE == "none":
            return "remote_not_wanted"
        # "country": other countries were already dropped above; "anywhere": all kept
    elif wa == "Hybrid":
        if not HYBRID_AREA.search(loc or ""):
            return "hybrid_outside_area"
    elif not ONSITE_AREA.search(loc or ""):
        return "onsite_outside_area"   # Onsite, and Not Specified (treated as onsite)
    row["_priority"] = wa != "Remote" and bool(PRIORITY.search(loc or ""))

    # salary
    lo, hi = annual_inr(*row["_sal"])
    if lo is not None:
        if hi < 20000 or hi > 2e8:  # garbage parse (checked in INR, the internal unit)
            lo = hi = None
        else:
            lo, hi = lo / FX_TO_INR[CURRENCY], hi / FX_TO_INR[CURRENCY]
            if hi < SALARY_FLOOR:
                return "below_salary_floor"
    row["salary_est"] = fmt_salary(lo, hi)
    row["_sal_hi"] = hi or 0

    # skills + certs
    hay = title + "\n" + desc + "\n" + row["_skills_raw"]
    skills = [n for n, rx in SKILLS if rx.search(hay)]
    certs = [n for n, rx in CERTS if rx.search(hay)]
    row["skills"] = ", ".join(skills)
    row["certs"] = ", ".join(certs)
    row["skill_match"] = len(skills)
    row["status"] = "Not Applied"
    row["description"] = desc          # kept for lib/ai_tailor.py (not shown in the tracker)
    return None


def dedupe_key(row):
    comp = re.sub(r"[^a-z0-9]", "", row["company"].lower())
    comp = re.sub(r"(privatelimited|pvtltd|limited|ltd|inc|llp|llc|technologies|solutions)$", "", comp)
    title = re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", row["job_title"].lower()))
    if comp and title:
        return comp + "|" + title
    return "url|" + row["apply_link"]


def better(a, b):
    """True if row a should be kept over b."""
    return (bool(a["salary_est"]), a["skill_match"], bool(a["company"])) > \
           (bool(b["salary_est"]), b["skill_match"], bool(b["company"]))


def norm_link(url):
    """Apply link without tracking params; Indeed keeps its jk= job id."""
    url = (url or "").strip()
    m = re.search(r"[?&]jk=([0-9a-f]+)", url)
    if m:
        return "indeed:" + m.group(1)
    return url.split("?")[0].split("#")[0].rstrip("/").lower()


def previously_listed(date):
    """Keys and apply links of every job shown in a tracker before <date>."""
    keys, links = set(), set()
    for p in glob.glob(os.path.join(ROOT, "output", "jobs_*.json")):
        d = os.path.basename(p)[5:15]
        if d >= date:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for r in json.load(f):
                    keys.add(r.get("_key") or dedupe_key(r))
                    if r.get("apply_link"):
                        links.add(norm_link(r["apply_link"]))
        except (ValueError, OSError):
            continue
    links.discard("")
    return keys, links


def main():
    if len(sys.argv) != 2:
        print("usage: classify_from_raw.py <YYYY-MM-DD>", file=sys.stderr)
        return 2
    date = sys.argv[1]
    files = sorted(glob.glob(os.path.join(ROOT, "output", "raw", "%s_*.json" % date)))
    if not files:
        print("[classify] no raw files for %s" % date)
        return 1

    raw_counts, drops = Counter(), Counter()
    kept = {}
    old_keys, old_links = previously_listed(date)   # no job is ever listed twice
    for path in files:
        site, _, label = os.path.basename(path)[len(date) + 1:-len(".json")].partition("_")
        remote_search = label.endswith("remote")
        norm = NORMALISERS.get(site)
        if not norm:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
        except (ValueError, OSError) as e:
            print("[classify] cannot read %s: %s" % (path, e))
            continue
        if isinstance(items, dict):
            items = items.get("items") or items.get("data") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            raw_counts[site] += 1
            row = norm(it)
            row["_remote_search"] = remote_search
            reason = classify(row)
            if reason:
                drops[reason] += 1
                continue
            key = dedupe_key(row)
            if key in old_keys or norm_link(row["apply_link"]) in old_links:
                drops["listed_before"] += 1
                continue
            if key in kept:
                drops["duplicate"] += 1
                old = kept[key]
                winner, loser = (row, old) if better(row, old) else (old, row)
                if loser["source"] != winner["source"] and "also on " + loser["source"] not in winner["notes"]:
                    winner["notes"] = (winner["notes"] + "; also on " + loser["source"]).strip("; ")
                kept[key] = winner
            else:
                kept[key] = row

    rows = list(kept.values())
    # Priority cities (onsite/hybrid) first, then everything else; within each, newest first.
    rows.sort(key=lambda r: (not r["_priority"], r["_age"], WA_ORDER.get(r["work_arrangement"], 9),
                             ET_ORDER.get(r["employment_type"], 3), -r["skill_match"]))
    out = []
    for r in rows:
        r["_key"] = dedupe_key(r)
        out.append({k: v for k, v in r.items() if not k.startswith("_") or k == "_key"})

    with open(os.path.join(ROOT, "output", "jobs_%s.json" % date), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("[classify] raw per site: %s (total %d)" % (dict(raw_counts), sum(raw_counts.values())))
    print("[classify] dropped: %s" % dict(drops.most_common()))
    print("[classify] kept: %d" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
