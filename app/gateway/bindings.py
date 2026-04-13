from __future__ import annotations


class BindingStore:
    def __init__(self, default_agent_id: str) -> None:
        self.default_agent_id = default_agent_id

    def resolve_agent_id(self, channel: str, peer_id: str) -> str:
        # MVP 阶段统一绑定到主控 Agent，后续可按 peer / channel / team 细化。
        _ = (channel, peer_id)
        return self.default_agent_id

