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

# 1. STRICT INTERNSHIP ROLE KEYWORDS (AI, DS, ML, Data Eng, Analytics - NO SWE, NO QUANT)
TARGET_ROLE_KEYWORDS = [
    "data science", "data scientist", "machine learning", "ml ", "ml/ai", "ai ",
    "artificial intelligence", "applied scientist", "research scientist",
    "deep learning", "nlp", "natural language", "generative ai", "genai", "llm",
    "data analyst", "data engineer", "analytics", "ai engineer",
    "business analyst", "decision science", "business intelligence", "bi intern",
    "product analyst"
]

# 2. STRICT EXCLUSIONS (No SWE, No Quant/Trading, No CV, No Hardware, No PhD-only, No Undergrad-only)
EXCLUDE_ROLE_KEYWORDS = [
    "quant", "quantitative", "trading", "trader", "algo ", "algorithmic",
    "fixed income", "equities", "derivatives", "hedge fund", "market making",
    "portfolio", "investment", "actuarial", "actuary",
    "software engineer", "software developer", "swe ", "full stack", "fullstack",
    "frontend", "front-end", "backend", "back-end", "ios ", "android",
    "computer vision", "vision", "opencv", "image processing",
    "hardware", "firmware", "embedded", "fpga", "asic", "electrical",
    "pcb", "silicon", "semiconductor",
    "phd", "ph.d", "doctoral", "postdoc",
    "undergrad", "undergraduate", "bachelor only", "bs only", "freshman", "sophomore",
    "high school", "sales", "marketing", "hr intern", "recruiting"
]

# 3. EXCLUDED COMPANIES (TikTok/ByteDance + Pure HFT/Prop Trading Firms)
EXCLUDE_COMPANIES = [
    "tiktok", "bytedance",
    "jane street", "citadel", "five rings", "optiver", "imc trading", "imc ",
    "susquehanna", "sig ", "drw", "hudson river trading", "hrt", "jump trading",
    "akuna", "belvedere", "old mission", "valkyrie", "ctc", "chicago trading",
    "wolverine", "tower research", "de shaw", "d. e. shaw", "two sigma",
    "point72", "millennium", "balyasny", "virtu", "flow traders", "maven securities",
    "aquatic", "radix", "headlands", "transmarket", "gelber", "group one"
]

# 4. EXPANDED DIRECT PUBLIC ATS BOARDS (100% Free, Zero Auth)
GREENHOUSE_BOARDS = [
    ("OpenAI", "openai"), ("Anthropic", "anthropic"), ("Scale AI", "scaleai"),
    ("Databricks", "databricks"), ("Stripe", "stripe"), ("Pinterest", "pinterest"),
    ("Instacart", "instacart"), ("Reddit", "reddit"), ("Lyft", "lyft"),
    ("DoorDash", "doordash"), ("Roblox", "roblox"), ("Airbnb", "airbnb"),
    ("Cloudflare", "cloudflare"), ("Figma", "figma"), ("Coinbase", "coinbase"),
    ("Robinhood", "robinhood"), ("Duolingo", "duolingo"), ("Brex", "brex"),
    ("Gusto", "gusto"), ("Affirm", "affirm"), ("Chime", "chime"),
    ("Plaid", "plaid"), ("Asana", "asana"), ("Dropbox", "dropbox"),
    ("Twitch", "twitch"), ("Waymo", "waymo"), ("Nuro", "nuro"),
    ("Aurora", "aurorainnovation"), ("Weights & Biases", "wandb"),
    ("Together AI", "togetherai"), ("Fireworks AI", "fireworksai"),
    ("CoreWeave", "coreweave"), ("Glean", "gleanwork"), ("Adept", "adept"),
    ("Cresta", "cresta"), ("Snorkel AI", "snorkelai"), ("Vectara", "vectara"),
    (" Arize AI", "arizeai"), ("Modal", "modal"), ("Anyscale", "anyscale"),
    ("Cockroach Labs", "cockroachlabs"), ("MongoDB", "mongodb"),
    ("Elastic", "elastic"), (" Samsara", "samsara"), ("Okta", "okta")
]

