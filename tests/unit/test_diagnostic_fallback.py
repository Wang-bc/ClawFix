from __future__ import annotations

import unittest

from app.runtime.diagnostic_engine import DiagnosticEngine


class DiagnosticFallbackTestCase(unittest.TestCase):
    def test_java_null_pointer_fallback_is_code_logic(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        text = """
        public class DataProcessor {
            private List<String> dataList;
            public void addData(String data) { dataList.add(data); }
            public void processData() {
                for (int i = 0; i <= dataList.size(); i++) {
                    String item = dataList.get(i);
                    System.out.println(item.toUpperCase());
                    if (item == null) { int result = 10 / 0; }
                }
            }
        }
        Exception in thread "main" java.lang.NullPointerException: Cannot invoke "java.util.List.add(Object)" because "this.dataList" is null
        """
        category = engine._fallback_category(text)
        root_causes = engine._fallback_root_causes(text, category, "")
        steps = engine._fallback_steps(text, category)

        self.assertEqual("代码逻辑问题", category)
        self.assertTrue(root_causes)
        self.assertIn("dataList", root_causes[0]["title"])
        self.assertTrue(any("dataList = new ArrayList<>()" in step for step in steps))

    def test_similar_java_collection_null_pointer_is_generalized(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        text = """
        import java.util.LinkedList;
        import java.util.List;

        public class TaskManager {
            private List<String> taskQueue;

            public void addTask(String taskName) {
                taskQueue.add(taskName);
            }
        }

        Exception in thread "main" java.lang.NullPointerException: Cannot invoke "java.util.List.add(Object)" because "this.taskQueue" is null
        """
        category = engine._fallback_category(text)
        root_causes = engine._fallback_root_causes(text, category, "")
        steps = engine._fallback_steps(text, category)
        summary = engine._fallback_summary(text, category, root_causes, "llm_result_too_empty")

        self.assertEqual("代码逻辑问题", category)
        self.assertIn("taskQueue", root_causes[0]["title"])
        self.assertIn("this.taskQueue", root_causes[0]["reasoning"])
        self.assertTrue(any("taskQueue = new LinkedList<>()" in step for step in steps))
        self.assertNotIn("llm_result_too_empty", summary)

    def test_irrelevant_references_are_filtered(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        text = 'java.lang.NullPointerException: Cannot invoke "java.util.List.add(Object)" because "this.dataList" is null'
        references = [
            {
                "type": "知识文档",
                "title": "Redis 排查手册",
                "location": "memory/runbook.md:1",
                "snippet": "连接拒绝时先确认实例监听端口和防火墙规则。",
            }
        ]

        filtered = engine._filter_references(text, references)
        self.assertFalse(filtered)

    def test_low_quality_result_is_rejected(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        result = {
            "task_type": "diagnostic",
            "problem_category": "未分类",
            "summary": "模型已返回结果，但未提供诊断摘要。",
            "candidate_root_causes": [],
            "troubleshooting_steps": ["请补充更多上下文后重新分析。"],
            "references": [],
            "missing_information": [],
            "agents_used": ["coordinator"],
            "reply_markdown": "",
        }

        self.assertTrue(engine._is_low_quality_result(result))  # type: ignore[arg-type]

    def test_fallback_missing_information_hides_internal_runtime_notes(self) -> None:
        engine = object.__new__(DiagnosticEngine)
        items = engine._fallback_missing_information(
            "Exception in thread \"main\" java.lang.NullPointerException: Cannot invoke \"java.util.List.add(Object)\" because \"this.taskQueue\" is null\nat TaskManager.addTask(TaskManager.java:15)",
            "llm_result_too_empty",
        )

        self.assertFalse(any("llm_result_too_empty" in item for item in items))
        self.assertFalse(any("JSON Schema" in item for item in items))


if __name__ == "__main__":
    unittest.main()
