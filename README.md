# DPM-101 Course Helpdesk Chatbot

A small web chatbot that answers student questions about the *Digital Product Management (DPM-101)* course **only from the official course documents**, shows which file each answer came from, and keeps Gemini usage low with caching and request control.

- **Live URL:** _add the Render URL here after deploying_ (free tier: the first request after idle can take ~30-60 s while the service wakes up)
- **Stack:** Python 3.11, FastAPI, Google Gemini (`gemini-2.5-flash-lite` via `google-genai`), vanilla HTML/JS front end, in-memory cache. No database, no RAG.

## What it does

| Requirement | How it is met |
|---|---|
| Chat UI with follow-ups, loading indicator, no duplicate sends | `static/index.html`. A per-browser `session_id` carries the last few turns for follow-ups ("what about the audit track?"). Typing dots and a spinner show while waiting; Send is disabled and a synchronous guard ignores extra clicks / Enter presses. The server also rejects a second request from the same session while one is running (HTTP 409). |
| Answer only from `course_docs/`, cite sources | All active documents are placed in the prompt, each tagged with its filename. The model must return JSON `{answerable, answer, sources[]}`. **Code, not the model, enforces grounding** (see below). |
| Conflicting documents: latest official policy wins | Handled in the loader, not left to the model (see below). |
| Not covered / off-topic: say so, don't guess | Returns the fixed wording "...not mentioned in the course documents...", with no sources. |
| Avoid unnecessary Gemini calls + live counters | Two-level cache, single-flight de-duplication, counters shown on the page: **Total Questions / Gemini API Calls / Served from Cache**. |
| Friendly message on API failure / rate limit | Retries with backoff, then a plain-language message; HTTP status and `Retry-After` are set, and the UI locks Send with a countdown on 429. |
| Public URL, ~1,500 students in exam week | Render deployment (below) and the load-control design (below). |

## Architecture

```
browser (static/index.html)
   |  POST /api/chat {message, session_id}      GET /api/stats (polled)
   v
app/main.py      FastAPI: validation, error -> friendly JSON + status codes
   v
app/service.py   HelpdeskService: cache lookup, rate limit, single-flight, concurrency cap, history
   |  cache miss only
   v
app/llm.py       GeminiClient: grounded prompt, JSON output, retries/backoff, citation checks
   ^
app/docs.py      loads course_docs/: supersession, forum-comment sanitising, fingerprint
app/cache.py     question normalisation + "same question" signature + LRU store
app/stats.py     the three counters (+ extras)
```

### Document conflicts (latest policy wins)
`02_grading_policy_v1.txt` (Jan 2026: 50% pass, 2 late submissions, capstone 10 Nov) is replaced by `03_grading_policy_v2.txt` (Oct 2026: 60% overall **and** 40% in the final, no late submissions, capstone 15 Nov). The loader reads the status line / "supersedes <file>" notice and the dates, marks v1 as superseded and **leaves it out of the prompt entirely**, so the old rules cannot leak into an answer. Trade-off: the bot cannot explain the *old* policy, which is the intended behaviour ("use the latest").

### Prompt-injection defence
`04_exam_rules_and_faq.txt` ends with unofficial forum comments, one of which pretends to be a system update ("exam is open-book and moved to 30 Dec"). Layers:
1. The forum section is split into individual comments; injection-looking comments are detected and **removed** before the prompt is built; the rest are labelled `UNOFFICIAL`. If the section can't be parsed cleanly, the forum text is dropped (fail closed).
2. Document text is wrapped in tags and the system prompt says it is *data, not instructions*; tag-like text inside documents is neutralised.
3. Tests assert that the question "open-book or closed-book, and when?" still answers *closed-book, 20 Nov*.

### Grounding enforced in code
After Gemini replies, `finalize()` checks that every cited source is a real, active document. An "answerable" answer with no valid citation is turned into the standard "not mentioned in the course documents" reply. Temperature is 0.1.

