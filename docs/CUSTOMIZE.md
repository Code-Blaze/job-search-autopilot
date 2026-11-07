# Customising the job search

You set everything in three files in `config/` (created by `first_time_setup.py` from an example).
They're JSON: text in `"quotes"`, lists in `[square brackets]`, commas between items but **not after
the last one**. Run `python3 lib/config.py check` after every change (Windows: `py lib/config.py check`);
it tells you about typos and what it understood. Keys starting with `_` (like `_help`) are notes and are ignored.

**Tip:** after changing `profile.json` you can re-check a day you already fetched, for free:
`python3 lib/classify_from_raw.py 2026-11-07` (then `python3 lib/write_tracker.py output/jobs_2026-11-07.json`
for the spreadsheet). Note that jobs already shown on an earlier day are never shown again.

---

## Keywords: how matching works

Most settings are **lists of keywords**. Each keyword is a word or phrase, matched as a whole word,
ignoring upper/lower case:

| You write | It matches | It doesn't match |
|---|---|---|
| `"react"` | React, REACT, React.js | Reactive (it's a different word) |
| `"incident response"` | Incident Response, incident  response | |
| `"front-end"` | front-end, front end, frontend | |
| `"forensic*"` | forensic, forensics, forensically | |
| `"c++"`, `".net"`, `"node.js"` | those exact terms | |
| `"re:\\bQA\\b"` | a raw [regular expression](https://docs.python.org/3/library/re.html) (advanced; `\` is written `\\` in JSON) | |

---

## profile.json

### `name`, `field`
- `name`: used in the spreadsheet file name (`Jane_Doe_Job_Tracker_<date>.xlsx`).
- `field`: a few words like `"nursing"` or `"data analytics"`, used to brief the optional AI resume writer.

### `roles`: which job titles to keep
A job goes through these checks in order:

1. Title has a word from **`title_exclude`** → dropped (`hard_negative_title`). Use this for
   look-alike jobs: "security guard" when you want "security engineer", "sales" when you want "engineer".
2. Title has a word from **`title_keywords`** → **kept**. These are the job titles you want.
3. Title has a word from **`other_field_titles`** → dropped (`other_field_title`). Neighbouring roles
   you don't want, e.g. "developer" for a security analyst.
4. Title is generic (a word from **`generic_titles`**, like "Analyst" or "Associate") **and** the
   description has at least **`description_min_matches`** of the **`description_keywords`** → kept
   (marked "relevance from description" in Notes).
5. Anything else → dropped (`not_relevant`).

Start broad, run for a few days, and look at the `dropped:` counts in the log.

### `experience`
- `max_years_required`: drop jobs whose posting asks for more years than this (e.g. "5+ years").
  `null` = no limit.
- `drop_levels`: the levels to drop. The program labels each job as **Entry, Associate, Mid,
  Senior** or **Exec** from the title (Senior, Lead, Principal, Manager, Head, Director...), the
  site's own level, and the years asked. For senior people: `["Entry"]` or `[]`.

### `employment`
`keep_internships`, `keep_contract`: `true` or `false`. Full-time, part-time and freelance jobs are always kept.

### `max_posting_age_days`
Drop postings older than this. 3 is a good default for a daily run.

### `salary`
- `currency`: your currency code (`INR`, `USD`, `EUR`, `GBP`, `CAD`, `AUD`, `SGD`, `AED`, ...).
  Salaries in other currencies are converted with rough built-in rates.
- `minimum_per_year`: jobs that **show** a salary below this per year are dropped. Jobs that don't
  show a salary are always kept (most don't). `0` = no minimum.

### `locations`
Rules depend on the job's work arrangement (Onsite, Hybrid, Remote, or Not Specified, which
counts as Onsite):

| Setting | Meaning |
|---|---|
| `onsite_cities` | onsite (and unspecified) jobs must be in one of these |
| `hybrid_cities` | hybrid jobs must be in one of these (often a wider commuting area) |
| `remote` | `"country"`: remote jobs anywhere in your country · `"anywhere"`: any country · `"none"`: no remote jobs |
| `country_names` | words that mean your country in a location ("india", "united states") |
| `country_codes` | short codes ("in", "us"), only used to recognise locations like "Remote, IN" |
| `country_places` | cities/states in your country, so "Pune" counts as India even without the word India |
| `remote_location_words` | locations meaning "no place" ("remote", "worldwide"); add your region, e.g. "emea", "asia" |
| `priority_cities` | onsite/hybrid jobs here are sorted to the top of the spreadsheet |
| `alternate_regions` | see below |

**`alternate_regions`**: if you search several cities but have little Apify credit, list them here:

```json
"alternate_regions": [
  {"id": "ncr", "name": "Delhi NCR", "match": ["delhi", "gurgaon", "noida"]},
  {"id": "blr", "name": "Bengaluru", "match": ["bengaluru", "bangalore"]}
]
```

Each paid search whose `location` (in `searches.json`) matches a region runs only on that region's
days: with two regions, even dates for the first and odd dates for the second (with three, they rotate
by date). Remote / country-wide searches run any day. Cheap "_always_run" sites (Naukri) aren't
affected. Leave it as `[]` to turn this off.

### `skills` and `certs`
What to look for in each job, from **your** resume. They fill the Skills, Skill Match and Certs
Mentioned columns, and decide which bullets and skills move up in the tailored resume.

```json
"skills": [
  "Python",                                              // name = what to look for
  {"name": "AWS", "exact_case": true},                   // only "AWS", not "aws" in other words
  {"name": "Excel", "match": ["excel", "spreadsheet*"]}  // several ways to say it
]
```
(JSON doesn't allow `//` comments; they're only here to explain.)

### `resume_tracks`
If you apply for two or three kinds of roles (e.g. SOC analyst vs application security, or frontend
vs full-stack), define a **track** for each. For every job the program picks the track whose
`title_keywords` appear in the title (worth 3 points) and whose `skills` appear in the job (1 point
each). That track's headline is used and its skills category moves to the top. Ties go to the track
listed first; no match uses `default`. One track, or `[]`, is fine too.

### `resume`
`page_size` (`"A4"` or `"Letter"`), `font`, `language` (spell-check language, e.g. `"en-US"`, `"en-GB"`).

---

## resume.json

Your real resume. The program **only reorders** it (never adds or rewrites), so everything in it
must be true and ready to send.

| Field | Content |
|---|---|
| `name` | your name |
| `headlines` | one line under your name, per track id from `resume_tracks`, plus `"default"` |
| `contact` | list of `{"text": "...", "url": "..."}` (url optional: `mailto:`, `tel:`, `https://`) |
| `experience` | list of jobs: `title`, `org`, `location`, `dates`, `bullets` (list). Order stays as you write it. |
| `skills` | list of categories: `label`, `items`, optional `track` (moves to the top for that track) |
| `projects` | `name`, `tech`, optional `url`, `bullets` |
| `certifications` | list of strings |
| `achievements` | list of `{"text": ...}`; add `link_text` + `url` to link part of the text |
| `education` | `school`, `dates`, `degree`, `bullets` |
| `section_order` | order of sections per track (and `default`), from: experience, skills, projects, certifications, achievements, education |

Sections you leave empty are skipped. The resume is a simple one-column `.docx`, which works well
with applicant tracking systems. Open one in Word/Pages/Google Docs to check it before sending.

---

## searches.json

One block per job site; each runs an [Apify](https://apify.com) "actor" (a ready-made scraper) with
the inputs you give. Open the actor's page on Apify (search its name, e.g. `crawlworks/linkedin-jobs-scraper`)
to see every input it accepts.

```json
"linkedin": {
  "actor": "crawlworks~linkedin-jobs-scraper",
  "_enabled": true,
  "_cost_usd": 0.1,
  "runs": [
    {"_label": "nurse-austin", "query": "Registered Nurse", "location": "Austin, Texas, United States", ...},
    {"_label": "nurse-remote", "query": "Telehealth Nurse", "location": "United States", "remote": true, "hybrid": false, "onSite": false, ...}
  ]
}
```

- `_label`: a short unique name; used in logs and file names. **End it with `-remote`** for
  remote-only searches (helps classify jobs whose location says just "United States").
- `_enabled: false` on a site or a run turns it off without deleting it.
- `_cost_usd`: rough cost per search, used for budgeting. `_always_run: true` for very cheap sites.
- `_region`: force a run's region for `alternate_regions` (`"any"` = every day).
- The posting-age fields (`timePostedRange`, `fromDays`, `freshness`) are set automatically to cover
  the days since the last run.

**Which sites?** LinkedIn and Indeed work in most countries (Indeed: set `country` to your 2-letter
code). Naukri is India-only and very cheap. Wellfound is startups (mostly US/remote). To use
another site, find an Apify actor for it; adding it needs a small "normaliser" function in
`lib/classify_from_raw.py` (contributions welcome).

**Fewer, better searches.** Each LinkedIn/Indeed search costs about $0.10. A good search is one
specific job title in one place. After a week, compare how many jobs each search contributed
(`output/raw/` has what each search returned) and turn off the ones that add nothing new.
