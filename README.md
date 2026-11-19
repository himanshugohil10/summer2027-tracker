# summer2027-tracker

Automated pipeline that finds **Summer 2027 AI / Data Science / ML internships** and lines up **Northeastern University alumni** at those companies as potential referral contacts. Everything lands in a Google Sheet, and a Discord digest tells you what was added each day.

It runs entirely on GitHub Actions, so there is no server to maintain.

## How it works

The workflow runs two jobs every day (Boston time):

| Time (Boston) | Mode | What it does |
|---|---|---|
| ~8:45 AM | `discover` | Finds new internships, finds alumni at each company, appends rows to the sheet, posts a Discord digest |
| ~3:07 PM | `enrich` | Fills in guessed work emails for rows added in the last 12 hours and flags how confident each guess is |

### 1. Discover (`python tracker.py`)

1. **Collect postings** from five sources:
   1. Community lists: [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships) and [pittcsc/Summer2027-Internships](https://github.com/pittcsc/Summer2027-Internships)
   2. Greenhouse job boards (about 45 companies)
   3. Lever job boards
   4. Ashby job boards
   5. Serper (Google search API) dorks over Workday, Greenhouse, Lever and Ashby (only if `SERPER_API_KEY` is set)
2. **Filter** each posting. It must:
   - be an internship whose title matches a target role (data science, ML, AI, NLP, LLM, data analyst, data engineer, analytics, BI, and similar)
   - not match the exclusions (SWE, quant and trading, computer vision, hardware, PhD-only, undergrad-only, sales and marketing)
   - not be at an excluded company (TikTok/ByteDance and HFT/prop-trading firms)
   - be explicitly **Summer 2027** (not Spring, Fall, Winter, co-op or earlier years)
   - be open to graduate students
   - be in the US (or have no location listed)
   - be new, meaning not already in the sheet or in `seen_jobs.json`
3. **Find alumni.** For each new role (up to 10 per run), search LinkedIn profiles through Serper for people who list Northeastern and currently work at that company. Up to 3 per group are kept, split between two groups using a surname and location heuristic, so a role can get up to 6 contacts.
4. **Write results.** Append rows to Google Sheets, update `seen_jobs.json`, and send a Discord summary.

### 2. Enrich (`python tracker.py --enrich`)

For rows from the last 12 hours that have no email and status `PENDING`:

1. Cross-check the person's name against their LinkedIn URL slug.
2. Look up the company's email format (a built-in table first, then one Serper lookup, then a `first.last@company.com` guess).
3. Write the guessed email into the sheet.
4. Set the status to `PENDING` (high confidence) or `REVIEW` (uncertain slug, unverified format, or a very large company where name collisions are likely).

> The emails are **guesses**, not verified addresses. Check anything you plan to send.

## Sheet layout

The script reads and writes `Sheet1` with this header row in A1:H1:

| Col | Content |
|---|---|
| A | Alumni name (`First Last`) |
| B | Company |
| C | Work email (filled by `--enrich`) |
| D | LinkedIn profile URL |
| E | Role title |
| F | Application link |
| G | Status (`PENDING` / `REVIEW`) |
| H | Date added (`YYYY-MM-DD HH:MM:SS`) |

## Setup

### 1. Fork or clone

```bash
git clone https://github.com/himanshugohil10/summer2027-tracker.git
cd summer2027-tracker
```

### 2. Create the services

- **Serper API key** from [serper.dev](https://serper.dev). It is used for LinkedIn alumni search and the Workday/ATS search.
- **Discord webhook** (Channel settings → Integrations → Webhooks).
- **Google Sheet** with the columns above, plus a **Google Cloud service account** with the Sheets API enabled. Share the sheet with the service account's email as an editor.

### 3. Add GitHub repository secrets

Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `SERPER_API_KEY` | Your Serper key |
| `DISCORD_WEBHOOK_URL` | Your Discord webhook URL |
| `SPREADSHEET_ID` | The ID in the sheet URL (`/d/<ID>/edit`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The service account JSON, either raw or base64-encoded |

### 4. Enable Actions

The workflow in `.github/workflows/daily_tracker.yml` runs on schedule once Actions is enabled. To run it immediately, go to **Actions → Daily Summer 2027 Internship & Alumni Tracker → Run workflow** and choose `discover` or `enrich`.

## Running locally

```bash
pip install requests google-api-python-client google-auth

# PowerShell: $env:SERPER_API_KEY = "..."
export SERPER_API_KEY=...
export DISCORD_WEBHOOK_URL=...
export SPREADSHEET_ID=...
export GOOGLE_SERVICE_ACCOUNT_JSON=...

python tracker.py            # discover
python tracker.py --enrich   # email enrichment
```

Without the Sheets credentials the script still runs but skips the sheet read and write steps.

## Repository layout

```
.
├── tracker.py                       # discovery + enrichment logic
├── seen_jobs.json                   # dedupe cache (committed by the workflow)
└── .github/workflows/
    └── daily_tracker.yml            # schedule, Boston-time gate, run, commit cache, alert on failure
```

## Customizing

All settings are constants at the top of `tracker.py`:

- `TARGET_ROLE_KEYWORDS` / `EXCLUDE_ROLE_KEYWORDS`: which titles count
- `EXCLUDE_COMPANIES`: companies to skip
- `GREENHOUSE_BOARDS` / `LEVER_BOARDS` / `ASHBY_BOARDS`: add `("Display Name", "board-token")` to watch another company
- `KNOWN_EMAIL_PATTERNS`: company domain and email format (`first`, `flast`, `first.last`, `firstl`, `first_last`)
- `MASSIVE_ENTERPRISES`: large companies whose rows always go to `REVIEW`
- The `"Northeastern"` checks in `is_verified_current_neu_alum` and the alumni search queries: change these to target a different school
- Roles per run: `jobs_populated >= 10` in `main()`

## Notes and limitations

- **Deduplication:** a job is added to `seen_jobs.json` as soon as it is processed, even if no alumni were found. It won't be retried later.
- **Alumni data** comes from Google search snippets of public LinkedIn results. It can be incomplete or stale, so verify before reaching out.
- **Respect the services you use.** Check LinkedIn's terms, and only message people in a personal, relevant way. Don't bulk-send to guessed addresses.
- Most network errors are swallowed so one failing source doesn't stop the run. Check the Actions logs if results look thin.
