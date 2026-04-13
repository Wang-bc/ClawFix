from __future__ import annotations

from datetime import datetime

from app.config.models import AgentEvent


def build_event(
    run_id: str,
    session_key: str,
    agent_id: str,
    stream: str,
    phase: str | None,
    payload: dict[str, object],
) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        session_key=session_key,
        agent_id=agent_id,
        stream=stream,
        phase=phase,
        payload=payload,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )

