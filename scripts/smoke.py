"""Dev smoke test: run questions through the REAL Gemini pipeline (uses API quota).

Usage: venv\\Scripts\\python scripts/smoke.py [N]   -> N = how many of the 12 sample questions (default: all)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.docs import load_docs  # noqa: E402
from app.llm import GeminiClient, LLMError, Turn  # noqa: E402

QUESTIONS = [
    "What is the minimum score needed to pass the course and get a Verified Certificate?",
    "how much do i need to score to pass dpm-101?",
    "What happens if I submit my Capstone project 1 day late?",
    "When is the Capstone Product Case due?",
    "Is the final exam open-book or closed-book, and when is it scheduled?",
    "Can I take the proctored final exam on my iPad or mobile phone?",
    "How much does the Verified Certificate track cost?",
    "FOLLOWUP:If I pay the Rs. 1,500 fee and drop the course in Week 2, how do I get a refund?",
    "How many weekly quizzes are there, and what if I miss one?",
    "Does this course teach SQL database indexing and Kubernetes deployment?",
    "Who won the cricket match last night?",
]


async def main(arg: str) -> None:
    # arg: "N" = first N questions, "@10" = only the 10th (1-based)
    picked = [QUESTIONS[int(arg[1:]) - 1]] if arg.startswith("@") else QUESTIONS[: int(arg)]
    s = get_settings()
    c = GeminiClient(s.gemini_api_key, s.gemini_model, load_docs(), history_turns=s.history_turns)
    history: list[Turn] = []
    print(f"model={s.gemini_model}\n")
    for q in picked:
        followup = q.startswith("FOLLOWUP:")
        q = q.removeprefix("FOLLOWUP:")
        if followup:  # Q9 follows Q8 (fee question) in the sample list
            history = [Turn("user", QUESTIONS[6]), Turn("assistant", "The Verified Certificate track costs Rs. 1,500.")]
        else:
            history = []
        try:
            r = await c.generate(q, history)
            print(f"Q: {q}\n   answerable={r.answerable} sources={r.sources}\n   {r.answer}\n")
        except LLMError as e:
            print(f"Q: {q}\n   LLMError[{e.kind}] -> {e.user_message}\n")
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else str(len(QUESTIONS))))
