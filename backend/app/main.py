"""FastAPI surface over the agent runtime, tools, and persisted state."""
from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from . import agent
from .models import CreateIncidentRequest, DecisionRequest, Run, ToolSpec
from .state import store
from .tools import list_tool_specs

app = FastAPI(title="Incident Triage Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/tools", response_model=list[ToolSpec])
def get_tools() -> list[ToolSpec]:
    return list_tool_specs()


@app.post("/incidents", response_model=Run)
def create_incident(req: CreateIncidentRequest) -> Run:
    return agent.start_run(req.description)


@app.get("/runs", response_model=list[Run])
def list_runs() -> list[Run]:
    return store.list()


@app.get("/runs/{run_id}", response_model=Run)
def get_run(run_id: str) -> Run:
    run = store.get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@app.post("/runs/{run_id}/step", response_model=Run)
def step_run(run_id: str) -> Run:
    try:
        return agent.step(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/runs/{run_id}/run", response_model=Run)
def run_run(run_id: str) -> Run:
    try:
        return agent.run_autonomously(run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/runs/{run_id}/decision", response_model=Run)
def decide_run(run_id: str, decision: DecisionRequest) -> Run:
    try:
        return agent.decide(run_id, decision.approve, decision.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
