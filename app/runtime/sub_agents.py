from __future__ import annotations

import logging
import re
from typing import Any

from app.config.models import (
    AgentResearchReport,
    DiagnosticReference,
    EvidenceJudgeResult,
    EvidenceJudgementItem,
)
from app.llm.client import LLMClient
from app.llm.schemas import AGENT_RESEARCH_REPORT_SCHEMA, EVIDENCE_JUDGE_SCHEMA
from app.prompt.builder import PromptBuilder
from app.sessions.session_store import SessionStore
from app.tools.dispatcher import ToolDispatcher

PLACEHOLDER_REPORT_SUMMARIES = (
    "agent report generated without a usable summary",
    "sub-agent report generation failed",
    "fallback report generated from current evidence",
    "llm is disabled in the current runtime",
)
QUERY_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.:/-]+|[\u4e00-\u9fff]{2,}")
QUERY_STOPWORDS = {
    "error",
    "exception",
    "issue",
    "problem",
    "debug",
    "stack",
    "trace",
    "with",
    "from",
    "this",
    "that",
    "java",
    "python",
    "project",
}
HARD_ANCHOR_HINTS = (
    "exception",
    "error",
    "timeout",
    "timedout",
    "refused",
    "failed",
    "failure",
    "nullpointer",
    "indexoutofbounds",
    "filenotfound",
    "classcast",
    "illegalstate",
    "illegalargument",
    "socket",
    "connect",
    "redis",
    "mysql",
    "postgres",
    "kafka",
    "spring",
    "http",
)

logger = logging.getLogger("clawfix")


