"""Cache layer: question normalisation, a conservative "same question" signature, LRU store.

Two lookups, both local and free (no Gemini call):
  exact : the normalised question text            -> catches exact repeats (even with different case/punctuation)
  sig   : a canonical set of meaningful words     -> catches rephrasings
          "What is the minimum score needed to pass ... Verified Certificate?"
          "how much do i need to score to pass dpm-101?"        -> both {pass, score}

The signature is deliberately strict: words that change the meaning (numbers, capstone vs quiz,
exam, late, refund, audit, distinction, not, ...) are KEPT, so "1 day late" never matches
"2 days late" and "capstone" never matches "quiz". Cost of a miss = one extra API call.
Cost of a false hit = a wrong answer, so we prefer misses.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from .llm import LLMResult

# ------------------------------------------------------------------ text normalisation

_PHRASES = [
    (r"(?<=\d),(?=\d{3}\b)", ""),  # 1,500 -> 1500
    (r"\b(?:rs|inr)\.?\s*(?=\d)", " "),  # "Rs. 1500" -> "1500"
    (r"\u20b9", " "),
    (r"(\d)\s*(?:%|percent|per\s*cent|percentage)", r"\1% "),  # "60 percent" -> "60%"
    (r"\bdpm[\s\-]*101\b", " "),  # the course code is in every question
    (r"\bcapstone\s+(?:product\s+)?(?:case|project)\b|\bproduct\s+case\b", " capstone "),
    (r"\bopen[\s\-]*book\b", " openbook "),
    (r"\bclosed?[\s\-]*book\b", " closedbook "),
    (r"\bmoney\s+back\b", " refund "),
    (r"\bat\s+least\b", " "),
    (r"\bcan't\b|\bcannot\b", " can not "),
    (r"\bwon't\b", " will not "),
    (r"n't\b", " not"),
    (r"'s\b", ""),
]


def _base(q: str) -> str:
    s = q.lower().replace("\u2019", "'")
    for pat, rep in _PHRASES:
        s = re.sub(pat, rep, s)
    return re.sub(r"[^a-z0-9%\s]", " ", s)


def normalize_text(q: str) -> str:
    return " ".join(_base(q).split())


# ------------------------------------------------------------------ canonical tokens

_SYNONYMS: dict[str, list[str]] = {
    "score": ["score", "scores", "mark", "marks", "grade", "grades", "percent", "percentage", "points"],
    "pass": ["pass", "passing", "passed", "passes", "qualify", "qualifying", "clear", "cutoff", "threshold"],
    "fee": ["fee", "fees", "cost", "costs", "price", "pricing", "charge", "charges", "pay", "paying",
            "paid", "payment", "payments", "amount"],
    "late": ["late", "delay", "delayed", "overdue", "lateness"],
    "when": ["when", "date", "dates", "deadline", "deadlines", "due", "schedule", "scheduled"],
    "mobile": ["phone", "phones", "mobile", "smartphone", "cellphone"],
    "tablet": ["ipad", "tablet", "tablets"],
    "laptop": ["laptop", "laptops", "computer", "desktop", "pc"],
    "quiz": ["quiz", "quizzes", "quizz"],
    "refund": ["refund", "refunds", "refunded", "refundable", "reimburse", "reimbursement"],
    "exam": ["exam", "exams", "examination"],
    "drop": ["drop", "dropping", "dropped", "withdraw", "withdrawal", "quit", "unenroll"],
    "miss": ["miss", "missed", "missing", "skip", "skipped"],
}
_CANON = {w: canon for canon, words in _SYNONYMS.items() for w in words}

# Words that carry no question-specific meaning here. NOT in this list (so they matter):
# numbers, not/no/never, maximum/highest/distinction, capstone, quiz, exam, final, late, audit, week...
_GENERIC = set(
    """the a an is are was were be been am do does did done i me my mine we our you your yours to of for in on
    at by with from as and or but if then so than that this these those it its there here what which who whom
    whose how why where can could would will shall should may might must have has had having get got gets
    getting take taking takes need needed needs much many more most some any also just really please tell know
    want wish like about regarding exactly actually official officially currently current latest now today
    still ever minimum min least lowest course courses verified track student students learner learners
    happen happens happening mean means submit submission submitted submitting turn certificate certification
    ok hi hello hey thanks thank""".split()
)

_FOLLOWUP_WORDS = {
    "it", "its", "that", "this", "these", "those", "they", "them", "their", "there", "then",
    "also", "again", "same", "previous", "above", "earlier", "former", "latter", "another", "else",
}
_FOLLOWUP_STARTS = ("and ", "what about", "how about", "why", "so ", "but ", "ok ", "okay", "then ")


def _stem(t: str) -> str:
    if t.isdigit() or "%" in t or len(t) <= 3:
        return t
    if t.endswith("ies") and len(t) > 4:
        t = t[:-3] + "y"
    elif t.endswith("sses"):
        t = t[:-2]
    elif t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    for suf in ("ing", "ed"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            t = t[: -len(suf)]
            if len(t) > 3 and t[-1] == t[-2] and t[-1] not in "sl":  # submitt -> submit
                t = t[:-1]
            break
    return t


def tokens(q: str) -> frozenset[str]:
    out = set()
    for raw in _base(q).split():
        t = _CANON.get(raw)
        if t is None:
            t = _CANON.get(_stem(raw), _stem(raw))
        if t not in _GENERIC and raw not in _GENERIC:
            out.add(t)
    return frozenset(out)


def is_followup(q: str) -> bool:
    """Heuristic: does this question probably depend on the previous turn?"""
    raw = normalize_text(q).split()
    low = q.strip().lower()
    return (
        len(raw) <= 2
        or not tokens(q)
        or bool(set(raw) & _FOLLOWUP_WORDS)
        or low.startswith(_FOLLOWUP_STARTS)
    )


# ------------------------------------------------------------------ keys + store

@dataclass(frozen=True)
class Keys:
    exact: str
    sig: str | None  # None when the question has no meaningful words
    flight: str  # key used to merge identical in-flight requests (sig if available)


def make_keys(question: str, fingerprint: str, ctx: str | None = None) -> Keys:
    """fingerprint = docs version (cache self-invalidates when docs change);
    ctx = key of the previous turn, only for follow-up questions."""
    prefix = f"{fingerprint}|{ctx or '-'}|"
    exact = prefix + "t:" + normalize_text(question)
    toks = tokens(question)
    sig = prefix + "s:" + " ".join(sorted(toks)) if toks else None
    return Keys(exact=exact, sig=sig, flight=sig or exact)


class AnswerCache:
    def __init__(self, max_entries: int = 2000):
        self.max_entries = max_entries
        self._d: OrderedDict[str, LLMResult] = OrderedDict()

    def __len__(self) -> int:
        return len(self._d)

    def get(self, keys: Keys) -> tuple[LLMResult, str] | None:
        for key, via in ((keys.exact, "exact"), (keys.sig, "similar")):
            if key is not None and key in self._d:
                self._d.move_to_end(key)
                return self._d[key], via
        return None

    def put(self, keys: Keys, result: LLMResult) -> None:
        for key in (keys.exact, keys.sig):
            if key is not None:
                self._d[key] = result
                self._d.move_to_end(key)
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)
