"""Small async wrapper around the Groq chat API."""

import os

from dotenv import load_dotenv
from groq import AsyncGroq, GroqError

load_dotenv()

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")


class LLMError(Exception):
    """Raised when the Groq API call fails."""


def get_client() -> AsyncGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "your_groq_api_key_here":
        raise LLMError("GROQ_API_KEY is not set. Add it to the .env file (see .env.example).")
    return AsyncGroq(api_key=api_key)


async def ask_llm(client: AsyncGroq, system_prompt: str, user_prompt: str) -> str:
    """Send one prompt to the model and return its text answer."""
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_completion_tokens=2000,
        )
    except GroqError as e:
        raise LLMError(f"Groq API call failed: {e}") from e

    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise LLMError("Groq returned an empty answer.")
    return text
