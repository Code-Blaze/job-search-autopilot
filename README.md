# Job Search Autopilot

**Every morning, get a spreadsheet of fresh jobs that actually fit you, plus a resume tailored to each one.**

Built by someone who was job hunting and got tired of scrolling the same listings on five sites.
If you've been laid off or are looking for your next role, this does the boring part for you so
you can spend your energy on applying and interviewing.

Free and open source (GPL-3.0). Runs on your own computer. Your data stays with you.

---

## What you get each day

- **A job tracker spreadsheet** (`.xlsx`, opens in Excel, Numbers, Google Sheets or LibreOffice) with
  only the jobs that match *your* rules: role, seniority, years of experience, city, onsite / hybrid /
  remote, salary floor, posted in the last few days. Each row has the apply link, the skills the job
  asks for, how many of them match yours, and a **Status** column (Applied, Interviewing, Offer...)
  you can track in.
- **No repeats.** A job you saw yesterday never shows up again, even if it's re-posted on another site.
- **A tailored resume for every job** (`.docx`). Your real resume, reordered so the experience and
  skills that job cares about come first. It **never invents anything**: a built-in check refuses to
  write a resume whose wording differs from yours.
- **Optional:** the spreadsheet and resumes sent to your phone on **Telegram**, and an **AI rewrite**
  of the resume for the top 3 jobs (still checked line by line so it can't add skills you don't have).

