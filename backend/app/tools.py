"""Mock tool registry the agent can call.

Read tools (logs/metrics/status/deploys) return simulated but internally-consistent
data derived from the incident text. Action tools (restart/scale/rollback/page) are
gated behind human approval by the agent loop before `execute` is ever reached.

Tools fail transiently about 1-in-4 calls to force the agent to exercise error
recovery (retry / route around) rather than assuming every call succeeds.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable

from .models import ToolSpec

TRANSIENT_FAILURE_RATE = 0.25


class ToolError(Exception):
    pass


@dataclass
class Tool:
    spec: ToolSpec
    fn: Callable[..., str]

    def execute(self, **kwargs: Any) -> str:
        if random.random() < TRANSIENT_FAILURE_RATE:
            raise ToolError(
                f"{self.spec.name}: transient error (timeout talking to backend), try again"
            )
        return self.fn(**kwargs)


def _service_from(text: str) -> str:
    for svc in ["checkout-service", "auth-service", "payments-service", "api-gateway"]:
        if svc.split("-")[0] in text.lower():
            return svc
    return "checkout-service"


def query_logs(service: str, minutes: int = 15) -> str:
    time.sleep(0.2)
    samples = [
        f"[{service}] ERROR connection pool exhausted (active=200/200)",
        f"[{service}] WARN upstream db p99 latency 1800ms (baseline 120ms)",
        f"[{service}] ERROR 5 consecutive timeouts to db-primary",
    ]
    return (
        f"Last {minutes}m of logs for {service}: {len(samples)} notable lines.\n"
        + "\n".join(samples)
    )


def query_metrics(service: str) -> str:
    time.sleep(0.2)
    return (
        f"Metrics for {service}: p99_latency_ms=2450 (baseline 180), "
        f"error_rate=7.8% (baseline 0.2%), cpu_pct=92, db_connections=200/200 (pool exhausted)"
    )


def get_service_status(service: str) -> str:
    time.sleep(0.2)
    return f"{service}: 3/5 pods Ready, 2 pods CrashLoopBackOff, last deploy 42m ago (v2.14.1)"


def get_deploy_history(service: str) -> str:
    time.sleep(0.2)
    return (
        f"Deploy history for {service}: v2.14.1 deployed 42m ago by ci-bot "
        f"(preceding incident window), v2.14.0 stable for 6 days prior."
    )


def restart_service(service: str) -> str:
    time.sleep(0.3)
    return f"{service}: rolling restart triggered, 5/5 pods Ready after restart."


def scale_service(service: str, replicas: int) -> str:
    time.sleep(0.3)
    return f"{service}: scaled to {replicas} replicas."


def rollback_deployment(service: str, to_version: str) -> str:
    time.sleep(0.3)
    return f"{service}: rolled back to {to_version}, 5/5 pods Ready."


def page_oncall(service: str, summary: str) -> str:
    time.sleep(0.2)
    return f"Paged on-call for {service}: \"{summary}\" (ack pending)."


REGISTRY: dict[str, Tool] = {
    "query_logs": Tool(
        ToolSpec(
            name="query_logs",
            description="Fetch recent log lines for a service.",
            parameters={"service": "string", "minutes": "integer (optional, default 15)"},
            requires_approval=False,
        ),
        query_logs,
    ),
    "query_metrics": Tool(
        ToolSpec(
            name="query_metrics",
            description="Fetch current latency/error-rate/resource metrics for a service.",
            parameters={"service": "string"},
            requires_approval=False,
        ),
        query_metrics,
    ),
    "get_service_status": Tool(
        ToolSpec(
            name="get_service_status",
            description="Fetch pod/replica health status for a service.",
            parameters={"service": "string"},
            requires_approval=False,
        ),
        get_service_status,
    ),
    "get_deploy_history": Tool(
        ToolSpec(
            name="get_deploy_history",
            description="Fetch recent deploy history for a service.",
            parameters={"service": "string"},
            requires_approval=False,
        ),
        get_deploy_history,
    ),
    "restart_service": Tool(
        ToolSpec(
            name="restart_service",
            description="Rolling-restart a service's pods. Destructive: requires human approval.",
            parameters={"service": "string"},
            requires_approval=True,
        ),
        restart_service,
    ),
    "scale_service": Tool(
        ToolSpec(
            name="scale_service",
            description="Change replica count for a service. Destructive: requires human approval.",
            parameters={"service": "string", "replicas": "integer"},
            requires_approval=True,
        ),
        scale_service,
    ),
    "rollback_deployment": Tool(
        ToolSpec(
            name="rollback_deployment",
            description="Roll a service back to a previous version. Destructive: requires human approval.",
            parameters={"service": "string", "to_version": "string"},
            requires_approval=True,
        ),
        rollback_deployment,
    ),
    "page_oncall": Tool(
        ToolSpec(
            name="page_oncall",
            description="Page the human on-call engineer with a summary. Requires human approval.",
            parameters={"service": "string", "summary": "string"},
            requires_approval=True,
        ),
        page_oncall,
    ),
}


def list_tool_specs() -> list[ToolSpec]:
    return [t.spec for t in REGISTRY.values()]


def requires_approval(tool_name: str) -> bool:
    return REGISTRY[tool_name].spec.requires_approval


def call_tool(tool_name: str, **kwargs: Any) -> str:
    if tool_name not in REGISTRY:
        raise ToolError(f"unknown tool: {tool_name}")
    return REGISTRY[tool_name].execute(**kwargs)
