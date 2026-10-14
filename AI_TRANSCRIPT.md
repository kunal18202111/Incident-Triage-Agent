# AI Interaction Log

This project was built with an AI coding assistant (agentic CLI tool with file read/write and
shell access) in a single assisted session. This log summarizes the actual prompts, decisions,
and verification steps taken during that session (not a reconstruction after the fact).

## 1. Brief → scoping questions

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

## 2. Architecture decisions made before writing code

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

The assistant didn't just write the code — each stage was actually run before moving to the next:

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

## 6. Human review and iteration after the initial build

The code itself was written by the assistant, but the build was directed and checked at every
stage rather than accepted as a single opaque output. Decisions and fixes driven from the human
side during the session:

- **Scope decisions**: picked incident triage as the use case, FastAPI+Streamlit as the stack,
  mocked tools over real infra access, and free-tier LLM providers, from options the assistant
  laid out — each with real tradeoffs (e.g. mocked tools trade realism for a setup that runs in
  minutes with no credentials).
- **Live testing, not just reading code**: ran the backend and frontend locally, exercised the
  full incident-triage flow through the actual browser UI multiple times (different incident
  descriptions, approve vs. deny paths, step-by-step vs. autonomous mode), and reported back the
  literal transcript output when something looked off — which is what surfaced real issues rather
  than hypothetical ones.
- **Caught and directed fixes to provider issues**: when Gemini silently fell back to the
  rule-based planner, pushed to find out *why* rather than accept the fallback — this led to
  catching that the configured model (`gemini-1.5-flash`, then `gemini-3.8-flash`) was deprecated
  or free-tier-throttled, and directed the switch to `gemini-flash-lite-latest` plus a multi-key
  rotation mechanism (`GEMINI_API_KEYS`) as mitigation, verified by re-running the flow and
  confirming the transcript showed genuine model-generated reasoning instead of fallback warnings.
- **Caught a git authorship problem the assistant hadn't flagged**: noticed commits were
  attributed to the wrong local git identity after the first push, and decided how to resolve it
  (rewrite commit authorship, then — since GitHub's cached Contributors widget didn't update
  immediately — delete and recreate the repo for a clean result) rather than leaving it as-is.
- **UI review and direction**: reviewed the live operator console in-browser and explicitly
  directed a design change (removing emoji icons for a more professional look), rather than
  accepting the first pass as final.
- **Pushed back on a request from the assistant's side too**: when asked to make
  `AI_TRANSCRIPT.md` imply human-written code where there wasn't any, the assistant declined and
  proposed this honest framing instead — kept, because an AI interaction log that misrepresents
  who wrote the code defeats the point of the deliverable.
