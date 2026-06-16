import os
import argparse
from openai import OpenAI

# ===== Simple toggles =====
DEFAULT_MODE = "chat"  # "chat" or "responses"
DEFAULT_MODEL = "Claude-Sonnet-4.6"

DEFAULT_SYSTEM_PROMPT = """
You are a senior Python engineer helping build a production-grade Kalshi trading app.

Requirements:
- Target environment: Windows + Python 3.14 + VS Code.
- Prefer simple, clean solutions over over-engineered abstractions.
- Return complete working code blocks/files when asked.
- Use robust error handling, retries, and clear logging.
- Keep async code indentation and structure correct.
- Add practical risk controls (position limits, max loss per day, kill switch, no duplicate orders).
- Separate concerns cleanly: config, API client, strategy, execution, risk, logging.
- Never fabricate API endpoints/fields; if uncertain, say so clearly.
- Keep explanations concise and implementation-focused.
"""

def ask_chat(client: OpenAI, model: str, system_prompt: str, prompt: str) -> str:
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": prompt},
        ],
        n=1,  # Poe requires n=1
    )
    return completion.choices[0].message.content or ""

def ask_responses(client: OpenAI, model: str, prompt: str) -> str:
    response = client.responses.create(
        model=model,
        input=prompt,
    )
    return response.output_text or ""

def main():
    # Simple manual env check if .env exists
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v.strip().strip('"')

    api_key = os.getenv("POE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing POE_API_KEY in .env")

    parser = argparse.ArgumentParser(description="Ask Poe from VS Code")
    parser.add_argument("--prompt", required=True, help="Prompt text")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Poe model/bot name")
    parser.add_argument("--mode", choices=["chat", "responses"], default=DEFAULT_MODE)
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT, help="System prompt (chat mode)")
    args = parser.parse_args()

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.poe.com/v1",
    )

    if args.mode == "responses":
        text = ask_responses(client, args.model, args.prompt)
    else:
        text = ask_chat(client, args.model, args.system, args.prompt)

    print("\n=== Poe Reply ===\n")
    print(text.strip())
    print("\n=================\n")

if __name__ == "__main__":
    main()