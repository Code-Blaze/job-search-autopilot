# Fill in your settings with an AI assistant

Writing keyword lists by hand takes a while. An AI assistant (Claude, ChatGPT, Gemini...) can draft
all three files from your resume in a few minutes. **You still review them**, especially
`resume.json`, which must contain only true things.

## How

1. Run `first_time_setup.py` and pick the example closest to you.
2. Open a new chat with the assistant and paste **the prompt below**.
3. Paste after it:
   - your resume (as text: copy it out of the PDF or Word file),
   - the three files from your `config/` folder (`profile.json`, `searches.json`, `resume.json`).
4. Answer its questions, then copy each file it gives you back into `config/`, replacing the old one.
5. Run `python3 lib/config.py check` (Windows: `py lib/config.py check`) and fix anything it reports.

**Privacy:** your resume goes to the AI service you use. Remove anything you don't want to share
(phone number, address) and add it back into `config/resume.json` yourself afterwards.
Never paste `secrets.env`.

If you use an AI coding tool that can open your files (like Claude Code), you can instead
open the program's folder in it and ask it to follow this prompt directly.

## The prompt

```text
I'm setting up "Job Search Autopilot", a program that searches job sites every day, filters jobs
with rules from a settings file, and makes a tailored copy of my resume for each job. Help me fill
in its three JSON settings files. I'll paste my resume and the three example files after this.

First, ask me (all at once, briefly) whatever you need that isn't in my resume:
- the job titles I want, and nearby titles I do NOT want
- my years of experience, and the most years a job may ask for
- my country, the cities I'd go to the office in, cities OK for hybrid, and whether I want
  remote jobs (in my country / anywhere / none); my top-priority city
- minimum yearly salary and currency (or none)
- internships / contract jobs OK?
- which job sites to use (LinkedIn, Indeed; Naukri if in India) and roughly how many searches
  I can afford (each LinkedIn/Indeed search costs about $0.10 and the free plan gives $5/month)

Then give me the three complete files, each in its own code block, keeping exactly the same
structure and key names as the examples (keep the "_help" notes):

1. profile.json
   - roles.title_keywords: the words that make a title relevant to me (short words and
     phrases, lowercase; use "-" for words that may be joined, like "front-end"; "*" at the end for
     any ending). Be generous: include variations and abbreviations.
   - roles.title_exclude: look-alike titles to always drop; roles.other_field_titles: neighbouring
     fields I don't want; generic_titles + description_keywords for vague titles like "Analyst".
   - experience, employment, salary, locations as I answer. country_places should list the main
     cities and states/regions of my country. If I search more than one city and have little
     credit, set alternate_regions (one entry per city, with the words that identify it).
   - skills and certs: ONLY things in my resume plus the ones most often asked for in my target
     roles, each written the way job posts write it. Use {"name": ..., "exact_case": true} for
     short acronyms that are also normal words.
   - resume_tracks: one per kind of role I'm targeting (1-3), each with title words and the skill
     names (from "skills") that point to it.
2. searches.json: one run per (job title, place) I want, labels short and unique, remote-only
   runs with labels ending in "-remote". Keep each site's actor and "_" settings as in the example.
   Fit the number of paid searches to my budget.
3. resume.json: my resume transcribed into the structure, word for word. Do NOT improve, add,
   or invent anything: no new skills, numbers, tools or titles. One headline per track plus a
   "default" headline, each built only from roles and skills already in my resume.

The output must be valid JSON (double quotes, no trailing commas, no comments).
```
