from __future__ import annotations

import logging
import re
from typing import Any

from app.config.models import AgentResearchReport, AgentRunRequest, DiagnosticResult, EvidenceJudgeResult
from app.llm.client import LLMClient
from app.llm.schemas import DIAGNOSTIC_RESULT_SCHEMA
from app.prompt.builder import PromptBuilder
from app.runtime.sub_agents import SubAgentRunner

REFERENCE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.:/-]+|[\u4e00-\u9fff]{2,}")
NULL_FIELD_PATTERN = re.compile(r'because\s+"this\.([A-Za-z_][A-Za-z0-9_]*)"\s+is null', re.IGNORECASE)
SIZE_BOUNDARY_PATTERN = re.compile(r"for\s*\([^;]+;\s*[^;]+<=\s*([A-Za-z_][A-Za-z0-9_]*)\.size\(\)", re.IGNORECASE)
REFERENCE_STOPWORDS = {
    "error",
    "exception",
    "traceback",
    "thread",
    "main",
    "public",
    "class",
    "void",
    "string",
    "import",
    "list",
    "item",
    "null",
    "问题",
    "报错",
    "异常",
    "日志",
    "分析",
    "代码",
}
PLACEHOLDER_SUMMARY_MARKERS = (
    "已生成子 Agent 报告，但模型未提供摘要",
    "真实 LLM 子 Agent 报告生成失败",
    "已基于当前证据生成回退报告",
    "当前运行环境未启用真实 LLM",
    "模型已返回结果，但未提供诊断摘要",
)

logger = logging.getLogger("clawfix")


