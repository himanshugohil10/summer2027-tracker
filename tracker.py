import os
import re
import json
import time
import base64
import hashlib
import requests
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON_RAW = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

CACHE_FILE = "seen_jobs.json"

# 1. STRICT INTERNSHIP ROLE KEYWORDS (NO SWE/Software Engineering Internships)
TARGET_ROLE_KEYWORDS = [
    "data science", "data scientist", "machine learning", "ml ", "ml/ai", "ai ",
    "artificial intelligence", "applied scientist", "research scientist",
    "deep learning", "nlp", "natural language", "generative ai", "genai", "llm",
    "data analyst", "data engineer", "analytics intern", "ai engineer"
]

# 2. STRICT EXCLUSIONS (No SWE, No CV, No Hardware, No PhD-only, No Undergrad-only)
EXCLUDE_ROLE_KEYWORDS = [
    "software engineer", "software developer", "swe ", "full stack", "fullstack",
    "frontend", "front-end", "backend", "back-end", "ios ", "android",
    "computer vision", "vision", "opencv", "image processing",
    "hardware", "firmware", "embedded", "fpga", "asic", "electrical",
    "pcb", "silicon", "semiconductor",
    "phd", "ph.d", "doctoral", "postdoc",
    "undergrad", "undergraduate", "bachelor only", "bs only", "freshman", "sophomore",
    "high school", "sales", "marketing", "hr intern", "recruiting"
]

# Direct Public ATS Boards
GREENHOUSE_BOARDS = [
    ("OpenAI", "openai"),
    ("Anthropic", "anthropic"),
    ("Scale AI", "scaleai"),
    ("Databricks", "databricks"),
    ("Stripe", "stripe"),
    ("Pinterest", "pinterest"),
    ("Instacart", "instacart"),
    ("Reddit", "reddit"),
    ("Lyft", "lyft"),
    ("DoorDash", "doordash"),
    ("Roblox", "roblox"),
    ("Airbnb", "airbnb"),
    ("Cloudflare", "cloudflare")
]

LEVER_BOARDS = [
    ("Palantir", "palantir"),
    ("Spotify", "spotify"),
    ("Cohere", "cohere")
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
    "jain", "bhandari", "kothari", "lodha", "parekh", "trivedi", "dave", "vyas"
}

def clean_url(url: str) -> str:
    if not url:
        return ""
    return url.strip().lower().split("?")[0].split("#")[0].rstrip("/")

def make_job_signature(company: str, title: str) -> str:
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
        for r in rows:
            company = r[1] if len(r) > 1 else ""
            alumni_link = r[3] if len(r) > 3 else ""
            role = r[4] if len(r) > 4 else ""
            job_link = r[5] if len(r) > 5 else ""

            if alumni_link:
                existing_alumni_urls.add(clean_url(alumni_link))
            if job_link:
                existing_job_urls.add(clean_url(job_link))
            if company and role:
                existing_signatures.add(make_job_signature(company, role))
    except Exception as e:
        print(f"Note on reading sheet: {e}")

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

def is_strictly_summer_2027(title: str, terms_list: list) -> bool:
    title_lower = title.lower()
    terms = [str(t).lower().strip() for t in terms_list]

    for bad in ["spring", "fall", "winter", "co-op", "coop", "2025", "2026"]:
        if any(bad in t for t in terms) and not any("summer" in t and "2027" in t for t in terms):
            return False
        if bad in title_lower and "summer 2027" not in title_lower:
            return False

    return any("summer" in t for t in terms) or "summer" in title_lower or "2027" in title_lower

def is_graduate_eligible(item: dict, title: str) -> bool:
    """
    Ensures the internship is eligible for MS / Graduate students.
    Rejects roles restricted solely to Bachelors/Undergrad or solely to PhD.
    """
    title_lower = title.lower()
    if any(k in title_lower for k in ["phd", "ph.d", "doctoral", "undergrad", "bachelor"]):
        if "master" not in title_lower and "ms " not in title_lower and "grad" not in title_lower:
            return False

    degrees = [str(d).lower() for d in item.get("degrees", [])]
    if degrees:
        # If degrees are specified, Masters/Graduate must be included
        has_masters = any("master" in d or "ms" in d or "grad" in d for d in degrees)
        if not has_masters:
            return False

    return True

def matches_ai_ds_ml_role(title: str) -> bool:
    """Strictly AI, ML, Data Science roles; blocks SWE, CV, Hardware, PhD, Undergrad."""
    title_lower = f" {title.lower()} "
    if any(exc in title_lower for exc in EXCLUDE_ROLE_KEYWORDS):
        return False
    return any(kw in title_lower for kw in TARGET_ROLE_KEYWORDS)

