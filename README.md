# Incident Triage Agent

An autonomous AI agent that triages production incidents: it investigates logs/metrics/service
health with tools, forms and revises a plan, recovers from simulated tool failures, and pauses
for human approval before taking any risky remediation action (restart/scale/rollback/page).

This is not a chatbot wrapper — the agent runs a **plan → act → observe → reflect** loop on its
own, persists its full state after every step (so a run survives a backend restart and can be
resumed), and exposes that state over a real API so a separate UI can watch and steer it live.

## What it does

Given a short incident description (e.g. "checkout-service p99 latency spiking, 5xx rate
climbing"), the agent:

1. **Investigates** by calling read-only tools — logs, metrics, service/pod status, deploy
   history — choosing which to call next based on what it's learned so far rather than following
   a fixed script.
2. **Forms a hypothesis** about the root cause from that evidence (e.g. a recent deploy
   correlating with DB connection pool exhaustion and crash-looping pods).
3. **Proposes a remediation action** — restart, scale, rollback, or paging on-call — along with
   its reasoning, and **halts for human approval** before anything destructive happens.
4. **Recovers from tool failures**: tools fail transiently on purpose (simulated flakiness), so
   the agent has to notice an error and retry or route around it instead of assuming every call
   succeeds.
5. **Resolves** the incident once the approved action succeeds, producing a final root-cause
   summary.

Every step — plan, tool call, tool result/error, approval request, human decision, resolution —
is appended to the run's transcript and persisted immediately, so a run can be watched live,
left mid-triage, and resumed later exactly where it stopped.

## Architecture

```
frontend/ (Streamlit)  ──HTTP──>  backend/ (FastAPI)
  operator UI:                      agent.py   — plan/act/observe/reflect loop
  - start incident                  tools.py   — mock log/metric/service/action tools
  - live run viewer (transcript)    llm.py     — pluggable LLM client (Gemini / Grok / mock)
  - approve / deny actions          state.py   — SQLite-backed run store (resumable)
  - run history                     main.py    — REST API over the above
```

- **State/context survives across steps**: every planning step, tool call, tool result, and human
  decision is appended to the run's transcript and persisted to SQLite (`backend/triage.db`)
  immediately. The agent's next LLM call is always rebuilt from that persisted transcript, not
  from in-memory state — so a killed/restarted backend can resume any run exactly where it left
  off (`GET /runs/{id}` + `POST /runs/{id}/step` work identically before/after a restart).
- **Operator UX**: the Streamlit UI polls `GET /runs/{id}` and renders the transcript as a step
  timeline (plan → tool call → result → reflection), with a distinct card for actions pending
  approval. Approve/deny are real API calls (`POST /runs/{id}/decision`), not UI-only state.
- **Backend API** exposes the agent runtime itself:
  - `POST /incidents` — start a new triage run from an incident description
  - `GET /runs` / `GET /runs/{id}` — list runs / fetch one run's full state + transcript
  - `POST /runs/{id}/step` — advance the agent one loop iteration (used for manual step mode)
  - `POST /runs/{id}/run` — let the agent run autonomously until it needs approval or finishes
  - `POST /runs/{id}/decision` — human approve/deny for a pending action
  - `GET /tools` — introspect the tool registry the agent can call

## LLM backing

The agent's planner is an LLM call with tool definitions; the concrete provider is pluggable via
`LLM_PROVIDER` in `.env`:

- `gemini` — Google Gemini (`google-generativeai`, free tier). Supports `GEMINI_API_KEYS` as a
  comma-separated list of keys — if one hits its free-tier rate limit, the next is tried
  automatically for that step, falling back to `GEMINI_API_KEY` (single key) if unset.
- `grok` — xAI Grok (OpenAI-compatible endpoint)
- `mock` — deterministic rule-based planner, **no API key required**. Useful for local dev/demo
  and for grading without burning quota. This is the default when no key is configured.

Regardless of provider, if a planning call fails for any reason (rate limit, invalid key, network
error), the agent falls back to the mock planner for that single step rather than halting the run
— so a free-tier quota hit degrades gracefully instead of breaking the demo.

## Running locally

### Backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # (or source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
copy .env.example .env   # then fill in GEMINI_API_KEY or XAI_API_KEY, or leave as LLM_PROVIDER=mock
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
pip install -r requirements.txt
streamlit run app.py
```

Then open the Streamlit URL, submit an incident (e.g. "checkout-service p99 latency spiking,
5xx rate climbing"), and watch the agent investigate. When it proposes a restart/scale/rollback,
approve or deny it from the UI.

## Design decisions

- **SQLite over in-memory dict**: the brief explicitly asks for state that survives across steps
  and is resumable — an in-memory store would fail the moment the process restarted, so persistence
  was non-negotiable even at this scope.
- **Explicit `awaiting_approval` state, not a confirmation prompt**: approval is a first-class run
  status, not a modal in the UI. This means the API itself enforces the gate (you cannot execute
  an action tool through the API without a prior `decision` call), not just the frontend.
- **Mock LLM provider**: free-tier API keys can be slow/rate-limited/unavailable during grading.
  The mock planner implements the same tool-calling contract as the real providers so the whole
  loop (including error recovery and HITL) is demonstrable with zero setup.
- **Simulated tool flakiness**: tools fail ~1-in-4 calls with transient errors so the "error
  recovery" requirement isn't theoretical — it reliably triggers in a short demo run.
- **Streamlit over a custom SPA**: given the 2-4 hour timebox, Streamlit gets a live, pollable
  operator view with approve/deny actions wired to real API calls without hand-rolling frontend
  state management, while still being a genuine separate UI talking to the backend over HTTP (not
  server-rendered from the same process).

## What's next

- Streaming the agent's token-by-token reasoning to the UI (currently it's step-by-step, not
  token-by-token)
- Multi-incident correlation (an agent that notices two "independent" incidents share a root cause)
- Real tool backends behind the same interface (swap mock `tools.py` implementations for real
  Prometheus/Loki/k8s clients without touching `agent.py`)
- Auth on the approval endpoint (right now any caller can approve/deny)
- WebSocket push instead of polling for the live transcript

## AI interaction log

See [`AI_TRANSCRIPT.md`](./AI_TRANSCRIPT.md) for the full log of how this was built with AI
assistance, including the prompts used and key decisions made along the way.
