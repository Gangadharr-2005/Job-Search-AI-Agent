import os
import re
import hashlib
import time
import sqlite3
import tempfile
import json
import concurrent.futures
from datetime import datetime
from typing import List, Dict

from flask import Flask, request, jsonify, g
from flask_cors import CORS
import requests
import PyPDF2  # pip install PyPDF2

# Import your LLM wrapper (assumed present)
from llm_helper import llm_call

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "7504a45c")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "f49d92ef8008b320dfaf1ec446e2ae55")
ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"

# JOOBLE key (you provided)
JOOBLE_KEY = "04b6671b-ad30-40a3-ad13-88b5b984d2df"
JOOBLE_URL = f"https://jooble.org/api/{JOOBLE_KEY}"

REMOTIVE_URL = "https://remotive.com/api/remote-jobs"

# Fallback logo (uploaded file path)
SAMPLE_LOGO_LOCAL_PATH = "/mnt/data/eb35e313-6469-4841-aed2-8c5319638488.png"

CACHE = {}
CACHE_TTL = 45  # seconds

DATABASE = os.getenv("CAREER_DB_PATH", "career_app.db")

# -----------------------
# Flask init
# -----------------------
app = Flask(__name__)
CORS(app)

# -----------------------
# Simple cache helpers
# -----------------------
def cache_get(key):
    v = CACHE.get(key)
    if not v:
        return None
    value, ts = v
    if time.time() - ts > CACHE_TTL:
        del CACHE[key]
        return None
    return value

def cache_set(key, value):
    CACHE[key] = (value, time.time())

# -----------------------
# Database helpers
# -----------------------
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        # users table (simple)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key TEXT UNIQUE,
                preferred_role TEXT,
                preferred_location TEXT,
                notice_period TEXT,
                created_at TEXT
            )
        """)
        # resumes table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key TEXT,
                parsed_text TEXT,
                skills_json TEXT,
                uploaded_at TEXT
            )
        """)
        db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db:
        db.close()

# Initialize DB on start
init_db()

# -----------------------
# HTTP helpers
# -----------------------
def safe_get(url, **kwargs):
    headers = kwargs.pop("headers", {"User-Agent": "Mozilla/5.0 (compatible; JobAssistant/1.0)"})
    timeout = kwargs.pop("timeout", 6)
    try:
        r = requests.get(url, headers=headers, timeout=timeout, **kwargs)
        r.raise_for_status()
        return r
    except Exception:
        return None

