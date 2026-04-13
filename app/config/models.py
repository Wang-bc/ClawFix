from __future__ import annotations

from typing import Any, TypedDict


class InboundMessage(TypedDict):
    message_id: str
    channel: str
    account_id: str
    peer_id: str
    parent_peer_id: str | None
    sender_id: str
    sender_name: str | None
    text: str
    raw_payload: dict[str, Any]
    received_at: str
    reply_to_message_id: str | None


class RouteDecision(TypedDict):
    agent_id: str
    rule_type: str
    session_key: str
    dm_scope: str | None


class AgentRunRequest(TypedDict):
    run_id: str
    agent_id: str
    session_key: str
    user_text: str
    inbound: InboundMessage | None
    source: str
    created_at: str
    timeout_s: int


class AgentEvent(TypedDict):
    run_id: str
    session_key: str
    agent_id: str
    stream: str
    phase: str | None
    payload: dict[str, Any]
    created_at: str


class QueuedDelivery(TypedDict):
    delivery_id: str
    run_id: str
    channel: str
    account_id: str
    peer_id: str
    chunks: list[str]
    retry_count: int
    next_attempt_at: str
    status: str
    metadata: dict[str, Any]


class ChatAttachment(TypedDict):
    name: str
    content: str


class CandidateRootCause(TypedDict):
    title: str
    reasoning: str
    confidence: str


class DiagnosticReference(TypedDict, total=False):
    id: str
    type: str
    source_type: str
    title: str
    location: str
    snippet: str
    url: str
    score: float


class DiagnosticResult(TypedDict):
    task_type: str
    problem_category: str
    summary: str
    candidate_root_causes: list[CandidateRootCause]
    troubleshooting_steps: list[str]
    references: list[DiagnosticReference]
    missing_information: list[str]
    agents_used: list[str]
    reply_markdown: str


class AgentResearchReport(TypedDict):
    agent_id: str
    session_key: str
    focus: str
    summary: str
    evidence: list[DiagnosticReference]
    gaps: list[str]
    recommended_actions: list[str]
    raw_markdown: str


class EvidenceJudgementItem(TypedDict):
    id: str
    decision: str
    relevance_score: float
    reason: str


class EvidenceJudgeResult(TypedDict):
    agent_id: str
    session_key: str
    support_level: str
    summary: str
    selected_evidence_ids: list[str]
    selected_evidence: list[DiagnosticReference]
    ranked_evidence: list[EvidenceJudgementItem]
    missing_evidence: list[str]
    raw_markdown: str


class SessionSummary(TypedDict):
    overview: str
    known_facts: list[str]
    attempted_actions: list[str]
    unresolved_questions: list[str]
    important_references: list[str]


class SessionMemoryItem(TypedDict):
    memory_id: str
    kind: str
    title: str
    content: str
    checksum: str
    created_at: str
    updated_at: str