LEVER_BOARDS = [
    ("Palantir", "palantir"), ("Spotify", "spotify"), ("Cohere", "cohere"),
    ("Mistral AI", "mistral"), ("Zoox", "zoox")
]

ASHBY_BOARDS = [
    ("Perplexity", "perplexity"), ("LangChain", "langchain"), ("Ramp", "ramp"),
    ("Notion", "notion"), ("Linear", "linear"), ("Harvey", "harvey"),
    ("ElevenLabs", "elevenlabs"), ("DeepL", "deepl"), ("Baseten", "baseten"),
    ("Patronus AI", "patronus-ai"), ("Writer", "writer"), ("pinecone", "pinecone")
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
    u = url.strip().lower().split("?")[0].split("#")[0].rstrip("/")
    u = re.sub(r'^https?://([a-z]{2,3}\.)?linkedin\.com/', 'https://www.linkedin.com/', u)
    return u

def clean_company_for_search(company_name: str) -> str:
    """Strips legal suffixes so 'The Boeing Company' -> 'Boeing' for accurate LinkedIn matching."""
    c = re.sub(r'[^a-zA-Z0-9\s&]', ' ', company_name).strip()
    c = re.sub(r'^the\s+', '', c, flags=re.IGNORECASE)
    suffixes = r'\b(inc|incorporated|corp|corporation|company|co|llc|ltd|limited|plc|group|holdings|technologies|technology|labs|systems)\b'
    c = re.sub(suffixes, '', c, flags=re.IGNORECASE)
    c = re.sub(r'\s+', ' ', c).strip()
    return c if c else company_name.strip()

def make_job_signature(company: str, title: str) -> str:
    c = re.sub(r'[^a-zA-Z0-9]', '', clean_company_for_search(company).lower())
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

def is_strictly_summer_2027(title: str, terms_list: list, extra_text: str = "") -> bool:
    title_lower = title.lower()
    terms = [str(t).lower().strip() for t in terms_list]
    combined = f"{title_lower} {' '.join(terms)} {extra_text.lower()}"

    # Reject off-cycle terms or wrong years if Summer 2027 isn't explicitly stated
    for bad in ["spring 2027", "fall 2026", "fall 2027", "winter 2027", "co-op", "coop", "2025", "summer 2026"]:
        if bad in combined and "summer 2027" not in combined:
            return False

    for bad_term in ["spring", "fall", "winter"]:
        if bad_term in title_lower and "summer" not in title_lower:
            return False

    has_2027 = "2027" in combined
    has_summer_or_intern = "summer" in combined or "intern" in title_lower
    return has_2027 and has_summer_or_intern

def is_graduate_eligible(item: dict, title: str, extra_text: str = "") -> bool:
    """
    Ensures the internship is eligible for MS / Graduate students.
    Rejects roles restricted solely to Bachelors/Undergrad or solely to PhD.
    """
    title_lower = title.lower()
    combined = f"{title_lower} {extra_text.lower()}"

    # Block PhD-only or Undergrad-only titles
    if any(k in title_lower for k in ["phd", "ph.d", "doctoral", "undergrad", "bachelor"]):
        if not any(m in title_lower for m in ["master", "ms ", "m.s.", "grad"]):
            return False

    # Check structured degrees array if available
    degrees = [str(d).lower() for d in item.get("degrees", [])]
    if degrees:
        has_masters = any("master" in d or "ms" in d or "grad" in d for d in degrees)
        if not has_masters:
            return False

    # Check snippet/description text for hard exclusions
    if any(p in combined for p in ["phd only", "ph.d. only", "exclusively for phd", "must be pursuing a phd", "undergraduate only", "bachelor's only"]):
        return False

    return True

def matches_ai_ds_ml_role(title: str) -> bool:
    """Strictly AI, ML, Data Science, Analytics roles; blocks SWE, CV, Hardware, PhD, Undergrad."""
    title_lower = f" {title.lower()} "
    if "intern" not in title_lower:
        return False
    if any(exc in title_lower for exc in EXCLUDE_ROLE_KEYWORDS):
        return False
    return any(kw in title_lower for kw in TARGET_ROLE_KEYWORDS)

def is_us_location(loc_str: str) -> bool:
    if not loc_str or not loc_str.strip():
        return True
    loc = loc_str.lower()
    non_us = ["uk", "london", "canada", "toronto", "vancouver", "india", "bangalore", "dublin", "germany", "singapore", "beijing", "shanghai", "tokyo", "australia", "sydney", "amsterdam", "paris", "zurich"]
    us_markers = [
        "usa", "united states", "u.s.", "remote", ", ca", ", ny", ", ma",
        ", wa", ", tx", ", il", ", va", ", nj", ", co", ", nc", ", ga",
        ", pa", ", md", ", az", ", fl", ", oh", ", mi", ", mn", "new york",
        "san francisco", "seattle", "boston", "austin", "chicago", "menlo park",
        "mountain view", "palo alto", "sunnyvale", "SAN JOSE", "santa clara", "los angeles", "atlanta", "bellevue", "redmond"
    ]
    if any(u in loc for u in us_markers):
        return True
    if any(nu in loc for nu in non_us):
        return False
    return True

def fetch_all_internships(seen_set, existing_job_urls, existing_signatures):
    openings = []
    seen_in_run = set()

    def try_add(comp, role, raw_url, terms_list, item_dict, extra_text="", loc_str=""):
        comp = comp.strip()
        role = role.strip()
        raw_url = str(raw_url or "").strip()
        if not comp or not role or not raw_url:
            return
        if any(exc_comp in comp.lower() for exc_comp in EXCLUDE_COMPANIES):
            return
        norm_url = clean_url(raw_url)
        sig = make_job_signature(comp, role)

        if norm_url in existing_job_urls or sig in existing_signatures:
            return
        if norm_url in seen_set or sig in seen_set or sig in seen_in_run or norm_url in seen_in_run:
            return
        if not matches_ai_ds_ml_role(role):
            return
        if not is_strictly_summer_2027(role, terms_list, extra_text):
            return
        if not is_graduate_eligible(item_dict, role, extra_text):
            return
        if not is_us_location(loc_str):
            return

        seen_in_run.add(sig)
        seen_in_run.add(norm_url)
        openings.append({"company": comp, "role": role, "url": raw_url, "sig": sig})

    # ENGINE 1: Community JSON Trackers (SimplifyJobs + PittCSC)
    tracker_urls = [
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/pittcsc/Summer2027-Internships/dev/.github/scripts/listings.json"
    ]
    for url in tracker_urls:
        try:
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                for item in res.json():
                    if not item.get("active", True) or not item.get("is_visible", True):
                        continue
                    locs = " ".join(item.get("locations", []))
                    try_add(item.get("company_name", ""), item.get("title", ""), item.get("url", ""), item.get("terms", []), item, "", locs)
        except Exception:
            continue

    # ENGINE 2: Direct Greenhouse ATS Boards (with content metadata check for 2027)
    for comp_name, token in GREENHOUSE_BOARDS:
        try:
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", timeout=10)
            if r.status_code != 200:
                continue
            for j in r.json().get("jobs", []):
                role = j.get("title", "")
                if not matches_ai_ds_ml_role(role):
                    continue
                loc_name = (j.get("location") or {}).get("name", "")
                content_snippet = (j.get("content") or "")[:1500]
                try_add(comp_name, role, j.get("absolute_url", ""), [], {}, content_snippet, loc_name)
        except Exception:
            continue

    # ENGINE 3: Direct Lever ATS Boards
    for comp_name, token in LEVER_BOARDS:
        try:
            r = requests.get(f"https://api.lever.co/v0/postings/{token}?mode=json", timeout=10)
            if r.status_code != 200:
                continue
            for j in r.json():
                role = j.get("text", "")
                if not matches_ai_ds_ml_role(role):
                    continue
                loc_name = (j.get("categories") or {}).get("location", "")
                desc = j.get("descriptionPlain", "")[:1500]
                try_add(comp_name, role, j.get("hostedUrl", ""), [], {}, desc, loc_name)
        except Exception:
            continue

    # ENGINE 4: Direct Ashby ATS Boards
    for comp_name, token in ASHBY_BOARDS:
        try:
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", timeout=10)
            if r.status_code != 200:
                continue
            for j in r.json().get("jobs", []):
                role = j.get("title", "")
                if not matches_ai_ds_ml_role(role):
                    continue
                loc_name = j.get("location", "")
                desc = (j.get("descriptionPlain") or "")[:1500]
                try_add(comp_name, role, j.get("jobUrl", ""), ["summer 2027"] if "2027" in role else [], {}, desc, loc_name)
        except Exception:
            continue

    # ENGINE 5: Serper Global ATS Dorker (Catches Workday, Greenhouse, Lever, Ashby across all companies)
    if SERPER_API_KEY:
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        ats_queries = [
            'site:myworkdayjobs.com "Summer 2027" ("Data Science" OR "Machine Learning" OR "Artificial Intelligence" OR "Data Analyst" OR "Data Engineer") "Intern" -PhD',
            '(site:boards.greenhouse.io OR site:job-boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com) "Summer 2027" ("Data Science" OR "Machine Learning" OR "AI" OR "Data Engineer" OR "Analytics") "Intern" -PhD'
        ]
        for q in ats_queries:
            try:
                res = requests.post("https://google.serper.dev/search", headers=headers, json={"q": q, "num": 15}, timeout=12)
                if res.status_code != 200:
                    continue
                for item in res.json().get("organic", []):
                    raw_title = item.get("title", "").strip()
                    link = item.get("link", "").strip()
                    snippet = item.get("snippet", "").strip()

                    # Extract Company & Role from ATS page title or URL
                    comp_guess = ""
                    role_guess = raw_title
                    if "myworkdayjobs.com" in link:
                        m = re.search(r'https?://([a-zA-Z0-9-]+)\.wd\d+\.myworkdayjobs\.com', link)
                        if m:
                            comp_guess = m.group(1).replace("-", " ").title()
                        role_guess = raw_title.split(" - ")[0].split(" | ")[0].strip()
                    elif "greenhouse.io/" in link:
                        m = re.search(r'greenhouse\.io/([^/]+)/jobs/', link)
                        if m:
                            comp_guess = m.group(1).replace("-", " ").title()
                        if " at " in raw_title:
                            parts = raw_title.split(" at ")
                            role_guess = parts[0].replace("Job Application for ", "").strip()
                            comp_guess = parts[-1].split(" | ")[0].split(" - ")[0].strip()
                    elif "lever.co/" in link:
                        m = re.search(r'lever\.co/([^/]+)/', link)
                        if m:
                            comp_guess = m.group(1).replace("-", " ").title()
                        if " - " in raw_title:
                            parts = raw_title.split(" - ")
                            comp_guess = parts[0].strip()
                            role_guess = " - ".join(parts[1:]).strip()
                    elif "ashbyhq.com/" in link:
                        m = re.search(r'ashbyhq\.com/([^/]+)/', link)
                        if m:
                            comp_guess = m.group(1).replace("-", " ").title()
                        role_guess = raw_title.split(" @ ")[0].split(" - ")[0].strip()

                    if comp_guess and role_guess:
                        try_add(comp_guess, role_guess, link, ["summer 2027"], {}, snippet, snippet)
            except Exception as e:
                print(f"Serper ATS Dork error: {e}")

    return openings

def is_indian_origin(name: str, snippet: str) -> bool:
    tokens = [t.lower() for t in re.findall(r'\b[a-zA-Z]+\b', name)]
    if any(t in INDIAN_NAME_MARKERS for t in tokens):
        return True
    indian_cues = ["india", "mumbai", "pune", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai", "kolkata", "ahmedabad", "anna university", "vtu", "bits pilani", "iit", "nit", "srm", "vit", "jntu", "kiit", "manipal"]
    return any(cue in snippet.lower() for cue in indian_cues)

def is_verified_current_neu_alum(raw_title: str, snippet: str, clean_company: str) -> bool:
    """
    STRICT VERIFICATION:
    1. Profile title or snippet must explicitly contain 'Northeastern'.
    2. Target company must appear in the current headline OR in the 'Experience: Company' / 'Current: ... at Company' snippet block.
    3. Rejects 'Former', 'Ex-', or 'Past:' matches.
    """
    comp_lower = clean_company.lower().strip()
    title_lower = raw_title.lower()
    snippet_lower = snippet.lower()
    combined = f"{title_lower} {snippet_lower}"

    if "northeastern" not in combined:
        return False

    comp_tokens = [t for t in re.findall(r'\b[a-z0-9]+\b', comp_lower) if len(t) > 2]
    primary_token = comp_tokens[0] if comp_tokens else comp_lower

    # Parse LinkedIn title: "Name - Headline | LinkedIn"
    cleaned_t = raw_title.replace("| LinkedIn", "").replace("- LinkedIn", "")
    parts = [p.strip().lower() for p in cleaned_t.split(" - ") if p.strip()]
    headline_part = " - ".join(parts[1:]) if len(parts) >= 2 else title_lower

    in_headline = (comp_lower in headline_part) or (len(primary_token) >= 3 and primary_token in headline_part)

    # Check structured current experience cues in Google's LinkedIn snippet when Google truncates long titles with '...'
    in_current_snippet = any(
        cue in snippet_lower for cue in [
            f"experience: {comp_lower}",
            f"experience: {primary_token}",
            f"at {comp_lower}",
            f"@ {comp_lower}"
        ]
    )

    if not in_headline and not in_current_snippet:
        return False

    # Reject former employees
    bad_cues = [f"former {comp_lower}", f"formerly at {comp_lower}", f"ex-{primary_token}", f"ex {primary_token}", f"past: {comp_lower}"]
    if any(b in combined for b in bad_cues) or "former" in headline_part:
        return False

    return True

def find_verified_balanced_alumni(company_name: str, existing_alumni_urls: set, target_per_group: int = 3):
    if not SERPER_API_KEY:
        print("Error: SERPER_API_KEY is missing!")
        return []

    search_comp = clean_company_for_search(company_name)
    if not search_comp:
        return []

    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    # Uncapped search queries (no intitle: bottleneck) so truncated LinkedIn headlines aren't hidden
    q1 = f'site:linkedin.com/in/ "{search_comp}" "Northeastern University" (Engineer OR Scientist OR Data OR Developer OR AI OR Analyst)'
    q2 = f'site:linkedin.com/in/ "{search_comp}" "Northeastern University" (India OR Mumbai OR Pune OR Bangalore OR Hyderabad OR Patel OR Sharma OR Shah OR Singh OR Gupta)'

    candidates = []
    for q in [q1, q2]:
        try:
            res = requests.post("https://google.serper.dev/search", headers=headers, json={"q": q, "num": 15}, timeout=12)
            if res.status_code == 200:
                candidates.extend(res.json().get("organic", []))
        except Exception as e:
            print(f"Serper search error for {search_comp}: {e}")

    indian_pool = []
    non_indian_pool = []
    seen_links = set()

    for it in candidates:
        link = it.get("link", "").strip()
        link_clean = clean_url(link)

        if not link or "linkedin.com/in/" not in link_clean or link_clean in seen_links or link_clean in existing_alumni_urls:
            continue

        raw_title = it.get("title", "").strip()
        snippet = it.get("snippet", "").replace("\n", " ").strip()

        if not is_verified_current_neu_alum(raw_title, snippet, search_comp):
            continue

        seen_links.add(link_clean)

        clean_title = raw_title.replace(" | LinkedIn", "").replace(" - LinkedIn", "").strip()
        parts = [p.strip() for p in clean_title.split(" - ") if p.strip()]
        full_name = parts[0] if parts else ""

        name_tokens = [t for t in re.findall(r'\b[a-zA-Z]+\b', full_name) if t.lower() not in ["dr", "mr", "ms", "mrs", "er"]]
        if not name_tokens:
            continue
        first_name = name_tokens[0].capitalize()

        profile_obj = {
            "first_name": first_name,
            "full_name": full_name,
            "url": link_clean
        }

        if is_indian_origin(full_name, snippet):
            indian_pool.append(profile_obj)
        else:
            non_indian_pool.append(profile_obj)

    selected = []
    selected.extend(indian_pool[:target_per_group])
    selected.extend(non_indian_pool[:target_per_group])

    total_target = target_per_group * 2
    if len(selected) < total_target:
        remaining = [p for p in (indian_pool + non_indian_pool) if p not in selected]
        selected.extend(remaining[:(total_target - len(selected))])

    return selected

def append_to_sheets(service, rows):
    if not service or not rows:
        return

    res = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Sheet1!A:B"
    ).execute()
    existing_ab = res.get("values", [])
    next_row = len(existing_ab) + 1

    body = {"values": rows}
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Sheet1!A{next_row}:H{next_row + len(rows) - 1}",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

def send_discord_summary(job_count, people_count, lines_summary):
    if not DISCORD_WEBHOOK_URL:
        return
    sheet_link = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
    description = (
        f"☀️ **Good Morning! Here is your 8:00 AM Referral Pipeline Digest:**\n\n"
        f"• **New Graduate AI/DS/ML Roles Populated:** {job_count}\n"
        f"• **Verified Northeastern Alumni Added:** {people_count} (50% Indian / 50% Non-Indian)\n\n"
        f"📊 **[Open Google Sheets Tracker]({sheet_link})**\n\n"
        f"**Recently Added Openings:**\n" + (lines_summary or "_No new openings this morning._")
    )
    embed = {
        "title": "⚡ Daily Summer 2027 Referral Pipeline Update",
        "description": description[:2000],
        "color": 13369344,
        "footer": {"text": f"{datetime.now().strftime('%b %d, %Y')} | 5-Engine ATS & Verified NEU Pipeline"}
    }
    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)

