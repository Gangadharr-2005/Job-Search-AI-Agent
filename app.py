# backend/app.py
import os
import sqlite3
import tempfile
import json
import time
import hashlib
import concurrent.futures
from datetime import datetime
from typing import List, Dict, Optional

from flask import Flask, request, jsonify, g
from flask_cors import CORS
import requests
import PyPDF2
from bs4 import BeautifulSoup

# import llm helper (if present)
try:
    from llm_helper import llm_call, llm_simple_completion
except Exception:
    # fallback simple functions
    def llm_call(name, prompt):
        return f"(LLM not configured) {prompt[:300]}"

    def llm_simple_completion(prompt):
        return f"(LLM fallback) {prompt[:300]}"

# CONFIG (use env vars in production)
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "your_id")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "your_api")
ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"

JOOBLE_KEY = os.getenv("JOOBLE_KEY", "your_key")
JOOBLE_URL = f"https://jooble.org/api/{JOOBLE_KEY}"

REMOTIVE_URL = "https://remotive.com/api/remote-jobs"

DATABASE = os.getenv("CAREER_DB_PATH", "career_app.db")
CACHE_TTL = 45
CACHE = {}

SAMPLE_LOGO_LOCAL_PATH = "/mnt/data/eb35e313-6469-4841-aed2-8c5319638488.png"

app = Flask(__name__)
CORS(app)

# ============================================================
# DB HELPER FUNCTIONS
# ============================================================
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db:
        db.close()

