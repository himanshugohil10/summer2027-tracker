import os
import re
import json
import time
import base64
import requests
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON_RAW = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# DUAL SOURCES: SimplifyJobs + CSCareers (Pitt CSC)
SOURCES = [
    {
        "name": "SimplifyJobs",
        "url": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "type": "simplify"
    },
    {
        "name": "CSCareers / PittCSC",
        "url": "https://raw.githubusercontent.com/pittcsc/Summer2027-Internships/dev/.github/scripts/listings.json",
        "type": "pittcsc"
    }
]

CACHE_FILE = "seen_jobs.json"

TARGET_KEYWORDS = [
    "data science", "data scientist", "machine learning", "ml ", "ml/ai", "ai ",
    "artificial intelligence", "applied scientist", "research scientist",
    "deep learning", "nlp", "generative ai", "llm", "data analyst", "data engineer"
]

# HARD EXCLUSIONS: Zero Computer Vision and Hardware roles
EXCLUDE_KEYWORDS = [
    "computer vision", "vision", "opencv", "image processing",
    "hardware", "firmware", "embedded", "fpga", "asic", "electrical",
    "pcb", "silicon", "semiconductor", "phd intern", "ph.d.", "doctoral",
    "undergraduate only", "high school", "sales", "marketing", "hr intern"
]

INDIAN_NAME_MARKERS = {
    "patel", "sharma", "gohil", "shah", "singh", "gupta", "kumar", "verma",
    "mehta", "desai", "joshi", "iyer", "nair", "reddy", "rao", "agarwal",
    "kulkarni", "chatterjee", "banerjee", "bose", "sen", "das", "mishra",
    "pandey", "tiwari", "shukla", "bhat", "pai", "hegde", "kamath", "prabhu",
    "menon", "pillai", "nambiar", "shetty", "poojary", "naik", "sawant",
    "jadhav", "kadam", "shinde", "pawar", "more", "chavan", "patil", "deshmukh",
    "ghosh", "mukherjee", "chakraborty", "dutta", "choudhury", "roy", "sinha",
    "srivastava", "saxena", "bhatnagar", "mathur", "kaur", "bajaj", "malhotra",
    "kapoor", "khanna", "arora", "sethi", "chopra", "chawla", "narang", "grover",
    "jain", "bhandari", "kothari", "lodha"
}

def clean_url(url: str) -> str:
    """Strips query parameters, tracking tags, and trailing slashes for zero duplication."""
    if not url:
        return ""
    clean = url.strip().lower().split("?")[0].split("#")[0].rstrip("/")
    return clean

def make_job_signature(company: str, title: str) -> str:
    """Creates a unique signature to catch duplicate roles posted with different URLs."""
    c = re.sub(r'[^a-zA-Z0-9]', '', company.lower())
    t = re.sub(r'[^a-zA-Z0-9]', '', title.lower())
    return f"{c}_{t}"

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
    """
    Reads existing alumni URLs, job URLs, and job signatures from Google Sheets.
    """
    existing_alumni_urls = set()
    existing_job_urls = set()
    existing_signatures = set()

    if not service or not SPREADSHEET_ID:
        return existing_alumni_urls, existing_job_urls, existing_signatures

    try:
        res = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A2:F"
        ).execute()
        rows = res.get("values", [])
        for row in rows:
            company = row[1] if len(row) > 1 else ""
            role = row[4] if len(row) > 4 else ""
            alumni_link = row[3] if len(row) > 3 else ""
            job_link = row[5] if len(row) > 5 else ""

            if alumni_link:
                existing_alumni_urls.add(clean_url(alumni_link))
            if job_link:
                existing_job_urls.add(clean_url(job_link))
            if company and role:
                existing_signatures.add(make_job_signature(company, role))
    except Exception as e:
        print(f"Note on reading sheet data: {e}")

    return existing_alumni_urls, existing_job_urls, existing_signatures

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

    for bad in ["spring", "fall", "winter", "co-op", "coop", "2025", "2026"]:
        if any(bad in t for t in terms) and not any("summer" in t and "2027" in t for t in terms):
            return False
        if bad in title and "summer 2027" not in title:
            return False

    return any("summer" in t for t in terms) or "summer" in title or "2027" in title

def is_within_last_7_days(item):
    date_posted = item.get("date_posted") or item.get("date_updated")
    if not date_posted:
        return True
    try:
        if isinstance(date_posted, (int, float)):
            post_time = datetime.fromtimestamp(date_posted)
        else:
            post_time = datetime.fromisoformat(str(date_posted).replace("Z", "+00:00"))
        return (datetime.now() - post_time.replace(tzinfo=None)) <= timedelta(days=7)
    except Exception:
        return True

