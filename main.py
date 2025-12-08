"""
Streamlit UI for Week-3:
- Upload resume
- Search jobs & show matched scores
- AI career assistant
- Company info (AmbitionBox)
"""

import streamlit as st
import requests
from bs4 import BeautifulSoup

st.set_page_config(page_title="Smart Career & Job Assistant", page_icon="💼", layout="wide")
st.title("💼 Smart Career & Job Assistant (Week 3)")
st.caption("Resume parsing, job matching, company info, and career AI assistant")

import os
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:5000")


tab1, tab2, tab3 = st.tabs(["🔍 Job Search", "💬 Career Assistant", "🏢 Company Info"])

# Utility to present job card neatly
def render_job(job):
    st.markdown(f"### [{job['title']}]({job['url']})")
    st.write(f"🏢 **Company:** {job['company']}")
    st.write(f"📍 **Location:** {job['location']}")
    st.write(f"💰 **Salary:** {job['salary'] if job['salary'] else 'Not disclosed'}")
    st.write(f"🌐 **Source:** {job['source']}")
    st.write(f"📊 **Match Score:** {job.get('match_score', 0)}%")
    if job.get("matched_skills"):
        st.write(f"✔ Matched skills: {', '.join(job['matched_skills'])}")
    st.write(f"📝 {job['description'][:300]}...")
    st.divider()

# ---------------- JOB SEARCH TAB ----------------
with tab1:
    st.header("🔍 Job Search & Resume Matcher")
    col1, col2 = st.columns([2, 1])
    with col1:
        query = st.text_input("Job Title / Skill", placeholder="e.g., Python Developer, Data Analyst")
        location = st.text_input("Location", placeholder="e.g., Bangalore, Hyderabad, Remote", value="India")
        per_page = st.selectbox("Results per page", [5, 10, 15, 20], index=2)
    with col2:
        st.markdown("### Resume")
        user_key = st.text_input("User Key (optional)", placeholder="a unique id to associate uploads", help="Use this to keep your resume & preferences tied")
        uploaded_file = st.file_uploader("Upload Resume (PDF)", type="pdf")
        if uploaded_file:
            st.info("Uploading and parsing resume...")
            try:
                files = {"file": uploaded_file.getvalue()}
                # Streamlit's uploaded file can be passed as raw bytes; but requests expects file-like tuple
                # We'll use requests with files=(name, bytes, mime)
                response = requests.post(
                    f"{BACKEND_URL}/upload_resume",
                    files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
                    data={"user_key": user_key} if user_key else None,
                    timeout=30
                )
                data = response.json()
                if response.status_code == 200:
                    st.success("Resume parsed successfully!")
                    st.session_state["resume_id"] = data.get("resume_id")
                    st.session_state["parsed_skills"] = data.get("skills", [])
                    st.write("Detected skills:", ", ".join(data.get("skills", [])) if data.get("skills") else "None detected")
                else:
                    st.error(data.get("error", "Failed to parse resume"))
            except Exception as e:
                st.error(f"Upload error: {e}")

    if st.button("Search & Match Jobs"):
        if not query:
            st.warning("Please enter job title")
        else:
            st.info("Searching for jobs and computing match scores...")
            params = {"title": query, "location": location, "per_page": per_page, "page": 1}
            # attach resume if we have one
            resume_id = st.session_state.get("resume_id")
            if resume_id:
                params["resume_id"] = resume_id
            else:
                # if user_key exists and they uploaded earlier, use that instead
                if user_key:
                    params["user_key"] = user_key

            try:
                res = requests.get(f"{BACKEND_URL}/match_jobs", params=params, timeout=30)
                if res.status_code == 200:
                    data = res.json()
                    jobs = data.get("jobs", [])
                    if not jobs:
                        st.warning("No jobs found.")
                    else:
                        for job in jobs:
                            render_job(job)
                else:
                    st.error(f"Error: {res.text}")
            except Exception as e:
                st.error(f"Error: {e}")

# ---------------- AI ASSISTANT TAB ----------------
with tab2:
    st.header("💬 AI Career Assistant")
    with st.form("ai_form", clear_on_submit=False):
        user_question = st.text_input("Ask a career question (you can include context e.g., 'Based on my resume...')", key="ai_input")
        ai_user_key = st.text_input("User Key (optional for contextualized answers)", value=user_key if "user_key" in locals() else "")
        submitted = st.form_submit_button("Ask AI")
        if submitted:
            if not user_question:
                st.warning("Please enter a question.")
            else:
                try:
                    payload = {"query": user_question}
                    if ai_user_key:
                        payload["user_key"] = ai_user_key
                    r = requests.post(f"{BACKEND_URL}/ask", json=payload, timeout=30)
                    ans = r.json().get("response", "No reply")
                    st.success("AI Response:")
                    st.write(ans)
                except Exception as e:
                    st.error(f"Error: {e}")

# ---------------- COMPANY INFO TAB ----------------
with tab3:
    st.header("🏢 Company Info (AmbitionBox + AI summary)")
    company_name = st.text_input("Company Name", placeholder="e.g., TCS, Infosys, Razorpay")
    if st.button("Search Company"):
        if not company_name:
            st.warning("Enter a company name.")
        else:
            try:
                r = requests.get(f"{BACKEND_URL}/company_info", params={"company": company_name}, timeout=20)
                if r.status_code == 200:
                    info = r.json()
                    st.markdown(f"### [{company_name}]({info.get('ambitionbox_url')})")
                    st.write(f"⭐ **AmbitionBox Rating:** {info.get('ambitionbox_rating') or 'N/A'}")
                    st.write(f"📝 **AmbitionBox Summary:** {info.get('ambitionbox_summary') or 'N/A'}")
                    st.write("🤖 **AI Summary / Tips:**")
                    st.write(info.get("ai_summary") or "No AI summary available.")
                else:
                    st.error("Could not fetch company info.")
            except Exception as e:
                st.error(f"Error: {e}")

# Footer
st.markdown("---")
st.caption("Week 3 - Resume parsing + job matching · Backend: Flask · Frontend: Streamlit")
