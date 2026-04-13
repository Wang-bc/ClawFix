from __future__ import annotations

from app.config.models import InboundMessage, RouteDecision
from app.gateway.bindings import BindingStore


class Router:
    def __init__(self, bindings: BindingStore) -> None:
        self.bindings = bindings

    def route(self, inbound: InboundMessage, requested_session_key: str | None = None) -> RouteDecision:
        agent_id = self.bindings.resolve_agent_id(inbound["channel"], inbound["peer_id"])
        session_key = requested_session_key or f"{agent_id}:{inbound['channel']}:{inbound['account_id']}:{inbound['peer_id']}"
        return RouteDecision(
            agent_id=agent_id,
            rule_type="default-agent",
            session_key=session_key,
            dm_scope=inbound["peer_id"],
        )