def fetch_all_internships(seen_set, existing_job_urls, existing_signatures):
    openings = []
    seen_in_run = set()

    # 1. Community Trackers (Simplify + PittCSC)
    tracker_urls = [
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/pittcsc/Summer2027-Internships/dev/.github/scripts/listings.json"
    ]
    for url in tracker_urls:
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
            sig = make_job_signature(comp, role)

            if not norm_url or norm_url in existing_job_urls or sig in existing_signatures:
                continue
            if norm_url in seen_set or sig in seen_set or sig in seen_in_run:
                continue
            if not item.get("active", True) or not item.get("is_visible", True):
                continue
            if not is_strictly_summer_2027(role, item.get("terms", [])):
                continue
            if not is_graduate_eligible(item, role):
                continue
            if not matches_ai_ds_ml_role(role):
                continue

            locations = " ".join(item.get("locations", [])).lower()
            is_us = any(x in locations for x in [
                "usa", "united states", "remote", ", ca", ", ny", ", ma",
                ", wa", ", tx", ", il", ", va", ", nj", ", co", ", nc"
            ])
            if not is_us and locations:
                continue

            seen_in_run.add(sig)
            seen_in_run.add(norm_url)
            openings.append({"company": comp, "role": role, "url": raw_url, "sig": sig})

    # 2. Direct Greenhouse ATS Boards
    for comp_name, token in GREENHOUSE_BOARDS:
        try:
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", timeout=10)
            if r.status_code != 200:
                continue
            for j in r.json().get("jobs", []):
                role = j.get("title", "").strip()
                raw_url = j.get("absolute_url", "").strip()
                norm_url = clean_url(raw_url)
                sig = make_job_signature(comp_name, role)

                if "intern" not in role.lower() or "2027" not in role.lower():
                    continue
                if norm_url in existing_job_urls or sig in existing_signatures or sig in seen_set or sig in seen_in_run:
                    continue
                if not is_strictly_summer_2027(role, ["summer 2027"]):
                    continue
                if not is_graduate_eligible({}, role):
                    continue
                if not matches_ai_ds_ml_role(role):
                    continue

                seen_in_run.add(sig)
                seen_in_run.add(norm_url)
                openings.append({"company": comp_name, "role": role, "url": raw_url, "sig": sig})
        except Exception:
            continue

    return openings

def is_indian_origin(name: str, snippet: str) -> bool:
    tokens = [t.lower() for t in re.findall(r'\b[a-zA-Z]+\b', name)]
    if any(t in INDIAN_NAME_MARKERS for t in tokens):
        return True
    indian_cues = ["india", "mumbai", "pune", "delhi", "bangalore", "hyderabad", "chennai", "anna university", "vtu", "bits pilani", "iit", "nit", "srm", "vit"]
    return any(cue in snippet.lower() for cue in indian_cues)

def is_verified_current_neu_alum(raw_title: str, snippet: str, company_name: str) -> bool:
    """
    STRICT VERIFICATION:
    1. Headline in `raw_title` must contain the target company as their current employer.
    2. Profile title or snippet must explicitly contain 'Northeastern'.
    3. Rejects snippets where the company is only listed under 'Past:' or 'Formerly'.
    """
    clean_comp = re.sub(r'[^a-zA-Z0-9\s]', '', company_name).strip().lower()
    title_lower = raw_title.lower()
    snippet_lower = snippet.lower()
    combined = f"{title_lower} {snippet_lower}"

    # Must explicitly mention Northeastern
    if "northeastern" not in combined:
        return False

    # LinkedIn titles follow: "Name - Role at Company | LinkedIn" or "Name - Role - Company | LinkedIn"
    parts = [p.strip().lower() for p in raw_title.replace("| LinkedIn", "").replace("- LinkedIn", "").split(" - ") if p.strip()]
    if len(parts) < 2:
        return False

    # Check that company name appears in the headline (parts[1] or parts[2]), proving current employment
    headline_part = " - ".join(parts[1:])
    comp_tokens = [t for t in clean_comp.split() if len(t) > 2]
    primary_comp_token = comp_tokens[0] if comp_tokens else clean_comp

    if clean_comp not in headline_part and primary_comp_token not in headline_part:
        return False

    # Guard against "Former ... at Company" in the headline
    if f"former" in headline_part or f"ex-{primary_comp_token}" in headline_part or f"past:" in headline_part:
        return False

    return True

