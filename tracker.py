import os
import re
import json
import time
import requests
from datetime import datetime

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

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
    """Rejects 2025/2026 roles and confirms Summer 2027 eligibility."""
    terms = [str(t).lower() for t in item.get("terms", [])]
    title = item.get("title", "").lower()
    
    # Filter out historical terms
    if any("2025" in t or "2026" in t for t in terms) and not any("2027" in t for t in terms):
        return False
    if ("summer 2026" in title or "fall 2026" in title or "winter 2026" in title) and "2027" not in title:
        return False

    has_2027_term = any("2027" in t for t in terms)
    has_2027_title = "2027" in title
    
    return has_2027_term or has_2027_title

def fetch_internships(seen_set):
    try:
        res = requests.get(UPSTREAM_URL, timeout=20)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        print(f"Error fetching listings: {e}")
        return []

    eligible_openings = []

    for item in data:
        job_id = str(item.get("id") or item.get("url") or "")
        if not job_id or job_id in seen_set:
            continue

        if not item.get("active", True) or not item.get("is_visible", True):
            continue

        # Strict Summer 2027 check
        if not is_strictly_summer_2027(item):
            continue

        title = item.get("title", "").lower()

        # Reject excluded titles (PhD only, non-technical)
        if any(exc in title for exc in EXCLUDE_TITLES):
            continue

        # Technical track check
        if not any(kw in title for kw in TARGET_KEYWORDS):
            continue

        # Location verification for USA / Remote US
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
    Finds Northeastern alumni working in SWE, Data, AI, or Engineering at target company
    using real-time Google search via Serper.
    """
    clean_company = re.sub(r'[^a-zA-Z0-9\s]', '', company_name).strip()
    if not clean_company:
        return []

    query = f'site:linkedin.com/in "Northeastern University" "{clean_company}" (engineer OR developer OR data OR scientist OR manager)'
    
    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "q": query,
        "num": max_results
    }

    try:
        res = requests.post("https://google.serper.dev/search", headers=headers, json=payload, timeout=12)
        if res.status_code != 200:
            print(f"Serper API error {res.status_code}: {res.text}")
            return []
        
        data = res.json()
        organic = data.get("organic", [])
        
        # Fallback query if specific role search yields nothing
        if not organic:
            fallback_query = f'site:linkedin.com/in "Northeastern University" "{clean_company}"'
            payload["q"] = fallback_query
            res = requests.post("https://google.serper.dev/search", headers=headers, json=payload, timeout=12)
            organic = res.json().get("organic", [])

        alumni_list = []
        seen_links = set()

        for item in organic:
            link = item.get("link", "")
            if "linkedin.com/in/" not in link or link in seen_links:
                continue
            seen_links.add(link)

            raw_title = item.get("title", "").replace(" - LinkedIn", "").strip()
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
    except Exception as e:
        print(f"Error querying alumni for {clean_company}: {e}")
        return []

def send_alert(job, alumni):
    company = job.get("company_name", "Unknown Company")
    role = job.get("title", "Summer 2027 Internship")
    url = job.get("url", "")
    locs = ", ".join(job.get("locations", [])) or "USA / Remote"
    terms = ", ".join(job.get("terms", ["Summer 2027"]))

    alumni_section = []
    clean_company = re.sub(r'[^a-zA-Z0-9\s]', '', company).strip()
    direct_search_url = f"https://www.google.com/search?q=site:linkedin.com/in+%22Northeastern+University%22+%22{clean_company.replace(' ', '+')}%22"

    if alumni:
        for idx, a in enumerate(alumni, 1):
            alumni_section.append(f"**{idx}. [{a['name']}]({a['url']})**\n_{a['headline']}_")
        alumni_section.append(f"\n🔍 [View All NEU Alumni at {company}]({direct_search_url})")
    else:
        alumni_section.append(f"_No profiles auto-parsed._ [Click to Search NEU Alumni on LinkedIn]({direct_search_url})")

    embed_payload = {
        "title": f"🚀 Summer 2027: {role} — {company}",
        "url": url,
        "color": 13369344,  # Northeastern Red
        "fields": [
            {"name": "📍 Location", "value": locs, "inline": True},
            {"name": "🗓️ Term", "value": terms, "inline": True},
            {"name": "🔗 Application", "value": f"[Apply Directly Here]({url})", "inline": False},
            {"name": "🤝 Potential Referrers (Northeastern Tech Alumni)", "value": "\n\n".join(alumni_section)[:1024], "inline": False}
        ],
        "footer": {
            "text": f"Scraped on {datetime.now().strftime('%b %d, %Y')} | Summer 2027 Pipeline"
        }
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed_payload]}, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"Failed to post Discord alert: {e}")

def main():
    if not all([SERPER_API_KEY, DISCORD_WEBHOOK_URL]):
        print("Missing SERPER_API_KEY or DISCORD_WEBHOOK_URL. Exiting.")
        return

    seen_cache = load_seen_cache()
    new_jobs = fetch_internships(seen_cache)

    print(f"Found {len(new_jobs)} valid Summer 2027 matching internships.")

    # Process up to 10 openings per batch
    for job in new_jobs[:10]:
        company = job.get("company_name", "")
        job_id = str(job.get("id") or job.get("url"))

        alumni = find_northeastern_alumni(company, max_results=6)
        send_alert(job, alumni)
        seen_cache.add(job_id)
        time.sleep(1)

    for job in new_jobs:
        job_id = str(job.get("id") or job.get("url"))
        seen_cache.add(job_id)

    save_seen_cache(seen_cache)
    print("Execution complete.")

if __name__ == "__main__":
    main()
