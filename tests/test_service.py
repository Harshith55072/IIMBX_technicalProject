import asyncio

import pytest

from app.docs import load_docs
from app.llm import FakeLLM, LLMError, LLMResult
from app.service import BadQuestion, Busy, HelpdeskService, TooFast

Q1 = "What is the minimum score needed to pass the course and get a Verified Certificate?"
Q3 = "how much do i need to score to pass dpm-101?"
FEE = "How much does the Verified Certificate track cost?"


@pytest.fixture(scope="module")
def ck():
    return load_docs()


def make(ck, llm=None, **kw):
    llm = llm or FakeLLM()
    return HelpdeskService(llm, ck, **kw), llm


def run(coro):
    return asyncio.run(coro)


def test_repeat_is_served_from_cache(ck):
    async def go():
        svc, llm = make(ck)
        a = await svc.ask("s1", Q1)
        b = await svc.ask("s2", Q1)  # exact repeat, different student
        return svc, llm, a, b

    svc, llm, a, b = run(go())
    assert llm.calls == 1 and not a.cached and b.cached and b.via == "exact"
    assert b.answer == a.answer and b.sources == a.sources
    assert svc.stats.snapshot()["total_questions"] == 2
    assert svc.stats.api_calls == 1 and svc.stats.cache_hits == 1


def test_rephrasing_hits_cache(ck):
    async def go():
        svc, llm = make(ck)
        await svc.ask("s1", Q1)
        return svc, llm, await svc.ask("s2", Q3)

    svc, llm, r = run(go())
    assert llm.calls == 1 and r.cached and r.via == "similar"


def test_different_numbers_are_not_merged(ck):
    async def go():
        svc, llm = make(ck)
        await svc.ask("s1", "What happens if I submit my Capstone 1 day late?")
        await svc.ask("s1", "What happens if I submit my Capstone 2 days late?")
        return llm

    assert run(go()).calls == 2


def test_negative_answers_are_cached(ck):
    nope = lambda q, h: LLMResult(False, "That is not mentioned in the course documents.", [])

    async def go():
        svc, llm = make(ck, FakeLLM(responder=nope))
        await svc.ask("a", "Who won the cricket match last night?")
        r = await svc.ask("b", "who won the cricket match last night")
        return llm, r

    llm, r = run(go())
    assert llm.calls == 1 and r.cached and not r.answerable


def test_errors_are_not_cached_and_counted(ck):
    async def go():
        llm = FakeLLM(fail_with=LLMError("rate_limited"))
        svc, _ = make(ck, llm)
        with pytest.raises(LLMError):
            await svc.ask("a", Q1)
        llm.fail_with = None
        r = await svc.ask("a", Q1)  # same question works once Gemini recovers
        return svc, llm, r

    svc, llm, r = run(go())
    assert llm.calls == 2 and not r.cached and svc.stats.errors == 1


def test_concurrent_identical_questions_share_one_call(ck):
    async def go():
        svc, llm = make(ck, FakeLLM(delay=0.05))
        rs = await asyncio.gather(*[svc.ask(f"s{i}", Q1 if i % 2 else Q3) for i in range(20)])
        return svc, llm, rs

    svc, llm, rs = run(go())
    assert llm.calls == 1
    assert len({r.answer for r in rs}) == 1
    s = svc.stats
    assert s.total_questions == 20 and s.api_calls == 1 and s.cache_hits == 19 and s.coalesced == 19


def test_double_click_same_session_is_blocked(ck):
    async def go():
        svc, llm = make(ck, FakeLLM(delay=0.05))
        t1 = asyncio.create_task(svc.ask("s1", Q1))
        await asyncio.sleep(0.01)
        with pytest.raises(Busy):
            await svc.ask("s1", Q1)
        await t1
        return svc, llm

    svc, llm = run(go())
    assert llm.calls == 1 and svc.stats.total_questions == 1  # the blocked click wasn't counted


def test_session_free_again_after_answer(ck):
    async def go():
        svc, _ = make(ck)
        await svc.ask("s1", Q1)
        return await svc.ask("s1", FEE)

    assert run(go())


def test_bad_questions_rejected_and_not_counted(ck):
    async def go():
        svc, llm = make(ck, max_question_chars=50)
        for bad in ["", "   ", "x" * 51]:
            with pytest.raises(BadQuestion):
                await svc.ask("s1", bad)
        return svc, llm

    svc, llm = run(go())
    assert svc.stats.total_questions == 0 and llm.calls == 0


