# frontend/main.py
import os
import streamlit as st
import requests
import json
from datetime import datetime

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:5000")

st.set_page_config(page_title="AI Career Assistant (Track A)", layout="wide")
st.title("💼 AI Career Assistant — Full Project (Track A)")

# Sidebar for user key (simple auth) and resume info
st.sidebar.header("Profile & Context")
user_key = st.sidebar.text_input("User Key (unique id)", value=st.session_state.get("user_key",""))
if user_key:
    st.session_state["user_key"] = user_key

st.sidebar.markdown("### Resume")
st.sidebar.write("Upload a PDF resume to enable skill-based matching.")
uploaded_resume = st.sidebar.file_uploader("Upload Resume (PDF)", type=["pdf"])
if uploaded_resume:
    st.sidebar.info("Uploading...")
    files = {"file": (uploaded_resume.name, uploaded_resume.getvalue(), "application/pdf")}
    data = {"user_key": user_key} if user_key else {}
    try:
        r = requests.post(f"{BACKEND_URL}/upload_resume", files=files, data=data, timeout=60)
        if r.status_code == 200:
            resp = r.json()
            st.sidebar.success("Uploaded.")
            st.sidebar.write("Detected skills:", ", ".join(resp.get("skills",[])))
            st.session_state["resume_id"] = resp.get("resume_id")
            # Also store parsed_text in session for immediate use (optional)
            try:
                # get parsed text immediately so analyzer can use it without extra request
                gr = requests.get(f"{BACKEND_URL}/get_latest_resume", params={"resume_id": resp.get("resume_id")}, timeout=10)
                if gr.status_code == 200:
                    st.session_state["resume_text"] = gr.json().get("resume_text", "")
            except:
                pass
        else:
            st.sidebar.error(r.text)
    except Exception as e:
        st.sidebar.error(f"Upload failed: {e}")

if st.sidebar.button("Use last uploaded resume"):
    # fallback: do nothing — resume_id already in session_state if uploaded
    pass

st.sidebar.markdown("---")
st.sidebar.caption("Tip: Set a stable user key to persist saved jobs across sessions.")

tabs = st.tabs(["🔍 Job Search","📄 Resume Analyzer","🏢 Company Info","🤖 Career Mentor","🎓 Internships/Scholarships","💾 Saved Jobs"])