class DiagnosticEngine:
    def __init__(
        self,
        *,
        llm_client: LLMClient,
        prompt_builder: PromptBuilder,
        sub_agent_runner: SubAgentRunner,
        enable_web_search: bool = True,
    ) -> None:
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder
        self.sub_agent_runner = sub_agent_runner
        self.enable_web_search = enable_web_search

    def analyze(
        self,
        request: AgentRunRequest,
        prepared_prompt: dict[str, object],
        emit,
    ) -> DiagnosticResult:
        logger.info(
            "Coordinator analyze start session=%s run_id=%s web_search=%s",
            request["session_key"],
            request["run_id"],
            self.enable_web_search,
        )
        internal_report = self.sub_agent_runner.run_internal_agent(
            parent_session_key=request["session_key"],
            run_id=request["run_id"],
            user_text=request["user_text"],
            emit=emit,
        )
        logger.info(
            "Coordinator received sub-agent report agent=%s summary=%s evidence=%s",
            "internal_retriever",
            self._compact_text(str(internal_report.get("summary", "")), 180),
            self._format_evidence(internal_report.get("evidence", [])),
        )

        external_report = None
        if self.enable_web_search:
            external_report = self.sub_agent_runner.run_external_agent(
                parent_session_key=request["session_key"],
                run_id=request["run_id"],
                user_text=request["user_text"],
                emit=emit,
            )
        if external_report is not None:
            logger.info(
                "Coordinator received sub-agent report agent=%s summary=%s evidence=%s",
                "external_researcher",
                self._compact_text(str(external_report.get("summary", "")), 180),
                self._format_evidence(external_report.get("evidence", [])),
            )
        evidence_judgement = self.sub_agent_runner.run_evidence_judge(
            parent_session_key=request["session_key"],
            run_id=request["run_id"],
            user_text=request["user_text"],
            internal_report=internal_report,
            external_report=external_report,
        )
        logger.info(
            "Coordinator received sub-agent report agent=%s support_level=%s evidence=%s summary=%s",
            "evidence_judge",
            evidence_judgement["support_level"],
            self._format_evidence(evidence_judgement.get("selected_evidence", [])),
            self._compact_text(str(evidence_judgement.get("summary", "")), 180),
        )

        emit(
            "assistant",
            "delta",
            {
                "message": "已完成子 Agent 证据整理，正在汇总最终诊断结论。",
                "internal_session_key": internal_report["session_key"],
                "external_session_key": external_report["session_key"] if external_report else "",
                "judge_session_key": evidence_judgement["session_key"],
            },
        )

        if self.llm_client.enabled:
            try:
                result = self._run_llm_coordinator(
                    request,
                    prepared_prompt,
                    internal_report,
                    external_report,
                    evidence_judgement,
                )
            except Exception as exc:  # noqa: BLE001
                emit("lifecycle", "error", {"reason": "llm_coordinator_failed", "error": str(exc)})
                result = self._fallback_result(
                    request,
                    internal_report,
                    external_report,
                    evidence_judgement,
                    failure_note=str(exc),
                )
        else:
            result = self._fallback_result(request, internal_report, external_report, evidence_judgement)
        final_result = self._post_process_result(request["user_text"], result, evidence_judgement=evidence_judgement)
        logger.info(
            "Coordinator final result session=%s category=%s references=%s summary=%s",
            request["session_key"],
            str(final_result.get("problem_category", "")),
            self._format_evidence(final_result.get("references", [])),
            self._compact_text(str(final_result.get("summary", "")), 200),
        )
        return final_result

    def _run_llm_coordinator(
        self,
        request: AgentRunRequest,
        prepared_prompt: dict[str, object],
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
        evidence_judgement: EvidenceJudgeResult,
    ) -> DiagnosticResult:
        system_prompt, user_prompt = self._build_coordinator_prompts(
            request=request,
            prepared_prompt=prepared_prompt,
            internal_report=internal_report,
            external_report=external_report,
            evidence_judgement=evidence_judgement,
        )

        result = self.llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name="diagnostic_result",
            schema=DIAGNOSTIC_RESULT_SCHEMA,
            model=self.llm_client.settings.llm_model,
            temperature=self.llm_client.settings.llm_temperature,
        )
        logger.info(
            "Coordinator llm_result raw category=%s references=%s agents_used=%s",
            str(result.get("problem_category", "")),
            self._format_evidence(result.get("references", [])),
            self._format_strings(result.get("agents_used", [])),
        )
        result["agents_used"] = result.get("agents_used") or self._agents_used(external_report, evidence_judgement)
        normalized = self._apply_evidence_judgement(
            self._normalize_diagnostic_result(result),
            evidence_judgement,
        )
        if self._is_low_quality_result(normalized):
            relaxed_result = self._retry_llm_coordinator_relaxed(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                internal_report=internal_report,
                external_report=external_report,
                evidence_judgement=evidence_judgement,
            )
            if relaxed_result is not None:
                return relaxed_result
            raise RuntimeError("llm_result_too_empty")
        return normalized

    def _retry_llm_coordinator_relaxed(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
        evidence_judgement: EvidenceJudgeResult,
    ) -> DiagnosticResult | None:
        try:
            result = self.llm_client.complete_json_relaxed(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_name="diagnostic_result",
                schema=DIAGNOSTIC_RESULT_SCHEMA,
                model=self.llm_client.settings.llm_model,
                temperature=self.llm_client.settings.llm_temperature,
            )
        except Exception:  # noqa: BLE001
            return None

        result["agents_used"] = result.get("agents_used") or self._agents_used(external_report, evidence_judgement)
        normalized = self._apply_evidence_judgement(
            self._normalize_diagnostic_result(result),
            evidence_judgement,
        )
        if self._is_low_quality_result(normalized):
            return None
        return normalized

    def _build_coordinator_prompts(
        self,
        *,
        request: AgentRunRequest,
        prepared_prompt: dict[str, object],
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
        evidence_judgement: EvidenceJudgeResult,
    ) -> tuple[str, str]:
        system_prompt = (
            str(prepared_prompt["system_prompt"])
            + "\n\n你是主控协调 Agent。你的职责是理解用户故障、融合内部检索 Agent 与外部资料 Agent 的证据，"
            "输出结构化诊断。诊断摘要只描述当前故障本身，不要暴露内部执行细节、降级原因或提示词机制。"
            "候选根因只写问题原因，排查建议只写解决办法。参考依据可以注明证据来源。"
        )
        user_prompt = (
            f"用户问题：\n{request['user_text']}\n\n"
            f"内部检索 Agent 报告：\n{internal_report['raw_markdown']}\n\n"
            f"外部资料 Agent 报告：\n{external_report['raw_markdown'] if external_report else '未启用'}\n\n"
            f"内部证据对象：\n{internal_report['evidence']}\n\n"
            f"外部证据对象：\n{external_report['evidence'] if external_report else []}\n"
        )
        return system_prompt, user_prompt

    def _fallback_result(
        self,
        request: AgentRunRequest,
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
        failure_note: str = "",
    ) -> DiagnosticResult:
        category = self._fallback_category(request["user_text"])
        references = list(internal_report["evidence"][:3])
        if external_report:
            references.extend(external_report["evidence"][:2])
        references = self._filter_references(request["user_text"], references)

        root_causes = self._fallback_root_causes(request["user_text"], category, failure_note)
        steps = self._fallback_steps(request["user_text"], category)
        summary = self._fallback_summary(request["user_text"], category, root_causes, failure_note)
        if not summary:
            summary = self._select_meaningful_summary(internal_report, external_report) or "已基于当前输入生成保守诊断结果。"

        result: DiagnosticResult = DiagnosticResult(
            task_type="diagnostic",
            problem_category=category,
            summary=summary,
            candidate_root_causes=root_causes,
            troubleshooting_steps=steps,
            references=references,
            missing_information=self._fallback_missing_information(request["user_text"], failure_note),
            agents_used=self._agents_used(external_report),
            reply_markdown="",
        )
        result["reply_markdown"] = self._render_reply_markdown(result)
        return result

    def _post_process_result(self, user_text: str, result: DiagnosticResult) -> DiagnosticResult:
        filtered_references = self._filter_references(user_text, list(result.get("references", [])))
        normalized = dict(result)
        normalized["references"] = filtered_references
        normalized = self._apply_reference_guardrail(user_text, normalized)
        normalized["reply_markdown"] = self._render_reply_markdown(normalized)
        return self._normalize_diagnostic_result(normalized)

    def _bind_allowed_references(
        self,
        result: DiagnosticResult,
        *,
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
    ) -> DiagnosticResult:
        allowed = list(internal_report.get("evidence", []))[:3]
        if external_report:
            allowed.extend(list(external_report.get("evidence", []))[:2])
        allowed_map = {self._reference_identity(item): item for item in allowed}
        bound: list[dict[str, object]] = []
        for item in result.get("references", []):
            allowed_item = allowed_map.get(self._reference_identity(item))
            if allowed_item is not None:
                bound.append(dict(allowed_item))
        normalized = dict(result)
        normalized["references"] = bound
        normalized["reply_markdown"] = self._render_reply_markdown(normalized)
        logger.info(
            "Coordinator bound references kept=%s allowed=%s",
            self._format_evidence(bound),
            self._format_evidence(allowed),
        )
        return DiagnosticResult(**normalized)

    def _normalize_diagnostic_result(self, payload: dict[str, Any]) -> DiagnosticResult:
        root_causes = self._normalize_root_causes(payload.get("candidate_root_causes"))
        steps = self._normalize_string_list(payload.get("troubleshooting_steps"), limit=8)
        references = self._normalize_references(payload.get("references"))
        missing_information = self._normalize_string_list(payload.get("missing_information"), limit=8)
        agents_used = self._normalize_string_list(payload.get("agents_used"), limit=6) or ["coordinator"]

        normalized: dict[str, Any] = {
            "task_type": str(payload.get("task_type", "")).strip() or "diagnostic",
            "problem_category": str(payload.get("problem_category", "")).strip() or "未分类",
            "summary": str(payload.get("summary", "")).strip() or "模型已返回结果，但未提供诊断摘要。",
            "candidate_root_causes": root_causes,
            "troubleshooting_steps": steps or ["请补充更多上下文后重新分析。"],
            "references": references,
            "missing_information": missing_information,
            "agents_used": agents_used,
            "reply_markdown": str(payload.get("reply_markdown", "")).strip(),
        }
        if not normalized["reply_markdown"]:
            normalized["reply_markdown"] = self._render_reply_markdown(normalized)
        return DiagnosticResult(**normalized)

    def _render_reply_markdown(self, result: dict[str, object]) -> str:
        lines = [
            "## 诊断摘要",
            str(result.get("summary", "") or "暂无摘要"),
            "",
            "## 问题分类",
            str(result.get("problem_category", "") or "未分类"),
        ]

        root_causes = result.get("candidate_root_causes", []) or []
        if root_causes:
            lines.extend(["", "## 候选根因"])
            lines.extend(
                [
                    f"{index}. {item.get('title', '未命名')}（置信度：{item.get('confidence', '未标注')}）\n   - 依据：{item.get('reasoning', '')}"
                    for index, item in enumerate(root_causes, start=1)
                ]
            )

        steps = result.get("troubleshooting_steps", []) or []
        if steps:
            lines.extend(["", "## 排查建议"])
            lines.extend([f"{index}. {step}" for index, step in enumerate(steps, start=1)])

        references = result.get("references", []) or []
        if references:
            lines.extend(["", "## 参考依据"])
            lines.extend(
                [
                    f"- [{item.get('type', '资料')}] {item.get('title', '未命名')} - {item.get('url') or item.get('location', '')}"
                    for item in references
                ]
            )

        missing_information = result.get("missing_information", []) or []
        if missing_information:
            lines.extend(["", "## 建议补充的信息"])
            lines.extend([f"- {item}" for item in missing_information])

        return "\n".join(lines)

    def _format_evidence(self, evidence: object) -> list[str]:
        if not isinstance(evidence, list):
            return []
        formatted: list[str] = []
        for item in evidence[:5]:
            if not isinstance(item, dict):
                continue
            title = self._compact_text(str(item.get("title", "")), 80) or "(untitled)"
            location = self._compact_text(str(item.get("url", "") or item.get("location", "")), 120)
            formatted.append(f"{title} @ {location}".strip())
        return formatted

    def _format_strings(self, values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        return [self._compact_text(str(item), 120) for item in values[:5] if str(item).strip()]

    def _compact_text(self, text: str, limit: int) -> str:
        normalized = " ".join(text.split())
        return normalized[:limit]

    def _apply_reference_guardrail(self, user_text: str, result: dict[str, object]) -> dict[str, object]:
        references = list(result.get("references", []))
        aligned_references = self._filter_result_aligned_references(user_text, result, references)
        if references and not aligned_references:
            logger.info(
                "Coordinator evidence guardrail cleared all references category=%s summary=%s original=%s",
                str(result.get("problem_category", "")),
                self._compact_text(str(result.get("summary", "")), 180),
                self._format_evidence(references),
            )
        if len(aligned_references) != len(references):
            result = dict(result)
            result["references"] = aligned_references

        if not result.get("references"):
            note = "以下结论基于当前输入的保守判断，当前未找到直接证据支持。"
            summary = str(result.get("summary", "")).strip()
            if summary and note not in summary:
                result = dict(result)
                result["summary"] = f"{note} {summary}"
        return result

    def _filter_result_aligned_references(
        self,
        user_text: str,
        result: dict[str, object],
        references: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if not references:
            return []
        combined_text = " ".join(
            [
                user_text,
                str(result.get("problem_category", "")),
                str(result.get("summary", "")),
            ]
        )
        target_terms = self._reference_tokens(combined_text)
        if not target_terms:
            return references[:5]

        required_matches = 1
        aligned: list[dict[str, object]] = []
        for item in references:
            haystack = " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("snippet", "")),
                    str(item.get("location", "")),
                    str(item.get("url", "")),
                ]
            ).lower()
            matches = sum(1 for token in target_terms if token in haystack)
            if matches >= required_matches:
                aligned.append(item)
        return aligned[:5]

    def _filter_references(self, user_text: str, references: list[dict[str, object]]) -> list[dict[str, object]]:
        if not references:
            return []

        query_tokens = self._reference_tokens(user_text)
        if not query_tokens:
            return references[:5]

        required_matches = 1 if len(query_tokens) <= 2 else 2
        filtered: list[dict[str, object]] = []

        for item in references:
            haystack = " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("snippet", "")),
                    str(item.get("location", "")),
                    str(item.get("url", "")),
                ]
            ).lower()
            matches = sum(1 for token in query_tokens if token in haystack)
            if matches >= required_matches:
                filtered.append(item)

        return filtered[:5]

    def _reference_tokens(self, text: str) -> list[str]:
        tokens = [token.lower() for token in REFERENCE_TOKEN_PATTERN.findall(text)]
        unique: list[str] = []
        for token in tokens:
            if len(token) < 3 or token in REFERENCE_STOPWORDS:
                continue
            if token not in unique:
                unique.append(token)
        return unique[:24]

    def _normalize_root_causes(self, raw_items: object) -> list[dict[str, str]]:
        if not isinstance(raw_items, list):
            return []
        normalized: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            reasoning = str(item.get("reasoning", "")).strip()
            if not title or not reasoning:
                continue
            normalized.append(
                {
                    "title": title,
                    "reasoning": reasoning,
                    "confidence": str(item.get("confidence", "")).strip() or "未标注",
                }
            )
        return normalized[:5]

    def _normalize_references(self, raw_items: object) -> list[dict[str, object]]:
        if not isinstance(raw_items, list):
            return []
        normalized: list[dict[str, object]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            if not title or not snippet:
                continue
            normalized.append(
                {
                    "type": str(item.get("type", "资料")).strip() or "资料",
                    "title": title,
                    "location": str(item.get("location", "")).strip(),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": snippet,
                    "score": float(item.get("score", 0.0) or 0.0),
                }
            )
        return normalized[:5]

    def _normalize_string_list(self, raw_items: object, limit: int = 6) -> list[str]:
        if not isinstance(raw_items, list):
            return []
        items = [str(item).strip() for item in raw_items if str(item).strip()]
        return items[:limit]

    def _reference_identity(self, item: dict[str, object]) -> str:
        url = str(item.get("url", "")).strip().lower()
        if url:
            return f"url:{url}"
        location = str(item.get("location", "")).strip().lower()
        return f"location:{location}"

    def _is_low_quality_result(self, result: DiagnosticResult) -> bool:
        summary = str(result.get("summary", "")).strip()
        root_causes = result.get("candidate_root_causes", [])
        steps = result.get("troubleshooting_steps", [])
        category = str(result.get("problem_category", "")).strip()
        if not root_causes and not steps:
            return True
        if category in {"", "未分类"} and not root_causes:
            return True
        if not summary or self._looks_like_placeholder_summary(summary):
            return True
        return False

    def _fallback_category(self, text: str) -> str:
        lowered = text.lower()
        if any(word in lowered for word in ("nullpointerexception", "cannot invoke", "indexoutofbounds", "java.lang", "traceback", "空指针")):
            return "代码逻辑问题"
        if any(word in lowered for word in ("redis", "mysql", "postgres", "mongodb", "connection refused", "连接拒绝")):
            return "数据库/缓存连接异常"
        if any(word in lowered for word in ("http", "api", "401", "403", "404", "500", "502", "503", "504")):
            return "接口调用异常"
        if any(word in lowered for word in ("config", "配置", "env", "yaml", "yml")):
            return "配置异常"
        if any(word in lowered for word in ("依赖", "版本", "module", "import", "pip", "npm")):
            return "依赖冲突"
        return "环境异常"

    def _fallback_root_causes(self, text: str, category: str, failure_note: str) -> list[dict[str, str]]:
        _ = failure_note
        lowered = text.lower()
        causes: list[dict[str, str]] = []

        if category == "代码逻辑问题":
            field_name = self._extract_null_field_name(text)
            if field_name:
                causes.append(
                    {
                        "title": f"构造函数或字段声明处没有初始化 {field_name}",
                        "reasoning": (
                            f"异常栈已明确指出 `this.{field_name}` 为 null，说明在调用成员集合方法之前，"
                            f"`{field_name}` 还没有被实例化；当前最先触发的根因就是集合字段初始化缺失。"
                        ),
                        "confidence": "高",
                    }
                )

            if self._has_size_boundary_issue(text):
                causes.append(
                    {
                        "title": "循环边界存在越界风险",
                        "reasoning": "循环条件使用了 `<= size()` 这类写法，当索引等于 size() 时会访问不存在的元素，后续可能触发越界异常。",
                        "confidence": "中",
                    }
                )

            if self._has_late_null_check(text):
                causes.append(
                    {
                        "title": "空值判断顺序错误",
                        "reasoning": "代码先调用对象方法，再判断变量是否为 null；即使修复了首个异常，后续仍可能在这里再次触发空指针。",
                        "confidence": "中",
                    }
                )

            if "10 / 0" in lowered:
                causes.append(
                    {
                        "title": "存在确定性的除零异常分支",
                        "reasoning": "当分支命中时会执行 `10 / 0`，这是另一个必然触发的运行时错误，只是当前被更早的异常遮挡了。",
                        "confidence": "中",
                    }
                )

            if causes:
                return causes[:4]

        return [
            {
                "title": "当前为保守诊断结果",
                "reasoning": "系统已根据输入文本、异常关键词和现有证据生成启发式判断，建议结合完整日志与上下文进一步确认。",
                "confidence": "低",
            }
        ]

    def _fallback_steps(self, text: str, category: str) -> list[str]:
        if category != "代码逻辑问题":
            return ["补充更完整的错误日志、运行环境和近期变更后，再重新分析。"]

        steps: list[str] = []
        field_name = self._extract_null_field_name(text)
        if field_name:
            initializer = self._infer_collection_initializer(text, field_name)
            steps.append(
                f"先在构造函数或字段声明处初始化 `{field_name}`，例如 `{field_name} = {initializer};`，再重新运行验证首个异常是否消失。"
            )
            steps.append(
                f"检查 `{field_name}` 的所有使用点，确保在执行 `add/remove/size/get` 之前对象已经完成构造并且不为 null。"
            )

        if self._has_size_boundary_issue(text):
            boundary_field = self._extract_size_boundary_field(text) or "集合"
            steps.append(f"把循环条件从 `i <= {boundary_field}.size()` 改为 `i < {boundary_field}.size()`，避免访问越界元素。")

        if self._has_late_null_check(text):
            steps.append("把 `item == null` 的判断放到方法调用之前，或先过滤掉 null 元素。")

        if "10 / 0" in text.lower():
            steps.append("删除或保护 `10 / 0` 这类调试代码，避免修复首个异常后又触发新的运行时错误。")

        steps.append("修复后补一组回归测试，至少覆盖未初始化集合、正常元素、空元素/边界索引这几类输入。")
        return steps[:5]

    def _fallback_summary(
        self,
        text: str,
        category: str,
        root_causes: list[dict[str, str]],
        failure_note: str,
    ) -> str:
        _ = text
        _ = failure_note
        if category == "代码逻辑问题":
            field_name = self._extract_null_field_name(text)
            if field_name:
                return (
                    f"当前报错是典型的 Java 空指针异常，首个触发点出现在对成员变量 `{field_name}` 调用集合方法时；"
                    f"最可能的根因是该字段声明后没有完成实例化。"
                )
            if root_causes:
                return f"当前报错属于典型的代码逻辑问题，最先需要处理的是“{root_causes[0]['title']}”。"
        if root_causes:
            return f"已基于当前输入生成保守诊断，优先关注“{root_causes[0]['title']}”。"
        return ""

    def _fallback_missing_information(self, text: str, failure_note: str) -> list[str]:
        _ = failure_note
        lowered = text.lower()
        items: list[str] = []
        if "exception in thread" not in lowered and "traceback" not in lowered:
            items.append("补充完整异常堆栈，确认首个抛错方法和行号。")
        if ".java:" not in lowered:
            items.append("补充触发异常的方法所在文件和具体行号，便于定位第一现场。")
        if "public class" not in lowered and "class " not in lowered:
            items.append("补充相关类的完整代码片段，尤其是字段声明、构造函数和报错方法。")
        return items[:3]

    def _select_meaningful_summary(
        self,
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
    ) -> str:
        candidates = [internal_report.get("summary", "")]
        if external_report:
            candidates.append(external_report.get("summary", ""))
        for summary in candidates:
            text = str(summary).strip()
            if text and not self._looks_like_placeholder_summary(text):
                return text
        return ""

    def _looks_like_placeholder_summary(self, summary: str) -> bool:
        stripped = summary.strip()
        if not stripped:
            return True
        return any(marker in stripped for marker in PLACEHOLDER_SUMMARY_MARKERS)

    def _extract_null_field_name(self, text: str) -> str | None:
        match = NULL_FIELD_PATTERN.search(text)
        if match:
            return match.group(1)
        return None

    def _has_size_boundary_issue(self, text: str) -> bool:
        return SIZE_BOUNDARY_PATTERN.search(text) is not None or "for (int i = 0; i <=" in text.lower()

    def _extract_size_boundary_field(self, text: str) -> str | None:
        match = SIZE_BOUNDARY_PATTERN.search(text)
        if match:
            return match.group(1)
        return None

    def _has_late_null_check(self, text: str) -> bool:
        lowered = text.lower()
        if "if (item == null)" not in lowered:
            return False
        method_index = lowered.find("touppercase()")
        null_check_index = lowered.find("if (item == null)")
        return method_index != -1 and null_check_index != -1 and method_index < null_check_index

    def _infer_collection_initializer(self, text: str, field_name: str) -> str:
        lowered = text.lower()
        if "linkedlist" in lowered or "queue" in field_name.lower():
            return "new LinkedList<>()"
        if "arraylist" in lowered:
            return "new ArrayList<>()"
        if "list<" in lowered:
            return "new ArrayList<>()"
        return "new ArrayList<>()"

    def _build_coordinator_prompts(
        self,
        *,
        request: AgentRunRequest,
        prepared_prompt: dict[str, object],
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
        evidence_judgement: EvidenceJudgeResult,
    ) -> tuple[str, str]:
        system_prompt = (
            str(prepared_prompt["system_prompt"])
            + "\n\nYou are the coordinator agent. Produce the final diagnosis from the user issue, the internal report, the external report, and the evidence_judge decision."
            " Keep the summary focused on the bug itself. Do not mention hidden system mechanics."
            " References must only come from the approved evidence objects provided by evidence_judge."
            " If evidence_judge says support_level is not sufficient, return an empty references array."
        )
        user_prompt = (
            f"User issue:\n{request['user_text']}\n\n"
            f"Internal report:\n{internal_report['raw_markdown']}\n\n"
            f"External report:\n{external_report['raw_markdown'] if external_report else 'Not available'}\n\n"
            f"Evidence judge report:\n{evidence_judgement['raw_markdown']}\n\n"
            f"Approved evidence objects:\n{evidence_judgement.get('selected_evidence', [])}\n"
        )
        return system_prompt, user_prompt

    def _fallback_result(
        self,
        request: AgentRunRequest,
        internal_report: AgentResearchReport,
        external_report: AgentResearchReport | None,
        evidence_judgement: EvidenceJudgeResult,
        failure_note: str = "",
    ) -> DiagnosticResult:
        category = self._fallback_category(request["user_text"])
        selected_evidence = list(evidence_judgement.get("selected_evidence", []))[:2]
        references = selected_evidence if evidence_judgement.get("support_level") == "sufficient" else []

        root_causes = self._fallback_root_causes(request["user_text"], category, failure_note)
        steps = self._fallback_steps(request["user_text"], category)
        summary = self._fallback_summary(request["user_text"], category, root_causes, failure_note)
        if not summary:
            summary = self._select_meaningful_summary(internal_report, external_report) or "Generated a conservative diagnosis from the current input."

        result: DiagnosticResult = DiagnosticResult(
            task_type="diagnostic",
            problem_category=category,
            summary=summary,
            candidate_root_causes=root_causes,
            troubleshooting_steps=steps,
            references=references,
            missing_information=self._fallback_missing_information(request["user_text"], failure_note),
            agents_used=self._agents_used(external_report, evidence_judgement),
            reply_markdown="",
        )
        result = self._apply_evidence_judgement(result, evidence_judgement)
        result["reply_markdown"] = self._render_reply_markdown(result)
        return result

    def _post_process_result(
        self,
        user_text: str,
        result: DiagnosticResult,
        *,
        evidence_judgement: EvidenceJudgeResult | None = None,
    ) -> DiagnosticResult:
        normalized = dict(result)
        if evidence_judgement is not None:
            normalized = self._apply_evidence_judgement(normalized, evidence_judgement)
        else:
            filtered_references = self._filter_references(user_text, list(result.get("references", [])))
            normalized["references"] = filtered_references
            normalized = self._apply_reference_guardrail(user_text, normalized)
        normalized["reply_markdown"] = self._render_reply_markdown(normalized)
        return self._normalize_diagnostic_result(normalized)

    def _apply_evidence_judgement(
        self,
        result: dict[str, object],
        evidence_judgement: EvidenceJudgeResult,
    ) -> DiagnosticResult:
        normalized = dict(result)
        support_level = str(evidence_judgement.get("support_level", "")).strip().lower()
        selected = [dict(item) for item in evidence_judgement.get("selected_evidence", [])[:2]]
        normalized["references"] = selected if support_level == "sufficient" and selected else []
        normalized = self._ensure_no_reference_note(normalized)
        return self._normalize_diagnostic_result(normalized)

    def _ensure_no_reference_note(self, result: dict[str, object]) -> dict[str, object]:
        if result.get("references"):
            return result
        note = "以下结论基于当前输入的保守判断，暂未找到可直接支撑本次诊断的参考依据。"
        summary = str(result.get("summary", "")).strip()
        if summary and note not in summary:
            updated = dict(result)
            updated["summary"] = f"{note} {summary}"
            return updated
        return result

    def _normalize_references(self, raw_items: object) -> list[dict[str, object]]:
        if not isinstance(raw_items, list):
            return []
        normalized: list[dict[str, object]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            if not title or not snippet:
                continue
            normalized.append(
                {
                    "id": str(item.get("id", "")).strip(),
                    "type": str(item.get("type", "资料")).strip() or "资料",
                    "source_type": str(item.get("source_type", "")).strip(),
                    "title": title,
                    "location": str(item.get("location", "")).strip(),
                    "url": str(item.get("url", "")).strip(),
                    "snippet": snippet,
                    "score": float(item.get("score", 0.0) or 0.0),
                }
            )
        return normalized[:5]

    def _reference_identity(self, item: dict[str, object]) -> str:
        ref_id = str(item.get("id", "")).strip().lower()
        if ref_id:
            return f"id:{ref_id}"
        url = str(item.get("url", "")).strip().lower()
        if url:
            return f"url:{url}"
        location = str(item.get("location", "")).strip().lower()
        return f"location:{location}"

    def _agents_used(
        self,
        external_report: AgentResearchReport | None,
        evidence_judgement: EvidenceJudgeResult | None = None,
    ) -> list[str]:
        agents = ["coordinator", "internal_retriever"]
        if external_report:
            agents.append("external_researcher")
        if evidence_judgement is not None:
            agents.append("evidence_judge")
        return agents
