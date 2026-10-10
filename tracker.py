import os
import re
import json
import time
import base64
import requests
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON_RAW = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# Primary & Secondary Data Sources for Summer 2027
SOURCES = [
    {
        "url": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "type": "simplify"
    }
]

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

# Common Indian surname indicators for balanced 50/50 quota
INDIAN_NAME_MARKERS = {
    "patel", "sharma", "gohil", "shah", "singh", "gupta", "kumar", "verma",
    "mehta", "desai", "joshi", "iyer", "nair", "reddy", "rao", "agarwal",
    "kulkarni", "chatterjee", "banerjee", "bose", "sen", "das", "mishra",
    "pandey", "tiwari", "shukla", "bhat", "pai", "hegde", "kamath", "prabhu",
    "menon", "pillai", "nambiar", "shetty", "poojary", "naik", "sawant",
    "jadhav", "kadam", "shinde", "pawar", "more", "chavan", "patil", "deshmukh",
    "joshi", "ghosh", "mukherjee", "chakraborty", "dutta", "choudhury", "roy",
    "sinha", "srivastava", "saxena", "bhatnagar", "mathur", "kaur", "bajaj",
    "malhotra", "kapoor", "khanna", "arora", "sethi", "chopra", "chawla",
    "narang", "grover", "chadha", "ahuja", "sehgal", "trehan", "sur", "oberoi",
    "jain", "bhandari", "kothari", "lodha", "ostwal", "porwal", "sachdev"
}

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
        print(f"Failed to authenticate with Google Sheets: {e}")
        return None

def fetch_existing_sheet_data(service):
    """
    Reads existing LinkedIn URLs (Col D) and Application Links (Col F)
    to prevent re-adding duplicate people or jobs.
    """
    existing_alumni_urls = set()
    existing_job_urls = set()

    if not service or not SPREADSHEET_ID:
        return existing_alumni_urls, existing_job_urls

    try:
        res = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2:F"
        ).execute()
        rows = res.get("values", [])
        for row in rows:
            if len(row) >= 4 and row[3]:
                existing_alumni_urls.add(row[3].strip().lower())
            if len(row) >= 6 and row[5]:
                existing_job_urls.add(row[5].strip().lower())
    except Exception as e:
        print(f"Note: Could not fetch existing sheet rows: {e}")

    return existing_alumni_urls, existing_job_urls

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
    terms = [str(t).lower().strip() for t in item.get("terms", [])]
    title = item.get("title", "").lower()

    # Reject off-season / prior years
    for bad in ["spring", "fall", "winter", "co-op", "coop", "2025", "2026"]:
        if any(bad in t for t in terms) and not any("summer" in t and "2027" in t for t in terms):
            return False
        if bad in title and "summer 2027" not in title:
            return False

    return any("summer" in t for t in terms) or "summer" in title or "2027" in title

def fetch_internships(seen_set, existing_job_urls):
    eligible = []
    for src in SOURCES:
        try:
            res = requests.get(src["url"], timeout=20)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print(f"Error reading source {src['url']}: {e}")
            continue

        for item in data:
            url = str(item.get("url") or "").strip()
            job_id = str(item.get("id") or url)

            # Deduplication Check 1: In local cache or already in Sheet
            if not job_id or job_id in seen_set or url.lower() in existing_job_urls:
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

            locations = " ".join(item.get("locations", [])).lower()
            is_us = any(x in locations for x in [
                "usa", "united states", "remote", ", ca", ", ny", ", ma", 
                ", wa", ", tx", ", il", ", va", ", nj", ", co", ", nc"
            ])
            if not is_us and locations:
                continue

            eligible.append(item)
    return eligible

def is_indian_origin(name, snippet):
    tokens = [t.lower() for t in re.findall(r'\b[a-zA-Z]+\b', name)]
    if any(t in INDIAN_NAME_MARKERS for t in tokens):
        return True
    
    # Check Indian education / background references in profile snippet
    indian_cues = ["india", "mumbai", "pune", "delhi", "bangalore", "anna university", "vtu", "bits pilani", "iit", "nit"]
    snippet_lower = snippet.lower()
    return any(cue in snippet_lower for cue in indian_cues)

