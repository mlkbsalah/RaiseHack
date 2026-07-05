"""Generalized version of ``tap_agent``'s specialists: one multimodal call
per scheduled tick, per task.

Where the reference project hard-codes "vision specialist + audio specialist
+ deterministic tap rules + decision specialist" for exactly one use case,
here a single multimodal model call plays the role of all three specialist
roles at once, scoped to whatever ``task.focus`` says to look for. The
call is still stateless per the architecture: everything it needs (task
definition, its own memory tail, the subject's memory tail, current stream
frames) is assembled fresh from disk before the call, and everything it
learns is written back to disk after — no state lives in this object
between runs.
"""

from __future__ import annotations

from time import time
from typing import Any

from .approvals import ApprovalStore
from .llm_client import LLMClient, schema_instruction, validate_json
from .memory_store import MemoryStore
from .models import ActionProposal, AgentObservation, AgentRunResult, TaskSpec
from .stream_registry import StreamPayload, StreamRegistry

_OBSERVATION_SCHEMA = {
    "summary": "one or two plain-language sentences on what was observed this run",
    "anomaly_detected": (
        "boolean — true only if this run deviates from the agent memory's "
        "record of what is normal for this task"
    ),
    "anomaly_description": "string explaining the anomaly, or null if none",
    "subject_findings": (
        "array of short strings: durable new facts worth remembering about "
        "the subject (empty array if nothing new was learned)"
    ),
    "action_proposal": (
        "null unless a resident-facing action should be taken, else an "
        "object {action, reason, risk: low|medium|high}"
    ),
    "confidence": "number from 0.0 to 1.0",
}

_SYSTEM_PROMPT = (
    "You are one scheduled run of a smart-home monitoring agent assigned to "
    "a single task. You only observe and, when warranted, propose an action "
    "for a human to approve — you never directly control any device and "
    "your proposal will not execute automatically. Never claim more "
    "certainty than the evidence in the provided data streams supports; "
    "missing or ambiguous evidence should lower confidence and set "
    "anomaly_detected to false rather than produce a confident claim. "
    + schema_instruction("AgentObservation", _OBSERVATION_SCHEMA)
)


class TaskAgent:
    def __init__(
        self,
        llm: LLMClient,
        memory: MemoryStore,
        streams: StreamRegistry,
        approvals: ApprovalStore,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.streams = streams
        self.approvals = approvals
        # Set by app.py once the optional Telegram bridge exists; see orchestrator.py
        # for why this is duck-typed instead of importing TelegramBot directly.
        self.telegram: Any | None = None

    def run(self, task: TaskSpec) -> AgentRunResult:
        agent_memory = self.memory.read_agent_memory(task.task_id, task.title)
        subject_memory = (
            self.memory.read_subject_memory(task.subject_id) if task.subject_id else ""
        )
        payloads = [self.streams.get_payload(s.stream_id, s.kind) for s in task.streams]

        if self.llm.settings.mock_mode:
            observation = self._mock_observation(task, payloads)
        else:
            observation = self._live_observation(task, agent_memory, subject_memory, payloads)

        self._record(task, observation)

        pending_id = None
        if observation.action_proposal is not None:
            approval = self.approvals.create(task.task_id, task.title, observation.action_proposal)
            pending_id = approval.approval_id
            if self.telegram is not None:
                self.telegram.notify_approval_created(approval)

        return AgentRunResult(
            task_id=task.task_id,
            ran_at=time(),
            observation=observation,
            pending_approval_id=pending_id,
        )

    def _record(self, task: TaskSpec, observation: AgentObservation) -> None:
        pieces = [observation.summary]
        if observation.anomaly_detected:
            pieces.append(f"ANOMALY: {observation.anomaly_description or 'unspecified'}")
        if observation.action_proposal:
            proposal = observation.action_proposal
            pieces.append(
                f"proposed action pending approval: {proposal.action} ({proposal.risk} risk)"
            )
        self.memory.append_agent_log(task.task_id, task.title, " | ".join(pieces))
        if task.subject_id and observation.subject_findings:
            self.memory.append_subject_findings(
                task.subject_id, task.subject_id, task.title, observation.subject_findings
            )

    def _context_text(
        self,
        task: TaskSpec,
        agent_memory: str,
        subject_memory: str,
        payloads: list[StreamPayload],
    ) -> str:
        missing = [p.stream_id for p in payloads if not p.connected]
        lines = [
            f"Task: {task.title}",
            f"Description: {task.description}",
            f"What to look for this run: {task.focus}",
            f"Any action you propose requires human approval: {task.requires_approval}",
            "",
            "Tail of this agent's own memory log (may be empty on first run):",
            agent_memory or "(empty)",
        ]
        if task.subject_id:
            lines += ["", f"Tail of subject memory for '{task.subject_id}':", subject_memory or "(empty)"]
        if missing:
            lines += ["", f"No data received yet for stream(s): {', '.join(missing)}."]
        return "\n".join(lines)

    def _live_observation(
        self,
        task: TaskSpec,
        agent_memory: str,
        subject_memory: str,
        payloads: list[StreamPayload],
    ) -> AgentObservation:
        content: list[dict] = [
            {"type": "text", "text": self._context_text(task, agent_memory, subject_memory, payloads)}
        ]
        for payload in payloads:
            if not payload.connected or payload.data_url is None:
                continue
            if payload.kind == "image":
                content.append({"type": "image_url", "image_url": {"url": payload.data_url}})
            else:
                content.append({"type": "audio_url", "audio_url": {"url": payload.data_url}})

        raw = self.llm.chat_json(
            model=self.llm.settings.multimodal_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        return validate_json(raw, AgentObservation)

    def _mock_observation(self, task: TaskSpec, payloads: list[StreamPayload]) -> AgentObservation:
        connected = [p for p in payloads if p.connected]
        if not connected:
            return AgentObservation(
                summary="No connected data streams yet; nothing to observe.",
                anomaly_detected=False,
                confidence=0.0,
            )
        run_count = self.memory.run_count(task.task_id)
        if run_count % 3 == 2:
            return AgentObservation(
                summary=f"Mock run #{run_count + 1}: evidence has persisted past the usual pattern.",
                anomaly_detected=True,
                anomaly_description=f"{task.focus} — deviates from the learned baseline.",
                subject_findings=(
                    [f"Unusual activity observed during '{task.title}'."] if task.subject_id else []
                ),
                action_proposal=ActionProposal(
                    action=f"Notify resident about: {task.focus}",
                    reason="Sustained anomaly across mock observation ticks.",
                    risk="medium",
                ),
                confidence=0.8,
            )
        return AgentObservation(
            summary=f"Mock run #{run_count + 1}: '{task.title}' looks normal.",
            anomaly_detected=False,
            confidence=0.75,
        )