# ---- Job Search tab ----
with tabs[0]:
    st.header("🔍 Job Search & Matching")
    st.write("Use filters on the left and refine results. Results are ranked using skills, experience, salary, location & job type.")

    left, right = st.columns([3,1])
    with left:
        job_title = st.text_input("Job Title / Skill", placeholder="e.g., Data Analyst")
        location = st.text_input("Location", value="India")

        SALARY_BUCKETS = [
            "Any",
            "0 - 3 LPA",
            "3 - 6 LPA",
            "6 - 10 LPA",
            "10 - 20 LPA",
            "20+ LPA"
        ]
        salary_bucket = st.selectbox("Salary Range (LPA)", SALARY_BUCKETS)

        per_page = st.selectbox("Per page", [5,10,15,20], index=2)
        experience = st.selectbox("Experience (Years)", [0, 1, 2, 3, 4, 5, 8, 10], index=0)
        job_type = st.selectbox("Job Type", ["Any", "full-time", "internship", "remote"])

        search_btn = st.button("Search & Match", use_container_width=True)

    with right:
        st.markdown("### Context")
        resume_id = st.text_input("Resume ID (optional)", value=st.session_state.get("resume_id",""))
        if st.button("Clear resume context"):
            st.session_state.pop("resume_id", None)
            st.experimental_rerun()
        st.markdown("---")
        st.write("Quick tips:")
        st.write("- Upload resume in the sidebar for tailored skill matching.")
        st.write("- Use job type to prefer internships/remote roles.")

    if search_btn:
        if not job_title:
            st.warning("Please enter a job title or skill to search.")
        else:
            params = {
                "title": job_title,
                "location": location,
                "per_page": per_page,
                "page": 1,
                "experience": experience,
                "salary_bucket": salary_bucket,
                "job_type": job_type
            }

            if resume_id:
                params["resume_id"] = resume_id
            elif user_key:
                params["user_key"] = user_key

            try:
                r = requests.get(f"{BACKEND_URL}/match_jobs", params=params, timeout=30)
                data = r.json()
                jobs = data.get("jobs", [])
                st.info(f"Found {data.get('total',0)} jobs — displaying {len(jobs)}")
                for idx, j in enumerate(jobs):
                    with st.container():
                        header_cols = st.columns([0.85, 0.15])
                        header_cols[0].markdown(f"### [{j.get('title')}]({j.get('url')})")
                        header_cols[1].metric("Score", f"{j.get('match_score',0)}%")
                        st.write(f"**{j.get('company')}** • {j.get('location')} • {j.get('job_type')}")
                        with st.expander("Details & description", expanded=False):
                            st.write(j.get("description","")[:1500] + ("..." if len(j.get("description",""))>1500 else ""))
                            st.write(f"Source: {j.get('source')} | Posted: {j.get('posted_date')}")
                            st.write(f"Salary (raw): {j.get('salary') or 'N/A'} | Salary (LPA): {j.get('salary_lpa') or 'N/A'}")
                            if j.get("matched_skills"):
                                st.write("Matched skills:", ", ".join(j.get("matched_skills")))
                        a_col, b_col, c_col = st.columns([1,1,1])
                        with a_col:
                            if st.button("Save Job", key=f"save_{idx}_{j.get('url')}"):
                                payload = {"user_key": user_key, "job": j}
                                try:
                                    r2 = requests.post(f"{BACKEND_URL}/save_job", json=payload, timeout=10)
                                    if r2.status_code == 200:
                                        st.success("Saved!")
                                    else:
                                        st.error("Save failed")
                                except Exception as e:
                                    st.error(f"Save error: {e}")
                        with b_col:
                            if st.button("Apply (mark)", key=f"apply_{idx}_{j.get('url')}"):
                                try:
                                    r2 = requests.post(f"{BACKEND_URL}/save_job", json={"user_key": user_key, "job": j})
                                    if r2.status_code == 200:
                                        st.success("Saved & marked applied (locally tracked).")
                                    else:
                                        st.error("Apply failed")
                                except Exception as e:
                                    st.error(f"Apply error: {e}")
                        with c_col:
                            st.markdown(f"[Open Posting]({j.get('url')})")
                        st.markdown("---")

            except Exception as e:
                st.error(f"Error: {e}")

# ---- Resume Analyzer tab ----
with tabs[1]:
    st.header("📄 Resume Analyzer & Improvement")
    st.write("Upload or paste resume text. You can ask for 1) detected skills 2) AI suggestions to improve for a role.")

    # Auto-load resume text into the text area if available
    resume_text_val = st.session_state.get("resume_text", "")

    # If session does not have resume_text but resume_id or user_key exists, fetch it
    if not resume_text_val:
        params = {}
        if st.session_state.get("resume_id"):
            params["resume_id"] = st.session_state.get("resume_id")
        elif user_key:
            params["user_key"] = user_key

        if params:
            try:
                r = requests.get(f"{BACKEND_URL}/get_latest_resume", params=params, timeout=10)
                if r.status_code == 200:
                    resume_text_val = r.json().get("resume_text", "")
                    st.session_state["resume_text"] = resume_text_val
                    st.success("Using uploaded resume ✅")
            except:
                # silent fail — user can paste manually
                pass

    resume_text = st.text_area("Paste resume text (optional)", value=resume_text_val, height=250)
    jd = st.text_area("Paste job description (optional)", height=250)
    if st.button("Get resume improvement suggestions"):
        if not resume_text.strip():
            st.warning("Paste your resume text (or upload via sidebar).")
        else:
            payload = {"resume_text": resume_text, "job_description": jd}
            try:
                r = requests.post(f"{BACKEND_URL}/resume_improve", json=payload, timeout=30)
                if r.status_code == 200:
                    st.success("Suggestions (AI):")
                    st.write(r.json().get("advice"))
                else:
                    st.error("Failed to get suggestions.")
            except Exception as e:
                st.error(f"Error: {e}")