def parse_profile(item):
    raw_title = item.get("title", "").replace(" - LinkedIn", "").strip()
    snippet = item.get("snippet", "").replace("\n", " ").strip()
    link = item.get("link", "").strip()

    parts = [p.strip() for p in raw_title.split(" - ") if p.strip()]
    full_name = parts[0] if parts else "Alum"

    name_tokens = full_name.split()
    first_name = name_tokens[0] if name_tokens else "Hi"
    if first_name.lower() in ["dr.", "mr.", "ms.", "mrs."] and len(name_tokens) > 1:
        first_name = name_tokens[1]

    return {
        "first_name": first_name,
        "full_name": full_name,
        "url": link,
        "snippet": snippet,
        "is_indian": is_indian_origin(full_name, snippet)
    }

def find_balanced_neu_alumni(company_name, existing_alumni_urls, target_per_group=3):
    """
    Searches and selects a 50/50 distribution of Indian and Non-Indian alumni.
    """
    clean_company = re.sub(r'[^a-zA-Z0-9\s]', '', company_name).strip()
    if not clean_company:
        return []

    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    query = f'site:linkedin.com/in "Northeastern University" "{clean_company}" (engineer OR developer OR data OR scientist)'
    
    candidates = []
    try:
        res = requests.post("https://google.serper.dev/search", headers=headers, json={"q": query, "num": 10}, timeout=12)
        if res.status_code == 200:
            candidates.extend(res.json().get("organic", []))
    except Exception as e:
        print(f"Alumni search error: {e}")

    # Fallback to broader company query if pool is thin
    if len(candidates) < 6:
        try:
            fallback = f'site:linkedin.com/in "Northeastern University" "{clean_company}"'
            res = requests.post("https://google.serper.dev/search", headers=headers, json={"q": fallback, "num": 10}, timeout=12)
            if res.status_code == 200:
                candidates.extend(res.json().get("organic", []))
        except Exception:
            pass

    indian_pool = []
    non_indian_pool = []
    seen = set()

    for it in candidates:
        link = it.get("link", "").strip()
        link_clean = link.lower().split("?")[0]

        # Deduplication Check 2: Skip if already in Sheet or already seen in this run
        if not link or "linkedin.com/in/" not in link or link_clean in seen or link_clean in existing_alumni_urls:
            continue
        seen.add(link_clean)

        parsed = parse_profile(it)
        if parsed["is_indian"]:
            indian_pool.append(parsed)
        else:
            non_indian_pool.append(parsed)

    # Balance 50% Indian and 50% Non-Indian
    selected = []
    selected.extend(indian_pool[:target_per_group])
    selected.extend(non_indian_pool[:target_per_group])

    # If one pool is under-represented, backfill from the other
    if len(selected) < (target_per_group * 2):
        shortfall = (target_per_group * 2) - len(selected)
        remaining = [c for c in (indian_pool + non_indian_pool) if c not in selected]
        selected.extend(remaining[:shortfall])

    return selected

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
        f"• **Northeastern Alumni Added:** {people_count} (Balanced 50/50 Split)\n\n"
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
    sheets_service = get_sheets_service()

    # Synchronize state directly from live Google Sheet
    existing_alumni_urls, existing_job_urls = fetch_existing_sheet_data(sheets_service)
    new_jobs = fetch_internships(seen_cache, existing_job_urls)
    print(f"Identified {len(new_jobs)} net-new Summer 2027 roles.")

    sheet_rows = []
    job_summary_lines = []
    total_people_added = 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    for job in new_jobs[:10]:
        company = job.get("company_name", "Company")
        role = job.get("title", "Summer 2027 Intern")
        url = job.get("url", "")
        job_id = str(job.get("id") or url)

        # Balanced 50% Indian and 50% Non-Indian selection
        alumni = find_balanced_neu_alumni(company, existing_alumni_urls, target_per_group=3)
        job_summary_lines.append(f"• **{company}** — [{role}]({url}) ({len(alumni)} alumni)")

        for alum in alumni:
            sheet_rows.append([
                alum["first_name"],
                company,
                "",  # Left empty for you to paste work email
                alum["url"],
                role,
                url,
                "PENDING",
                today_str
            ])
            total_people_added += 1
            existing_alumni_urls.add(alum["url"].lower().split("?")[0])

        seen_cache.add(job_id)
        time.sleep(1)

    if sheet_rows:
        append_to_sheets(sheets_service, sheet_rows)

    for job in new_jobs:
        job_id = str(job.get("id") or job.get("url"))
        seen_cache.add(job_id)
    save_seen_cache(seen_cache)

    summary_text = "\n".join(job_summary_lines)
    send_discord_summary(len(new_jobs[:10]), total_people_added, summary_text)
    print("Execution complete.")

if __name__ == "__main__":
    main()
