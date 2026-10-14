"""Pluggable LLM planner.

The agent loop only depends on `plan()`, which turns (incident, transcript-so-far,
available tools, last error if any) into a `PlanResult`. Three interchangeable
backends implement it:

- `mock`   : deterministic rule-based planner, no API key needed. Default.
- `gemini` : Google Gemini (free tier), via `google-generativeai`.
- `grok`   : xAI Grok, via its OpenAI-compatible endpoint.

Real providers are instructed to respond with strict JSON matching PlanResult's
schema; if a real provider errors or returns something unparsable, we log that as
a tool_error-style event and fall back to the mock planner for that step so a demo
never hard-fails just because a free-tier key is rate-limited.
"""
from __future__ import annotations

import json
import os
import re
from typing import Literal, Optional

from pydantic import BaseModel

from .models import ToolSpec


class PlanResult(BaseModel):
    reasoning: str
    action: Literal["call_tool", "finish"]
    tool_name: Optional[str] = None
    tool_args: dict = {}
    final_summary: Optional[str] = None


SYSTEM_PROMPT = """You are an autonomous incident-triage agent for a platform engineering team.
You investigate a production incident by calling read-only tools, then propose a remediation
action using an action tool. You never execute actions yourself — a human approves them.

Respond with ONLY a JSON object, no prose, matching this schema:
{{
  "reasoning": "<your reasoning about what to do next, 1-3 sentences>",
  "action": "call_tool" | "finish",
  "tool_name": "<tool name, required if action=call_tool>",
  "tool_args": {{...}},
  "final_summary": "<root cause + what was done, required if action=finish>"
}}

Available tools:
{tools}

Rules:
- Investigate with read-only tools (query_logs, query_metrics, get_service_status,
  get_deploy_history) before proposing an action tool.
- Propose exactly one action tool (restart_service, scale_service, rollback_deployment,
  page_oncall) once you have enough evidence, then wait for its result.
- If a tool call errored, do not repeat the exact same call blindly — retry at most once, or
  pick a different tool if it keeps failing.
- Once an action tool has succeeded, finish with a final_summary.
"""


def _tools_block(tool_specs: list[ToolSpec]) -> str:
    lines = []
    for t in tool_specs:
        approval = " (requires human approval)" if t.requires_approval else ""
        lines.append(f"- {t.name}{approval}: {t.description} params={t.parameters}")
    return "\n".join(lines)


def _service_from_text(text: str) -> str:
    for svc in ["checkout-service", "auth-service", "payments-service", "api-gateway"]:
        if svc.split("-")[0] in text.lower():
            return svc
    return "checkout-service"


def _mock_plan(incident: str, transcript_text: str, tool_specs: list[ToolSpec], last_error: Optional[str]) -> PlanResult:
    service = _service_from_text(incident)
    called = re.findall(r"TOOL_RESULT\[(\w+)\]", transcript_text)
    errored = re.findall(r"TOOL_ERROR\[(\w+)\]", transcript_text)

    if last_error:
        failed_tool = errored[-1] if errored else None
        if failed_tool and errored.count(failed_tool) >= 2:
            return PlanResult(
                reasoning=f"{failed_tool} failed twice; skipping it and trying a different signal.",
                action="call_tool",
                tool_name="get_service_status" if failed_tool != "get_service_status" else "query_metrics",
                tool_args={"service": service},
            )
        return PlanResult(
            reasoning=f"Last call to {failed_tool} hit a transient error; retrying once.",
            action="call_tool",
            tool_name=failed_tool or "query_logs",
            tool_args={"service": service},
        )

    investigation_order = ["query_logs", "query_metrics", "get_service_status", "get_deploy_history"]
    for tool in investigation_order:
        if tool not in called:
            return PlanResult(
                reasoning=f"Haven't checked {tool} yet; gathering evidence before proposing a fix.",
                action="call_tool",
                tool_name=tool,
                tool_args={"service": service},
            )

    acted = any(t in called for t in ["restart_service", "scale_service", "rollback_deployment"])
    if acted:
        return PlanResult(
            reasoning="Remediation action completed successfully; wrapping up triage.",
            action="finish",
            final_summary=(
                f"Root cause: {service} exhausted its DB connection pool shortly after the v2.14.1 "
                f"deploy, causing timeouts, pod crash-loops, and elevated 5xx/latency. Remediation "
                f"was applied and the service has recovered to healthy pod counts."
            ),
        )

    if "deploy" in transcript_text.lower() and "42m ago" in transcript_text:
        return PlanResult(
            reasoning=(
                "Logs/metrics show DB pool exhaustion and crash-looping pods starting right after "
                "a deploy 42 minutes ago — strongly suggests the new version regressed. Rolling back "
                "is the safest fix."
            ),
            action="call_tool",
            tool_name="rollback_deployment",
            tool_args={"service": service, "to_version": "v2.14.0"},
        )

    return PlanResult(
        reasoning="Evidence points to resource exhaustion; a rolling restart should recover pods.",
        action="call_tool",
        tool_name="restart_service",
        tool_args={"service": service},
    )


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in LLM response")
    return json.loads(match.group(0))


def _gemini_plan(prompt: str) -> PlanResult:
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"))
    response = model.generate_content(prompt)
    return PlanResult.model_validate(_extract_json(response.text))


def _grok_plan(prompt: str) -> PlanResult:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=os.environ.get("GROK_MODEL", "grok-beta"),
        messages=[{"role": "user", "content": prompt}],
    )
    return PlanResult.model_validate(_extract_json(response.choices[0].message.content))


def plan(
    incident: str,
    transcript_text: str,
    tool_specs: list[ToolSpec],
    last_error: Optional[str] = None,
) -> tuple[PlanResult, Optional[str]]:
    """Returns (plan_result, provider_warning). provider_warning is set when a real
    provider failed and we fell back to the mock planner for this step."""
    provider = os.environ.get("LLM_PROVIDER", "mock").lower()

    if provider == "mock":
        return _mock_plan(incident, transcript_text, tool_specs, last_error), None

    prompt = (
        SYSTEM_PROMPT.format(tools=_tools_block(tool_specs))
        + f"\n\nIncident: {incident}\n\nTranscript so far:\n{transcript_text or '(nothing yet)'}\n"
        + (f"\nLast tool error: {last_error}\n" if last_error else "")
        + "\nWhat do you do next? Respond with ONLY the JSON object."
    )

    try:
        if provider == "gemini":
            return _gemini_plan(prompt), None
        if provider == "grok":
            return _grok_plan(prompt), None
        raise ValueError(f"unknown LLM_PROVIDER: {provider}")
    except Exception as exc:  # noqa: BLE001 - deliberate broad fallback for demo robustness
        fallback = _mock_plan(incident, transcript_text, tool_specs, last_error)
        return fallback, f"{provider} planner failed ({exc}); used rule-based fallback for this step."