It searches **LinkedIn, Indeed, Naukri** (India) and **Wellfound** (startups) through
[Apify](https://apify.com), a service that runs ready-made job-site scrapers for you.

## What it costs

| Thing | Cost |
|---|---|
| This program | Free |
| Apify | **Free plan: $5 of credit every month.** A LinkedIn or Indeed search is about $0.10; Naukri is under $0.01. The program paces itself so it never runs out mid-month (see [Costs](#costs-and-the-free-apify-credit)). |
| Telegram | Free (optional) |
| AI resume rewrite | Optional. Needs an Anthropic **API key** (pay-per-use; a Claude Pro/Max chat subscription does not include API use). Roughly $0.05–0.12 per resume, about $5–10/month for 3 a day. Without a key, the free rule-based resumes are used. |

## What you need

- A **Windows, Mac or Linux** computer that's on at the time you schedule it.
- **Python 3.8 or newer** (nothing else to install):
  - **Windows:** install it from [python.org/downloads](https://www.python.org/downloads/). In the
    installer, tick **"Add python.exe to PATH"**.
  - **Mac:** open Terminal and run `xcode-select --install` once.
  - **Linux:** usually already installed (`python3 --version`).
- A free **Apify account**.
- About 30–60 minutes for the first setup, mostly to describe what you're looking for.

You don't need to know how to code. You'll edit three text files, and there's an
[AI-assisted way](docs/PERSONALIZE_WITH_AI.md) to fill them in from your resume.

> **Commands below:** on **Windows** type `py` where it says `python3`, in **Command Prompt** or
> **PowerShell** (Start menu → type "cmd"). On Mac/Linux use **Terminal**. First move into the
> program's folder with `cd`, e.g. `cd Downloads\job-search-autopilot` (Windows) or
> `cd ~/Downloads/job-search-autopilot` (Mac).

---

## Quick start

### 1. Download

```bash
git clone https://github.com/Code-Blaze/job-search-autopilot.git
cd job-search-autopilot
```

**No git?** On the GitHub page click **Code → Download ZIP**, unzip it, and open a terminal in that
folder. (Windows: open the folder in File Explorer, click the address bar, type `cmd`, press Enter.)

### 2. Run setup and pick a starting example

```bash
python3 first_time_setup.py
```

It lists the examples. Pick the closest one; you'll change it to fit you:

| Example | For |
|---|---|
| `cybersecurity-india` | SOC / AppSec / DevSecOps roles in Pune and Hyderabad, India, using LinkedIn, Indeed and Naukri |
| `software-engineer-usa` | Frontend / full-stack roles in Austin and Seattle, USA, using LinkedIn and Indeed |

Setup copies the example into `config/` and creates `secrets.env`. Nothing in `config/` or
`secrets.env` is ever uploaded anywhere.

### 3. Make it yours

Edit these three files with any plain-text editor (Notepad on Windows, TextEdit in plain-text mode
on Mac, or [VS Code](https://code.visualstudio.com/) anywhere, which also flags typos in the file):

| File | What to put in it |
|---|---|
| `config/profile.json` | Your name, the job titles you want and don't want, years of experience, cities and onsite/hybrid/remote, minimum salary, your skills |
| `config/searches.json` | What to type into each job site's search box, and where |
| `config/resume.json` | Your real resume, so it can be tailored for each job |

Every setting is explained in **[docs/CUSTOMIZE.md](docs/CUSTOMIZE.md)**.
**Shortcut:** paste your resume and the prompt in [docs/PERSONALIZE_WITH_AI.md](docs/PERSONALIZE_WITH_AI.md)
into an AI assistant (Claude, ChatGPT...) and it will draft all three files for you to review.

Then check your work (free, nothing is searched):

```bash
python3 lib/config.py check
```

### 4. Add your Apify token

1. Sign up at [apify.com](https://apify.com) (free plan).
2. Go to **Settings → API & Integrations** and copy your **Personal API token**.
3. Open `secrets.env` and paste it in place of `REPLACE_WITH_YOUR_APIFY_TOKEN`.

Keep `secrets.env` private: anyone with the token can spend your Apify credit.

### 5. Test, then run for real

```bash
python3 run_daily.py --dry-run    # shows what it WOULD search; costs nothing
python3 run_daily.py              # real run, a few minutes
```

Your spreadsheet is in `output/` and the resumes in `output/resumes/<date>/`.
If something went wrong, read `logs/run_<date>.log`.

### 6. Run it every day

```bash
python3 schedule.py 9 0        # every day at 09:00 (pick your own time)
python3 schedule.py --remove   # to stop it later
```

Windows uses Task Scheduler, Mac uses launchd, Linux uses cron. The computer needs to be on at
that time (on Windows, see the tip it prints about catching up after the PC was off).
If it misses a day, the next run looks back up to 3 days, so you don't miss jobs.

---

## Optional extras

### Get it on your phone (Telegram)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the steps, and copy the **bot token**.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in a browser and find
   `"chat":{"id":123456789` — that number is your **chat id**.
4. Put both in `secrets.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

Each day you'll get the spreadsheet with the top 3 picks in the message, the resumes as a zip, and
a warning if a search failed or your Apify credit is low.

### AI-rewritten resume for the top 3 jobs

Put an Anthropic API key ([console.anthropic.com](https://console.anthropic.com), billed separately
from any Claude chat subscription) in `secrets.env` and run `python3 -m pip install --user anthropic`.
It costs roughly $0.05–0.12 per resume; set the environment variable `AI_TAILOR_TOP_N=1` to rewrite
only the top job (the run log prints the actual cost each day). Each rewritten line is checked against your real
resume and thrown away if it adds a number, tool, certification, employer or seniority claim
("led", "managed"...) that isn't already there. What the job asks for that your resume lacks goes
into the Notes column, for interview prep. Without a key, the free rule-based resumes are used.

---

## Costs and the free Apify credit

- At the start of each run the program asks Apify how much credit you have left and how many days
  until it resets, and spends only about `credit left ÷ days left` today.
- **Cheap searches** (Naukri, under $0.01) run every day. **LinkedIn/Indeed** searches (~$0.10 each)
  take turns, the longest-waiting first. With $5/month that's about 1–2 of them a day.
- **Several cities?** List them under `alternate_regions` in `profile.json` and each city's paid
  searches run on its own days (e.g. even dates Pune, odd dates Hyderabad), so each city is
  covered every other day instead of one city crowding out the other.
- Need more searches? Upgrade your Apify plan, or add a second token (`APIFY_TOKEN_2`, e.g. from
  a paid plan or a team account); it's used when the first runs out. Don't open extra free
  accounts to get around the free limit; check Apify's terms.
- Find weak searches (ones that never produce a job you keep) and turn them off with
  `"_enabled": false` in `searches.json`; that frees credit for the good ones.

## FAQ

**Is this allowed?** Job sites' terms of service generally restrict automated access. This program
doesn't scrape anything itself; it calls scrapers that third parties publish on Apify, using
*your* Apify account, at a small personal scale (a few dozen listings a day). You are responsible
for how you use it. See the [disclaimer](#disclaimer).

**Does it apply to jobs for me?** No, on purpose. It finds and sorts; you decide and apply.

**Windows?** Yes, it works natively: install Python from python.org (tick "Add to PATH") and use
`py` instead of `python3` in the commands. The daily run uses Task Scheduler and runs quietly in
the background (no window pops up).

**I'm in another country / field.** Start from the closest example and change the keywords,
cities, currency and searches. LinkedIn and Indeed work in most countries. If you build a good
setup for your field or country, please contribute it as a new folder in `examples/`!

**Few or no jobs?** Run `python3 run_daily.py`, then look at the `dropped:` line in the log. It
says why jobs were dropped (`not_relevant`, `seniority_senior`, `onsite_outside_area`...). Loosen the
matching setting in `profile.json`. Old days can be re-checked for free with
`python3 lib/classify_from_raw.py <date>` after changing settings.

**Where is my data?** Only on your computer: `config/`, `output/`, `logs/`, `secrets.env`.
They're in `.gitignore`, so they're never committed if you fork this repo.

More: [docs/OPERATIONS.md](docs/OPERATIONS.md) (logs, re-runs, budget, troubleshooting).

## Responsible use

This tool is for **your own job search**, at a small personal scale: a few searches a day, read by
you. Please don't use it to collect job listings in bulk, build a database of them, or resell the
data, and keep the number of searches modest.

## Contributing

Issues and pull requests are welcome, especially:
- new `examples/` for other fields and countries (with a made-up sample resume, never a real one),
- support for more job sites (each is an Apify actor + a small "normaliser" in `lib/classify_from_raw.py`),
- clearer docs for non-technical job seekers.

## Disclaimer

This software is provided as is, without warranty (see [LICENSE](LICENSE)). It is not affiliated
with LinkedIn, Indeed, Naukri, Wellfound, Apify, Telegram or Anthropic. Third-party scrapers can
break or change price at any time. You are responsible for complying with the terms of the
services you use, and for checking every job and resume before you send it.

## License

Copyright (C) 2026 Code-Blaze and contributors.

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version. See [LICENSE](LICENSE).