def safe_post(url, json=None, timeout=8):
    try:
        r = requests.post(url, json=json, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return r
    except Exception:
        return None

# -----------------------
# Utility - normalize jobs for frontend
# -----------------------
def normalize_job(job: Dict) -> Dict:
    return {
        "title": job.get("title") or job.get("job_title") or "Unknown",
        "company": job.get("company") or job.get("company_name") or job.get("company_display") or "Unknown",
        "location": job.get("location") or job.get("candidate_required_location") or "Unknown",
        "salary": job.get("salary") or job.get("salary_min") or job.get("salary_max") or None,
        "description": job.get("description") or job.get("snippet") or "",
        "url": job.get("url") or job.get("redirect_url") or job.get("link") or "#",
        "source": job.get("source") or "unknown",
        "company_logo": job.get("company_logo") or SAMPLE_LOGO_LOCAL_PATH,
        "posted_date": job.get("created") or job.get("publication_date") or datetime.utcnow().isoformat()
    }

def dedupe_jobs(jobs: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for j in jobs:
        key = ( (j.get("title") or "").strip().lower(),
                (j.get("company") or "").strip().lower(),
                (j.get("location") or "").strip().lower() )
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out

# -----------------------
# Adzuna fetcher
# -----------------------
def fetch_adzuna(title, location, page=1):
    cache_key = f"adzuna::{title}::{location}::{page}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": 20,
        "what": title,
        "where": location
    }
    url = ADZUNA_URL.format(page=page)
    try:
        r = safe_get(url, params=params, timeout=5)
        if not r:
            cache_set(cache_key, [])
            return []
        data = r.json()
        results = []
        for item in data.get("results", []):
            results.append({
                "title": item.get("title"),
                "company": item.get("company", {}).get("display_name"),
                "location": item.get("location", {}).get("display_name"),
                "salary": item.get("salary_min") or item.get("salary_max"),
                "description": item.get("description"),
                "url": item.get("redirect_url"),
                "source": "Adzuna",
                "created": item.get("created")
            })
        cache_set(cache_key, results)
        return results
    except Exception:
        cache_set(cache_key, [])
        return []

# -----------------------
# Jooble fetcher
# -----------------------
def fetch_jooble(title, location):
    if not JOOBLE_URL:
        return []
    cache_key = f"jooble::{title}::{location}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    payload = {"keywords": title, "location": location}
    try:
        r = safe_post(JOOBLE_URL, json=payload, timeout=7)
        if not r:
            cache_set(cache_key, [])
            return []
        data = r.json()
        results = []
        for item in data.get("jobs", []):
            results.append({
                "title": item.get("title"),
                "company": item.get("company"),
                "location": item.get("location"),
                "salary": item.get("salary"),
                "description": item.get("snippet") or item.get("description"),
                "url": item.get("link"),
                "source": "Jooble",
                "created": item.get("date")
            })
        cache_set(cache_key, results)
        return results
    except Exception:
        cache_set(cache_key, [])
        return []

# -----------------------
# Remotive fetcher
# -----------------------
def fetch_remotive(title, location):
    cache_key = f"remotive::{title}::{location}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        r = safe_get(REMOTIVE_URL, timeout=5)
        if not r:
            cache_set(cache_key, [])
            return []
        data = r.json()
        results = []
        for item in data.get("jobs", []):
            if title.lower() in (item.get("title") or "").lower():
                results.append({
                    "title": item.get("title"),
                    "company": item.get("company_name"),
                    "location": item.get("candidate_required_location") or "Remote",
                    "salary": item.get("salary"),
                    "description": (item.get("description") or "")[:400],
                    "url": item.get("url"),
                    "source": "Remotive",
                    "created": item.get("publication_date")
                })
        cache_set(cache_key, results)
        return results
    except Exception:
        cache_set(cache_key, [])
        return []

# -----------------------
# Aggregator endpoint (parallel)
# -----------------------
@app.route("/jobs", methods=["GET"])
def jobs_aggregator():
    title = request.args.get("title", "").strip()
    location = request.args.get("location", "India").strip()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 15))

    if not title:
        return jsonify({"jobs": [], "total": 0, "page": page})

    jobs_combined = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        fut_adz = executor.submit(fetch_adzuna, title, location, page)
        fut_joob = executor.submit(fetch_jooble, title, location)
        fut_rem = executor.submit(fetch_remotive, title, location)

        adz_jobs = fut_adz.result()
        joob_jobs = fut_joob.result()
        rem_jobs = fut_rem.result()

    jobs_combined.extend(adz_jobs)
    jobs_combined.extend(joob_jobs)
    jobs_combined.extend(rem_jobs)

    normalized = [normalize_job(j) for j in jobs_combined]
    deduped = dedupe_jobs(normalized)

    # scoring (title match + fields)
    def score(j):
        s = 0
        t = title.lower()
        if t in (j.get("title") or "").lower():
            s += 5
        if j.get("company") and j.get("company") != "Unknown":
            s += 1
        if j.get("salary"):
            s += 1
        return s

    deduped.sort(key=score, reverse=True)

    total = len(deduped)
    start = (page - 1) * per_page
    end = start + per_page
    paged = deduped[start:end]

    resp = {
        "jobs": paged,
        "total": total,
        "page": page,
        "per_page": per_page,
        "sources": ["Adzuna", "Jooble", "Remotive"],
        "timestamp": datetime.utcnow().isoformat()
    }
    return jsonify(resp), 200

# -----------------------
# Resume parsing & skill extraction
# -----------------------
# A compact skill list for matching (extend as needed)
COMMON_SKILLS = [
    "python","java","c++","c","javascript","react","node","sql","mysql","postgres","mongodb",
    "flask","django","aws","azure","gcp","docker","kubernetes","html","css","git","linux",
    "excel","tableau","powerbi","machine learning","deep learning","nlp","pandas","numpy",
    "tensorflow","pytorch","scikit-learn","data analysis","spark","hadoop","rest api"
]

def extract_text_from_pdf(pdf_path: str) -> str:
    try:
        reader = PyPDF2.PdfReader(pdf_path)
        text_parts = []
        for p in reader.pages:
            txt = p.extract_text()
            if txt:
                text_parts.append(txt)
        return "\n".join(text_parts).lower()
    except Exception:
        return ""

def extract_skills_from_text(text: str) -> List[str]:
    found = set()
    for s in COMMON_SKILLS:
        if s in text:
            found.add(s)
    # also attempt to capture "b.tech", "bachelor", "mtech", "mba" etc.
    edu_patterns = ["b.tech","bachelor","mtech","mba","bsc","msc","phd"]
    for e in edu_patterns:
        if e in text:
            found.add(e)
    return sorted(found)

