import os
from dotenv import load_dotenv

load_dotenv()

USE_GROQ = bool(os.getenv("GROQ_API_KEY"))
USE_GEMINI = bool(os.getenv("GEMINI_API_KEY"))


# =====================================================
# Main LLM Call (system + user prompt)
# =====================================================
def llm_call(system: str, user: str, temperature: float = 0.2) -> str:
    if USE_GROQ:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])

        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()

    if USE_GEMINI:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])

        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f"System:\n{system}\n\nUser:\n{user}"
            resp = model.generate_content(prompt)
            return resp.text.strip()
        except Exception as e:
            print("⚠️ Gemini error:", e)
            return "Sorry, Gemini API call failed."

    raise RuntimeError("❌ No valid API key found. Set GROQ_API_KEY or GEMINI_API_KEY.")


# =====================================================
# Simple Short Completion (no system prompt)
# Used for summaries, short answers, etc.
# =====================================================
def llm_simple_completion(prompt: str, temperature: float = 0.2) -> str:
    """A minimal helper to generate short text outputs."""

    # --- GROQ ---
    if USE_GROQ:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])

        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()

    # --- GEMINI ---
    if USE_GEMINI:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])

        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt)
            return resp.text.strip()
        except Exception as e:
            print("⚠️ Gemini error:", e)
            return "Sorry, Gemini API call failed."

    # --- NO API FOUND ---
    raise RuntimeError("❌ No valid LLM API key found for simple completion.")