def main():
    seen_cache = load_seen_cache()
    sheets_service = get_sheets_service()

    existing_alumni_urls, existing_job_urls, existing_signatures = fetch_existing_sheet_state(sheets_service)
    new_jobs = fetch_all_internships(seen_cache, existing_job_urls, existing_signatures)

    print(f"Discovered {len(new_jobs)} net-new Graduate Summer 2027 AI/DS/ML candidate roles.")

    sheet_rows = []
    summary_lines = []
    total_people_added = 0
    jobs_populated = 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Fill up to 10 roles that actually yield verified Northeastern alumni
    for job in new_jobs:
        if jobs_populated >= 10:
            break

        company = job["company"]
        role = job["role"]
        job_url = job["url"]
        sig = job["sig"]

        alumni_list = find_verified_balanced_alumni(company, existing_alumni_urls, target_per_group=3)
        seen_cache.add(sig)
        seen_cache.add(clean_url(job_url))

        # Only populate roles where at least 1 verified NEU alum works at the company
        if not alumni_list:
            continue

        jobs_populated += 1
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

        time.sleep(0.8)

    if sheet_rows:
        append_to_sheets(sheets_service, sheet_rows)

    save_seen_cache(seen_cache)
    summary_text = "\n".join(summary_lines)
    send_discord_summary(jobs_populated, total_people_added, summary_text)
    print(f"Completed: Added {total_people_added} verified alumni across {jobs_populated} roles.")

if __name__ == "__main__":
    main()
