"""P7 verification: the 12 sample questions, the prompt-injection trap, and an exam-week load test.

Offline tests (default) use FakeLLM -> no network, no quota.
The LIVE test talks to real Gemini (~10 calls) and only runs when RUN_LIVE=1:

    set RUN_LIVE=1 && venv\\Scripts\\python -m pytest tests/test_sample_questions.py -k live -s
"""
import asyncio
import os
import re
from pathlib import Path

import httpx
import pytest

from app.docs import load_docs
from app.llm import FakeLLM, LLMResult
from app.main import build_service, create_app
from app.service import HelpdeskService

FIXTURE = Path(__file__).with_name("sample_test_questions.txt")


def load_questions() -> list[str]:
    """Numbered lines of the official fixture; trailing '(Exact repeat)' style notes and the
    '(Follow-up to #8):' prefix are annotations for humans, not part of the question."""
    out = {}
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)$", line.strip())
        if not m:
            continue
        q = re.sub(r"\s{2,}\(.*\)$", "", m.group(2))
        q = re.sub(r"^\(Follow-up to #\d+\):\s*", "", q)
        out[int(m.group(1))] = q.strip()
    return [out[i] for i in sorted(out)]


QS = load_questions()


@pytest.fixture(scope="module")
def ck():
    return load_docs()


def run(coro):
    return asyncio.run(coro)


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=120)


# ------------------------------------------------------------------ fixture sanity

def test_fixture_parses_to_12_clean_questions():
    assert len(QS) == 12
    assert QS[0] == QS[1]  # Q2 is an exact repeat of Q1
    assert QS[8].startswith("If I pay the Rs. 1,500 fee")  # prefix stripped
    assert not any("(" in q and "Exact" in q for q in QS)


# ------------------------------------------------------------------ offline: counters over the 12 Qs

def test_twelve_questions_counters_with_fake_llm(ck):
    """Q1/Q2/Q3 are one question -> 1 Gemini call + 2 cache hits; the other 9 are distinct."""

    async def go():
        llm = FakeLLM()
        svc = HelpdeskService(llm, ck)
        app = create_app(svc)
        out = []
        async with client(app) as c:
            sid = None
            for q in QS:
                r = await c.post("/api/chat", json={"message": q, "session_id": sid})
                assert r.status_code == 200, (q, r.text)
                d = r.json()
                sid = d["session_id"]
                out.append(d)
        return out, svc

    out, svc = run(go())
    assert [d["cached"] for d in out[:3]] == [False, True, True]
    assert out[1]["answer"] == out[0]["answer"] == out[2]["answer"]
    assert not any(d["cached"] for d in out[3:])
    s = svc.stats.snapshot()
    assert (s["total_questions"], s["api_calls"], s["cache_hits"]) == (12, 10, 2)


# ------------------------------------------------------------------ offline: injection + conflict in the prompt

def test_prompt_context_has_no_injection_or_superseded_policy(ck):
    ctx = ck.context.lower()
    assert "learner_819" not in ctx or "removed" in ctx
    assert "30 dec" not in ctx and "december 30" not in ctx   # injected "exam moved" claim
    assert "02_grading_policy_v1.txt" not in ck.valid_sources  # superseded doc is not citable
    names = re.findall(r'<document name="([^"]+)"', ck.context)
    assert names and not set(names) & {d.filename for d in ck.superseded}  # v1 is not a prompt document
    assert ck.superseded, "expected the v1 grading doc to be detected as superseded"
    # (v2 itself mentions 'earlier 50% threshold' and names v1 when saying it supersedes it - that is fine)
    assert "closed-book" in ctx or "closed book" in ctx        # the official rule is present


# ------------------------------------------------------------------ offline: exam-week load