class SubAgentRunner:
    def __init__(
        self,
        session_store: SessionStore,
        prompt_builder: PromptBuilder,
        dispatcher: ToolDispatcher,
        llm_client: LLMClient,
    ) -> None:
        self.session_store = session_store
        self.prompt_builder = prompt_builder
        self.dispatcher = dispatcher
        self.llm_client = llm_client

    def run_internal_agent(
        self,
        *,
        parent_session_key: str,
        run_id: str,
        user_text: str,
        emit,
    ) -> AgentResearchReport:
        child_session_key = f"{parent_session_key}::internal::{run_id}"
        logger.info(
            "Sub-agent start agent=%s parent_session=%s child_session=%s",
            "internal_retriever",
            parent_session_key,
            child_session_key,
        )
        results = self._call_tool(emit, "memory_search", {"query": user_text, "limit": 6}).get("results", [])
        logger.info(
            "Sub-agent tool_result agent=%s tool=%s hits=%s items=%s",
            "internal_retriever",
            "memory_search",
            len(results),
            self._format_tool_items(results, location_key="path"),
        )
        evidence = [
            self._make_reference(
                ref_id=f"internal_{index}",
                source_type="internal",
                title=str(item.get("title", "Untitled")),
                location=f"{item.get('path')}:{item.get('line_start', 1)}",
                snippet=str(item.get("snippet", "")),
                score=float(item.get("score", 0.0) or 0.0),
            )
            for index, item in enumerate(results, start=1)
        ]
        prompt = self.prompt_builder.build(
            "internal_retriever",
            user_text,
            [],
            extra_sections=[
                "You are the internal retrieval agent. Summarize only evidence that directly supports the current diagnosis.",
                "Do not describe internal execution details. Focus on what the evidence says.",
                "Internal evidence candidates:\n"
                + "\n".join(
                    f"- {item['id']} | {item['title']} | {item.get('location', '')} | {item['snippet']}"
                    for item in evidence
                ),
            ],
        )
        report = self._generate_report(
            agent_id="internal_retriever",
            session_key=child_session_key,
            focus="Search internal cases, knowledge, and runbooks for directly relevant evidence.",
            user_text=user_text,
            evidence=evidence,
            system_prompt=prompt["system_prompt"],
        )
        self._log_report("internal_retriever", report)
        return report

    def run_external_agent(
        self,
        *,
        parent_session_key: str,
        run_id: str,
        user_text: str,
        emit,
    ) -> AgentResearchReport:
        child_session_key = f"{parent_session_key}::external::{run_id}"
        logger.info(
            "Sub-agent start agent=%s parent_session=%s child_session=%s",
            "external_researcher",
            parent_session_key,
            child_session_key,
        )

        queries = self._build_external_queries(user_text)
        merged_results: dict[str, dict[str, object]] = {}
        for query in queries:
            response = self._call_tool(
                emit,
                "web_search",
                {
                    "query": query,
                    "limit": 3,
                    "search_depth": "advanced",
                    "topic": "general",
                },
            )
            search_results = response.get("results", [])
            logger.info(
                "Sub-agent tool_result agent=%s tool=%s query=%s hits=%s items=%s",
                "external_researcher",
                "web_search",
                self._compact_text(query, 120),
                len(search_results),
                self._format_tool_items(search_results, location_key="url"),
            )
            self._merge_external_search_results(merged_results, query, search_results)

        ranked_results = sorted(
            merged_results.values(),
            key=lambda item: float(item.get("score", 0.0) or 0.0),
            reverse=True,
        )
        evidence: list[DiagnosticReference] = []
        fetch_blocks: list[str] = []
        for index, item in enumerate(ranked_results[:3], start=1):
            fetched = self._call_tool(
                emit,
                "web_fetch",
                {
                    "url": str(item.get("url", "")),
                    "max_chars": 1800,
                    "query": str(item.get("query", "") or user_text),
                    "extract_depth": "advanced",
                },
            )
            logger.info(
                "Sub-agent tool_result agent=%s tool=%s url=%s ok=%s chars=%s",
                "external_researcher",
                "web_fetch",
                str(item.get("url", "")),
                bool(fetched.get("ok")),
                len(str(fetched.get("content", ""))),
            )
            snippet = self._compact_text(
                str(fetched.get("content", "")).strip() or str(item.get("snippet", "")).strip() or "No useful content extracted.",
                320,
            )
            evidence.append(
                self._make_reference(
                    ref_id=f"external_{index}",
                    source_type="external",
                    title=str(item.get("title", "Untitled")),
                    url=str(item.get("url", "")),
                    snippet=snippet,
                    score=float(item.get("score", 0.0) or 0.0),
                )
            )
            fetch_blocks.append(
                f"- {item.get('title')} | {item.get('url')} | score={float(item.get('score', 0.0) or 0.0):.3f}\n{snippet}"
            )

        prompt = self.prompt_builder.build(
            "external_researcher",
            user_text,
            [],
            extra_sections=[
                "You are the external research agent. Prefer official docs, standards, issues, and high-quality technical references.",
                "Ignore generic tutorials unless they directly explain the reported bug or fix path.",
                "External search queries:\n" + "\n".join(f"- {query}" for query in queries),
                "Fetched external evidence:\n" + ("\n\n".join(fetch_blocks) if fetch_blocks else "- No external evidence found."),
            ],
        )
        report = self._generate_report(
            agent_id="external_researcher",
            session_key=child_session_key,
            focus="Search the web for external evidence that directly supports or challenges the diagnosis.",
            user_text=user_text,
            evidence=evidence,
            system_prompt=prompt["system_prompt"],
        )
        self._log_report("external_researcher", report)
        return report

    def run_evidence_judge(
        self,
        *,
        parent_session_key: str,
        run_id: str,
        user_text: str,
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
    ) -> EvidenceJudgeResult:
        child_session_key = f"{parent_session_key}::judge::{run_id}"
        logger.info(
            "Sub-agent start agent=%s parent_session=%s child_session=%s",
            "evidence_judge",
            parent_session_key,
            child_session_key,
        )
        candidates = self._dedupe_evidence(
            list(internal_report.get("evidence", []))[:6]
            + (list(external_report.get("evidence", []))[:3] if external_report else []),
            limit=8,
        )
        logger.info(
            "Sub-agent candidate_evidence agent=%s count=%s evidence=%s",
            "evidence_judge",
            len(candidates),
            self._format_evidence(candidates),
        )

        prompt = self.prompt_builder.build(
            "evidence_judge",
            user_text,
            [],
            extra_sections=[
                "You are the evidence_judge agent. Your only job is to decide whether the candidate evidence is directly usable.",
                "Treat internal and external evidence with equal priority. Rank only by direct support for the current bug.",
                "Only keep evidence that directly supports the likely root cause, mechanism, or a concrete troubleshooting action.",
                "Reject background material, generic tutorials, and evidence for a different exception, error code, or subsystem.",
                "If nothing is directly usable, return support_level=none and an empty selected_evidence_ids array.",
            ],
        )
        self.session_store.append_message(
            "evidence_judge",
            child_session_key,
            "user",
            user_text,
            metadata={"candidate_count": len(candidates)},
        )
        user_prompt = self._build_evidence_judge_user_prompt(
            user_text=user_text,
            internal_report=internal_report,
            external_report=external_report,
            candidates=candidates,
        )

        if self.llm_client.enabled:
            try:
                raw_judgement = self.llm_client.complete_json(
                    system_prompt=prompt["system_prompt"],
                    user_prompt=user_prompt,
                    schema_name="evidence_judge",
                    schema=EVIDENCE_JUDGE_SCHEMA,
                    model=self.llm_client.settings.llm_summary_model,
                    temperature=0.0,
                )
                judgement = self._normalize_evidence_judgement(
                    session_key=child_session_key,
                    candidates=candidates,
                    payload=raw_judgement,
                )
                if self._is_low_quality_evidence_judgement(judgement):
                    relaxed_payload = self.llm_client.complete_json_relaxed(
                        system_prompt=prompt["system_prompt"],
                        user_prompt=user_prompt,
                        schema_name="evidence_judge",
                        schema=EVIDENCE_JUDGE_SCHEMA,
                        model=self.llm_client.settings.llm_summary_model,
                        temperature=0.0,
                    )
                    judgement = self._normalize_evidence_judgement(
                        session_key=child_session_key,
                        candidates=candidates,
                        payload=relaxed_payload,
                    )
            except Exception as exc:  # noqa: BLE001
                judgement = self._fallback_evidence_judgement(
                    session_key=child_session_key,
                    user_text=user_text,
                    candidates=candidates,
                    failure_note=str(exc),
                )
        else:
            judgement = self._fallback_evidence_judgement(
                session_key=child_session_key,
                user_text=user_text,
                candidates=candidates,
                failure_note="",
            )

        self.session_store.append_message(
            "evidence_judge",
            child_session_key,
            "assistant",
            judgement["raw_markdown"],
            metadata={"judge": judgement},
        )
        logger.info(
            "Sub-agent report agent=%s support_level=%s selected=%s summary=%s",
            "evidence_judge",
            judgement["support_level"],
            self._format_evidence(judgement.get("selected_evidence", [])),
            self._compact_text(judgement.get("summary", ""), 180),
        )
        return judgement

    def _generate_report(
        self,
        *,
        agent_id: str,
        session_key: str,
        focus: str,
        user_text: str,
        evidence: list[DiagnosticReference],
        system_prompt: str,
    ) -> AgentResearchReport:
        self.session_store.append_message(agent_id, session_key, "user", user_text, metadata={"focus": focus})
        user_prompt = self._build_user_prompt(user_text, focus, evidence)

        if self.llm_client.enabled:
            try:
                raw_report = self.llm_client.complete_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_name=f"{agent_id}_report",
                    schema=AGENT_RESEARCH_REPORT_SCHEMA,
                    model=self.llm_client.settings.llm_summary_model,
                    temperature=0.1,
                )
                report = self._normalize_report(
                    agent_id=agent_id,
                    session_key=session_key,
                    focus=focus,
                    evidence=evidence,
                    payload=raw_report,
                )
                if self._is_low_quality_report(report):
                    relaxed_report = self.llm_client.complete_json_relaxed(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        schema_name=f"{agent_id}_report",
                        schema=AGENT_RESEARCH_REPORT_SCHEMA,
                        model=self.llm_client.settings.llm_summary_model,
                        temperature=0.1,
                    )
                    report = self._normalize_report(
                        agent_id=agent_id,
                        session_key=session_key,
                        focus=focus,
                        evidence=evidence,
                        payload=relaxed_report,
                    )
            except Exception as exc:  # noqa: BLE001
                report = self._fallback_report(agent_id, session_key, focus, evidence, str(exc))
        else:
            report = self._fallback_report(agent_id, session_key, focus, evidence, "")

        report["evidence"] = evidence[:4]
        if not report["summary"].strip():
            report["summary"] = self._fallback_report_summary(agent_id, report["evidence"])
        report["raw_markdown"] = self._build_report_markdown(
            agent_id=agent_id,
            focus=report["focus"],
            summary=report["summary"],
            evidence=report["evidence"],
            gaps=report["gaps"],
            recommended_actions=report["recommended_actions"],
        )
        self.session_store.append_message(
            agent_id,
            session_key,
            "assistant",
            report["raw_markdown"],
            metadata={"report": report},
        )
        return report

    def _normalize_report(
        self,
        *,
        agent_id: str,
        session_key: str,
        focus: str,
        evidence: list[DiagnosticReference],
        payload: dict[str, Any],
    ) -> AgentResearchReport:
        normalized_evidence = self._normalize_evidence(payload.get("evidence"), fallback=evidence)
        trusted_evidence = self._bind_evidence(normalized_evidence, evidence)
        raw_summary = str(payload.get("summary", "")).strip()
        summary = raw_summary or self._fallback_report_summary(agent_id, trusted_evidence)
        if trusted_evidence != normalized_evidence:
            summary = self._fallback_report_summary(agent_id, trusted_evidence)
        gaps = self._normalize_string_list(payload.get("gaps"))
        recommended_actions = self._normalize_string_list(payload.get("recommended_actions"))
        raw_markdown = str(payload.get("raw_markdown", "")).strip()
        if not raw_markdown:
            raw_markdown = self._build_report_markdown(
                agent_id=agent_id,
                focus=str(payload.get("focus", "")).strip() or focus,
                summary=summary,
                evidence=trusted_evidence,
                gaps=gaps,
                recommended_actions=recommended_actions,
            )

        return AgentResearchReport(
            agent_id=agent_id,
            session_key=session_key,
            focus=str(payload.get("focus", "")).strip() or focus,
            summary=summary,
            evidence=trusted_evidence,
            gaps=gaps,
            recommended_actions=recommended_actions,
            raw_markdown=raw_markdown,
        )

    def _normalize_evidence(
        self,
        raw_items: object,
        *,
        fallback: list[DiagnosticReference],
    ) -> list[DiagnosticReference]:
        if not isinstance(raw_items, list):
            return fallback[:4]

        normalized: list[DiagnosticReference] = []
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            if not title or not snippet:
                continue
            normalized.append(
                DiagnosticReference(
                    id=str(item.get("id", "")).strip() or f"candidate_{index}",
                    type=str(item.get("type", "Reference")).strip() or "Reference",
                    source_type=str(item.get("source_type", "")).strip(),
                    title=title,
                    location=str(item.get("location", "")).strip(),
                    url=str(item.get("url", "")).strip(),
                    snippet=snippet,
                    score=float(item.get("score", 0.0) or 0.0),
                )
            )
        return normalized[:4] or fallback[:4]

    def _bind_evidence(
        self,
        candidates: list[DiagnosticReference],
        allowed: list[DiagnosticReference],
    ) -> list[DiagnosticReference]:
        allowed_map = {self._evidence_identity(item): item for item in allowed[:8]}
        bound: list[DiagnosticReference] = []
        for item in candidates:
            trusted = allowed_map.get(self._evidence_identity(item))
            if trusted is not None:
                bound.append(dict(trusted))
        return bound or allowed[:4]

    def _evidence_identity(self, item: DiagnosticReference) -> str:
        ref_id = str(item.get("id", "")).strip().lower()
        if ref_id:
            return f"id:{ref_id}"
        url = str(item.get("url", "")).strip().lower()
        if url:
            return f"url:{url}"
        location = str(item.get("location", "")).strip().lower()
        return f"location:{location}"

    def _normalize_string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [str(item).strip() for item in value if str(item).strip()]
        return items[:6]

    def _normalize_evidence_judgement(
        self,
        *,
        session_key: str,
        candidates: list[DiagnosticReference],
        payload: dict[str, Any],
    ) -> EvidenceJudgeResult:
        candidate_map = {
            str(item.get("id", "")).strip(): dict(item)
            for item in candidates
            if str(item.get("id", "")).strip()
        }
        support_level = str(payload.get("support_level", "none")).strip().lower()
        if support_level not in {"sufficient", "weak", "none"}:
            support_level = "none"

        selected_ids: list[str] = []
        for item in payload.get("selected_evidence_ids", []):
            evidence_id = str(item).strip()
            if evidence_id and evidence_id in candidate_map and evidence_id not in selected_ids:
                selected_ids.append(evidence_id)

        ranked_evidence = self._normalize_ranked_evidence(payload.get("ranked_evidence"), candidate_map)
        if not selected_ids:
            for item in ranked_evidence:
                if item["decision"] == "keep" and item["id"] not in selected_ids:
                    selected_ids.append(item["id"])
                if len(selected_ids) >= 2:
                    break

        selected_evidence = [candidate_map[item_id] for item_id in selected_ids if item_id in candidate_map][:2]
        if not selected_evidence:
            support_level = "none" if support_level == "sufficient" else support_level
        elif support_level == "none":
            support_level = "sufficient"

        summary = str(payload.get("summary", "")).strip()
        if not summary:
            if support_level == "sufficient":
                summary = "Selected evidence directly supports the current diagnosis."
            elif support_level == "weak":
                summary = "Candidate evidence is only weakly related to the current diagnosis."
            else:
                summary = "No directly usable evidence was found for the current diagnosis."

        missing_evidence = self._normalize_string_list(payload.get("missing_evidence"))
        raw_markdown = str(payload.get("raw_markdown", "")).strip()
        if not raw_markdown:
            raw_markdown = self._build_evidence_judge_markdown(
                support_level=support_level,
                summary=summary,
                selected_evidence=selected_evidence,
                ranked_evidence=ranked_evidence,
                missing_evidence=missing_evidence,
            )

        return EvidenceJudgeResult(
            agent_id="evidence_judge",
            session_key=session_key,
            support_level=support_level,
            summary=summary,
            selected_evidence_ids=[str(item.get("id", "")) for item in selected_evidence if str(item.get("id", "")).strip()],
            selected_evidence=selected_evidence,
            ranked_evidence=ranked_evidence,
            missing_evidence=missing_evidence,
            raw_markdown=raw_markdown,
        )

    def _normalize_ranked_evidence(
        self,
        raw_items: object,
        candidate_map: dict[str, dict[str, object]],
    ) -> list[EvidenceJudgementItem]:
        if not isinstance(raw_items, list):
            return []
        normalized: list[EvidenceJudgementItem] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("id", "")).strip()
            if not evidence_id or evidence_id not in candidate_map:
                continue
            decision = str(item.get("decision", "reject")).strip().lower()
            if decision not in {"keep", "reject"}:
                decision = "reject"
            normalized.append(
                EvidenceJudgementItem(
                    id=evidence_id,
                    decision=decision,
                    relevance_score=float(item.get("relevance_score", 0.0) or 0.0),
                    reason=str(item.get("reason", "")).strip() or "No reason provided.",
                )
            )
        return normalized[:8]

    def _build_report_markdown(
        self,
        *,
        agent_id: str,
        focus: str,
        summary: str,
        evidence: list[DiagnosticReference],
        gaps: list[str],
        recommended_actions: list[str],
    ) -> str:
        lines = [
            f"## {agent_id} report",
            focus,
            "",
            "### Summary",
            summary,
        ]

        if evidence:
            lines.extend(["", "### Evidence"])
            lines.extend(
                [
                    f"- {item.get('id', '')} | {item.get('title', 'Untitled')} | {item.get('url') or item.get('location', '')}"
                    for item in evidence
                ]
            )

        if gaps:
            lines.extend(["", "### Gaps"])
            lines.extend([f"- {item}" for item in gaps])

        if recommended_actions:
            lines.extend(["", "### Recommended Actions"])
            lines.extend([f"- {item}" for item in recommended_actions])

        return "\n".join(lines)

    def _build_evidence_judge_markdown(
        self,
        *,
        support_level: str,
        summary: str,
        selected_evidence: list[DiagnosticReference],
        ranked_evidence: list[EvidenceJudgementItem],
        missing_evidence: list[str],
    ) -> str:
        lines = [
            "## evidence_judge report",
            f"support_level={support_level}",
            "",
            "### Summary",
            summary,
        ]
        if selected_evidence:
            lines.extend(["", "### Selected Evidence"])
            lines.extend(
                [
                    f"- {item.get('id', '')} | [{item.get('type', 'Reference')}] {item.get('title', 'Untitled')} | {item.get('url') or item.get('location', '')}"
                    for item in selected_evidence
                ]
            )
        if ranked_evidence:
            lines.extend(["", "### Ranked Evidence"])
            lines.extend(
                [
                    f"- {item['id']} | {item['decision']} | score={item['relevance_score']:.3f} | {item['reason']}"
                    for item in ranked_evidence
                ]
            )
        if missing_evidence:
            lines.extend(["", "### Missing Evidence"])
            lines.extend([f"- {item}" for item in missing_evidence])
        return "\n".join(lines)

    def _build_user_prompt(
        self,
        user_text: str,
        focus: str,
        evidence: list[DiagnosticReference],
    ) -> str:
        return f"User issue:\n{user_text}\n\nFocus:\n{focus}\n\nEvidence candidates:\n{evidence}"

    def _build_evidence_judge_user_prompt(
        self,
        *,
        user_text: str,
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
        candidates: list[DiagnosticReference],
    ) -> str:
        return (
            f"User issue:\n{user_text}\n\n"
            f"Internal report summary:\n{internal_report.get('summary', '')}\n\n"
            f"External report summary:\n{external_report.get('summary', '') if external_report else 'Not available'}\n\n"
            f"Candidate evidence objects:\n{candidates}\n"
        )

    def _fallback_report(
        self,
        agent_id: str,
        session_key: str,
        focus: str,
        evidence: list[DiagnosticReference],
        failure_note: str,
    ) -> AgentResearchReport:
        summary = self._fallback_report_summary(agent_id, evidence)
        gaps = ["Structured summarization failed; using evidence-driven fallback report."] if failure_note else []
        recommended_actions = (
            ["Continue the diagnosis using the evidence already retrieved."]
            if evidence
            else ["Collect more context and search again."]
        )
        raw_markdown = self._build_report_markdown(
            agent_id=agent_id,
            focus=focus,
            summary=summary,
            evidence=evidence[:4],
            gaps=gaps,
            recommended_actions=recommended_actions,
        )
        return AgentResearchReport(
            agent_id=agent_id,
            session_key=session_key,
            focus=focus,
            summary=summary,
            evidence=evidence[:4],
            gaps=gaps,
            recommended_actions=recommended_actions,
            raw_markdown=raw_markdown,
        )

    def _fallback_evidence_judgement(
        self,
        *,
        session_key: str,
        user_text: str,
        candidates: list[DiagnosticReference],
        failure_note: str,
    ) -> EvidenceJudgeResult:
        scored: list[tuple[float, int, DiagnosticReference]] = []
        tokens = self._query_tokens(user_text)
        for item in candidates:
            total_score = 0.0
            hard_hits = 0
            haystack = " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("snippet", "")),
                    str(item.get("location", "")),
                    str(item.get("url", "")),
                ]
            ).lower()
            for token in tokens:
                if token in haystack:
                    if self._is_hard_anchor(token):
                        total_score += 0.35
                        hard_hits += 1
                    else:
                        total_score += 0.08
            total_score += min(float(item.get("score", 0.0) or 0.0), 1.0) * 0.15
            scored.append((total_score, hard_hits, item))

        scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        selected = [
            dict(item)
            for score, hard_hits, item in scored
            if score >= 0.35 and (hard_hits > 0 or score >= 0.55)
        ][:2]
        top_score = scored[0][0] if scored else 0.0
        support_level = "sufficient" if selected else ("weak" if top_score >= 0.2 else "none")
        summary = (
            "Fallback evidence judge found directly usable evidence."
            if selected
            else (
                "Fallback evidence judge found only weakly related evidence."
                if support_level == "weak"
                else "Fallback evidence judge found no directly usable evidence."
            )
        )
        if failure_note:
            summary = f"{summary} Reason: {self._compact_text(failure_note, 160)}"

        ranked: list[EvidenceJudgementItem] = []
        for score, _hard_hits, item in scored[:6]:
            ranked.append(
                EvidenceJudgementItem(
                    id=str(item.get("id", "")),
                    decision="keep" if any(str(item.get("id", "")) == str(selected_item.get("id", "")) for selected_item in selected) else "reject",
                    relevance_score=score,
                    reason="Lexical anchor fallback score.",
                )
            )
        raw_markdown = self._build_evidence_judge_markdown(
            support_level=support_level,
            summary=summary,
            selected_evidence=selected,
            ranked_evidence=ranked,
            missing_evidence=[] if selected else ["Need stronger evidence tied to the exact error, exception, or subsystem."],
        )
        return EvidenceJudgeResult(
            agent_id="evidence_judge",
            session_key=session_key,
            support_level=support_level,
            summary=summary,
            selected_evidence_ids=[str(item.get("id", "")) for item in selected if str(item.get("id", "")).strip()],
            selected_evidence=selected,
            ranked_evidence=ranked,
            missing_evidence=[] if selected else ["Need stronger evidence tied to the exact error, exception, or subsystem."],
            raw_markdown=raw_markdown,
        )

    def _fallback_report_summary(self, agent_id: str, evidence: list[DiagnosticReference]) -> str:
        if not evidence:
            if agent_id == "internal_retriever":
                return "No directly relevant internal evidence was found."
            return "No directly relevant external evidence was found."

        titles = [str(item.get("title", "")).strip() for item in evidence if str(item.get("title", "")).strip()]
        focus_titles = "; ".join(titles[:2]) if titles else "current candidates"
        if agent_id == "internal_retriever":
            return f"Found {len(evidence)} internal evidence items, mainly about: {focus_titles}."
        return f"Found {len(evidence)} external evidence items, mainly about: {focus_titles}."

    def _is_low_quality_report(self, report: AgentResearchReport) -> bool:
        summary = str(report.get("summary", "")).strip()
        if not summary:
            return True
        if any(marker in summary.lower() for marker in PLACEHOLDER_REPORT_SUMMARIES):
            return True
        return False

    def _is_low_quality_evidence_judgement(self, judgement: EvidenceJudgeResult) -> bool:
        summary = str(judgement.get("summary", "")).strip()
        support_level = str(judgement.get("support_level", "")).strip().lower()
        selected = judgement.get("selected_evidence", [])
        if support_level not in {"sufficient", "weak", "none"}:
            return True
        if not summary:
            return True
        if support_level == "sufficient" and not selected:
            return True
        return False

    def _merge_external_search_results(
        self,
        merged_results: dict[str, dict[str, object]],
        query: str,
        search_results: object,
    ) -> None:
        if not isinstance(search_results, list):
            return
        for item in search_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip() or url
            if not url or not title:
                continue
            current_score = float(item.get("score", 0.0) or 0.0)
            existing = merged_results.get(url)
            if existing is None or current_score > float(existing.get("score", 0.0) or 0.0):
                merged_results[url] = {
                    "title": title,
                    "url": url,
                    "snippet": self._compact_text(str(item.get("snippet", "")).strip(), 240),
                    "score": current_score,
                    "query": query,
                }

    def _build_external_queries(self, user_text: str) -> list[str]:
        normalized = " ".join(user_text.split())
        if not normalized:
            return []
        tokens = self._query_tokens(user_text)
        hard_tokens = [token for token in tokens if self._is_hard_anchor(token)]
        queries: list[str] = []
        if hard_tokens:
            queries.append(" ".join(hard_tokens[:6]))
        compact = self._compact_text(normalized, 180)
        if compact and compact not in queries:
            queries.append(compact)
        return queries[:2] or [compact]

    def _query_tokens(self, text: str) -> list[str]:
        tokens = [token.lower() for token in QUERY_TOKEN_PATTERN.findall(text)]
        unique: list[str] = []
        for token in tokens:
            if len(token) < 3 or token in QUERY_STOPWORDS:
                continue
            if token not in unique:
                unique.append(token)
        return unique[:24]

    def _is_hard_anchor(self, token: str) -> bool:
        lowered = token.lower()
        if any(hint in lowered for hint in HARD_ANCHOR_HINTS):
            return True
        return any(marker in token for marker in (".", "_", "/", ":")) or any(char.isdigit() for char in token)

    def _dedupe_evidence(self, evidence: list[DiagnosticReference], *, limit: int) -> list[DiagnosticReference]:
        merged: dict[str, DiagnosticReference] = {}
        for item in evidence:
            key = self._evidence_identity(item)
            if key not in merged:
                merged[key] = dict(item)
        return list(merged.values())[:limit]

    def _make_reference(
        self,
        *,
        ref_id: str,
        source_type: str,
        title: str,
        snippet: str,
        location: str = "",
        url: str = "",
        score: float = 0.0,
    ) -> DiagnosticReference:
        return DiagnosticReference(
            id=ref_id,
            type="内部资料" if source_type == "internal" else "外部资料",
            source_type=source_type,
            title=title.strip() or "Untitled",
            location=location.strip(),
            url=url.strip(),
            snippet=snippet.strip() or "No snippet provided.",
            score=float(score or 0.0),
        )

    def _log_report(self, agent_id: str, report: AgentResearchReport) -> None:
        logger.info(
            "Sub-agent report agent=%s summary=%s evidence=%s gaps=%s actions=%s",
            agent_id,
            self._compact_text(str(report.get("summary", "")), 180),
            self._format_evidence(report.get("evidence", [])),
            self._format_strings(report.get("gaps", [])),
            self._format_strings(report.get("recommended_actions", [])),
        )

    def _format_tool_items(self, items: list[dict[str, object]], *, location_key: str) -> list[str]:
        formatted: list[str] = []
        for item in items[:4]:
            title = self._compact_text(str(item.get("title", "")), 80) or "(untitled)"
            location = self._compact_text(str(item.get(location_key, "")), 120)
            formatted.append(f"{title} @ {location}".strip())
        return formatted

    def _format_evidence(self, evidence: object) -> list[str]:
        if not isinstance(evidence, list):
            return []
        formatted: list[str] = []
        for item in evidence[:4]:
            if not isinstance(item, dict):
                continue
            title = self._compact_text(str(item.get("title", "")), 80) or "(untitled)"
            location = self._compact_text(str(item.get("url", "") or item.get("location", "")), 120)
            formatted.append(f"{title} @ {location}".strip())
        return formatted

    def _format_strings(self, values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        return [self._compact_text(str(item), 120) for item in values[:4] if str(item).strip()]

    def _compact_text(self, text: str, limit: int) -> str:
        normalized = " ".join(str(text).split())
        return normalized[:limit]

    def _call_tool(self, emit, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        emit("tool", "start", {"tool_name": tool_name, "input": payload})
        result = self.dispatcher.dispatch(tool_name, payload)
        emit("tool", "end", {"tool_name": tool_name, "output": result})
        return result
