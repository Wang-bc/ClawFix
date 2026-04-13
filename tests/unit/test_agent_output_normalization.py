from __future__ import annotations

import unittest

from app.runtime.diagnostic_engine import DiagnosticEngine
from app.runtime.sub_agents import SubAgentRunner


class AgentOutputNormalizationTestCase(unittest.TestCase):
    def test_sub_agent_report_fills_missing_raw_markdown(self) -> None:
        runner = object.__new__(SubAgentRunner)
        report = runner._normalize_report(  # type: ignore[attr-defined]
            agent_id="internal_retriever",
            session_key="session-1",
            focus="search internal evidence",
            evidence=[
                {
                    "id": "internal_1",
                    "type": "内部资料",
                    "source_type": "internal",
                    "title": "Java NPE guide",
                    "location": "knowledge/java.md:1",
                    "snippet": "Uninitialized collections trigger NullPointerException.",
                }
            ],
            payload={
                "evidence": [
                    {
                        "id": "internal_1",
                        "type": "内部资料",
                        "source_type": "internal",
                        "title": "Java NPE guide",
                        "location": "knowledge/java.md:1",
                        "snippet": "Uninitialized collections trigger NullPointerException.",
                    }
                ],
            },
        )

        self.assertIn("raw_markdown", report)
        self.assertTrue(report["raw_markdown"])
        self.assertIn("Java NPE guide", report["summary"])

    def test_diagnostic_result_fills_missing_reply_markdown(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        result = engine._normalize_diagnostic_result(  # type: ignore[attr-defined]
            {
                "problem_category": "code logic issue",
                "summary": "dataList was never initialized.",
                "candidate_root_causes": [
                    {
                        "title": "dataList missing initialization",
                        "reasoning": "The exception says this.dataList is null.",
                    }
                ],
                "troubleshooting_steps": ["Initialize dataList before calling addData."],
            }
        )

        self.assertEqual("diagnostic", result["task_type"])
        self.assertTrue(result["reply_markdown"])
        self.assertEqual("code logic issue", result["problem_category"])

    def test_bind_allowed_references_drops_unknown_reference(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        bound = engine._bind_allowed_references(  # type: ignore[attr-defined]
            {
                "task_type": "diagnostic",
                "problem_category": "code logic issue",
                "summary": "summary",
                "candidate_root_causes": [],
                "troubleshooting_steps": [],
                "references": [
                    {
                        "type": "Case",
                        "title": "Unknown case",
                        "location": "cases/missing.md:1",
                        "snippet": "missing",
                    },
                    {
                        "type": "Knowledge",
                        "title": "Java NPE guide",
                        "location": "knowledge/java.md:1",
                        "snippet": "real",
                    },
                ],
                "missing_information": [],
                "agents_used": ["coordinator"],
                "reply_markdown": "",
            },
            internal_report={
                "agent_id": "internal_retriever",
                "session_key": "session-1",
                "focus": "internal",
                "summary": "summary",
                "evidence": [
                    {
                        "type": "Knowledge",
                        "title": "Java NPE guide",
                        "location": "knowledge/java.md:1",
                        "snippet": "real",
                    }
                ],
                "gaps": [],
                "recommended_actions": [],
                "raw_markdown": "",
            },
            external_report=None,
        )

        self.assertEqual(1, len(bound["references"]))
        self.assertEqual("knowledge/java.md:1", bound["references"][0]["location"])

    def test_evidence_judgement_binds_selected_ids(self) -> None:
        runner = object.__new__(SubAgentRunner)
        judgement = runner._normalize_evidence_judgement(  # type: ignore[attr-defined]
            session_key="session-1",
            candidates=[
                {
                    "id": "internal_1",
                    "type": "内部资料",
                    "source_type": "internal",
                    "title": "Java NPE guide",
                    "location": "knowledge/java.md:1",
                    "snippet": "Uninitialized collections trigger NullPointerException.",
                },
                {
                    "id": "external_1",
                    "type": "外部资料",
                    "source_type": "external",
                    "title": "Oracle Java docs",
                    "url": "https://docs.oracle.com/java",
                    "snippet": "NullPointerException is thrown when an application attempts to use null.",
                },
            ],
            payload={
                "support_level": "sufficient",
                "summary": "Two references directly support the diagnosis.",
                "selected_evidence_ids": ["external_1", "internal_1"],
                "ranked_evidence": [
                    {"id": "external_1", "decision": "keep", "relevance_score": 0.92, "reason": "Directly explains the exception."},
                    {"id": "internal_1", "decision": "keep", "relevance_score": 0.89, "reason": "Matches the internal case."},
                ],
                "missing_evidence": [],
                "raw_markdown": "",
            },
        )

        self.assertEqual("sufficient", judgement["support_level"])
        self.assertEqual(["external_1", "internal_1"], judgement["selected_evidence_ids"])
        self.assertEqual(2, len(judgement["selected_evidence"]))

    def test_post_process_uses_evidence_judge_selection(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        result = engine._post_process_result(  # type: ignore[attr-defined]
            "java.io.FileNotFoundException file path missing",
            {
                "task_type": "diagnostic",
                "problem_category": "Java IO Exception",
                "summary": "Java tried to read a missing file and raised FileNotFoundException.",
                "candidate_root_causes": [],
                "troubleshooting_steps": ["Check the file path."],
                "references": [],
                "missing_information": [],
                "agents_used": ["coordinator"],
                "reply_markdown": "",
            },
            evidence_judgement={
                "agent_id": "evidence_judge",
                "session_key": "session-1",
                "support_level": "sufficient",
                "summary": "The selected evidence is directly relevant.",
                "selected_evidence_ids": ["internal_1"],
                "selected_evidence": [
                    {
                        "id": "internal_1",
                        "type": "内部资料",
                        "source_type": "internal",
                        "title": "Java FileNotFoundException guide",
                        "location": "knowledge/java_io.md:12",
                        "snippet": "FileNotFoundException is commonly triggered by a missing file or wrong path.",
                    }
                ],
                "ranked_evidence": [],
                "missing_evidence": [],
                "raw_markdown": "",
            },
        )

        self.assertEqual(1, len(result["references"]))
        self.assertEqual("internal_1", result["references"][0]["id"])

    def test_post_process_clears_references_when_judge_finds_no_support(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        result = engine._post_process_result(  # type: ignore[attr-defined]
            "java.io.FileNotFoundException file path missing",
            {
                "task_type": "diagnostic",
                "problem_category": "Java IO Exception",
                "summary": "Java tried to read a missing file and raised FileNotFoundException.",
                "candidate_root_causes": [],
                "troubleshooting_steps": ["Check the file path."],
                "references": [
                    {
                        "id": "internal_1",
                        "type": "内部资料",
                        "source_type": "internal",
                        "title": "Java FileNotFoundException guide",
                        "location": "knowledge/java_io.md:12",
                        "snippet": "FileNotFoundException is commonly triggered by a missing file or wrong path.",
                    }
                ],
                "missing_information": [],
                "agents_used": ["coordinator"],
                "reply_markdown": "",
            },
            evidence_judgement={
                "agent_id": "evidence_judge",
                "session_key": "session-1",
                "support_level": "none",
                "summary": "No directly usable evidence was found.",
                "selected_evidence_ids": [],
                "selected_evidence": [],
                "ranked_evidence": [],
                "missing_evidence": [],
                "raw_markdown": "",
            },
        )

        self.assertEqual([], result["references"])
        self.assertIn("保守判断", result["summary"])

    def test_post_process_clears_mismatched_references(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        result = engine._post_process_result(  # type: ignore[attr-defined]
            "java.io.FileNotFoundException file path missing",
            {
                "task_type": "diagnostic",
                "problem_category": "Java IO Exception",
                "summary": "Java tried to read a missing file and raised FileNotFoundException.",
                "candidate_root_causes": [],
                "troubleshooting_steps": ["Check the file path."],
                "references": [
                    {
                        "type": "Knowledge",
                        "title": "Java NPE guide",
                        "location": "knowledge/java.md:1",
                        "snippet": "Uninitialized collections trigger NullPointerException.",
                    }
                ],
                "missing_information": [],
                "agents_used": ["coordinator"],
                "reply_markdown": "",
            },
        )

        self.assertEqual([], result["references"])
        self.assertIn("保守判断", result["summary"])

    def test_post_process_keeps_aligned_references(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        result = engine._post_process_result(  # type: ignore[attr-defined]
            "java.io.FileNotFoundException file path missing",
            {
                "task_type": "diagnostic",
                "problem_category": "Java IO Exception",
                "summary": "Java tried to read a missing file and raised FileNotFoundException.",
                "candidate_root_causes": [],
                "troubleshooting_steps": ["Check the file path."],
                "references": [
                    {
                        "type": "Knowledge",
                        "title": "Java FileNotFoundException guide",
                        "location": "knowledge/java_io.md:12",
                        "snippet": "FileNotFoundException is commonly triggered by a missing file or wrong path.",
                    }
                ],
                "missing_information": [],
                "agents_used": ["coordinator"],
                "reply_markdown": "",
            },
        )

        self.assertEqual(1, len(result["references"]))
        self.assertNotIn("保守判断", result["summary"])


if __name__ == "__main__":
    unittest.main()
