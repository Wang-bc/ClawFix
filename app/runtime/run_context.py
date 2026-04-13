from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config.models import AgentRunRequest


@dataclass(slots=True)
class RunContext:
    run_id: str
    agent_id: str
    session_key: str
    created_at: str
    deadline: str

    @classmethod
    def from_request(cls, request: AgentRunRequest) -> "RunContext":
        created = datetime.fromisoformat(request["created_at"])
        deadline = created + timedelta(seconds=request["timeout_s"])
        return cls(
            run_id=request["run_id"],
            agent_id=request["agent_id"],
            session_key=request["session_key"],
            created_at=request["created_at"],
            deadline=deadline.isoformat(timespec="seconds"),
        )

