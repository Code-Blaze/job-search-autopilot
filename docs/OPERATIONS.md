# Running it day to day

On Windows, type `py` where these commands say `python3`.

## Commands

| What | Command |
|---|---|
| Check your settings | `python3 lib/config.py check` |
| See what today's run would search (free) | `python3 run_daily.py --dry-run` |
| Run now | `python3 run_daily.py` |
| Run again today (reuses searches already paid for today) | `python3 run_daily.py --force` |
| Run every day at 09:00 / stop | `python3 schedule.py 9 0` / `python3 schedule.py --remove` |
| Re-filter a past day after changing settings (free) | `python3 lib/classify_from_raw.py 2026-11-07` then `python3 lib/write_tracker.py output/jobs_2026-11-07.json` |

## Where things are

| Path | What |
|---|---|
| `output/<Name>_Job_Tracker_<date>.xlsx` | the day's spreadsheet |
| `output/resumes/<date>/NN_Company_Title.docx` | tailored resumes, numbered like the spreadsheet rows (`_AI` = AI-rewritten) |
| `output/raw/<date>_<site>_<label>.json` | what each search returned, before filtering |
| `output/jobs_<date>.json` | the jobs that passed your filters |
| `output/.fetch_status_<date>.json` | which searches ran, failed or were skipped, and Apify credit |
| `logs/run_<date>.log` | everything that happened; start here when something looks wrong |

## Reading the log

- `[fetch] budget: today's allowance $0.26`: how much it will spend today.
- `[fetch] linkedin/x: skipped to stay on budget`: waits for a later day (most-waiting first).
- `[fetch] linkedin/x: Hyderabad search, runs on Hyderabad days`: an `alternate_regions` off-day.
- `[classify] dropped: {...}`: why jobs were filtered out. The common ones:

| Reason | Meaning / what to change in profile.json |
|---|---|
| `not_relevant` | title didn't match `roles.title_keywords` |
| `other_field_title`, `hard_negative_title` | matched `other_field_titles` / `title_exclude` |
| `seniority_senior`, `seniority_exec` | senior title or level (`experience.drop_levels`) |
| `needs_N+_years` | asks for more than `experience.max_years_required` |
| `onsite_outside_area`, `hybrid_outside_area` | not in `onsite_cities` / `hybrid_cities` |
| `outside_country`, `remote_not_wanted` | `locations.remote` and country settings |
| `too_old` | older than `max_posting_age_days` |
| `listed_before` | already shown on an earlier day (never repeated) |
| `duplicate` | same job found by two searches today (merged into one row) |

## Apify credit

- Each run reads your remaining credit and reset date from Apify and spends about
  `credit left ÷ days left` per day. Turn this off with the environment variable `BUDGET_PACING=0`.
- If a token runs out mid-run, the next one (`APIFY_TOKEN_2`, ...) is used automatically.
- If a search times out, the program waits for the run already started on Apify instead of paying
  for a second one, and a temporary failure is retried once.
- The Telegram message warns when a search failed, a token is at its limit, or credit is below $1.

## When something breaks

- **"no valid Apify token"**: check `secrets.env` (the whole token, inside the quotes).
- **A site's searches all fail**: the Apify actor may have changed its inputs. Open its page on
  Apify, compare with your `searches.json`, or try another actor for that site.
- **Settings errors**: `python3 lib/config.py check` points at the problem. JSON errors give a line
  and column number; usually a missing or extra comma or quote.
- **The daily run didn't happen**: the computer was off or asleep. It catches up on the next run
  (up to 3 days back). On Windows, enable "Run task as soon as possible after a scheduled start is
  missed" in Task Scheduler.
- **Telegram not arriving**: send your bot a message first, and check the chat id.
