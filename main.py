import streamlit as st
import requests
from bs4 import BeautifulSoup

st.set_page_config(page_title="AI Job Search Assistant", page_icon="💼", layout="centered")

st.title("💼 Job Search AI Assistant")
st.write("An AI tool to help you find relevant job openings easily.")

# --- Search Bar ---
query = st.text_input("🔍 Enter job title or skill:", placeholder="e.g., Python Developer")
location = st.text_input("📍 Location:", placeholder="e.g., Bengaluru, India")

if st.button("Search Jobs"):
    if not query or not location:
        st.warning("Please enter both job title and location.")
    else:
        st.info("Fetching jobs... Please wait.")
        try:
            url = f"https://in.indeed.com/jobs?q={query}&l={location}"
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(response.text, "html.parser")

            results = soup.find_all("div", class_="job_seen_beacon")

            if results:
                for job in results[:10]:
                    title = job.find("h2").text.strip() if job.find("h2") else "No title"
                    company = job.find("span", class_="companyName")
                    company = company.text.strip() if company else "Unknown Company"
                    link = job.find("a")["href"] if job.find("a") else "#"

                    st.markdown(f"### [{title}](https://in.indeed.com{link})")
                    st.write(f"**Company:** {company}")
                    st.write("---")
            else:
                st.warning("No jobs found. Try a different search.")
        except Exception as e:
            st.error(f"An error occurred: {e}")

# --- Ask AI Section ---
st.markdown("## 🤖 Ask AI Career Assistant")
st.write("You can ask for career guidance, resume tips, or interview advice.")

user_question = st.text_input("💬 Ask your question:", placeholder="e.g., How can I improve my resume for a data analyst role?")

if st.button("Ask AI"):
    if not user_question:
        st.warning("Please enter a question for the AI assistant.")
    else:
        try:
            # Backend Flask server endpoint
            response = requests.post(
                "http://127.0.0.1:5000/ask",
                json={"query": user_question},
                timeout=15
            )

            if response.status_code == 200:
                reply = response.json().get("response", "No response from AI.")
                st.success(reply)
            else:
                st.error(f"Error: {response.status_code} - {response.text}")
        except Exception as e:
            st.error(f"Failed to connect to AI server: {e}")
