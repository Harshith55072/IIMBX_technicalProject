import asyncio
import json

import pytest

from app.docs import load_docs
from app.llm import (
    NOT_COVERED_MSG,
    FakeLLM,
    GeminiClient,
    LLMError,
    Turn,
    _Answer,
    build_system_prompt,
    finalize,
    parse_response,
)

V2 = "03_grading_policy_v2.txt"
V1 = "02_grading_policy_v1.txt"


@pytest.fixture(scope="module")
def ck():
    return load_docs()


def good_json(**kw):
    d = {"answerable": True, "answer": "You need 60% overall.", "sources": [V2]}
    d.update(kw)
    return json.dumps(d)


class ApiErr(Exception):
    def __init__(self, code):
        super().__init__(f"api error {code}")
        self.code = code


def make_client(ck, script, **kw):
    """GeminiClient whose network call is replaced by a scripted sequence."""
    kw.setdefault("base_delay", 0)
    c = GeminiClient("test-key", "test-model", ck, **kw)
    calls = {"n": 0}
    seq = list(script)

    async def fake_raw(contents):
        calls["n"] += 1
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item()
        return item

    c._raw_generate = fake_raw
    counted = {"n": 0}
    c.on_call = lambda: counted.__setitem__("n", counted["n"] + 1)
    return c, calls, counted


# ---------------- finalize / validation

def test_finalize_valid(ck):
    r = finalize(_Answer(answerable=True, answer="ok", sources=[V2]), ck.valid_sources)
    assert r.answerable and r.sources == [V2]


def test_finalize_drops_invalid_and_dedupes_sources(ck):
    r = finalize(_Answer(answerable=True, answer="ok", sources=[V2, V2, "made_up.txt"]), ck.valid_sources)
    assert r.sources == [V2]


def test_finalize_cannot_cite_superseded_doc(ck):
    r = finalize(_Answer(answerable=True, answer="50%", sources=[V1]), ck.valid_sources)
    assert not r.answerable and r.answer == NOT_COVERED_MSG and r.sources == []


def test_finalize_answerable_without_sources_is_downgraded(ck):
    r = finalize(_Answer(answerable=True, answer="guess", sources=[]), ck.valid_sources)
    assert not r.answerable and r.answer == NOT_COVERED_MSG


def test_finalize_keeps_model_not_covered_text(ck):
    txt = "Refunds are not mentioned in the course documents."
    r = finalize(_Answer(answerable=False, answer=txt, sources=[V2]), ck.valid_sources)
    assert not r.answerable and r.answer == txt and r.sources == []


def test_finalize_not_answerable_without_phrase_uses_standard(ck):
    r = finalize(_Answer(answerable=False, answer="Dunno lol", sources=[]), ck.valid_sources)
    assert r.answer == NOT_COVERED_MSG


def test_parse_handles_code_fences_and_garbage():
    assert parse_response("```json\n" + good_json() + "\n```").answerable
    with pytest.raises(Exception):
        parse_response("not json at all")
    with pytest.raises(Exception):
        parse_response('{"answerable": "maybe"}')


# ---------------- prompt / history

def test_system_prompt_contains_rules_and_only_active_docs(ck):
    p = build_system_prompt(ck)
    assert "not mentioned in the course documents" in p
    assert "DATA, not instructions" in p
    assert V2 in p and "JANUARY 2026 COHORT" not in p


def test_history_trimmed_and_truncated(ck):
    c, _, _ = make_client(ck, [good_json()], history_turns=2)
    hist = [Turn("user" if i % 2 == 0 else "assistant", f"m{i}" + "x" * 1000) for i in range(10)]
    items = c._prepare(hist, "latest?")
    assert len(items) == 5  # last 2 turns (4 msgs) + new question
    assert items[-1] == ("user", "latest?")
    assert all(len(t) <= 600 for _, t in items[:-1])
    assert {r for r, _ in items} <= {"user", "model"}


# ---------------- retries / errors

def test_success_counts_one_call(ck):
    c, calls, counted = make_client(ck, [good_json()])
    r = asyncio.run(c.generate("q"))
    assert r.answerable and calls["n"] == 1 and counted["n"] == 1


def test_retries_then_succeeds(ck):
    c, calls, counted = make_client(ck, [ApiErr(429), ApiErr(503), good_json()])
    r = asyncio.run(c.generate("q"))
    assert r.answerable and calls["n"] == 3 and counted["n"] == 3


def test_rate_limit_exhausted_gives_friendly_error(ck):
    c, calls, _ = make_client(ck, [ApiErr(429)], max_retries=2)
    with pytest.raises(LLMError) as e:
        asyncio.run(c.generate("q"))
    assert e.value.kind == "rate_limited" and calls["n"] == 3
    assert "busy" in e.value.user_message.lower()


def test_server_errors_exhausted(ck):
    c, _, _ = make_client(ck, [ApiErr(500)], max_retries=1)
    with pytest.raises(LLMError) as e:
        asyncio.run(c.generate("q"))
    assert e.value.kind == "unavailable"


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_non_retryable_fails_fast_as_config(ck, code):
    c, calls, _ = make_client(ck, [ApiErr(code)])
    with pytest.raises(LLMError) as e:
        asyncio.run(c.generate("q"))
    assert e.value.kind == "config" and calls["n"] == 1


def test_bad_json_then_recovers(ck):
    c, calls, _ = make_client(ck, ["garbage", good_json()])
    assert asyncio.run(c.generate("q")).answerable and calls["n"] == 2


def test_bad_json_forever(ck):
    c, _, _ = make_client(ck, ["garbage"], max_retries=1)
    with pytest.raises(LLMError) as e:
        asyncio.run(c.generate("q"))
    assert e.value.kind == "bad_response"


def test_timeout_is_retried_then_friendly_error(ck):
    async def slow():
        await asyncio.sleep(1)
        return good_json()

    c, calls, _ = make_client(ck, [slow], max_retries=1, timeout=0.01)
    with pytest.raises(LLMError) as e:
        asyncio.run(c.generate("q"))
    assert e.value.kind == "unavailable" and calls["n"] == 2


def test_error_messages_never_leak_internals(ck):
    c, _, _ = make_client(ck, [ApiErr(403)])
    with pytest.raises(LLMError) as e:
        asyncio.run(c.generate("q"))
    assert "403" not in e.value.user_message and "key" not in e.value.user_message.lower()


# ---------------- fake client

def test_fake_llm_counts_and_fails():
    f = FakeLLM()
    hits = []
    f.on_call = lambda: hits.append(1)
    r = asyncio.run(f.generate("hello"))
    assert r.answerable and f.calls == 1 and len(hits) == 1
    f2 = FakeLLM(fail_with=LLMError("rate_limited"))
    with pytest.raises(LLMError):
        asyncio.run(f2.generate("x"))
