# llm_test.py
from llm_helper import llm_call

if __name__ == "__main__":
    print("🔍 Testing LLM connection...\n")

    system_prompt = "You are a helpful assistant."
    user_prompt = "Write a short paragraph about roman reigns in education."

    response = llm_call(system_prompt, user_prompt)

    print("\n🧠 Model Response:\n")
    print(response)