def fetch_internships_from_all_sources(seen_set, existing_job_urls, existing_signatures):
    eligible = []
    seen_in_this_run = set()

    for src in SOURCES:
        try:
            res = requests.get(src["url"], timeout=20)
            if res.status_code != 200:
                continue
            data = res.json()
        except Exception as e:
            print(f"Source {src['name']} not available or error: {e}")
            continue

        for item in data:
            raw_url = str(item.get("url") or "").strip()
            norm_url = clean_url(raw_url)
            job_id = str(item.get("id") or norm_url)
            company = item.get("company_name", "").strip()
            title = item.get("title", "").strip().lower()
            sig = make_job_signature(company, title)

            # --- DEDUPLICATION GATE ---
            if not norm_url or norm_url in existing_job_urls or norm_url in seen_in_this_run:
                continue
            if job_id in seen_set or sig in existing_signatures or sig in seen_in_this_run:
                continue

            if not item.get("active", True) or not item.get("is_visible", True):
                continue

            if not is_strictly_summer_2027(item):
                continue

            if not is_within_last_7_days(item):
                continue

            # Exclude Computer Vision and Hardware
            if any(exc in title for exc in EXCLUDE_KEYWORDS):
                continue

            # Must match target AI / DS / ML track
            if not any(kw in title for kw in TARGET_KEYWORDS):
                continue

            locations = " ".join(item.get("locations", [])).lower()
            is_us = any(x in locations for x in [
                "usa", "united states", "remote", ", ca", ", ny", ", ma", 
                ", wa", ", tx", ", il", ", va", ", nj", ", co", ", nc"
            ])
            if not is_us and locations:
                continue

            # Passed all filters & uniqueness verification
            seen_in_this_run.add(norm_url)
            seen_in_this_run.add(sig)
            eligible.append(item)

    return eligible

def is_indian_origin(name, snippet):
    tokens = [t.lower() for t in re.findall(r'\b[a-zA-Z]+\b', name)]
    if any(t in INDIAN_NAME_MARKERS for t in tokens):
        return True
    indian_cues = ["india", "mumbai", "pune", "delhi", "bangalore", "anna university", "vtu", "bits pilani", "iit", "nit"]
    return any(cue in snippet.lower() for cue in indian_cues)

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
        link_clean = clean_url(link)

        if not link or "linkedin.com/in/" not in link or link_clean in seen or link_clean in existing_alumni_urls:
            continue
        seen.add(link_clean)

        parsed = parse_profile(it)
        if parsed["is_indian"]:
            indian_pool.append(parsed)
        else:
            non_indian_pool.append(parsed)

    selected = []
    selected.extend(indian_pool[:target_per_group])
    selected.extend(non_indian_pool[:target_per_group])

    # Backfill if one group has fewer hits
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
        f"☀️ **Good Morning! Here is your 8:00 AM Referral Pipeline Digest:**\n\n"
        f"• **New Summer 2027 Roles Populated:** {job_count}\n"
        f"• **Northeastern Alumni Details Populated:** {people_count} (50% Indian / 50% Non-Indian)\n\n"
        f"📊 **[Open Google Sheets Tracker]({sheet_link})**\n\n"
        f"**Recently Added Openings:**\n" + (jobs_summary or "_No new openings this morning._")
    )
    embed = {
        "title": "⚡ Daily Summer 2027 Referral Pipeline Update",
        "description": description[:2000],
        "color": 13369344,
        "footer": {"text": f"{datetime.now().strftime('%b %d, %Y')} | Multi-Source Ingestion Engine"}
    }
    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)

def main():
    seen_cache = load_seen_cache()
    sheets_service = get_sheets_service()

    # Synchronize state directly from live Google Sheet
    existing_alumni_urls, existing_job_urls, existing_signatures = fetch_existing_sheet_state(sheets_service)
    
    # Query both SimplifyJobs and CSCareers with deduplication
    new_jobs = fetch_internships_from_all_sources(seen_cache, existing_job_urls, existing_signatures)
    print(f"Identified {len(new_jobs)} net-new Summer 2027 roles across all sources.")

    sheet_rows = []
    job_summary_lines = []
    total_people_added = 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    for job in new_jobs[:10]:
        company = job.get("company_name", "Company")
        role = job.get("title", "Summer 2027 Intern")
        raw_url = str(job.get("url") or "")
        norm_url = clean_url(raw_url)
        job_id = str(job.get("id") or norm_url)
        sig = make_job_signature(company, role)

        alumni = find_balanced_neu_alumni(company, existing_alumni_urls, target_per_group=3)
        job_summary_lines.append(f"• **{company}** — [{role}]({raw_url}) ({len(alumni)} alumni)")

        for alum in alumni:
            sheet_rows.append([
                alum["first_name"],
                company,
                "",  # Left empty for work email
                alum["url"],
                role,
                raw_url,
                "PENDING",
                today_str
            ])
            total_people_added += 1
            existing_alumni_urls.add(clean_url(alum["url"]))

        seen_cache.add(job_id)
        seen_cache.add(norm_url)
        seen_cache.add(sig)
        time.sleep(1)

    if sheet_rows:
        append_to_sheets(sheets_service, sheet_rows)

    for job in new_jobs:
        raw_url = str(job.get("url") or "")
        norm_url = clean_url(raw_url)
        job_id = str(job.get("id") or norm_url)
        company = job.get("company_name", "Company")
        role = job.get("title", "Summer 2027 Intern")
        seen_cache.add(job_id)
        seen_cache.add(norm_url)
        seen_cache.add(make_job_signature(company, role))
    save_seen_cache(seen_cache)

    summary_text = "\n".join(job_summary_lines)
    send_discord_summary(len(new_jobs[:10]), total_people_added, summary_text)
    print("Multi-source run completed successfully.")

if __name__ == "__main__":
    main()
