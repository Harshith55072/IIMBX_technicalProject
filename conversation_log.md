# Conversation Log

Lean log of the user–Claude (AI coding agent) collaboration for the interview project. Format: **U** = user, **C** = Claude.

---

## Session 1 — 2026-11-10

**U:** Connect to MCP server, register `C:\Users\Lenovo\Documents\programing\IIMBX\` as the project. Building a project for a technical interview (coding agents allowed). Log the whole conversation (incl. this one) to a lean .md file for submission. Create `project.md` as cross-session context with two rules: keep it lean, and auto-clean it when bloated. Then review the provided project details and make a plan together.

**C:** Registered project `IIMBX` via neuraldocker MCP (folder was empty). Created `project.md` (with the two rules) and this log. Asked for the assignment brief.

**U:** Shared the assignment brief (Course Helpdesk Chatbot for DPM-101), the 12 sample test questions, and the invite email. Said 4 course .txt files exist. Did not paste the Gemini key. Asked what I think.

**C:** Reviewed the brief. Key points: docs are tiny, so no RAG, just put them all in the prompt with source tags. Conflict handling is done in code (latest policy wins). Tiered cache (exact, then local fuzzy match) to save API calls. Single-flight dedup, retries/backoff, friendly errors. Live counters. Python/FastAPI + vanilla JS, deployed on Render (or HF Spaces). Flagged: deadline mismatch (2 PM in brief vs 3 PM in email, so aim for 2 PM); the Gemini key is an API key for LLM calls only, hosting is separate, keep it in an env var; the 4 course docs were not received yet, so I asked the user to drop them into `IIMBX/course_docs/`. Full plan saved in `project.md`.

**U:** Provided the 4 course docs (handbook, grading v1, grading v2, exam rules/FAQ). Asked me to explain the problem statement, what we build and how, and the technologies required/planned.

**C:** Copied the docs verbatim into `course_docs/`. Found the traps: (1) v1 vs v2 grading conflict (50% → 60% + 40% in final; late submissions removed; capstone 10 Nov → 15 Nov), so v2 wins and v1 is excluded in code; (2) a prompt-injection forum comment in doc 04 claiming open-book/30 Dec, which must be ignored, since the official rules say closed-book/20 Nov; (3) forum comments are unofficial; (4) no refund policy exists, so Q9 must say "not mentioned"; (5) Q11/Q12 are uncovered/off-topic. Checked Gemini docs: free-tier per-model limits are not published (only in the AI Studio console), so the model name is configurable via env var and the app is designed for tight limits. Updated `project.md` with quirks, expected answers and design decisions. Awaiting approval of the plan (FastAPI + Render).

**U:** Confirmed Python + Render (Python already installed). Asked for a step-by-step roadmap first, then start building. Wants the log updated after every exchange since multiple agents may work on this, and `project.md` must make sense to any of them.

**C:** Wrote the roadmap (P0–P9) into `project.md`: scaffold → doc layer → LLM layer → cache/control layer → API → frontend → verification (12 Qs + injection + load test) → Render deploy → README/ai_sessions/submit. Strategy: build offline-testable layers with a fake LLM first to save Gemini quota. Added a multi-agent rule to `project.md` (read project.md + log tail first; append to log after every exchange). Listed what's needed from the user (key in `.env`, GitHub, Render, rate-limit numbers). Waiting for go-ahead to start P1.

**U:** Confirmed everything is installed; start P1 and P2.

**C:** P1 done: Python 3.11.9, git 2.50 OK; created venv, requirements.txt, .gitignore (covers .env), .env.example, folders, git init. P2 done: wrote app/docs.py (loader, supersession by filename + date sanity check, forum split, injection regex filter, tag neutralization, fingerprint) and tests/test_docs.py. First test run failed (2/19): my comment regex didn't allow the '- ' list marker, so the injection comment got merged into a benign one. Fixed the regex and made the parser fail closed (count mismatch drops forum text + warns). Now 20/20 pass. Verified final prompt context: v1 absent, Learner_819 replaced with a REMOVED note, official closed-book/20 Nov rules intact. project.md updated (P1, P2 ticked). Next: P3 (Gemini LLM layer) which needs the key in local .env.


**U:** Said .env is done; check and continue P3.

**C:** Checked: no .env file exists in IIMBX (only .env.example; key check returned empty), so I could not run the real Gemini smoke test. Told the user. Continued P3 offline: wrote app/config.py, app/llm.py (GeminiClient w/ grounded system prompt, JSON schema output, retry/backoff, friendly LLMError kinds, on_call counter hook; FakeLLM; finalize() enforces citations in code; dev CLI --list/--ask) and tests/test_llm.py. 42/42 tests pass (20 docs + 22 llm). project.md updated. Next: user creates .env - smoke test + pick model; meanwhile P4 (cache/control layer) can proceed offline.


**U:** Asked me to check for .env again.

**C:** .env now exists, key set (non-standard length/prefix, but tested live). Listing models first failed: my CLI let the genai client be garbage-collected/closed (fixed). First real call failed with 400 'Developer instruction is not enabled for api version v1' - http api_version=v1beta in GeminiClient (fixed). Chose gemini-2.5-flash-lite (stable, cheap, in the key's model list); updated .env and .env.example. Added scripts/smoke.py. Ran the sample questions on the real API: all correct (v2 policy used, injection ignored, refund/SQL-K8s/cricket - mentioned, follow-up works). Tightened prompt so uncovered topics use the exact phrase 'not mentioned in the course documents' (Q11). 42/42 unit tests pass. P3 complete; project.md updated. Next: P4 cache + control layer.


**U:** Continue with P4.

**C:** Built P4: app/stats.py (3 required counters + coalesced/errors/rate_limited), app/cache.py (normalization, synonym/stopword signature so Q1/Q2/Q3 share a key but '1 day late' != '2 days late' and capstone != quiz, follow-up detection, LRU store), app/service.py (cache lookup, session busy guard, per-session rate limit, single-flight merging identical in-flight questions, concurrency cap + bounded queue, errors never cached, chat history). Added MAX_QUEUE to config. Tests: test_cache.py (34, incl. 12 must-not-match pairs) and test_service.py (18: repeats, rephrasing, 20 concurrent identical - API call, double-click, rate limit, overload, leader cancel, follow-up context). One bug found by tests: retry_after off-by-one (61s) - with ceil. 94/94 pass. Design call: standalone questions are answered WITHOUT history so cached answers are context-free; follow-ups are keyed on the previous turn. project.md updated. Next: P5 FastAPI endpoints.


## Session 2 — 2026-11-11

**U:** New session: read project.md + log, check readiness for P5.

**C:** Read both + code, ran tests (94 pass). Found a real-looking Gemini key pasted in `.env.example` (not gitignored → would have been committed). Told the user. Also found 2 empty stray files (`not`, `real`) in the root.

**U:** Cleaned `.env.example`. Key can't be regenerated (it was issued to them); same key is in `.env` (gitignored), nothing committed yet, so OK. Asked to continue with P5.

**C:** Verified `.env.example` is clean; deleted the 2 stray files. Built P5: `app/main.py` (`create_app(service, meta)` factory + module-level `app`; service built once in lifespan from settings; missing key → app still starts, chat returns friendly config error). Endpoints: `POST /api/chat` (message, optional session_id → answer/sources/answerable/cached/via/session_id/stats), `GET /api/stats`, `GET /health`, static mounted last at `/`. Errors → `{error, message}` JSON: BadQuestion 400, Busy 409, TooFast 429 + Retry-After, LLM rate_limited 429 (Retry-After 30) / unavailable 503 / bad_response 502 / config 500; pydantic validation → friendly 400; unexpected → generic 500 (no detail leak). Invalid/missing session_id → server generates one. `tests/test_api.py` (20 tests via httpx ASGITransport + FakeLLM: counters, cache, validation, error mapping + not cached, rate limit, double-click 409, 30 concurrent students → 1 Gemini call, follow-up history, no-key mode, no detail leak). 114/114 pass. Added `scripts/serve_check.py` (spawns uvicorn; `--real` = 1 real Gemini call + 1 cache hit): worked end to end (answer 60% + 40% final, source 03_grading_policy_v2.txt, repeat served from cache, stats 2/1/1). P5 complete; project.md updated. Next: P6 frontend.

**U:** Yes, start P6.

**C:** Built `static/index.html` (single-file vanilla JS, light/dark): chat bubbles via textContent (no HTML injection), typing dots, spinner + disabled Send and a synchronous busy guard (double-click/Enter spam), 500-char counter, source pills, "from cache" pill, 3 live counters (from each chat response + 5s polling), friendly errors from the API `message` (429 + Retry-After locks Send with a countdown), failed text restored to the input, 60s timeout, sample-question chips, New chat. Added a served-page test (115/115 pass) and `scripts/check_js.js` (JS syntax OK). Could not view it in a real browser from here, so asked the user to open it once. P6 complete; project.md updated. Next: P7 verification (needs `sample_test_questions.txt`).

**U:** Uploaded `sample_test_questions.txt`; run P7.

**C:** Copied it to `tests/` as the fixture and wrote `tests/test_sample_questions.py` (fixture parse, 12-Q counters with fake LLM, prompt-context injection/superseded check, 1,500-student load test, opt-in LIVE test with `RUN_LIVE=1`). Two of my own test expectations were wrong, not the app: (1) v2 doc names v1 and mentions the "earlier 50% threshold", so I check document tags instead of strings; (2) Q4's correct answer mentions the discontinued 10% penalty, so I dropped that negative assertion. Live run on real Gemini: all 12 correct (see project.md P7), stats 12 total / 10 API / 2 cache. Full suite 119 pass + 1 skipped. P7 complete; project.md updated. Next: P8 deploy (GitHub + Render).

**U:** Checked the page in a browser, works, no errors on their test questions. Will push to GitHub now. Asked me to make sure `.gitignore` is accurate, `project.md` + log are current, and to add a README.

**C:** Secrets scan (key only in local `.env`, nothing committed, no git history). Rewrote `.gitignore` (`.env`, `.env.*` but keep `.env.example`, venv, caches, `*.db`, `*.log`, editor/OS files) and verified with `git check-ignore` and `git add -n .` (only intended files; `.env` not staged). Pinned runtime deps in `requirements.txt` to the tested versions and moved pytest to `requirements-dev.txt`. Added `render.yaml` (free web service, 1 worker, `/health`, secret key `sync: false`) and `README.md` (requirements mapping, architecture, conflict + injection handling, cache/counter semantics, load design, local run, tests, Render deploy, assumptions/limits, AI-assistance note). Created `ai_sessions/` content (README + log snapshot) so the folder is tracked by git. Updated `project.md` (repo layout, P8 prep done, P9 steps). Remaining: Render deploy + live URL in README, re-copy log before submit, raw claude.ai export.

