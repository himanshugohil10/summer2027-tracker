import os
import re
import json
import time
import requests
from datetime import datetime

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

UPSTREAM_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
CACHE_FILE = "seen_jobs.json"

TARGET_KEYWORDS = [
    "data science", "data scientist", "machine learning", "ml ", "ml/ai", "ai ",
    "artificial intelligence", "applied scientist", "research scientist",
    "deep learning", "nlp", "computer vision", "generative ai", "llm"
]

EXCLUDE_TITLES = [
    "phd intern", "ph.d. intern", "doctoral intern", "undergraduate only",
    "high school", "sales intern", "marketing intern", "hr intern"
]

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

def fetch_internships(seen_set):
    try:
        res = requests.get(UPSTREAM_URL, timeout=20)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        print(f"Error pulling listings: {e}")
        return []

    eligible_openings = []

    for item in data:
        # Fallback to application URL if ID is not explicitly set
        job_id = str(item.get("id") or item.get("url") or "")
        if not job_id or job_id in seen_set:
            continue

        if not item.get("active", True) or not item.get("is_visible", True):
            continue

        title = item.get("title", "").lower()

        # Check for title exclusion (e.g. PhD-only)
        if any(exc in title for exc in EXCLUDE_TITLES):
            continue

        # Check for target technical track
        if not any(kw in title for kw in TARGET_KEYWORDS):
            continue

        # Location verification for US roles
        locations = " ".join(item.get("locations", [])).lower()
        is_us = any(x in locations for x in [
            "usa", "united states", "remote", ", ca", ", ny", ", ma", 
            ", wa", ", tx", ", il", ", va", ", nj", ", co", ", nc"
        ])
        if not is_us and locations:
            continue

        eligible_openings.append(item)

    return eligible_openings

def find_northeastern_alumni(company_name, max_results=6):
    """
    Finds Northeastern alumni across the tech org umbrella (AI, Data, and SWE).
    """
    clean_company = re.sub(r'[^a-zA-Z0-9\s]', '', company_name).strip()
    if not clean_company:
        return []

    query = (
        f'site:linkedin.com/in "Northeastern University" "{clean_company}" '
        f'("Software Engineer" OR "SWE" OR "Data" OR "Machine Learning" OR "AI" OR "Engineering")'
    )
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": max_results
    }

    try:
        response = requests.get(endpoint, params=params, timeout=15)
        if response.status_code != 200:
            print(f"CSE API responded with status {response.status_code}")
            return []
            
        items = response.json().get("items", [])
        alumni_list = []

        for item in items:
            raw_title = item.get("title", "").replace(" - LinkedIn", "").strip()
            link = item.get("link", "")
            snippet = item.get("snippet", "").replace("\n", " ").strip()

            parts = [p.strip() for p in raw_title.split(" - ") if p.strip()]
            name = parts[0] if parts else "Northeastern Alum"
            headline = parts[1] if len(parts) > 1 else (snippet[:70] + "...")

            alumni_list.append({
                "name": name,
                "headline": headline,
                "url": link
            })

        return alumni_list
    except Exception as err:
        print(f"Failed alumni lookup for {clean_company}: {err}")
        return []

def send_alert(job, alumni):
    company = job.get("company_name", "Unknown Company")
    role = job.get("title", "Internship Role")
    url = job.get("url", "")
    locs = ", ".join(job.get("locations", [])) or "USA / Remote"
    
    # Check degree preference flag from upstream schema
    degree_note = "Master's / Graduate Eligible"
    if job.get("degrees") and "Bachelors" in job.get("degrees") and len(job.get("degrees")) == 1:
        degree_note = "Tagged Undergrad-leaning (Check JD)"

    alumni_section = []
    if alumni:
        for idx, a in enumerate(alumni, 1):
            alumni_section.append(f"**{idx}. [{a['name']}]({a['url']})**\n_{a['headline']}_")
    else:
        # Fallback dynamic search link if no direct indexed profiles were hit
        clean_company = re.sub(r'[^a-zA-Z0-9\s]', '', company).strip()
        search_link = f"https://www.google.com/search?q=site:linkedin.com/in+%22Northeastern+University%22+%22{clean_company.replace(' ', '+')}%22"
        alumni_section.append(f"_No profiles returned automatically._ [Run Manual Search]({search_link})")

    embed_payload = {
        "title": f"🚀 {role} — {company}",
        "url": url,
        "color": 13369344,  # Northeastern Red
        "fields": [
            {"name": "📍 Location", "value": locs, "inline": True},
            {"name": "🎓 Eligibility", "value": degree_note, "inline": True},
            {"name": "🔗 Application", "value": f"[Apply Directly Here]({url})", "inline": False},
            {"name": "🤝 Northeastern Alumni Contacts (Referral Targets)", "value": "\n\n".join(alumni_section)[:1000], "inline": False}
        ],
        "footer": {
            "text": f"Scraped on {datetime.now().strftime('%b %d, %Y')} | Daily 2027 Pipeline"
        }
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed_payload]}, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"Failed to deliver Discord notification: {e}")

def main():
    if not all([GOOGLE_API_KEY, GOOGLE_CSE_ID, DISCORD_WEBHOOK_URL]):
        print("Missing required environment variables. Exiting.")
        return

    seen_cache = load_seen_cache()
    new_jobs = fetch_internships(seen_cache)

    print(f"Detected {len(new_jobs)} new qualifying postings.")

    # Process max 10 jobs per day to avoid hitting Google's 100 free query/day cap
    for job in new_jobs[:10]:
        company = job.get("company_name", "")
        job_id = str(job.get("id") or job.get("url"))

        alumni = find_northeastern_alumni(company, max_results=6)
        send_alert(job, alumni)
        seen_cache.add(job_id)
        time.sleep(1)  # Polite pacing

    # Record all newly processed IDs so they don't re-trigger
    for job in new_jobs:
        job_id = str(job.get("id") or job.get("url"))
        seen_cache.add(job_id)

    save_seen_cache(seen_cache)
    print("Run completed successfully.")

if __name__ == "__main__":
    main()