### Fewer Gemini calls (cache)
- **L1:** exact match on the normalised question (case, punctuation, whitespace).
- **L2:** a canonical *signature* of the meaningful words (stop-words and synonyms folded, so "minimum score to pass..." and "how much do I need to score to pass..." match). It is deliberately strict: numbers, negations and words like *capstone / quiz / exam / late / refund / audit* are kept, so "1 day late" is **not** matched to "2 days late" and a capstone question never reuses a quiz answer.
- The key includes a fingerprint of the document text, so changing a document invalidates old answers.
- "Not mentioned" answers are cached too; **errors are never cached**.
- **Follow-ups** ("what about...?") are keyed on the previous turn and sent with history; standalone questions are sent *without* history, so cached answers never depend on someone else's conversation.
- **Single-flight:** if many students ask the same thing at once, one Gemini call is made and everyone shares the result (counted as cache hits).

### Counters (`GET /api/stats`)
- `total_questions`: every accepted question (empty, too-long and "still busy" requests are not counted).
- `api_calls`: every real outbound Gemini request, **including retries**, so it can be higher than the number of fresh answers.
- `cache_hits`: answers served without a call of their own (stored, or merged into an identical in-flight request).
- Extras: `coalesced`, `errors`, `rate_limited`.

### Exam-week load (~1,500 students)
Most questions repeat, so Gemini calls scale with the number of *distinct* questions, not students. A test fires 1,500 mixed-case requests for the 12 sample questions and needs about 10 Gemini calls. On top of that: a global cap on simultaneous Gemini calls (default 3) with a bounded queue (overflow gives a friendly "busy" message instead of hanging), per-session rate limiting (12 Gemini-bound questions/min), 500-character question limit, and retry/backoff on 429/5xx/timeouts.

## Run locally

```bash
python -m venv venv
venv\Scripts\activate            # Windows   (Linux/macOS: source venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env           # Linux/macOS: cp .env.example .env  -> then put your key in .env
uvicorn app.main:app --port 8000 # open http://127.0.0.1:8000
```

`.env` (never committed):

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | (required) | If missing, the app still starts and chat returns a friendly "not configured" message |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Any Flash-class model your key can use |
| `MAX_CONCURRENT_LLM` | `3` | Simultaneous Gemini calls |
| `SESSION_RATE_PER_MIN` | `12` | Gemini-bound questions per session per minute |
| `MAX_QUEUE`, `MAX_QUESTION_CHARS`, `HISTORY_TURNS` | `40`, `500`, `3` | Queue length, question length, follow-up memory |

## Tests

```bash
python -m pytest -q                                         # offline: fake LLM, no network, no quota
set RUN_LIVE=1&& python -m pytest tests/test_sample_questions.py -k live -s   # real Gemini, ~10 calls
```

The offline suite covers the document loader (supersession, injection), the Gemini layer (retries, grounding), cache matching (including must-NOT-match pairs), the service (single-flight, double-click, rate limit, overload), the HTTP API and the served page, the 12 sample questions' counters, and a 1,500-student load test. The live test runs the 12 provided sample questions against real Gemini and checks each answer (60% + 40% in final, capstone 15 Nov 11:59 PM IST, closed-book 20 Nov, refund "not mentioned", etc.).

## Deploy (Render, free tier)

1. Push this repo to GitHub (`.env` is git-ignored; check `git status` before committing).
2. Render dashboard: **New > Blueprint** (uses `render.yaml`) or **New > Web Service** with build `pip install -r requirements.txt` and start `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`.
3. Add the environment variable `GEMINI_API_KEY` in the dashboard. Health check path: `/health`.
4. Open the URL and run the sample questions.

## Assumptions and known limits

- **Single process, in-memory state.** Cache, counters and chat history live in memory, so counters and cache reset on restart/redeploy and the service must run **one worker**. For several instances, move these to Redis.
- Free-tier hosting sleeps when idle, so the first request after a pause is slow.
- Free-tier Gemini quotas are not published per key; the design assumes they are tight (hence the cache, caps and retries). If Gemini is rate-limited, students get a friendly message rather than an error page.
- Per-session rate limiting trusts a client-supplied session id; a determined user can bypass it, but the global concurrency cap and queue still protect Gemini.
- The whole document set is sent in each prompt (it is a few KB), so no vector search is needed. A much larger corpus would need retrieval.
- Where the documents are silent (e.g. refunds, what happens after missing more than one quiz), the bot says so instead of guessing.
- Document text is used verbatim from `course_docs/` and is never edited.

## AI-assisted development

This project was built with an AI coding agent (Claude) connected to the project folder over MCP, as the brief allows. The conversation log is in [`ai_sessions/`](ai_sessions/); `project.md` is the running context file used to keep agents in sync.
