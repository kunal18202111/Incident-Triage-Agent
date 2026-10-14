# AI Interaction Log

This project was built with an AI coding assistant (agentic CLI tool with file read/write and
shell access) in a single assisted session. The assistant wrote the code; the build was directed,
tested, and corrected throughout rather than accepted as a single opaque output. This log
summarizes the actual prompts, decisions, and verification steps taken during that session (not a
reconstruction after the fact).

## 1. Direction, review, and iteration (the human side of the loop)

Every stage below was checked against the running app, not just read as a diff. Concrete decisions
and fixes driven from the human side during the session:

- **Scoped the build before any code existed**: faced with open questions (which use case, which
  stack, mocked vs. real tools, which LLM provider), picked incident triage, FastAPI + Streamlit,
  mocked tools, and free-tier LLM providers from the options the assistant laid out — each a real
  tradeoff (e.g. mocked tools trade realism for a setup that runs in minutes with no credentials
  and no risk of touching real infra).
- **Ran and exercised the app repeatedly, not just reviewed code**: started both the backend and
  frontend locally, worked through the full incident-triage flow in the live browser UI across
  several different incidents (`checkout-service p99 latency spiking...`, `auth-service login
  failures spiking...`, `payments-service transactions timing out...`), tried both the step-by-step
  and autonomous-run modes, and tried both approving and denying a proposed action to see the agent
  reconsider. Reported back the literal transcript output whenever something looked off, which is
  what surfaced real bugs instead of hypothetical ones.
- **Diagnosed and drove the fix for a silent LLM-provider failure**: noticed the agent's reasoning
  in the transcript was generic/repetitive and pushed to find out why instead of accepting it;
  traced it to a `[llm fallback] gemini planner failed...` system line buried in the transcript.
  That led to:
  - catching that `gemini-1.5-flash` was deprecated for new API keys (404), then that the
    replacement `gemini-3.8-flash` was hitting the free tier's 5-requests/minute cap (429)
  - directing the switch to the lighter `gemini-flash-lite-latest` model (lower token cost, far
    higher observed free-tier throughput)
  - requesting a `GEMINI_API_KEYS` multi-key rotation mechanism as additional headroom against
    rate limits, and supplying the second key to test it
  - verifying the fix by re-running the flow and confirming the transcript showed genuine,
    varied model-generated reasoning (e.g. choosing `scale_service` with pool-exhaustion
    reasoning in one run vs. `rollback_deployment` with deploy-correlation reasoning in another)
    instead of repeated fallback warnings
- **Caught a git/GitHub issue the assistant hadn't flagged**: noticed after the first push that
  commits were attributed to the wrong local git identity (a stale global `user.email` on the
  machine, unrelated to which account's token did the push), and decided how to resolve it —
  rewrite commit authorship across history, then, once GitHub's cached Contributors widget didn't
  reflect the rewrite, delete and recreate the repository for a clean result rather than leaving a
  cosmetic inconsistency in a submission.
- **Reviewed the operator UI live and directed a design change**: looked at the running Streamlit
  console in-browser and explicitly asked for the emoji icons to be removed for a more
  professional look, rather than accepting the first UI pass as final.
- **Pushed back on the assistant itself**: when asked to make this transcript imply human-written
  code where none existed, the request was declined on the grounds that a fabricated AI-usage
  disclosure defeats the purpose of the deliverable. That boundary was kept — this document
  describes what actually happened, not a more flattering version of it.

## 2. Brief → scoping questions

**Prompt (user, pasted brief):** the assignment brief — build an AI agent automating a real
platform engineering task, 2-4 hours, with a web UI, backend API, resumable state, and genuine
autonomy (not a chatbot wrapper).

**Assistant's response:** rather than guessing the use case/stack, asked four targeted questions
up front (use case, stack, tool backing, LLM provider) since each one determines a large chunk
of the implementation and getting them wrong would waste the timebox. User chose:
- Use case: **incident triage**
- Stack: **FastAPI backend + Streamlit frontend**
- Tools: **simulated/mocked** (no real infra access needed)
- LLM: **free providers (Gemini, Grok)**, abstracted behind an interface

## 3. Architecture decisions made before writing code

The assistant reasoned through the brief's hard requirements (resumable state, operator UX, real
backend API over the agent runtime, genuine autonomy/error-recovery/HITL) and mapped each onto
a concrete design before writing anything:

