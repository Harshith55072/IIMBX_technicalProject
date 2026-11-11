"""API-layer tests: real FastAPI app + real service/cache, fake LLM (no network, no quota)."""
import asyncio

import httpx
import pytest

from app.docs import load_docs
from app.llm import FakeLLM, LLMError, LLMResult
from app.main import _Unconfigured, create_app
from app.service import HelpdeskService

Q1 = "What is the minimum score needed to pass the course and get a Verified Certificate?"
FEE = "How much does the Verified Certificate track cost?"


@pytest.fixture(scope="module")
def ck():
    return load_docs()


def run(coro):
    return asyncio.run(coro)


def make_app(ck, llm=None, meta=None, **kw):
    llm = llm or FakeLLM()
    svc = HelpdeskService(llm, ck, **kw)
    return create_app(svc, meta), svc, llm


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_health(ck):
    async def go():
        app, _, _ = make_app(ck, meta={"configured": True, "model": "m", "docs": ["a.txt"]})
        async with client(app) as c:
            return await c.get("/health")

    r = run(go())
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "configured": True, "model": "m", "docs": ["a.txt"]}


def test_chat_returns_answer_sources_session_and_stats(ck):
    async def go():
        app, _, _ = make_app(ck)
        async with client(app) as c:
            return await c.post("/api/chat", json={"message": Q1})

    r = run(go())
    body = r.json()
    assert r.status_code == 200
    assert body["answer"] and body["sources"] == ["01_course_handbook.txt"]
    assert body["answerable"] is True and body["cached"] is False and body["via"] is None
    assert len(body["session_id"]) >= 8
    assert body["stats"]["total_questions"] == 1 and body["stats"]["api_calls"] == 1
    assert r.headers["cache-control"] == "no-store"


def test_repeat_is_cached_and_stats_endpoint_matches(ck):
    async def go():
        app, _, llm = make_app(ck)
        async with client(app) as c:
            a = (await c.post("/api/chat", json={"message": Q1})).json()
            b = (await c.post("/api/chat", json={"message": Q1, "session_id": a["session_id"]})).json()
            s = (await c.get("/api/stats")).json()
        return llm, a, b, s

    llm, a, b, s = run(go())
    assert llm.calls == 1
    assert b["cached"] is True and b["via"] == "exact" and b["session_id"] == a["session_id"]
    assert s["total_questions"] == 2 and s["api_calls"] == 1 and s["cache_hits"] == 1
    assert b["stats"] == s


@pytest.mark.parametrize(
    "payload",
    [{"message": ""}, {"message": "   "}, {"message": "x" * 501}, {"message": "x" * 6000}, {}, {"message": 5}],
)
def test_bad_input_is_friendly_400_and_not_counted(ck, payload):
    async def go():
        app, _, llm = make_app(ck)
        async with client(app) as c:
            r = await c.post("/api/chat", json=payload)
            s = (await c.get("/api/stats")).json()
        return r, s, llm

    r, s, llm = run(go())
    assert r.status_code == 400 and r.json()["message"]
    assert s["total_questions"] == 0 and llm.calls == 0


def test_invalid_session_id_is_replaced(ck):
    async def go():
        app, _, _ = make_app(ck)
        async with client(app) as c:
            return (await c.post("/api/chat", json={"message": Q1, "session_id": "../../etc"})).json()

    sid = run(go())["session_id"]
    assert sid != "../../etc" and len(sid) >= 8


@pytest.mark.parametrize(
    "kind,status,retry",
    [("rate_limited", 429, "30"), ("unavailable", 503, "10"), ("bad_response", 502, None), ("config", 500, None)],
)
def test_llm_errors_map_to_friendly_responses_and_are_not_cached(ck, kind, status, retry):
    async def go():
        llm = FakeLLM(fail_with=LLMError(kind, "boom: secret detail"))
        app, svc, _ = make_app(ck, llm)
        async with client(app) as c:
            r1 = await c.post("/api/chat", json={"message": Q1})
            r2 = await c.post("/api/chat", json={"message": Q1})
        return r1, r2, svc, llm

    r1, r2, svc, llm = run(go())
    assert r1.status_code == status and r1.headers.get("retry-after") == retry
    assert "secret detail" not in r1.text and r1.json()["message"]
    assert llm.calls == 2  # the failure was not cached
    assert svc.stats.errors == 2