# ---- Company Info tab ----
with tabs[2]:
    st.header("🏢 Company Research")
    company = st.text_input("Company name (e.g., TCS, Infosys)")
    if st.button("Get Company Info"):
        if not company:
            st.warning("Enter a company")
        else:
            try:
                r = requests.get(f"{BACKEND_URL}/company_info", params={"company": company}, timeout=20)
                if r.status_code == 200:
                    d = r.json()
                    st.subheader(f"{d.get('company')}")
                    st.write("⭐ AmbitionBox Rating:", d.get("ambitionbox_rating") or "N/A")
                    st.write("📝 AmbitionBox Summary:", d.get("ambitionbox_summary") or "N/A")
                    st.write("🤖 AI Summary:", d.get("ai_summary") or "N/A")
                    st.markdown(f"[AmbitionBox]({d.get('ambitionbox_url')})")
                else:
                    st.error("Failed to fetch company info")
            except Exception as e:
                st.error(f"Error: {e}")

# ---- Career Mentor tab ----
with tabs[3]:
    st.header("🤖 AI Career Mentor")
    q = st.text_area("Ask a career question (e.g. 'How to become a Data Scientist?')")
    if st.button("Ask Mentor"):
        if not q:
            st.warning("Type a question")
        else:
            try:
                payload = {"query": q}
                if user_key:
                    payload["user_key"] = user_key
                r = requests.post(f"{BACKEND_URL}/ask", json=payload, timeout=30)
                st.write(r.json().get("response"))
            except Exception as e:
                st.error(f"Error: {e}")

# ---- Internships/Scholarships tab ----
with tabs[4]:
    st.header("🎓 Internships & Scholarships")
    q = st.text_input("Search internships/scholarships (keyword)")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Show internships"):
            try:
                r = requests.get(f"{BACKEND_URL}/internships", params={"q": q}, timeout=20)
                data = r.json()
                for it in data.get("items", []):
                    with st.expander(it["title"] + " — " + it["company"]):
                        st.write(it["company"], "|", it["location"], "| Stipend:", it["stipend"])
                        st.markdown(f"[Apply]({it['url']})")
                        st.write("Deadline:", it["deadline"])
            except Exception as e:
                st.error(e)
    with col2:
        if st.button("Show scholarships"):
            try:
                r = requests.get(f"{BACKEND_URL}/scholarships", params={"q": q}, timeout=20)
                data = r.json()
                for it in data.get("items", []):
                    with st.expander(it["title"] + " — " + it["provider"]):
                        st.write("Eligibility:", it["eligibility"])
                        st.markdown(f"[Link]({it['url']})")
                        st.write("Deadline:", it["deadline"])
            except Exception as e:
                st.error(e)

# ---- Saved Jobs tab ----
with tabs[5]:
    st.header("💾 Saved Jobs & Tracker")
    if user_key:
        if st.button("Refresh saved jobs"):
            try:
                r = requests.get(f"{BACKEND_URL}/get_saved_jobs", params={"user_key": user_key}, timeout=20)
                items = r.json().get("items",[])
                if not items:
                    st.info("No saved jobs yet.")
                for it in items:
                    with st.container():
                        st.subheader(it["job"].get("title"))
                        st.write(it["job"].get("company"), "|", it["job"].get("location"))
                        st.write("Saved at:", it["saved_at"], "| Applied:", it["applied"])
                        cols = st.columns([1,1])
                        if not it["applied"]:
                            if cols[0].button("Mark as applied", key=f"mark_apply_{it['id']}"):
                                requests.post(f"{BACKEND_URL}/apply_job", json={"saved_id": it["id"]})
                                st.success("Marked as applied")
                        if cols[1].button("Open posting", key=f"open_{it['id']}"):
                            st.markdown(f"[Open]({it['job'].get('url')})")
            except Exception as e:
                st.error(e)
    else:
        st.info("Set user key in sidebar to see saved jobs.")

st.markdown("---")
st.caption("Full project — Track A (Weeks 1–8) — Flask backend + Streamlit frontend")
