# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from llm_helper import llm_call

app = Flask(__name__)
CORS(app)  # ✅ allows Streamlit (port 8501) to access this API

@app.route("/")
def home():
    return jsonify({"message": "Flask AI Job Assistant Backend is running ✅"})

@app.route("/ask", methods=["POST"])
def ask_ai():
    data = request.get_json()
    user_query = data.get("query", "")
    if not user_query.strip():
        return jsonify({"response": "Please provide a valid question."})
    
    system_prompt = (
        "You are an intelligent career assistant that helps users find jobs, "
        "improve resumes, and prepare for interviews."
    )
    reply = llm_call(system_prompt, user_query)
    return jsonify({"response": reply})

if __name__ == "__main__":
    app.run(debug=True)