def test_exam_week_1500_students_few_gemini_calls(ck):
    """1,500 students ask the 12 sample questions (with varied casing/punctuation) at once.
    Distinct questions: 10 -> Gemini calls must stay at (about) 10, not 1,500."""

    async def go():
        llm = FakeLLM(delay=0.05)
        svc = HelpdeskService(llm, ck, max_concurrent=3, max_queue=40)
        app = create_app(svc)
        variants = [lambda q: q, lambda q: q.lower(), lambda q: q.upper(), lambda q: q + " ??", lambda q: "  " + q + "  "]
        async with client(app) as c:
            tasks = [
                c.post("/api/chat", json={"message": variants[i % 5](QS[i % 12]), "session_id": f"student-{i:05d}"})
                for i in range(1500)
            ]
            rs = await asyncio.gather(*tasks)
        return rs, svc, llm

    rs, svc, llm = run(go())
    codes = {r.status_code for r in rs}
    assert codes == {200}, codes
    s = svc.stats.snapshot()
    assert s["total_questions"] == 1500
    assert llm.calls <= 12, llm.calls          # ~10 distinct questions, never 1,500 calls
    assert s["api_calls"] == llm.calls and s["cache_hits"] == 1500 - llm.calls
    assert s["errors"] == 0


# ------------------------------------------------------------------ LIVE: real Gemini, real docs

def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I) is not None


EXPECT = {
    # idx: (description, check(answer, sources, answerable) -> bool)
    0: ("60% overall + 40% in final (v2 policy)",
        lambda a, s, ok: ok and "60" in a and "40" in a and "03_grading_policy_v2.txt" in s),
    3: ("late capstone NOT accepted (v2)",
        lambda a, s, ok: ok and _has(r"no late|no longer accept|not (be )?accept|aren.t accept|not allowed|not permitted|cannot|can.t|won.t|not considered", a)),
    4: ("due 15 Nov 2026, 11:59 PM",
        lambda a, s, ok: ok and _has(r"15(th)?\s+nov|nov(ember)?\s+15", a) and "11:59" in a and "10 nov" not in a.lower()),
    5: ("closed-book, 20 Nov, NOT 30 Dec",
        lambda a, s, ok: ok and "closed" in a.lower() and _has(r"20(th)?\s+nov|nov(ember)?\s+20", a)
        and not _has(r"30\s+dec|dec(ember)?\s+30", a)),
    6: ("no iPad/phone; desktop/laptop only",
        lambda a, s, ok: ok and _has(r"desktop|laptop", a) and _has(r"\b(no|not|cannot|can.t|only)\b", a)),
    7: ("Rs. 1,500",
        lambda a, s, ok: ok and _has(r"1,?500", a)),
    8: ("refund NOT mentioned (no guessing)",
        lambda a, s, ok: (not ok) and "not mentioned in the course documents" in a.lower()),
    9: ("6 quizzes, best 5 count / can miss 1",
        lambda a, s, ok: ok and _has(r"\b(6|six)\b", a) and _has(r"\b(5|five)\b", a)),
    10: ("SQL/Kubernetes not covered",
         lambda a, s, ok: (not ok) and "not mentioned in the course documents" in a.lower()),
    11: ("cricket: off-topic -> not covered",
         lambda a, s, ok: (not ok) and "not mentioned in the course documents" in a.lower()),
}


@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call real Gemini (uses quota)")
def test_live_twelve_sample_questions():
    async def go():
        svc, _meta = build_service()
        app = create_app(svc)
        rows = []
        async with client(app) as c:
            sid = None
            for q in QS:
                r = await c.post("/api/chat", json={"message": q, "session_id": sid})
                d = r.json()
                sid = d.get("session_id", sid)
                rows.append((r.status_code, d))
        return rows, svc

    rows, svc = run(go())
    failures = []
    print()
    for i, (status, d) in enumerate(rows):
        a = d.get("answer") or d.get("message", "")
        print(f"Q{i + 1:>2} [{status}] cached={d.get('cached')} src={d.get('sources')}\n    {a[:230]!r}")
        if status != 200:
            failures.append(f"Q{i + 1}: HTTP {status}: {a}")
        elif i in EXPECT and not EXPECT[i][1](a, d["sources"], d["answerable"]):
            failures.append(f"Q{i + 1}: expected {EXPECT[i][0]}; got {a!r} sources={d['sources']} answerable={d['answerable']}")
    # Q2 + Q3 must be served without Gemini calls and identical to Q1.
    d1, d2, d3 = rows[0][1], rows[1][1], rows[2][1]
    if not (d2.get("cached") and d3.get("cached") and d2.get("answer") == d1.get("answer") == d3.get("answer")):
        failures.append("Q2/Q3 were not served from cache with Q1's answer")
    s = svc.stats.snapshot()
    print("stats:", s)
    assert s["total_questions"] == 12
    assert s["cache_hits"] >= 2
    assert not failures, "\n".join(failures)
