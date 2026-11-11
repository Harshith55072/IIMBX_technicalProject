import pytest

from app.cache import AnswerCache, is_followup, make_keys, normalize_text, tokens
from app.llm import LLMResult

FP = "fp1"


def sig(q):
    return make_keys(q, FP).sig


# ------------- the sample-question pairs that SHOULD match

def test_q1_q2_q3_same_signature():
    q1 = "What is the minimum score needed to pass the course and get a Verified Certificate?"
    q3 = "how much do i need to score to pass dpm-101?"
    assert tokens(q1) == tokens(q3) == {"score", "pass"}
    assert sig(q1) == sig(q3)


def test_exact_repeat_and_case_punctuation():
    a = make_keys("When is the Capstone Product Case due?", FP)
    b = make_keys("when is the capstone product case due", FP)
    assert a.exact == b.exact


@pytest.mark.parametrize(
    "a,b",
    [
        ("When is the Capstone Product Case due?", "What is the deadline for the capstone project?"),
        ("How much does the Verified Certificate track cost?", "what is the fee for the verified track"),
        ("Can I take the final exam on my iPad?", "Can I take the final exam on a tablet"),
        ("What happens if I submit my Capstone project 1 day late?",
         "what if my capstone is submitted 1 day late"),
    ],
)
def test_rephrasings_match(a, b):
    assert sig(a) == sig(b) and sig(a) is not None


# ------------- near-misses that MUST NOT match (a false hit = a wrong answer)

@pytest.mark.parametrize(
    "a,b",
    [
        ("What happens if I submit my Capstone 1 day late?", "What happens if I submit my Capstone 2 days late?"),
        ("What happens if I submit the capstone late?", "What happens if I submit a quiz late?"),
        ("When is the capstone due?", "When is the final exam?"),
        ("What score do I need to pass?", "What score do I need for distinction?"),
        ("What is the minimum score to pass?", "What is the maximum score?"),
        ("Can I take the exam on my iPad?", "Can I take the exam on my phone?"),
        ("Is the exam open-book?", "Is the exam closed-book?"),
        ("Is the exam open-book?", "Is the exam not open-book?"),
        ("How much does the verified track cost?", "How much does the audit track cost?"),
        ("What is the pass mark for the final exam?", "What is the pass mark for the course?"),
        ("How do I get a refund?", "How much is the fee?"),
        ("What is 60% of the score?", "What is 50% of the score?"),
    ],
)
def test_near_misses_do_not_match(a, b):
    assert sig(a) != sig(b)


def test_numbers_and_currency_normalised():
    assert "1500" in tokens("I paid Rs. 1,500 for the fee")
    assert "60%" in tokens("need 60 percent to pass")


def test_empty_signature_falls_back_to_exact_only():
    k = make_keys("hello please", FP)
    assert k.sig is None and k.flight == k.exact


def test_docs_fingerprint_and_context_isolate_keys():
    assert make_keys("pass score", "v1").sig != make_keys("pass score", "v2").sig
    assert make_keys("pass score", FP, ctx="abc").sig != make_keys("pass score", FP).sig


# ------------- follow-up detection

@pytest.mark.parametrize(
    "q",
    ["Is it open-book?", "what about the audit track?", "why?", "And the capstone?",
     "Can I do that on a phone?", "refund?", "ok thanks"],
)
def test_followups_detected(q):
    assert is_followup(q)


@pytest.mark.parametrize(
    "q",
    [
        "What is the minimum score needed to pass the course and get a Verified Certificate?",
        "If I pay the Rs. 1,500 fee and drop the course in Week 2, how do I get a refund?",
        "When is the Capstone Product Case due?",
        "Who won the cricket match last night?",
    ],
)
def test_standalone_detected(q):
    assert not is_followup(q)


# ------------- store

def test_lru_eviction_and_hit_kind():
    c = AnswerCache(max_entries=4)
    r = LLMResult(True, "a", ["x"])
    k1 = make_keys("capstone due date", FP)
    c.put(k1, r)
    assert c.get(make_keys("capstone due date", FP))[1] == "exact"
    assert c.get(make_keys("when is the capstone due", FP))[1] == "similar"
    for i in range(5):
        c.put(make_keys(f"unique question number {i} about quiz", FP), r)
    assert len(c) <= 4
    assert c.get(k1) is None  # oldest evicted


def test_normalize_text_basics():
    assert normalize_text("Can't I?  DPM-101 ") == "can not i"