@app.route("/upload_resume", methods=["POST"])
def upload_resume():
    """
    Accepts multipart/form-data file upload with fields:
      - file (PDF)
      - user_key (optional string to associate resume)
    Returns parsed skills and a resume_id
    """
    if "file" not in request.files:
        return jsonify({"error": "no file provided"}), 400
    f = request.files["file"]
    user_key = request.form.get("user_key", None) or request.form.get("userKey", None) or request.args.get("user_key", None)
    if f.filename == "":
        return jsonify({"error": "empty filename"}), 400

    # Save to temp and parse
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        f.save(tmp.name)
        parsed_text = extract_text_from_pdf(tmp.name)
    skills = extract_skills_from_text(parsed_text)

    # store in sqlite
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO resumes (user_key, parsed_text, skills_json, uploaded_at) VALUES (?, ?, ?, ?)",
        (user_key, parsed_text, json.dumps(skills), datetime.utcnow().isoformat())
    )
    db.commit()
    resume_id = cur.lastrowid

    return jsonify({"resume_id": resume_id, "skills": skills, "user_key": user_key}), 200

# -----------------------
# Job matching endpoint
# -----------------------
def compute_match_score(resume_skills: List[str], job: Dict, title: str) -> float:
    """
    Basic scoring:
     - overlap ratio of skills
     - title keyword boost
    Returns score 0..100
    """
    if not resume_skills:
        return 0.0
    job_text = (job.get("title","") + " " + job.get("description","")).lower()
    matched = 0
    for s in resume_skills:
        if s.lower() in job_text:
            matched += 1
    skill_ratio = matched / max(len(resume_skills), 1)
    # title bonus
    title_bonus = 0.2 if title.lower() in (job.get("title") or "").lower() else 0.0
    score = (skill_ratio * 0.8 + title_bonus) * 100
    if score > 100:
        score = 100
    return round(score, 2)

@app.route("/match_jobs", methods=["GET"])
def match_jobs():
    """
    Query params:
      - resume_id or user_key (prefer resume_id)
      - title (required)
      - location (optional)
      - per_page (optional)
    Returns jobs with added match_score and matched_skills list
    """
    title = request.args.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    location = request.args.get("location", "India")
    resume_id = request.args.get("resume_id", None)
    user_key = request.args.get("user_key", None)

    db = get_db()
    cur = db.cursor()
    parsed_text = ""
    skills = []
    if resume_id:
        cur.execute("SELECT parsed_text, skills_json FROM resumes WHERE id = ?", (resume_id,))
        row = cur.fetchone()
        if row:
            parsed_text = row["parsed_text"]
            skills = json.loads(row["skills_json"])
    elif user_key:
        cur.execute("SELECT parsed_text, skills_json FROM resumes WHERE user_key = ? ORDER BY uploaded_at DESC LIMIT 1", (user_key,))
        row = cur.fetchone()
        if row:
            parsed_text = row["parsed_text"]
            skills = json.loads(row["skills_json"])

    # get jobs from aggregator
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        fut = executor.submit(fetch_adzuna, title, location, 1)
        fut2 = executor.submit(fetch_jooble, title, location)
        fut3 = executor.submit(fetch_remotive, title, location)

        jobs = []
        for res in (fut.result(), fut2.result(), fut3.result()):
            if res:
                jobs.extend(res)

    normalized = [normalize_job(j) for j in jobs]
    deduped = dedupe_jobs(normalized)

    # compute match score & matched skills list
    for j in deduped:
        j_text = (j.get("title","") + " " + j.get("description","")).lower()
        matched_skills = [s for s in skills if s.lower() in j_text]
        j["match_score"] = compute_match_score(skills, j, title)
        j["matched_skills"] = matched_skills

    # sort by match_score desc
    deduped.sort(key=lambda x: x.get("match_score", 0), reverse=True)

    per_page = int(request.args.get("per_page", 15))
    page = int(request.args.get("page", 1))
    start = (page - 1) * per_page
    end = start + per_page

    resp = {
        "jobs": deduped[start:end],
        "total": len(deduped),
        "page": page,
        "per_page": per_page,
        "timestamp": datetime.utcnow().isoformat()
    }
    return jsonify(resp), 200

# -----------------------
# User preferences (save / get)
# -----------------------
@app.route("/save_preferences", methods=["POST"])
def save_preferences():
    data = request.get_json() or {}
    user_key = data.get("user_key") or data.get("userKey")
    preferred_role = data.get("preferred_role")
    preferred_location = data.get("preferred_location")
    notice_period = data.get("notice_period")

    if not user_key:
        return jsonify({"error": "user_key required"}), 400

    db = get_db()
    cur = db.cursor()
    # upsert
    cur.execute("SELECT id FROM user_profile WHERE user_key = ?", (user_key,))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE user_profile
            SET preferred_role = ?, preferred_location = ?, notice_period = ?
            WHERE user_key = ?
        """, (preferred_role, preferred_location, notice_period, user_key))
    else:
        cur.execute("""
            INSERT INTO user_profile (user_key, preferred_role, preferred_location, notice_period, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_key, preferred_role, preferred_location, notice_period, datetime.utcnow().isoformat()))
    db.commit()
    return jsonify({"saved": True, "user_key": user_key}), 200

