"""Check Gemini connectivity without printing credentials or request text."""

import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types


def main() -> int:
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        print("Set GEMINI_API_KEY in .env or the environment.", file=sys.stderr)
        return 1

    model = os.getenv("GEMMA_MODEL", "gemma-4-26b-a4b-it")
    try:
        with genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30_000)) as client:
            print("Available Gemma models:")
            for available in client.models.list():
                name = available.name or ""
                if "gemma" in name.lower():
                    print(name)
            result = client.models.generate_content(
                model=model,
                contents="Reply with OK",
                config=types.GenerateContentConfig(temperature=0),
            )
            if not result.text:
                print("Gemma returned no text.", file=sys.stderr)
                return 1
            print(result.text)
    except Exception:
        # SDK exception strings can contain request details; never print them.
        print("Gemini smoke test failed. Check the key, model access, and network.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
