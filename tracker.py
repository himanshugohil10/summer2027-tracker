import os
import re
import json
import time
import requests
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON_RAW = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

UPSTREAM_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
CACHE_FILE = "seen_jobs.json"

TARGET_KEYWORDS = [
    "data science", "data scientist", "machine learning", "ml ", "ml/ai", "ai ",
    "artificial intelligence", "applied scientist", "research scientist",
    "deep learning", "nlp", "computer vision", "generative ai", "llm",
    "data analyst", "data engineer"
]

EXCLUDE_TITLES = [
    "phd intern", "ph.d. intern", "doctoral intern", "undergraduate only",
    "high school", "sales intern", "marketing intern", "hr intern"
]

def get_sheets_service():
    if not SERVICE_ACCOUNT_JSON_RAW:
        return None
    info = json.loads(SERVICE_ACCOUNT_JSON_RAW)
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)

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

def is_strictly_summer_2027(item):
    """
    STRICT FILTER: Must be strictly Summer 2027.
    Explicitly rejects Spring, Fall, Winter, 2025, or 2026.
    """
    terms = [str(t).lower().strip() for t in item.get("terms", [])]
    title = item.get("title", "").lower()
    
    # Reject non-summer terms
    for bad_term in ["spring", "fall", "winter", "co-op", "coop", "2025", "2026"]:
        if any(bad_term in t for t in terms) and not any("summer" in t and "2027" in t for t in terms):
            return False
        if bad_term in title and "summer 2027" not in title:
            return False

    has_summer_2027_term = any("summer" in t and "2027" in t for t in terms)
    has_summer_2027_title = "summer 2027" in title or ("summer" in title and "2027" in title)
    
    return has_summer_2027_term or has_summer_2027_title

def fetch_internships(seen_set):
    try:
        res = requests.get(UPSTREAM_URL, timeout=20)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        print(f"Error fetching listings: {e}")
        return []

    eligible = []
    for item in data:
        job_id = str(item.get("id") or item.get("url") or "")
        if not job_id or job_id in seen_set:
            continue

        if not item.get("active", True) or not item.get("is_visible", True):
            continue

        if not is_strictly_summer_2027(item):
            continue

        title = item.get("title", "").lower()
        if any(exc in title for exc in EXCLUDE_TITLES):
            continue

        if not any(kw in title for kw in TARGET_KEYWORDS):
            continue

        # Location check: USA / Remote
        locations = " ".join(item.get("locations", [])).lower()
        is_us = any(x in locations for x in [
            "usa", "united states", "remote", ", ca", ", ny", ", ma", 
            ", wa", ", tx", ", il", ", va", ", nj", ", co", ", nc"
        ])
        if not is_us and locations:
            continue

        eligible.append(item)

    return eligible

def find_northeastern_alumni(company_name, max_results=5):
    clean_company = re.sub(r'[^a-zA-Z0-9\s]', '', company_name).strip()
    if not clean_company:
        return []

    query = f'site:linkedin.com/in "Northeastern University" "{clean_company}" (engineer OR developer OR data OR scientist)'
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": max_results}

    try:
        res = requests.post("https://google.serper.dev/search", headers=headers, json=payload, timeout=12)
        if res.status_code != 200:
            return []
        
        organic = res.json().get("organic", [])
        if not organic:
            fallback = f'site:linkedin.com/in "Northeastern University" "{clean_company}"'
            res = requests.post("https://google.serper.dev/search", headers=headers, json={"q": fallback, "num": max_results}, timeout=12)
            organic = res.json().get("organic", [])

        alumni = []
        seen_links = set()

        for it in organic:
            link = it.get("link", "")
            if "linkedin.com/in/" not in link or link in seen_links:
                continue
            seen_links.add(link)

            raw_title = it.get("title", "").replace(" - LinkedIn", "").strip()
            parts = [p.strip() for p in raw_title.split(" - ") if p.strip()]
            full_name = parts[0] if parts else "Alum"
            
            # Clean first name (strip Dr., Mr., etc.)
            name_tokens = full_name.split()
            first_name = name_tokens[0] if name_tokens else "Hi"
            if first_name.lower() in ["dr.", "mr.", "ms.", "mrs."] and len(name_tokens) > 1:
                first_name = name_tokens[1]

            alumni.append({
                "first_name": first_name,
                "full_name": full_name,
                "url": link
            })
        return alumni
    except Exception as e:
        print(f"Alumni search error: {e}")
        return []

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

def send_discord_summary(job_count, people_count, jobs_summary):
    if not DISCORD_WEBHOOK_URL:
        return
    
    sheet_link = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
    description = (
        f"🎯 **Run Complete!**\n\n"
        f"• **New Summer 2027 Roles Found:** {job_count}\n"
        f"• **Northeastern Alumni Added:** {people_count}\n\n"
        f"📊 **[Open Google Sheets Tracker]({sheet_link})**\n\n"
        f"**Latest Roles Added:**\n" + (jobs_summary or "None")
    )

    embed = {
        "title": "⚡ Daily Summer 2027 Referral Pipeline Update",
        "description": description[:2000],
        "color": 13369344,
        "footer": {"text": f"{datetime.now().strftime('%b %d, %Y')} | Automated Outreach System"}
    }
    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)

def main():
    seen_cache = load_seen_cache()
    new_jobs = fetch_internships(seen_cache)
    print(f"Identified {len(new_jobs)} new strictly Summer 2027 roles.")

    sheets_service = get_sheets_service()
    total_people_added = 0
    sheet_rows = []
    job_summary_lines = []

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Process up to 10 openings per day to preserve Serper API credits
    for job in new_jobs[:10]:
        company = job.get("company_name", "Company")
        role = job.get("title", "Summer 2027 Intern")
        url = job.get("url", "")
        job_id = str(job.get("id") or url)

        alumni = find_northeastern_alumni(company, max_results=5)
        
        job_summary_lines.append(f"• **{company}** — [{role}]({url}) ({len(alumni)} alumni)")

        for alum in alumni:
            # Row structure: First Name, Company, Email ID (blank), LinkedIn, Role, App Link, Status, Date
            sheet_rows.append([
                alum["first_name"],
                company,
                "", # Left empty for you to fill work emails
                alum["url"],
                role,
                url,
                "PENDING",
                today_str
            ])
            total_people_added += 1

        seen_cache.add(job_id)
        time.sleep(1)

    # Commit rows to Google Sheet in a single batch
    if sheet_rows:
        append_to_sheets(sheets_service, sheet_rows)

    # Cache all observed jobs
    for job in new_jobs:
        job_id = str(job.get("id") or job.get("url"))
        seen_cache.add(job_id)
    save_seen_cache(seen_cache)

    # Send status digest to Discord
    summary_text = "\n".join(job_summary_lines)
    send_discord_summary(len(new_jobs[:10]), total_people_added, summary_text)
    print("Pipeline run successfully completed.")

if __name__ == "__main__":
    main()