@app.route("/get_preferences", methods=["GET"])
def get_preferences():
    user_key = request.args.get("user_key")
    if not user_key:
        return jsonify({"error": "user_key required"}), 400
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT preferred_role, preferred_location, notice_period FROM user_profile WHERE user_key = ?", (user_key,))
    row = cur.fetchone()
    if not row:
        return jsonify({"preferences": {}}), 200
    return jsonify({"preferences": {
        "preferred_role": row["preferred_role"],
        "preferred_location": row["preferred_location"],
        "notice_period": row["notice_period"]
    }}), 200

# -----------------------
# AI assistant (career-aware)
# -----------------------
@app.route("/ask", methods=["POST"])
def ask_ai():
    data = request.get_json() or {}
    user_query = data.get("query", "")
    user_key = data.get("user_key", None)

    if not user_query.strip():
        return jsonify({"response": "Please provide a valid question."})

    # Fetch last resume (if any) to provide context
    resume_text = ""
    db = get_db()
    if user_key:
        cur = db.cursor()
        cur.execute("SELECT parsed_text FROM resumes WHERE user_key = ? ORDER BY uploaded_at DESC LIMIT 1", (user_key,))
        row = cur.fetchone()
        if row:
            resume_text = (row["parsed_text"] or "")[:4000]  # keep reasonably sized context

    system_prompt = (
        "You are an AI Career Assistant. You help with:\n"
        "- Reviewing resumes and extracting suggestions\n"
        "- Matching users to job roles and suggesting skill improvements\n"
        "- Preparing interview questions and tips\n"
        "- Suggesting company-specific insights for India job market\n\n"
        "When a resume is provided, use it to tailor advice. Be concise, actionable and polite."
    )

    # Build a context message to LLM including resume_text if present
    context = f"User question: {user_query}\n"
    if resume_text:
        context += f"\nResume excerpt:\n{resume_text}\n"

    # Call helper llm_call (assumed to exist)
    try:
        reply = llm_call(system_prompt, context)
    except Exception as e:
        reply = f"AI helper failed: {e}"

    return jsonify({"response": reply}), 200

# -----------------------
# Company summary endpoint (AmbitionBox scraping + AI summarization)
# -----------------------
from bs4 import BeautifulSoup  # pip install beautifulsoup4

@app.route("/company_info", methods=["GET"])
def company_info():
    company = request.args.get("company", "").strip()
    if not company:
        return jsonify({"error": "company required"}), 400
    name_slug = company.lower().replace(" ", "-")
    url = f"https://www.ambitionbox.com/reviews/{name_slug}-reviews"
    r = safe_get(url)
    rating_text = None
    summary_text = None
    if r:
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            rating = soup.find("span", {"class": "rating-number"})
            summary = soup.find("p", {"class": "bold-title-l"})
            rating_text = rating.text.strip() if rating else None
            summary_text = summary.text.strip() if summary else None
        except Exception:
            rating_text = None
            summary_text = None

    # Ask LLM for a short analysis based on scraped summary
    system_prompt = "You are an assistant that summarizes company culture and hiring tips for candidates in India."
    context = f"Company: {company}\nAmbitionBox rating: {rating_text}\nSummary: {summary_text}"
    try:
        ai_summary = llm_call(system_prompt, context)
    except Exception:
        ai_summary = "No AI summary available."

    return jsonify({
        "company": company,
        "ambitionbox_rating": rating_text,
        "ambitionbox_summary": summary_text,
        "ai_summary": ai_summary,
        "ambitionbox_url": url
    }), 200

# -----------------------
# Save job (simple)
# -----------------------
@app.route("/save_job", methods=["POST"])
def save_job():
    payload = request.get_json()
    if not payload:
        return jsonify({"error": "missing job payload"}), 400
    jid = hashlib.sha1((payload.get("url", "") + payload.get("title", "")).encode()).hexdigest()
    # TODO: persist to DB for full app — minimal return for frontend UX
    return jsonify({"saved": True, "id": jid}), 200

# -----------------------
# Health
# -----------------------
@app.route("/")
def home():
    return jsonify({"message": "Flask AI Job Assistant Backend (Week 3) is running ✅", "timestamp": datetime.utcnow().isoformat()})

# -----------------------
# Run (for local dev)
# -----------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