def find_verified_balanced_alumni(company_name: str, existing_alumni_urls: set, target_per_group: int = 3):
    """
    Finds 5-6 verified current employees at `company_name` who attended Northeastern University.
     Alumni can be SWE, Data Scientist, Data Engineer, ML Engineer, etc.
    Enforces a 50% Indian / 50% Non-Indian split.
    """
    if not SERPER_API_KEY:
        print("Error: SERPER_API_KEY is missing!")
        return []

    clean_company = re.sub(r'[^a-zA-Z0-9\s]', '', company_name).strip()
    if not clean_company:
        return []

    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    # Query 1: Broad Engineering & Data roles with company in LinkedIn title
    q1 = f'site:linkedin.com/in/ intitle:"{clean_company}" "Northeastern University" (Engineer OR Scientist OR Data OR Developer OR AI)'
    # Query 2: Targeted Indian background + Northeastern + Company in title
    q2 = f'site:linkedin.com/in/ intitle:"{clean_company}" "Northeastern University" (India OR Mumbai OR Pune OR Bangalore OR Delhi OR Patel OR Sharma OR Shah)'

    candidates = []
    for q in [q1, q2]:
        try:
            res = requests.post("https://google.serper.dev/search", headers=headers, json={"q": q, "num": 10}, timeout=12)
            if res.status_code == 200:
                candidates.extend(res.json().get("organic", []))
        except Exception as e:
            print(f"Serper search error for {clean_company}: {e}")

    indian_pool = []
    non_indian_pool = []
    seen_links = set()

    for it in candidates:
        link = it.get("link", "").strip()
        link_clean = clean_url(link)

        # Must be a personal profile URL (/in/) and not already in Sheet
        if not link or "linkedin.com/in/" not in link_clean or link_clean in seen_links or link_clean in existing_alumni_urls:
            continue

        raw_title = it.get("title", "").strip()
        snippet = it.get("snippet", "").replace("\n", " ").strip()

        # Run strict verification: Current Company in Headline + Northeastern in profile
        if not is_verified_current_neu_alum(raw_title, snippet, clean_company):
            continue

        seen_links.add(link_clean)

        clean_title = raw_title.replace(" | LinkedIn", "").replace(" - LinkedIn", "").strip()
        parts = [p.strip() for p in clean_title.split(" - ") if p.strip()]
        full_name = parts[0] if parts else ""

        # Extract clean First Name
        name_tokens = [t for t in re.findall(r'\b[a-zA-Z]+\b', full_name) if t.lower() not in ["dr", "mr", "ms", "mrs"]]
        if not name_tokens:
            continue
        first_name = name_tokens[0].capitalize()

        profile_obj = {
            "first_name": first_name,
            "full_name": full_name,
            "url": link.split("?")[0]
        }

        if is_indian_origin(full_name, snippet):
            indian_pool.append(profile_obj)
        else:
            non_indian_pool.append(profile_obj)

    # Select 50% Indian (up to 3) and 50% Non-Indian (up to 3)
    selected = []
    selected.extend(indian_pool[:target_per_group])
    selected.extend(non_indian_pool[:target_per_group])

    # Backfill if one side has fewer than 3 verified profiles
    total_target = target_per_group * 2
    if len(selected) < total_target:
        remaining = [p for p in (indian_pool + non_indian_pool) if p not in selected]
        selected.extend(remaining[:(total_target - len(selected))])

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

def send_discord_summary(job_count, people_count, lines_summary):
    if not DISCORD_WEBHOOK_URL:
        return
    sheet_link = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
    description = (
        f"☀️ **Good Morning! Here is your 8:00 AM Referral Pipeline Digest:**\n\n"
        f"• **New Graduate AI/DS/ML Roles Found:** {job_count}\n"
        f"• **Verified Northeastern Alumni Added:** {people_count} (50% Indian / 50% Non-Indian)\n\n"
        f"📊 **[Open Google Sheets Tracker]({sheet_link})**\n\n"
        f"**Recently Added Openings:**\n" + (lines_summary or "_No new openings this morning._")
    )
    embed = {
        "title": "⚡ Daily Summer 2027 Referral Pipeline Update",
        "description": description[:2000],
        "color": 13369344,
        "footer": {"text": f"{datetime.now().strftime('%b %d, %Y')} | Verified Alumni & Grad AI/DS/ML Filter"}
    }
    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)

def main():
    seen_cache = load_seen_cache()
    sheets_service = get_sheets_service()

    existing_alumni_urls, existing_job_urls, existing_signatures = fetch_existing_sheet_state(sheets_service)
    new_jobs = fetch_all_internships(seen_cache, existing_job_urls, existing_signatures)

    print(f"Discovered {len(new_jobs)} net-new Graduate Summer 2027 AI/DS/ML roles.")

    sheet_rows = []
    summary_lines = []
    total_people_added = 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Process up to 8 new jobs per run to keep Serper usage lean
    for job in new_jobs[:8]:
        company = job["company"]
        role = job["role"]
        job_url = job["url"]
        sig = job["sig"]

        alumni_list = find_verified_balanced_alumni(company, existing_alumni_urls, target_per_group=3)
        summary_lines.append(f"• **{company}** — [{role}]({job_url}) ({len(alumni_list)} verified alumni)")

        for alum in alumni_list:
            sheet_rows.append([
                alum["first_name"],  # Col A: Extracted First Name
                company,             # Col B: Company Name
                "",                  # Col C: Blank Email ID for you to fill
                alum["url"],         # Col D: Verified Personal LinkedIn Profile URL
                role,                # Col E: Graduate AI/DS/ML Role Title
                job_url,             # Col F: Application Link
                "PENDING",           # Col G: Status
                today_str            # Col H: Date Added
            ])
            total_people_added += 1
            existing_alumni_urls.add(clean_url(alum["url"]))

        seen_cache.add(sig)
        seen_cache.add(clean_url(job_url))
        time.sleep(1)

    if sheet_rows:
        append_to_sheets(sheets_service, sheet_rows)

    save_seen_cache(seen_cache)
    summary_text = "\n".join(summary_lines)
    send_discord_summary(len(new_jobs[:8]), total_people_added, summary_text)
    print(f"Completed: Added {total_people_added} verified alumni across {len(new_jobs[:8])} roles.")

if __name__ == "__main__":
    main()
