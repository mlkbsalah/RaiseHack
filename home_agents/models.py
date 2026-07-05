"""Structured data models shared across the framework."""

from __future__ import annotations

from time import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


StreamKind = Literal["image", "audio"]
GoogleActionType = Literal[
    "send_email",
    "create_calendar_event",
    "create_task",
    "create_keep_note",
]
Intent = Literal[
    "create_task",
    "update_task",
    "pause_task",
    "resume_task",
    "delete_task",
    "list_tasks",
    "list_streams",
    "connect_google",
    "chat",
]
Risk = Literal["low", "medium", "high"]


class SerializableModel(BaseModel):
    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


class StreamRequirement(SerializableModel):
    stream_id: str
    kind: StreamKind


class TaskDraft(SerializableModel):
    """What the orchestrator LLM proposes; not yet a persisted task."""

    title: str
    description: str
    focus: str
    interval_seconds: int = Field(default=300, ge=1)
    subject_id: str | None = None
    subject_label: str | None = None
    streams: list[StreamRequirement] = Field(default_factory=list)
    requires_approval: bool = True


class TaskUpdateDraft(SerializableModel):
    """A partial update to an existing task."""

    title: str | None = None
    description: str | None = None
    focus: str | None = None
    interval_seconds: int | None = Field(default=None, ge=1)
    subject_id: str | None = None
    subject_label: str | None = None
    streams: list[StreamRequirement] | None = None
    requires_approval: bool | None = None


class TaskSpec(SerializableModel):
    """A persisted, schedulable task."""

    task_id: str
    title: str
    description: str
    focus: str
    interval_seconds: int
    subject_id: str | None = None
    streams: list[StreamRequirement] = Field(default_factory=list)
    requires_approval: bool = True
    status: Literal["active", "paused"] = "active"
    created_at: float
    updated_at: float
    last_run_at: float | None = None

    @classmethod
    def from_draft(cls, draft: TaskDraft, task_id: str | None = None) -> "TaskSpec":
        now = time()
        return cls(
            task_id=task_id or str(uuid4())[:8],
            title=draft.title,
            description=draft.description,
            focus=draft.focus,
            interval_seconds=draft.interval_seconds,
            subject_id=draft.subject_id,
            streams=draft.streams,
            requires_approval=draft.requires_approval,
            status="active",
            created_at=now,
            updated_at=now,
        )


class ActionProposal(SerializableModel):
    action: str
    reason: str
    risk: Risk
    action_type: GoogleActionType | None = None
    action_payload: dict[str, Any] = Field(default_factory=dict)


class GoogleActionPlan(SerializableModel):
    """Structured plan for a direct Google Workspace chat request."""

    is_google_action: bool
    action_type: GoogleActionType | None = None
    action_payload: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    summary: str | None = None


class AgentObservation(SerializableModel):
    summary: str
    anomaly_detected: bool
    anomaly_description: str | None = None
    subject_findings: list[str] = Field(default_factory=list)
    action_proposal: ActionProposal | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ApprovalRequest(SerializableModel):
    approval_id: str
    task_id: str
    task_title: str
    action: str
    reason: str
    risk: Risk
    action_type: GoogleActionType | None = None
    action_payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "approved", "denied"] = "pending"
    execution_status: Literal["not_executable", "pending", "succeeded", "failed"] = "not_executable"
    execution_result: str | None = None
    created_at: float
    resolved_at: float | None = None


class AgentRunResult(SerializableModel):
    task_id: str
    ran_at: float
    observation: AgentObservation
    pending_approval_id: str | None = None


class OrchestratorReply(SerializableModel):
    intent: Intent
    reply: str
    task: TaskDraft | TaskUpdateDraft | None = None
    target_task_id: str | None = None


class DangerCheck(SerializableModel):
    """One safety-monitor tick's verdict for one camera+mic pair."""

    danger_detected: bool
    danger_type: str | None = None
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    urgency: Risk = "low"


class SafetyAlert(SerializableModel):
    """A raised danger check, kept for the web banner and Telegram push."""

    alert_id: str
    stream_name: str
    danger_type: str
    description: str
    confidence: float
    urgency: Risk
    status: Literal["active", "dismissed"] = "active"
    created_at: float
    dismissed_at: float | None = None
