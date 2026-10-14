"""The agent's plan -> act -> observe -> reflect loop.

A `step()` call advances a run by exactly one loop iteration and persists the
result immediately. The loop is resumable by construction: every call rebuilds
its view of "what's happened so far" from the persisted transcript (via
`state.store.get`), never from process memory, so stepping a run after a backend
restart behaves identically to stepping it mid-process.
"""
from __future__ import annotations

from .llm import plan
from .models import PendingAction, Run, RunStatus, Step, StepType
from .state import store
from .tools import ToolError, call_tool, list_tool_specs, requires_approval

MAX_RETRIES_PER_TOOL = 2


def _transcript_text(run: Run) -> str:
    lines = []
    for s in run.transcript:
        if s.type == StepType.TOOL_CALL:
            lines.append(f"TOOL_CALL[{s.tool_name}] args={s.tool_args}")
        elif s.type == StepType.TOOL_RESULT:
            lines.append(f"TOOL_RESULT[{s.tool_name}]: {s.content}")
        elif s.type == StepType.TOOL_ERROR:
            lines.append(f"TOOL_ERROR[{s.tool_name}]: {s.content}")
        elif s.type == StepType.PLAN:
            lines.append(f"PLAN: {s.content}")
        elif s.type == StepType.APPROVAL_DECISION:
            lines.append(f"APPROVAL_DECISION: {s.content}")
    return "\n".join(lines)


def start_run(incident: str) -> Run:
    run = Run(incident=incident, status=RunStatus.RUNNING)
    run.add_step(Step(type=StepType.SYSTEM, content=f"Incident reported: {incident}"))
    store.save(run)
    return run


def step(run_id: str) -> Run:
    """Advance one iteration: plan the next action, then either execute a
    read-only tool, request approval for an action tool, or finish."""
    run = store.get(run_id)
    if run is None:
        raise ValueError(f"no such run: {run_id}")

    if run.status not in (RunStatus.RUNNING,):
        return run  # nothing to do if awaiting approval / resolved / failed

    last_error_step = None
    for s in reversed(run.transcript):
        if s.type in (StepType.TOOL_RESULT, StepType.TOOL_ERROR):
            last_error_step = s if s.type == StepType.TOOL_ERROR else None
            break

    result, warning = plan(
        incident=run.incident,
        transcript_text=_transcript_text(run),
        tool_specs=list_tool_specs(),
        last_error=last_error_step.content if last_error_step else None,
    )
    if warning:
        run.add_step(Step(type=StepType.SYSTEM, content=f"[llm fallback] {warning}"))

    run.add_step(Step(type=StepType.PLAN, content=result.reasoning))

    if result.action == "finish":
        run.add_step(Step(type=StepType.RESOLUTION, content=result.final_summary or "Resolved."))
        run.status = RunStatus.RESOLVED
        store.save(run)
        return run

    tool_name = result.tool_name
    tool_args = result.tool_args or {}

    if tool_name is None:
        run.add_step(Step(type=StepType.SYSTEM, content="Planner returned no tool_name; stopping."))
        run.status = RunStatus.FAILED
        store.save(run)
        return run

    if requires_approval(tool_name):
        run.add_step(
            Step(
                type=StepType.APPROVAL_REQUEST,
                content=f"Requesting approval to run {tool_name}({tool_args}): {result.reasoning}",
                tool_name=tool_name,
                tool_args=tool_args,
            )
        )
        run.pending_action = PendingAction(tool_name=tool_name, tool_args=tool_args, reason=result.reasoning)
        run.status = RunStatus.AWAITING_APPROVAL
        store.save(run)
        return run

    _execute_tool(run, tool_name, tool_args)
    store.save(run)
    return run


def _execute_tool(run: Run, tool_name: str, tool_args: dict) -> None:
    run.add_step(Step(type=StepType.TOOL_CALL, content=f"Calling {tool_name}", tool_name=tool_name, tool_args=tool_args))
    try:
        result_text = call_tool(tool_name, **tool_args)
        run.add_step(Step(type=StepType.TOOL_RESULT, content=result_text, tool_name=tool_name, tool_args=tool_args))
        run.retry_count = 0
    except ToolError as exc:
        run.add_step(Step(type=StepType.TOOL_ERROR, content=str(exc), tool_name=tool_name, tool_args=tool_args))
        run.retry_count += 1
        if run.retry_count > MAX_RETRIES_PER_TOOL:
            run.add_step(
                Step(
                    type=StepType.SYSTEM,
                    content=f"{tool_name} failed {run.retry_count} times; marking run failed.",
                )
            )
            run.status = RunStatus.FAILED


def decide(run_id: str, approve: bool, note: str | None) -> Run:
    run = store.get(run_id)
    if run is None:
        raise ValueError(f"no such run: {run_id}")
    if run.status != RunStatus.AWAITING_APPROVAL or run.pending_action is None:
        raise ValueError("run has no pending action awaiting a decision")

    action = run.pending_action
    decision_text = f"{'APPROVED' if approve else 'DENIED'} {action.tool_name}({action.tool_args})"
    if note:
        decision_text += f" — {note}"
    run.add_step(Step(type=StepType.APPROVAL_DECISION, content=decision_text, tool_name=action.tool_name, tool_args=action.tool_args))

    if approve:
        _execute_tool(run, action.tool_name, action.tool_args)
        run.pending_action = None
        if run.status != RunStatus.FAILED:
            run.status = RunStatus.RUNNING
    else:
        run.pending_action = None
        run.add_step(Step(type=StepType.SYSTEM, content="Human denied the action; agent will reconsider."))
        run.status = RunStatus.RUNNING

    store.save(run)
    return run


def run_autonomously(run_id: str, max_steps: int = 10) -> Run:
    """Advance a run until it needs approval, resolves, fails, or hits max_steps."""
    run = store.get(run_id)
    if run is None:
        raise ValueError(f"no such run: {run_id}")

    for _ in range(max_steps):
        run = step(run_id)
        if run.status != RunStatus.RUNNING:
            break
    return run
