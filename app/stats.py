"""Live counters shown on the page (single event loop -> plain ints are safe).

Semantics (also documented in README):
  total_questions : every accepted question (empty / too-long / "still busy" requests are not counted)
  api_calls       : every REAL outbound Gemini request, retries included
  cache_hits      : answers served without a Gemini call of their own
                    (stored answer, or merged into an identical in-flight request)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Stats:
    total_questions: int = 0
    api_calls: int = 0
    cache_hits: int = 0
    # extra transparency (not required by the brief)
    coalesced: int = 0  # subset of cache_hits: merged into an in-flight identical request
    errors: int = 0  # Gemini failed after retries / system overloaded
    rate_limited: int = 0  # per-session rate limit hit

    def question(self) -> None:
        self.total_questions += 1

    def record_api_call(self) -> None:
        self.api_calls += 1

    def cache_hit(self, *, coalesced: bool = False) -> None:
        self.cache_hits += 1
        if coalesced:
            self.coalesced += 1

    def error(self) -> None:
        self.errors += 1

    def limited(self) -> None:
        self.rate_limited += 1

    def snapshot(self) -> dict:
        return asdict(self)
