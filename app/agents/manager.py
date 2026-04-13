from __future__ import annotations

from pathlib import Path

from app.agents.models import AgentProfile


class AgentManager:
    """Registry for all built-in agents in the workspace."""

    def __init__(self, workspace_root: Path) -> None:
        agent_root = workspace_root / "agents"
        self._agents = {
            "coordinator": AgentProfile(
                agent_id="coordinator",
                name="Coordinator Agent",
                description=(
                    "Owns the final diagnosis, coordinates sub-agents, and returns the final structured reply."
                ),
                workspace_root=agent_root / "coordinator",
            ),
            "internal_retriever": AgentProfile(
                agent_id="internal_retriever",
                name="Internal Retriever Agent",
                description=(
                    "Searches internal knowledge, prior cases, and memory indexes for directly relevant evidence."
                ),
                workspace_root=agent_root / "internal_retriever",
            ),
            "external_researcher": AgentProfile(
                agent_id="external_researcher",
                name="External Researcher Agent",
                description=(
                    "Searches external technical sources and extracts evidence that may support the diagnosis."
                ),
                workspace_root=agent_root / "external_researcher",
            ),
            "evidence_judge": AgentProfile(
                agent_id="evidence_judge",
                name="Evidence Judge Agent",
                description=(
                    "Reviews candidate evidence from internal and external agents, approves only directly useful evidence,"
                    " and returns a strict evidence allow-list."
                ),
                workspace_root=agent_root / "evidence_judge",
            ),
        }

    @property
    def default_agent_id(self) -> str:
        return "coordinator"

    def get_agent(self, agent_id: str) -> AgentProfile:
        return self._agents[agent_id]

    def list_agents(self) -> list[AgentProfile]:
        return list(self._agents.values())