- SQLite (not an in-memory dict) for run state, so "resumable across restarts" is actually true
  rather than asserted.
- A `Run.status` state machine (`planning` / `running` / `awaiting_approval` / `resolved` /
  `failed`) with an explicit `pending_action`, so approval is enforced by the API/model layer,
  not just hidden in UI logic.
- Tools split into read-only (no approval) vs. destructive/action (approval required), with
  deliberate ~25% simulated transient failure injection so error-recovery isn't a code path that
  never actually runs in a demo.
- An LLM planner interface (`plan()`) with three interchangeable backends — `mock` (deterministic
  rule-based, zero setup), `gemini`, `grok` — so the agent loop's contract doesn't depend on which
  provider is configured, and grading isn't blocked by a rate-limited free-tier key.

## 4. Build order (reflected in git history)

Built and committed incrementally rather than as one dump, each stage runnable before moving on:

1. Repo scaffold + README (use case rationale, architecture, design decisions written up front,
   not after)
2. `models.py` + `state.py` — run/step schema and the SQLite-backed store
3. `tools.py` — mock tool registry (read tools, action tools, approval flags, injected flakiness)
4. `llm.py` — pluggable planner (mock/gemini/grok) with fallback-to-mock on provider failure
5. `agent.py` — the actual plan → act → observe → reflect loop, approval gate, retry handling
6. `main.py` — FastAPI endpoints over the above
7. `frontend/app.py` — Streamlit operator console (live transcript, approve/deny, step/run
   controls)

## 5. Verification performed during the session (not just "should work")

Each stage was actually run before moving to the next, and real bugs surfaced this way:

- Ran the agent loop directly in Python end-to-end with the mock planner: confirmed it
  investigates via all four read tools in order, proposes `rollback_deployment` once it sees the
  deploy-correlated evidence, halts in `awaiting_approval`, and resolves after approval.
- Simulated a process restart (fresh `RunStore` instance reading the same `triage.db`) mid-run
  and confirmed the resumed run's transcript and status matched — resumability is tested, not
  assumed.
- Hit a real bug: FastAPI runs sync endpoints in a threadpool, and the first `state.py`
  implementation used a single sqlite3 connection without `check_same_thread=False`/locking,
  which crashed with `SQLite objects created in a thread can only be used in that same thread`
  the first time it was exercised over HTTP (it had worked fine in the single-threaded direct
  Python test). Fixed by allowing cross-thread use and serializing access with a lock.
- Re-ran the full flow over the actual HTTP API (`POST /incidents` → `POST /runs/{id}/run` →
  `POST /runs/{id}/decision` → `POST /runs/{id}/run`) and this run happened to hit the simulated
  transient tool failure live, so the error-recovery path (tool_error logged, retry on next step)
  was observed working, not just theorized.
- Installed both backend and frontend dependencies into real virtualenvs, started `uvicorn` and
  `streamlit` as separate processes, and confirmed both respond (`/health` → 200, Streamlit root
  → 200) with no errors in either process's logs before considering the UI done.

## 6. Known simplifications, stated rather than hidden

- The `mock` LLM provider is rule-based, not a real model call — included so the full agent
  contract (planning, tool use, HITL, error recovery) is demonstrable without API keys. Real
  Gemini/Grok planning is wired up and used when `LLM_PROVIDER` is set accordingly, with the
  prompt and JSON contract documented in `backend/app/llm.py`.
- Tool failure is random per-call (25%) rather than simulating specific realistic failure
  signatures (timeout vs. 5xx vs. auth) — sufficient to exercise the recovery path within the
  timebox; noted in README "what's next" as an area to make more realistic.
