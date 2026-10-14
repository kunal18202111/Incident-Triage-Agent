# AI Interaction Log

This project was built with Claude Code (Anthropic), model `claude-sonnet-5`, in a single
assisted session. This log summarizes the actual prompts, decisions, and verification steps
taken during that session (not a reconstruction after the fact).

## 1. Brief → scoping questions

**Prompt (user, pasted brief):** the assignment brief — build an AI agent automating a real
platform engineering task, 2-4 hours, with a web UI, backend API, resumable state, and genuine
autonomy (not a chatbot wrapper).

**Claude's response:** rather than guessing the use case/stack, asked four targeted questions
up front (use case, stack, tool backing, LLM provider) since each one determines a large chunk
of the implementation and getting them wrong would waste the timebox. User chose:
- Use case: **incident triage**
- Stack: **FastAPI backend + Streamlit frontend**
- Tools: **simulated/mocked** (no real infra access needed)
- LLM: **free providers (Gemini, Grok)**, abstracted behind an interface

## 2. Architecture decisions made before writing code

Claude reasoned through the brief's hard requirements (resumable state, operator UX, real
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

## 3. Build order (reflected in git history)

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

## 4. Verification performed during the session (not just "should work")

Claude didn't just write the code — each stage was actually run before moving to the next:

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
  transient tool failure live, so the error-recovery path (tool_error logged, retry on next step,
  resolved after retry succeeded... actually here it re-requested approval and resolved on the
  next approval) was observed working, not just theorized.
- Installed both backend and frontend dependencies into real virtualenvs, started `uvicorn` and
  `streamlit` as separate processes, and confirmed both respond (`/health` → 200, Streamlit root
  → 200) with no errors in either process's logs before considering the UI done.

## 5. Known simplifications, stated rather than hidden

- The `mock` LLM provider is rule-based, not a real model call — included so the full agent
  contract (planning, tool use, HITL, error recovery) is demonstrable without API keys. Real
  Gemini/Grok planning is wired up and used when `LLM_PROVIDER` is set accordingly, with the
  prompt and JSON contract documented in `backend/app/llm.py`.
- Tool failure is random per-call (25%) rather than simulating specific realistic failure
  signatures (timeout vs. 5xx vs. auth) — sufficient to exercise the recovery path within the
  timebox; noted in README "what's next" as an area to make more realistic.