def init_db():
    db = get_db()
    cur = db.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_profile (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_key TEXT UNIQUE,
      preferred_role TEXT,
      preferred_location TEXT,
      notice_period TEXT,
      email TEXT,
      created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS resumes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_key TEXT,
      parsed_text TEXT,
      skills_json TEXT,
      uploaded_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS saved_jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_key TEXT,
      job_json TEXT,
      saved_at TEXT,
      applied INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS internships (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT,
      company TEXT,
      location TEXT,
      stipend TEXT,
      url TEXT,
      deadline TEXT,
      tags TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS scholarships (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT,
      provider TEXT,
      eligibility TEXT,
      url TEXT,
      deadline TEXT
    )
    """)

    db.commit()

with app.app_context():
    init_db()

# ============================================================
# CACHE HELPERS
# ============================================================
def cache_get(key):
    v = CACHE.get(key)
    if not v:
        return None
    val, ts = v
    if time.time() - ts > CACHE_TTL:
        del CACHE[key]
        return None
    return val

def cache_set(key, val):
    CACHE[key] = (val, time.time())

# ============================================================
# SAFE REQUEST HELPERS
# ============================================================
def safe_get(url, **kwargs):
    headers = kwargs.pop("headers", {"User-Agent": "Mozilla/5.0 (JobAssistant/1.0)"})
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

# ============================================================
# JOB FETCHERS
# (unchanged from earlier — uses Adzuna/Jooble/Remotive)
# ============================================================
def fetch_adzuna(title, location, page=1):
    cache_key = f"adzuna::{title}::{location}::{page}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "what": title,
        "where": location,
        "results_per_page": 20
    }
    url = ADZUNA_URL.format(page=page)

    try:
        r = safe_get(url, params=params, timeout=6)
        if not r:
            cache_set(cache_key, [])
            return []

        data = r.json()
        out = []
        for item in data.get("results", []):
            out.append({
                "title": item.get("title"),
                "company": item.get("company", {}).get("display_name"),
                "location": item.get("location", {}).get("display_name"),
                "salary": item.get("salary_min") or item.get("salary_max") or item.get("salary"),
                "description": item.get("description"),
                "url": item.get("redirect_url"),
                "source": "Adzuna",
                "created": item.get("created")
            })

        cache_set(cache_key, out)
        return out

    except Exception:
        cache_set(cache_key, [])
        return []

def fetch_jooble(title, location):
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

def fetch_remotive(title, location):
    cache_key = f"remotive::{title}::{location}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        r = safe_get(REMOTIVE_URL, timeout=6)
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
                    "description": (item.get("description") or "")[:600],
                    "url": item.get("url"),
                    "source": "Remotive",
                    "created": item.get("publication_date")
                })

        cache_set(cache_key, results)
        return results

    except Exception:
        cache_set(cache_key, [])
        return []

# ============================================================
# NORMALIZE JOB & SALARY PARSING
# ============================================================
def parse_salary_to_lpa(s):
    if s is None:
        return None
    try:
        if isinstance(s, (int, float)):
            if s > 1000:
                lpa = s / 100000.0
                return round(lpa, 2)
            return float(s)
        text = str(s).lower().replace(",", "").strip()
        if "lpa" in text or "lakh" in text or "lac" in text:
            import re
            m = re.search(r"(\d+(\.\d+)?)", text)
            if m:
                return float(m.group(1))
        if "per year" in text or "year" in text or "pa" in text or "/yr" in text or "/year" in text:
            import re
            m = re.search(r"(\d+(\.\d+)?)", text)
            if m:
                v = float(m.group(1))
                if v > 1000:
                    return round(v / 100000.0, 2)
                else:
                    return v
        import re
        m = re.search(r"(\d+(\.\d+)?)", text)
        if m:
            v = float(m.group(1))
            if v > 1000:
                return round(v / 100000.0, 2)
            return v
    except Exception:
        pass
    return None

def normalize_job(job: Dict) -> Dict:
    salary_raw = job.get("salary")
    salary_lpa = parse_salary_to_lpa(salary_raw)

    title = job.get("title") or job.get("job_title") or "Unknown"
    desc = job.get("description") or job.get("snippet") or ""
    company = job.get("company") or job.get("company_name") or "Unknown"
    location = job.get("location") or job.get("candidate_required_location") or "Unknown"

    return {
        "title": title,
        "company": company,
        "location": location,
        "salary": salary_raw,
        "salary_lpa": salary_lpa,
        "description": desc,
        "url": job.get("url") or job.get("redirect_url") or job.get("link") or "#",
        "source": job.get("source") or "unknown",
        "company_logo": job.get("company_logo") or SAMPLE_LOGO_LOCAL_PATH,
        "posted_date": job.get("created") or job.get("publication_date") or datetime.utcnow().isoformat()
    }

def dedupe_jobs(jobs: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for j in jobs:
        key = (
            (j.get("title") or "").strip().lower(),
            (j.get("company") or "").strip().lower(),
            (j.get("location") or "").strip().lower()
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out

# ============================================================
# RESUME PARSING
# ============================================================
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
    if not text:
        return []
    for s in COMMON_SKILLS:
        if s in text:
            found.add(s)
    edu_patterns = ["b.tech","bachelor","mtech","mba","bsc","msc","phd","b.e","bcom"]
    for e in edu_patterns:
        if e in text:
            found.add(e)
    return sorted(found)

@app.route("/upload_resume", methods=["POST"])
def upload_resume():
    if "file" not in request.files:
        return jsonify({"error": "no file provided"}), 400
    f = request.files["file"]
    user_key = request.form.get("user_key") or request.args.get("user_key")

    if f.filename == "":
        return jsonify({"error": "empty filename"}), 400

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        f.save(tmp.name)
        parsed_text = extract_text_from_pdf(tmp.name)

    skills = extract_skills_from_text(parsed_text)

    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO resumes (user_key, parsed_text, skills_json, uploaded_at) VALUES (?, ?, ?, ?)",
        (user_key, parsed_text, json.dumps(skills), datetime.utcnow().isoformat())
    )
    db.commit()
    resume_id = cur.lastrowid

    return jsonify({"resume_id": resume_id, "skills": skills, "user_key": user_key}), 200

# ============================================================
# NEW: Get latest resume text endpoint (used by frontend)
# ============================================================
@app.route("/get_latest_resume", methods=["GET"])
def get_latest_resume():
    user_key = request.args.get("user_key")
    resume_id = request.args.get("resume_id")

    db = get_db()
    cur = db.cursor()

    if resume_id:
        cur.execute("SELECT parsed_text FROM resumes WHERE id = ?", (resume_id,))
    elif user_key:
        cur.execute("SELECT parsed_text FROM resumes WHERE user_key = ? ORDER BY uploaded_at DESC LIMIT 1", (user_key,))
    else:
        return jsonify({"error": "user_key or resume_id required"}), 400

    row = cur.fetchone()
    if not row:
        return jsonify({"error": "Resume not found"}), 404

    return jsonify({"resume_text": row["parsed_text"]}), 200

# ============================================================
# MATCHING LOGIC + EXPERIENCE & JOB TYPE SUPPORT
# ============================================================
EXPERIENCE_LEVELS = {
    "fresher": (0, 1),
    "junior": (1, 3),
    "mid": (3, 6),
    "senior": (6, 50)
}

JOB_TYPE_KEYWORDS = {
    "internship": ["intern", "trainee", "internship"],
    "full-time": ["full time", "permanent", "full-time"],
    "contract": ["contract"],
    "remote": ["remote", "work from home", "wfh"]
}

def detect_job_type(text: str) -> str:
    t = (text or "").lower()
    for jt, kws in JOB_TYPE_KEYWORDS.items():
        for k in kws:
            if k in t:
                return jt
    if "intern" in t:
        return "internship"
    if "remote" in t:
        return "remote"
    return "full-time"

def compute_match_score(resume_skills: List[str], job: Dict, title: str) -> float:
    if not resume_skills:
        base_skill_score = 0.0
    else:
        job_text = (job.get("title","") + " " + job.get("description","")).lower()
        matched = 0
        for s in resume_skills:
            if s.lower() in job_text:
                matched += 1
        base_skill_score = (matched / max(len(resume_skills), 1)) * 100

    title_bonus = 20.0 if title.lower() in (job.get("title") or "").lower() else 0.0
    source_bonus = 5.0 if job.get("source") == "Remotive" else 0.0

    score = base_skill_score * 0.7 + title_bonus * 0.25 + source_bonus * 0.05
    return round(min(score, 100.0), 2)

def experience_score(exp_years: Optional[float], job_text: str) -> float:
    if exp_years is None:
        return 0.0
    t = (job_text or "").lower()
    try:
        if "fresher" in t or "entry" in t:
            return 1.0 if exp_years <= 1 else 0.3
        if "junior" in t:
            return 1.0 if 1 <= exp_years <= 3 else 0.5
        if "senior" in t or "lead" in t:
            return 1.0 if exp_years >= 5 else 0.3
        if exp_years <= 1:
            return 0.9
        if exp_years <= 3:
            return 0.95
        if exp_years <= 6:
            return 1.0
        return 1.0
    except Exception:
        return 0.5

def salary_in_bucket(salary_lpa: Optional[float], bucket: str) -> bool:
    if bucket == "Any" or salary_lpa is None:
        return True
    ranges = {
        "0 - 3 LPA": (0, 3),
        "3 - 6 LPA": (3, 6),
        "6 - 10 LPA": (6, 10),
        "10 - 20 LPA": (10, 20),
        "20+ LPA": (20, 1000)
    }
    low, high = ranges.get(bucket, (0, 1000))
    return low <= salary_lpa <= high

@app.route("/match_jobs", methods=["GET"])
def match_jobs():
    title = request.args.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400

    location = request.args.get("location", "India").strip()
    resume_id = request.args.get("resume_id")
    user_key = request.args.get("user_key")

    try:
        exp_years = float(request.args.get("experience", 0))
    except Exception:
        exp_years = 0.0
    salary_bucket = request.args.get("salary_bucket", "Any")
    job_type_pref = request.args.get("job_type", "Any")

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

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fut1 = ex.submit(fetch_adzuna, title, location, 1)
        fut2 = ex.submit(fetch_jooble, title, location)
        fut3 = ex.submit(fetch_remotive, title, location)
        res_all = []
        for res in (fut1.result(), fut2.result(), fut3.result()):
            if res:
                res_all.extend(res)

    normalized = [normalize_job(j) for j in res_all]
    deduped = dedupe_jobs(normalized)

    filtered = []
    for j in deduped:
        j_text = (j.get("title","") + " " + j.get("description","")).lower()
        j["job_type"] = detect_job_type(j_text)
        if job_type_pref and job_type_pref != "Any" and j["job_type"] != job_type_pref:
            continue
        job_salary_lpa = j.get("salary_lpa")
        if not salary_in_bucket(job_salary_lpa, salary_bucket):
            continue

        j["matched_skills"] = [s for s in skills if s.lower() in j_text] if skills else []
        base_score = compute_match_score(skills, j, title)
        exp_boost = experience_score(exp_years, j_text) * 12
        location_bonus = 12 if location and location.lower() in (j.get("location") or "").lower() else 0
        type_bonus = 6 if (job_type_pref != "Any" and j["job_type"] == job_type_pref) else 0

        final_score = base_score + exp_boost + location_bonus + type_bonus
        final_score = min(final_score, 100.0)
        j["match_score"] = round(final_score, 2)

        filtered.append(j)

    filtered.sort(key=lambda x: x.get("match_score", 0), reverse=True)

    per_page = int(request.args.get("per_page", 15))
    page = int(request.args.get("page", 1))
    start = (page - 1) * per_page
    end = start + per_page

    resp = {
        "jobs": filtered[start:end],
        "total": len(filtered),
        "page": page,
        "per_page": per_page,
        "timestamp": datetime.utcnow().isoformat()
    }

    return jsonify(resp), 200

# ============================================================
# COMPANY INFO, SALARY BENCH, RESUME_IMPROVE, etc.
# (kept as in your original file)
# ============================================================
@app.route("/company_info", methods=["GET"])
def company_info():
    company = request.args.get("company", "").strip()
    if not company:
        return jsonify({"error": "company required"}), 400

    slug = company.lower().replace(" ", "-")
    url = f"https://www.ambitionbox.com/reviews/{slug}-reviews"

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
            pass

    system_prompt = "You are an assistant that summarizes company culture and hiring tips for candidates in India."
    context = f"Company: {company}\nAmbitionBox rating: {rating_text}\nSummary: {summary_text}"

    try:
        ai_summary = llm_call(system_prompt, context)
    except Exception:
        ai_summary = llm_simple_completion(f"Summarize: {company} rating {rating_text} {summary_text}")

    return jsonify({
        "company": company,
        "ambitionbox_rating": rating_text,
        "ambitionbox_summary": summary_text,
        "ai_summary": ai_summary,
        "ambitionbox_url": url
    }), 200

SAMPLE_SALARY_DATA = {
    "data analyst": (2, 4, 7),
    "data scientist": (6, 12, 30),
    "python developer": (3, 6, 12),
    "software engineer": (3, 8, 20),
    "product manager": (8, 18, 40)
}

@app.route("/salary_bench", methods=["GET"])
def salary_bench():
    role = request.args.get("role", "").lower()
    try:
        exp = float(request.args.get("exp", 0))
    except Exception:
        exp = 0.0
    city = request.args.get("city", "India")

    if not role:
        return jsonify({"error": "role required"}), 400

    low, med, high = SAMPLE_SALARY_DATA.get(role, (2, 5, 10))
    med_adj = med * (1 + min(exp/10, 1))

    return jsonify({
        "role": role,
        "city": city,
        "experience": exp,
        "low_lpa": low,
        "median_lpa": round(med_adj, 1),
        "high_lpa": high
    }), 200

@app.route("/resume_improve", methods=["POST"])
def resume_improve():
    data = request.get_json() or {}
    resume_text = data.get("resume_text", "")
    job_description = data.get("job_description", "")

    if not resume_text:
        return jsonify({"error": "resume_text required"}), 400

    prompt = (
        "You are a resume optimization assistant. Given a resume text and a job description,"
        " suggest 5 specific improvements to make the resume more ATS-friendly and tailored to the job."
        f"\n\nResume:\n{resume_text[:4000]}\n\nJob Description:\n{job_description[:4000]}"
    )

    try:
        advice = llm_call("Resume optimizer", prompt)
    except Exception:
        advice = llm_simple_completion(prompt)

    return jsonify({"advice": advice}), 200

@app.route("/mock_interview", methods=["POST"])
def mock_interview():
    data = request.get_json() or {}
    role = data.get("role", "software engineer")
    level = data.get("level", "junior")
    num_q = int(data.get("num_questions", 7))

    prompt = f"Generate {num_q} interview questions for a {level} {role}. Provide ideal answer outline + scoring rubric."

    try:
        out = llm_call("Mock interviewer", prompt)
    except Exception:
        out = llm_simple_completion(prompt)

    return jsonify({"mock": out}), 200

def seed_internships_and_scholarships_if_empty():
    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) as c FROM internships")
    if cur.fetchone()["c"] == 0:
        sample_interns = [
            ("Data Science Intern","ABC Tech","Bangalore","10,000/month","https://example.com/abc-intern","2025-06-30","data,python,ml"),
            ("Frontend Intern","XYZ Labs","Hyderabad","8,000/month","https://example.com/xyz-intern","2025-07-15","react,js,html"),
        ]
        for t,c,loc,stip,url,dl,tags in sample_interns:
            cur.execute("INSERT INTO internships (title,company,location,stipend,url,deadline,tags) VALUES (?,?,?,?,?,?,?)",
                        (t,c,loc,stip,url,dl,tags))

        sample_sch = [
            ("Karnataka Scholar Support","Karnataka Govt","Student of Karnataka, annual income < 2L","https://karnataka.gov.in/help","2025-12-31"),
            ("STEM Merit Scholarship","XYZ Foundation","B.Tech students, 1st-3rd year","https://example.com/stem","2025-09-30"),
        ]
        for t,p,elig,url,dl in sample_sch:
            cur.execute("INSERT INTO scholarships (title,provider,eligibility,url,deadline) VALUES (?,?,?,?,?)",
                        (t,p,elig,url,dl))

        db.commit()

with app.app_context():
    seed_internships_and_scholarships_if_empty()

@app.route("/internships", methods=["GET"])
def get_internships():
    q = request.args.get("q", "").lower()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM internships ORDER BY deadline ASC LIMIT 200")
    rows = [dict(r) for r in cur.fetchall()]

    if q:
        rows = [r for r in rows if q in (r["title"] + r["company"] + (r["tags"] or "")).lower()]

    return jsonify({"count": len(rows), "items": rows}), 200

@app.route("/scholarships", methods=["GET"])
def get_scholarships():
    q = request.args.get("q", "").lower()
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM scholarships ORDER BY deadline ASC LIMIT 200")
    rows = [dict(r) for r in cur.fetchall()]

    if q:
        rows = [r for r in rows if q in (r["title"] + r["provider"] + r["eligibility"]).lower()]

    return jsonify({"count": len(rows), "items": rows}), 200

@app.route("/save_job", methods=["POST"])
def save_job():
    payload = request.get_json() or {}
    user_key = payload.get("user_key") or payload.get("userKey")
    job = payload.get("job") or {}

    if not user_key or not job:
        return jsonify({"error": "user_key and job required"}), 400

    jid = hashlib.sha1((job.get("url","") + job.get("title","")).encode()).hexdigest()

    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO saved_jobs (user_key, job_json, saved_at) VALUES (?,?,?)",
                (user_key, json.dumps(job), datetime.utcnow().isoformat()))
    db.commit()

    return jsonify({"saved": True, "id": jid}), 200

@app.route("/get_saved_jobs", methods=["GET"])
def get_saved_jobs():
    user_key = request.args.get("user_key")
    if not user_key:
        return jsonify({"error": "user_key required"}), 400

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, job_json, applied, saved_at FROM saved_jobs WHERE user_key = ? ORDER BY id DESC",
                (user_key,))
    rows = cur.fetchall()

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "job": json.loads(r["job_json"]),
            "applied": bool(r["applied"]),
            "saved_at": r["saved_at"]
        })

    return jsonify({"items": items}), 200

@app.route("/apply_job", methods=["POST"])
def apply_job():
    data = request.get_json() or {}
    saved_id = data.get("saved_id")

    if not saved_id:
        return jsonify({"error": "saved_id required"}), 400

    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE saved_jobs SET applied = 1 WHERE id = ?", (saved_id,))
    db.commit()

    return jsonify({"applied": True}), 200

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json() or {}
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "query required"}), 400

    system_prompt = "You are a career mentor. Provide clear guidance and actionable suggestions."

    try:
        response = llm_call("Career Mentor", f"{system_prompt}\n\nQuestion: {query}")
    except Exception:
        response = llm_simple_completion(f"{system_prompt}\n\nQuestion: {query}")

    return jsonify({"response": response}), 200

@app.route("/")
def home():
    return jsonify({
        "message": "Career AI backend running",
        "timestamp": datetime.utcnow().isoformat()
    }), 200

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=True
    )
