"""Control layer: everything between "a student asked X" and "call Gemini".

ask(session_id, question) flow:
  validate -> busy-guard (one request per session at a time) -> count question
  -> cache lookup (exact, then canonical signature)          [hit: no Gemini call]
  -> per-session rate limit (only for requests that would hit Gemini)
  -> single-flight: identical in-flight questions share ONE Gemini call
  -> concurrency cap + bounded queue (overload -> friendly error, not a hang)
  -> store answer (errors are never cached), update chat history
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from .cache import AnswerCache, Keys, is_followup, make_keys
from .docs import CourseKnowledge
from .llm import LLMClient, LLMError, LLMResult, Turn
from .stats import Stats


class HelpdeskError(Exception):
    status = 400
    user_message = "Something went wrong."

    def __init__(self, user_message: str | None = None):
        super().__init__(user_message or self.user_message)
        if user_message:
            self.user_message = user_message


class BadQuestion(HelpdeskError):
    status = 400


class Busy(HelpdeskError):
    status = 409
    user_message = "I'm still working on your previous question. Please wait a moment."


class TooFast(HelpdeskError):
    status = 429
    user_message = "You're asking questions very quickly. Please wait a few seconds and try again."

    def __init__(self, retry_after: int = 10):
        super().__init__()
        self.retry_after = retry_after


@dataclass
class Reply:
    answer: str
    sources: list[str]
    answerable: bool
    cached: bool
    via: str | None  # "exact" | "similar" | "coalesced" | None (fresh Gemini answer)


@dataclass
class Session:
    history: list[Turn] = field(default_factory=list)
    last_key: str | None = None
    busy: bool = False
    stamps: deque = field(default_factory=deque)


class HelpdeskService:
    def __init__(
        self,
        llm: LLMClient,
        knowledge: CourseKnowledge,
        stats: Stats | None = None,
        *,
        max_concurrent: int = 3,
        max_queue: int = 40,
        session_rate_per_min: int = 12,
        history_turns: int = 3,
        max_question_chars: int = 500,
        max_sessions: int = 5000,
        cache: AnswerCache | None = None,
        clock=time.monotonic,
    ):
        self.llm = llm
        self.fingerprint = knowledge.fingerprint
        self.stats = stats or Stats()
        self.cache = cache or AnswerCache()
        self.max_queue = max_queue
        self.rate = session_rate_per_min
        self.history_turns = history_turns
        self.max_chars = max_question_chars
        self.max_sessions = max_sessions
        self._clock = clock
        self._sem = asyncio.Semaphore(max_concurrent)
        self._waiting = 0
        self._inflight: dict[str, asyncio.Task] = {}
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        llm.on_call = self.stats.record_api_call  # count every real outbound request

    # ------------------------------------------------------------ public

    async def ask(self, session_id: str, question: str) -> Reply:
        q = " ".join((question or "").split())
        if not q:
            raise BadQuestion("Please type a question.")
        if len(q) > self.max_chars:
            raise BadQuestion(f"Please keep your question under {self.max_chars} characters.")

        sess = self._session(session_id)
        if sess.busy:
            raise Busy()  # backend backstop for double-clicks; not counted, no API call
        sess.busy = True
        try:
            return await self._answer(sess, q)
        except LLMError:
            self.stats.error()
            raise
        finally:
            sess.busy = False

    # ------------------------------------------------------------ internals

    def _session(self, sid: str) -> Session:
        sess = self._sessions.get(sid)
        if sess is None:
            sess = self._sessions[sid] = Session()
            while len(self._sessions) > self.max_sessions:
                oldest = next(iter(self._sessions))
                if self._sessions[oldest].busy:
                    break
                self._sessions.pop(oldest)
        self._sessions.move_to_end(sid)
        return sess

    async def _answer(self, sess: Session, q: str) -> Reply:
        self.stats.question()
        followup = is_followup(q)
        use_ctx = followup and bool(sess.history)
        keys = make_keys(q, self.fingerprint, sess.last_key if use_ctx else None)
        # A follow-up with no history ("is it open-book?") can't be understood -> don't cache it.
        cacheable = not (followup and not sess.history)

        hit = self.cache.get(keys) if cacheable else None
        if hit:
            result, via = hit
            self.stats.cache_hit()
            reply = Reply(result.answer, result.sources, result.answerable, True, via)
        else:
            self._check_rate(sess)
            # Standalone questions are answered without history, so cached answers are context-free.
            history = list(sess.history) if use_ctx else []
            result, coalesced = await self._compute(keys, q, history, cacheable)
            reply = Reply(
                result.answer, result.sources, result.answerable,
                coalesced, "coalesced" if coalesced else None,
            )

        sess.history += [Turn("user", q), Turn("assistant", reply.answer)]
        del sess.history[: -self.history_turns * 2]
        sess.last_key = keys.flight
        return reply

    def _check_rate(self, sess: Session) -> None:
        now = self._clock()
        while sess.stamps and now - sess.stamps[0] > 60:
            sess.stamps.popleft()
        if len(sess.stamps) >= self.rate:
            self.stats.limited()
            raise TooFast(retry_after=max(1, math.ceil(60 - (now - sess.stamps[0]))))
        sess.stamps.append(now)

    async def _compute(self, keys: Keys, q: str, history: list[Turn], cacheable: bool):
        task = self._inflight.get(keys.flight)
        if task is not None and task.done():
            task = None  # finished (possibly failed) but not yet cleaned up; don't reuse stale errors
        coalesced = task is not None
        if task is None:
            task = asyncio.create_task(self._run(keys, q, history, cacheable))
            self._inflight[keys.flight] = task
            task.add_done_callback(lambda t, k=keys.flight: self._finish(k, t))
        # shield: if one student disconnects, the shared call keeps running for everyone else
        result = await asyncio.shield(task)
        if coalesced:
            self.stats.cache_hit(coalesced=True)
        return result, coalesced

    def _finish(self, key: str, task: asyncio.Task) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # mark retrieved -> no "never retrieved" log noise

    async def _run(self, keys: Keys, q: str, history: list[Turn], cacheable: bool) -> LLMResult:
        if self._waiting >= self.max_queue:
            raise LLMError("rate_limited", "queue full")
        self._waiting += 1
        try:
            await self._sem.acquire()  # concurrency cap
        finally:
            self._waiting -= 1
        try:
            result = await self.llm.generate(q, history)
        finally:
            self._sem.release()
        if cacheable:  # successes only (incl. "not mentioned" answers); errors raise before this line
            self.cache.put(keys, result)
        return result
