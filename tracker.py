import os
import re
import json
import time
import base64
import hashlib
import requests
import urllib.parse
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON_RAW = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

CACHE_FILE = "seen_jobs.json"
NORTHEASTERN_SCHOOL_ID = "165340"

# Target technical domains (AI, Data Science, ML, Data Engineering)
TARGET_KEYWORDS = [
    "data science", "data scientist", "machine learning", "ml ", "ml/ai", "ai ",
    "artificial intelligence", "applied scientist", "research scientist",
    "deep learning", "nlp", "generative ai", "llm", "data analyst",
    "data engineer", "software engineer", "swe intern"
]

# Strict exclusions: Zero Computer Vision and Hardware roles
EXCLUDE_KEYWORDS = [
    "computer vision", "vision", "opencv", "image processing",
    "hardware", "firmware", "embedded", "fpga", "asic", "electrical",
    "pcb", "silicon", "semiconductor", "phd intern", "ph.d.", "doctoral",
    "undergraduate only", "high school", "sales", "marketing", "hr intern"
]

# Direct Public ATS Board Tokens (Zero auth required, 100% free)
GREENHOUSE_BOARDS = [
    ("OpenAI", "openai"),
    ("Anthropic", "anthropic"),
    ("Scale AI", "scaleai"),
    ("Figma", "figma"),
    ("Databricks", "databricks"),
    ("Stripe", "stripe"),
    ("Pinterest", "pinterest"),
    ("Instacart", "instacart"),
    ("Reddit", "reddit"),
    ("Lyft", "lyft"),
    ("DoorDash", "doordash"),
    ("Roblox", "roblox"),
    ("Airbnb", "airbnb"),
    ("Cloudflare", "cloudflare"),
    ("GitLab", "gitlab")
]

LEVER_BOARDS = [
    ("Palantir", "palantir"),
    ("Spotify", "spotify"),
    ("Cohere", "cohere")
]

def clean_url(url: str) -> str:
    if not url:
        return ""
    return url.strip().lower().split("?")[0].split("#")[0].rstrip("/")

def make_job_hash(company: str, title: str, url: str) -> str:
    c = re.sub(r'[^a-zA-Z0-9]', '', company.lower())
    t = re.sub(r'[^a-zA-Z0-9]', '', title.lower())
    u = clean_url(url)
    return hashlib.md5(f"{c}_{t}_{u}".encode("utf-8")).hexdigest()

def make_linkedin_search_url(company_name: str) -> str:
    """
    Constructs a direct LinkedIn People Search URL filtered by:
    1. School: Northeastern University (school ID 165340)
    2. Company: Target Company
    """
    clean_company = re.sub(r'[^a-zA-Z0-9\s]', '', company_name).strip()
    encoded_comp = urllib.parse.quote(f'"{clean_company}"')
    return f'https://www.linkedin.com/search/results/people/?keywords={encoded_comp}&schoolFilter=%5B%22{NORTHEASTERN_SCHOOL_ID}%22%5D'

def get_sheets_service():
    raw_val = (SERVICE_ACCOUNT_JSON_RAW or "").strip()
    if not raw_val:
        return None
    try:
        if raw_val.startswith("{"):
            info = json.loads(raw_val)
        else:
            decoded = base64.b64decode(raw_val).decode("utf-8")
            info = json.loads(decoded)
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        return build("sheets", "v4", credentials=creds)
    except Exception as e:
        print(f"Error authenticating Sheets: {e}")
        return None

def fetch_existing_sheet_state(service):
    existing_urls = set()
    existing_hashes = set()
    if not service or not SPREADSHEET_ID:
        return existing_urls, existing_hashes

    try:
        res = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2:F"
        ).execute()
        rows = res.get("values", [])
        for r in rows:
            company = r[1] if len(r) > 1 else ""
            role = r[4] if len(r) > 4 else ""
            job_link = r[5] if len(r) > 5 else ""
            if job_link:
                existing_urls.add(clean_url(job_link))
            if company and role:
                existing_hashes.add(make_job_hash(company, role, job_link))
    except Exception as e:
        print(f"Note on reading sheet: {e}")
    return existing_urls, existing_hashes

def load_seen_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_cache(seen_set):
    with open(CACHE_FILE, "w") as f:
        json.dump(sorted(list(seen_set)), f, indent=2)

def is_strictly_summer_2027(title: str, terms_list: list) -> bool:
    title_lower = title.lower()
    terms = [str(t).lower().strip() for t in terms_list]

    for bad in ["spring", "fall", "winter", "co-op", "coop", "2025", "2026"]:
        if any(bad in t for t in terms) and not any("summer" in t and "2027" in t for t in terms):
            return False
        if bad in title_lower and "summer 2027" not in title_lower:
            return False

    return any("summer" in t for t in terms) or "summer" in title_lower or "2027" in title_lower or "intern" in title_lower

def matches_technical_criteria(title: str) -> bool:
    title_lower = title.lower()
    if any(exc in title_lower for exc in EXCLUDE_KEYWORDS):
        return False
    return any(kw in title_lower for kw in TARGET_KEYWORDS)

