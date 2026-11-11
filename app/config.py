"""Central settings, read from environment / .env (never hard-code secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    max_concurrent_llm: int
    session_rate_per_min: int
    max_question_chars: int
    history_turns: int
    max_queue: int

def get_settings() -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest").strip(),
        max_concurrent_llm=_int("MAX_CONCURRENT_LLM", 3),
        session_rate_per_min=_int("SESSION_RATE_PER_MIN", 12),
        max_question_chars=_int("MAX_QUESTION_CHARS", 500),
        history_turns=_int("HISTORY_TURNS", 3),
        max_queue=_int("MAX_QUEUE", 40),
    )
