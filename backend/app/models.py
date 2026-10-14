"""Pydantic models shared across the agent runtime and API."""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    FAILED = "failed"


class StepType(str, Enum):
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    REFLECTION = "reflection"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_DECISION = "approval_decision"
    RESOLUTION = "resolution"
    SYSTEM = "system"


class Step(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: StepType
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict[str, Any]] = None
    created_at: float = Field(default_factory=time.time)


class PendingAction(BaseModel):
    tool_name: str
    tool_args: dict[str, Any]
    reason: str


class Run(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    incident: str
    status: RunStatus = RunStatus.PLANNING
    transcript: list[Step] = Field(default_factory=list)
    pending_action: Optional[PendingAction] = None
    retry_count: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def add_step(self, step: Step) -> None:
        self.transcript.append(step)
        self.updated_at = time.time()


class CreateIncidentRequest(BaseModel):
    description: str


class DecisionRequest(BaseModel):
    approve: bool
    note: Optional[str] = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    requires_approval: bool