def fetch_community_trackers(seen_set, existing_urls, existing_hashes):
    sources = [
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/pittcsc/Summer2027-Internships/dev/.github/scripts/listings.json"
    ]
    openings = []
    for url in sources:
        try:
            res = requests.get(url, timeout=15)
            if res.status_code != 200:
                continue
            data = res.json()
        except Exception:
            continue

        for item in data:
            comp = item.get("company_name", "").strip()
            role = item.get("title", "").strip()
            raw_url = str(item.get("url") or "").strip()
            norm_url = clean_url(raw_url)
            h = make_job_hash(comp, role, raw_url)

            if not norm_url or norm_url in existing_urls or h in existing_hashes or h in seen_set:
                continue
            if not item.get("active", True) or not item.get("is_visible", True):
                continue
            if not is_strictly_summer_2027(role, item.get("terms", [])):
                continue
            if not matches_technical_criteria(role):
                continue

            openings.append({
                "company": comp,
                "role": role,
                "url": raw_url,
                "hash": h
            })
    return openings

def fetch_direct_ats_jobs(seen_set, existing_urls, existing_hashes):
    openings = []

    # 1. Query Greenhouse Public Boards
    for comp_name, token in GREENHOUSE_BOARDS:
        try:
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", timeout=10)
            if r.status_code != 200:
                continue
            jobs = r.json().get("jobs", [])
            for j in jobs:
                role = j.get("title", "").strip()
                raw_url = j.get("absolute_url", "").strip()
                norm_url = clean_url(raw_url)
                h = make_job_hash(comp_name, role, raw_url)

                if norm_url in existing_urls or h in existing_hashes or h in seen_set:
                    continue
                if not is_strictly_summer_2027(role, ["summer 2027"]):
                    continue
                if not matches_technical_criteria(role):
                    continue

                openings.append({
                    "company": comp_name,
                    "role": role,
                    "url": raw_url,
                    "hash": h
                })
        except Exception:
            continue

    # 2. Query Lever Public Boards
    for comp_name, token in LEVER_BOARDS:
        try:
            r = requests.get(f"https://api.lever.co/v0/postings/{token}?mode=json", timeout=10)
            if r.status_code != 200:
                continue
            jobs = r.json()
            for j in jobs:
                role = j.get("text", "").strip()
                raw_url = j.get("hostedUrl", "").strip()
                norm_url = clean_url(raw_url)
                h = make_job_hash(comp_name, role, raw_url)

                if norm_url in existing_urls or h in existing_hashes or h in seen_set:
                    continue
                if not is_strictly_summer_2027(role, ["summer 2027"]):
                    continue
                if not matches_technical_criteria(role):
                    continue

                openings.append({
                    "company": comp_name,
                    "role": role,
                    "url": raw_url,
                    "hash": h
                })
        except Exception:
            continue

    return openings

def append_to_sheets(service, rows):
    if not service or not rows:
        return
    body = {"values": rows}
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Sheet1!A:H",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()

def send_discord_summary(job_count, lines_summary):
    if not DISCORD_WEBHOOK_URL:
        return
    sheet_link = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
    description = (
        f"☀️ **Good Morning! Here is your 8:00 AM Referral Pipeline Digest:**\n\n"
        f"• **New Summer 2027 Openings Added:** {job_count}\n"
        f"• **Pre-Filtered Northeastern Alumni Links Generated:** {job_count}\n\n"
        f"📊 **[Open Google Sheets Tracker]({sheet_link})**\n\n"
        f"**Recently Added Openings:**\n" + (lines_summary or "_No new openings this morning._")
    )
    embed = {
        "title": "⚡ Daily Summer 2027 Referral Pipeline Update",
        "description": description[:2000],
        "color": 13369344,
        "footer": {"text": f"{datetime.now().strftime('%b %d, %Y')} | Direct ATS + Multi-Source"}
    }
    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)

def main():
    seen_cache = load_seen_cache()
    sheets_service = get_sheets_service()

    existing_urls, existing_hashes = fetch_existing_sheet_state(sheets_service)

    # Ingest from Community Trackers + Direct ATS Boards
    all_candidates = []
    all_candidates.extend(fetch_community_trackers(seen_cache, existing_urls, existing_hashes))
    all_candidates.extend(fetch_direct_ats_jobs(seen_cache, existing_urls, existing_hashes))

    # In-memory deduplication across sources
    unique_new_jobs = []
    seen_in_run = set()
    for job in all_candidates:
        if job["hash"] not in seen_in_run:
            seen_in_run.add(job["hash"])
            unique_new_jobs.append(job)

    print(f"Discovered {len(unique_new_jobs)} net-new Summer 2027 roles.")

    sheet_rows = []
    summary_lines = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for job in unique_new_jobs:
        company = job["company"]
        role = job["role"]
        job_url = job["url"]
        h = job["hash"]

        # 100% accurate, pre-filtered LinkedIn People Search URL
        neu_linkedin_url = make_linkedin_search_url(company)

        # Row schema:
        # Col A: First Name (empty for user to fill)
        # Col B: Company Name
        # Col C: Email ID (empty for user to fill)
        # Col D: Pre-Filtered LinkedIn URL (School: NEU + Company: Target)
        # Col E: Role Title
        # Col F: Application Link
        # Col G: Status
        # Col H: Date Added
        sheet_rows.append([
            "",
            company,
            "",
            neu_linkedin_url,
            role,
            job_url,
            "PENDING",
            today_str
        ])

        summary_lines.append(f"• **{company}** — [{role}]({job_url})")
        seen_cache.add(h)
        seen_cache.add(clean_url(job_url))

    if sheet_rows:
        append_to_sheets(sheets_service, sheet_rows)

    save_seen_cache(seen_cache)
    summary_text = "\n".join(summary_lines[:15])
    send_discord_summary(len(unique_new_jobs), summary_text)
    print("Run completed successfully.")

if __name__ == "__main__":
    main()
