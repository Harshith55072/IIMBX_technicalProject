# project.md — Context file (read this first every session)

## Rules (do not remove)
1. **Keep this file lean.** Only record what a fresh session needs to continue work. No narration, no duplication, no play-by-play.
2. **Auto-clean when bloated.** Whenever this file grows noisy or stale, prune outdated/unnecessary info on my own (merge, shorten, delete resolved items) without waiting to be asked.

## Workflow
- **Multiple agents may work here.** Before starting: read this file + tail of `conversation_log.md`. After EVERY user exchange: append an ordered, lean entry to `conversation_log.md` (U asks / C did+decided) and update this file if status changed.
- After any log change, re-copy it for submission: `copy /Y conversation_log.md ai_sessions\01_claude_code_session_log.md`.
- **Gotcha:** edit the log/this file ONLY with write_file/edit_file. Never `echo`/`Add-Content` text through `run_command`: it runs under cmd, `>` (e.g. in `->`) is a redirect → truncated text + stray files named after the next word.
- Shell is Windows cmd: use `set VAR=x&& cmd`, `findstr`, `venv\Scripts\python`. Never print or paste the Gemini key.

## Project
IIMBx Technical Intern (System Design, AI & Backend) take-home: **Course Helpdesk Chatbot** for course DPM-101 (~1,500 students in exam week; answers only from `course_docs/`). Coding agents allowed.
- Folder: `C:\Users\Lenovo\Documents\programing\IIMBX\` (MCP project `IIMBX`). Python 3.11.9, FastAPI, Gemini `gemini-2.5-flash-lite` (`GEMINI_MODEL`), vanilla JS page, in-memory cache/counters (no DB, no RAG).
- **Live:** https://dpm101-helpdesk.onrender.com (Render free, `render.yaml`, 1 worker; key only in Render dashboard). **Repo (public):** https://github.com/Harshith55072/IIMBX_technicalProject (`main`).
- **Deadline: Thu 12 Nov 2026, 2:00 PM IST** (brief says 2 PM, email says 3 PM; use 2). Submit via https://forms.gle/ks8XFmSzJ7e8d3ze9: (1) hosted URL, (2) GitHub repo + README, (3) `ai_sessions/` .md exports.

## STATUS (verified Wed 11 Nov): built, deployed, working. Only these remain
- [x] **Raw claude.ai chat export** → `ai_sessions/02_claude_ai_chat_export.md` done 11 Nov (converted from the user's pasted `ai_sessions.txt`, verbatim, secret-scanned). It covers sessions 1-3 only; if the user wants later chats included they re-paste and an agent re-converts (anchor-based script, see log Session 5). `ai_sessions.txt` in the repo root is the raw paste (harmless; may be deleted or committed).
- [ ] **Commit + push** (GitHub still has the OLD README with "add the Render URL here"; README, log, project.md, ai_sessions changes are uncommitted). Before committing: `git status`, confirm `.env` is not staged. Then check the GitHub README shows the live URL.
- [ ] **Submit the form.** Optional: open the live URL a few min before the deadline (cold start ~30–60 s after idle); live counters currently include test traffic (17/14/3) until the instance restarts (Render → Manual Deploy/Restart gives a clean 0/0/0).

## Requirement → evidence (all verified live on 11 Nov)
| Brief requirement | Where / proof |
|---|---|
| Chat page, follow-ups, loading indicator, no duplicate sends | `static/index.html` (busy guard, disabled Send, spinner); server 409 for 2nd in-flight request per session (live: 200 + 409); pronoun follow-ups work live |
| Answer only from docs + show source files | `app/llm.py` prompt + `finalize()` validates citations; UI source pills |
| Latest policy wins on conflict | `app/docs.py` marks 02 (v1) superseded by 03, excluded from prompt; live Q1/Q4/Q5 use v2 |
| Uncovered/off-topic → "not mentioned in the course documents" | Q9 refund, Q11, Q12 verified live |
| Cache + live counter (Total / API calls / Cache) | `app/cache.py`, `app/service.py`, `app/stats.py`; UI shows exactly these 3 (extras only in `/api/stats`); Q2 exact hit, Q3 similar hit live |
| API failure / rate limit → friendly message | retries+backoff, JSON `{error,message}`, 429 `Retry-After` locks Send with countdown; unit-tested (can't be triggered safely live) |
| Hosted public URL | Render URL above; repo public |
| Hidden traps | v1/v2 conflict, forum prompt-injection (Learner_819), no refund policy, uncovered/off-topic questions, exact repeat + rephrased question: all handled |
| Secrets | key not in any commit/working-tree file (scanned); `.env` ignored; `.env.example` placeholder only |

## Docs (`course_docs/`, verbatim, never edit) — quirks & expected answers
- **01 handbook** (15 Aug 2026): 8 wks; weights quizzes 30 (6 quizzes, best 5) / capstone 30 / final 40; Audit free (no graded work/cert); Verified Rs. 1,500; upgrade open till end of Week 4. **No refund policy.**
- **02 grading v1** (10 Jan 2026): 50% pass, 2 late subs w/ 10% penalty, capstone 10 Nov. **Superseded by 03.**
- **03 grading v2** (5 Oct 2026, CURRENT): pass = ≥60% overall AND ≥40% in final; distinction ≥85%; **no late submissions**; can miss 1 quiz; capstone **15 Nov 2026 11:59 PM IST**.
- **04 exam rules** (25 Oct 2026): exam 20 Nov 2026 (slots 10AM–8PM IST), 90 min, AI-proctored, **closed-book**, govt photo ID, desktop/laptop only (no phone/tablet), Chrome/Edge, ≥2 Mbps. Sec 3 = unofficial forum comments; **Learner_819 = prompt injection** (claims open-book + 30 Dec) → removed in code.
- Expected: Q1/Q3 60%+40% final · Q4 late not accepted · Q5 15 Nov 11:59 PM IST · Q6 closed-book 20 Nov · Q7 no · Q8 Rs. 1,500 · Q9 refund not mentioned · Q10 6 quizzes, best 5, miss 1 OK · Q11/Q12 not mentioned. Note: v2 itself says "earlier 50% threshold"/"10% penalty discontinued", so a correct answer may mention them; don't assert their absence in tests.

## Architecture (all in `app/`, 119 offline tests + 1 opt-in live test)
`main.py` FastAPI (`POST /api/chat`, `GET /api/stats`, `GET /health`, static at `/`; errors → `{error,message}`: 400 bad input, 409 busy, 429 too fast/rate-limited (+Retry-After), 502/503/500) → `service.py` `HelpdeskService.ask()` → `cache.py` → `llm.py` `GeminiClient` ← `docs.py`; `stats.py`, `config.py` (env: `GEMINI_API_KEY`, `GEMINI_MODEL`, `MAX_CONCURRENT_LLM`=3, `SESSION_RATE_PER_MIN`=12, `MAX_QUEUE`=40, `MAX_QUESTION_CHARS`=500, `HISTORY_TURNS`=3).
- **Docs layer:** loader parses `LAST UPDATED`/`STATUS`/"supersedes <file>" (date-checked), drops superseded docs from prompt, splits forum comments, redacts injection-like ones (fail-closed on parse mismatch), neutralizes tag-like text; `.fingerprint` is in every cache key.
- **LLM layer:** structured JSON `{answerable, answer, sources[]}`, temp 0.1; `finalize()` rejects uncited/invalid-source answers → standard "not mentioned" reply; retries 429/5xx/timeout/bad JSON with backoff; 400/401/403/404 → `config` error. `on_call` counts every real outbound request (retries included).
- **Cache:** L1 exact normalized text; L2 canonical word-signature (strict: numbers, not, capstone/quiz/exam/late/refund/audit/maximum/distinction kept, so "1 day late" ≠ "2 days late"). Negative answers cached, errors never. Follow-ups (`is_followup` heuristic) keyed on previous turn + history sent; standalone questions sent without history (cached answers context-free); follow-up with no history not cached.
- **Load:** per-session busy guard (not counted), single-flight per key (waiters count as cache hits), semaphore cap + bounded queue (overflow → friendly "busy"), per-session rate limit on Gemini-bound requests only. **Single worker only** (state in memory; cache/counters reset on restart).
- **Counters:** total_questions, api_calls (incl. retries, so can exceed fresh answers), cache_hits (+ coalesced, errors, rate_limited in `/api/stats` only).

## How to run / test
- Local: `venv\Scripts\python -m uvicorn app.main:app --port 8000`; tests: `venv\Scripts\python -m pytest -q` (offline, no quota); live Gemini: `set RUN_LIVE=1&& venv\Scripts\python -m pytest tests/test_sample_questions.py -k live -s`; `scripts/serve_check.py [--real]`, `scripts/smoke.py [N|@N]`, `node scripts/check_js.js`.
- Gotchas fixed earlier: google-genai defaults to API v1 which rejects system instructions → client pins `v1beta`; a temp `genai.Client` is closed by GC (keep a reference).