def test_per_session_rate_limit_only_counts_api_bound_requests(ck):
    now = [0.0]

    async def go():
        svc, llm = make(ck, session_rate_per_min=3, clock=lambda: now[0])
        await svc.ask("s", "capstone due date")
        await svc.ask("s", "exam duration minutes")
        await svc.ask("s", "refund policy details")
        await svc.ask("s", "capstone due date")  # cache hit: allowed even at the limit
        with pytest.raises(TooFast) as e:
            await svc.ask("s", "audit track content")  # 4th API-bound request in a minute
        await svc.ask("other", "audit track content")  # other students unaffected
        now[0] = 61.0
        await svc.ask("s", "audit track content")  # window passed
        return svc, e.value

    svc, err = run(go())
    assert svc.stats.rate_limited == 1 and 1 <= err.retry_after <= 60


def test_overload_returns_friendly_error_instead_of_hanging(ck):
    async def go():
        svc, llm = make(ck, FakeLLM(delay=0.05), max_concurrent=1, max_queue=2, session_rate_per_min=100)
        qs = [f"unique topic number {i} about {w}" for i, w in enumerate(
            ["capstone", "quiz", "exam", "refund", "audit", "badge", "forum", "week"])]
        res = await asyncio.gather(*[svc.ask(f"s{i}", q) for i, q in enumerate(qs)], return_exceptions=True)
        return svc, llm, res

    svc, llm, res = run(go())
    ok = [r for r in res if not isinstance(r, Exception)]
    bad = [r for r in res if isinstance(r, LLMError)]
    assert len(ok) + len(bad) == 8 and bad and ok
    assert all(b.kind == "rate_limited" for b in bad)
    assert svc.stats.errors == len(bad)


def test_leader_disconnect_does_not_kill_shared_call(ck):
    async def go():
        svc, llm = make(ck, FakeLLM(delay=0.05))
        t1 = asyncio.create_task(svc.ask("s1", Q1))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(svc.ask("s2", Q3))
        await asyncio.sleep(0.01)
        t1.cancel()
        r2 = await t2
        return svc, llm, r2

    svc, llm, r2 = run(go())
    assert llm.calls == 1 and r2.answer


def test_concurrency_cap_respected(ck):
    peak = {"now": 0, "max": 0}

    async def slow_gen(question, history=()):
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        await asyncio.sleep(0.03)
        peak["now"] -= 1
        return LLMResult(True, "ok", ["01_course_handbook.txt"])

    async def go():
        llm = FakeLLM()
        llm.generate = slow_gen
        svc = HelpdeskService(llm, ck, max_concurrent=2, max_queue=50, session_rate_per_min=100)
        qs = [f"distinct question {i} regarding {w}" for i, w in enumerate(
            ["capstone", "quiz", "exam", "refund", "audit", "badge"])]
        await asyncio.gather(*[svc.ask(f"s{i}", q) for i, q in enumerate(qs)])

    run(go())
    assert peak["max"] == 2


# ---------------- follow-ups and history

def test_followup_gets_history_standalone_does_not(ck):
    seen = []

    def responder(q, h):
        seen.append((q, [t.text for t in h]))
        return LLMResult(True, f"answer to {q}", ["01_course_handbook.txt"])

    async def go():
        svc, llm = make(ck, FakeLLM(responder=responder))
        await svc.ask("s", FEE)
        await svc.ask("s", "How do I get a refund for it?")  # follow-up -> gets history
        await svc.ask("s", "When is the Capstone Product Case due?")  # standalone -> no history

    run(go())
    assert seen[0][1] == []
    assert any(FEE == t for t in seen[1][1])
    assert seen[2][1] == []


def test_followup_cached_only_with_same_context(ck):
    async def go():
        svc, llm = make(ck)
        await svc.ask("A", FEE)
        await svc.ask("A", "How do I get a refund for it?")
        await svc.ask("B", FEE)  # cache hit
        r = await svc.ask("B", "How do I get a refund for it?")  # same context -> hit
        await svc.ask("C", "When is the capstone due?")
        await svc.ask("C", "How do I get a refund for it?")  # different context -> miss
        return llm, r

    llm, r = run(go())
    assert r.cached and llm.calls == 4  # FEE, refund|FEE, capstone, refund|capstone


def test_followup_without_history_is_not_cached(ck):
    async def go():
        svc, llm = make(ck)
        await svc.ask("A", "Is it open-book?")
        await svc.ask("B", "Is it open-book?")
        return llm

    assert run(go()).calls == 2


def test_standalone_question_reuses_cache_mid_conversation(ck):
    async def go():
        svc, llm = make(ck)
        await svc.ask("A", "When is the Capstone Product Case due?")
        await svc.ask("B", FEE)
        r = await svc.ask("B", "When is the Capstone Product Case due?")
        return llm, r

    llm, r = run(go())
    assert r.cached and llm.calls == 2


def test_history_is_bounded(ck):
    async def go():
        svc, _ = make(ck, history_turns=2, session_rate_per_min=100)
        for i in range(10):
            await svc.ask("s", f"unique question {i} about topic{i} quiz")
        return svc._sessions["s"]

    assert len(run(go()).history) == 4
