"""In-memory pending-approval queue.

An agent never executes an ``action_proposal`` itself — it only ever files
one here. The only way a proposal's status changes is a human clicking
Approve/Deny in the UI. Nothing in this codebase wires an approval to an
actual actuator: there is no smart-home/email/Doctolib integration behind
"approve", by design, so approving something in this prototype can never
have a real-world side effect.
"""

from __future__ import annotations

import threading
from time import time
from uuid import uuid4

from .models import ActionProposal, ApprovalRequest


class ApprovalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._approvals: dict[str, ApprovalRequest] = {}

    def create(self, task_id: str, task_title: str, proposal: ActionProposal) -> ApprovalRequest:
        approval = ApprovalRequest(
            approval_id=str(uuid4())[:8],
            task_id=task_id,
            task_title=task_title,
            action=proposal.action,
            reason=proposal.reason,
            risk=proposal.risk,
            status="pending",
            created_at=time(),
        )
        with self._lock:
            self._approvals[approval.approval_id] = approval
        return approval

    def list_pending(self) -> list[ApprovalRequest]:
        return sorted(
            (a for a in self._approvals.values() if a.status == "pending"),
            key=lambda a: a.created_at,
        )

    def list_for_task(self, task_id: str) -> list[ApprovalRequest]:
        return [a for a in self._approvals.values() if a.task_id == task_id]

    def decide(self, approval_id: str, approve: bool) -> ApprovalRequest | None:
        with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None or approval.status != "pending":
                return approval
            approval.status = "approved" if approve else "denied"
            approval.resolved_at = time()
            return approval

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._approvals.get(approval_id)