def test_session_rate_limit_returns_429_with_retry_after(ck):
    async def go():
        app, _, _ = make_app(ck, session_rate_per_min=2)
        async with client(app) as c:
            out = []
            for i in range(3):
                out.append(await c.post("/api/chat", json={"message": f"question number {i} about quizzes?", "session_id": "session-abc-123"}))
        return out

    r = run(go())
    assert [x.status_code for x in r] == [200, 200, 429]
    assert int(r[2].headers["retry-after"]) >= 1


def test_double_click_second_request_gets_409(ck):
    async def go():
        app, _, llm = make_app(ck, FakeLLM(delay=0.2))
        async with client(app) as c:
            kw = {"session_id": "session-abc-123"}
            return await asyncio.gather(
                c.post("/api/chat", json={"message": Q1, **kw}),
                c.post("/api/chat", json={"message": Q1, **kw}),
            ), llm

    (a, b), llm = run(go())
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert llm.calls == 1


def test_many_students_same_question_one_gemini_call(ck):
    async def go():
        app, svc, llm = make_app(ck, FakeLLM(delay=0.2))
        async with client(app) as c:
            rs = await asyncio.gather(
                *[c.post("/api/chat", json={"message": Q1, "session_id": f"student-{i:04d}"}) for i in range(30)]
            )
        return rs, svc, llm

    rs, svc, llm = run(go())
    assert all(r.status_code == 200 for r in rs)
    assert llm.calls == 1
    s = svc.stats.snapshot()
    assert s["total_questions"] == 30 and s["api_calls"] == 1 and s["cache_hits"] == 29


def test_followup_in_same_session_gets_history(ck):
    seen = {}

    def responder(q, history):
        seen[q] = len(history)
        return LLMResult(True, "ok", ["01_course_handbook.txt"])

    async def go():
        app, _, _ = make_app(ck, FakeLLM(responder=responder))
        async with client(app) as c:
            a = (await c.post("/api/chat", json={"message": FEE})).json()
            await c.post("/api/chat", json={"message": "What about the audit track?", "session_id": a["session_id"]})

    run(go())
    assert seen[FEE] == 0 and seen["What about the audit track?"] == 2


def test_missing_api_key_is_friendly_and_app_still_serves(ck):
    async def go():
        app = create_app(HelpdeskService(_Unconfigured(), ck), {"configured": False, "model": "m", "docs": []})
        async with client(app) as c:
            return await c.get("/health"), await c.post("/api/chat", json={"message": Q1})

    h, r = run(go())
    assert h.status_code == 200 and h.json()["configured"] is False
    assert r.status_code == 500 and "not configured" in r.json()["message"]
    assert "GEMINI" not in r.text


def test_unexpected_error_hides_details(ck):
    def boom(q, history):
        raise RuntimeError("internal secret")

    async def go():
        app, _, _ = make_app(ck, FakeLLM(responder=boom))
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post("/api/chat", json={"message": Q1})

    r = run(go())
    assert r.status_code == 500 and "secret" not in r.text and r.json()["message"]


def test_frontend_page_is_served_with_required_ui(ck):
    async def go():
        app, _, _ = make_app(ck)
        async with client(app) as c:
            return await c.get("/"), await c.get("/health")

    page, health = run(go())
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    for needle in ("Total Questions", "Gemini API Calls", "Served from Cache", "/api/chat", "/api/stats"):
        assert needle in page.text
    assert health.status_code == 200  # API routes are not shadowed by the static mount
