from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.gateway.server import create_fastapi_app


class HttpApiIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.workspace = root / "workspace"
        public = root / "public"
        self.workspace.mkdir(parents=True, exist_ok=True)
        public.mkdir(parents=True, exist_ok=True)
        (self.workspace / "MEMORY.md").write_text(
            "# Stable Memory\nRedis connection failures should first check service state and credentials.\n",
            encoding="utf-8",
        )
        (self.workspace / "AGENTS.md").write_text("# Agents\ncoordinator\n", encoding="utf-8")
        (public / "index.html").write_text("<!doctype html><title>ok</title>", encoding="utf-8")

        self.settings = Settings(
            host="127.0.0.1",
            port=0,
            workspace_dir=self.workspace,
            public_dir=public,
            enable_web_search=False,
        )
        self.app = create_fastapi_app(self.settings)
        self.feishu_messages: list[dict[str, object]] = []

        def fake_feishu_sender(account_id, peer_id, text, metadata=None):  # noqa: ANN001
            self.feishu_messages.append(
                {
                    "account_id": account_id,
                    "peer_id": peer_id,
                    "text": text,
                    "metadata": metadata or {},
                }
            )
            return {"sent": True, "channel": "feishu", "preview": text[:120]}

        self.app.state.application.sender_registry.register("feishu", fake_feishu_sender)
        self.client_cm = TestClient(self.app)
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_chat_and_finalize_flow(self) -> None:
        home = self.client.get("/")
        self.assertEqual(200, home.status_code)
        self.assertIn("<title>ok</title>", home.text)

        chat = self.client.post("/api/web/chat", json={"text": "Redis connection refused in logs"}).json()
        self.assertTrue(chat["ok"])
        self.assertIn("session_key", chat)
        self.assertTrue(chat["run"]["result"]["problem_category"])

        finalize = self.client.post(
            "/api/web/finalize",
            json={
                "session_key": chat["session_key"],
                "final_root_cause": "Redis service was down",
                "actual_fix": "Restarted Redis service",
            },
        ).json()
        self.assertTrue(finalize["ok"])
        self.assertIn("vector_sync", finalize)

        cases = self.client.get("/api/cases").json()
        sessions = self.client.get("/api/sessions").json()
        self.assertTrue(cases["items"])
        self.assertTrue(sessions["items"])

        deleted_case = self.client.post("/api/cases/delete", json={"path": finalize["path"]}).json()
        self.assertTrue(deleted_case["ok"])
        self.assertIn("vector_sync", deleted_case)

        deleted_session = self.client.post("/api/sessions/delete", json={"session_key": chat["session_key"]}).json()
        self.assertTrue(deleted_session["ok"])
        self.assertIn("vector_sync", deleted_session)

        self.assertFalse(self.client.get("/api/cases").json()["items"])
        self.assertFalse(self.client.get("/api/sessions").json()["items"])

    def test_stream_chat_and_session_detail(self) -> None:
        payload = {"text": "Java NullPointerException, this.dataList is null", "user_id": "browser-user"}
        response = self.client.post("/api/web/chat/stream", json=payload)
        self.assertEqual(200, response.status_code)

        stream_items = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        self.assertTrue(stream_items)
        self.assertEqual("meta", stream_items[0]["type"])
        final_item = next(item for item in stream_items if item["type"] == "result")
        self.assertTrue(final_item["ok"])

        session_key = final_item["session_key"]
        detail = self.client.get("/api/session", params={"session_key": session_key}).json()
        self.assertTrue(detail["ok"])
        self.assertEqual(session_key, detail["session_key"])
        self.assertEqual(2, len(detail["messages"]))
        self.assertTrue(any(msg["role"] == "assistant" for msg in detail["messages"]))

        sessions = self.client.get("/api/sessions").json()
        self.assertEqual(1, len(sessions["items"]))
        self.assertNotIn("::", sessions["items"][0]["session_key"])

        judge_dir = self.workspace / ".sessions" / "evidence_judge"
        self.assertTrue(judge_dir.exists())
        self.assertTrue(any(judge_dir.glob("*.jsonl")))
        self.assertTrue(any(judge_dir.glob("*.meta.json")))

    def test_feishu_challenge_and_async_event_flow(self) -> None:
        challenge = self.client.post("/api/feishu/events", json={"challenge": "challenge-token"}).json()
        self.assertEqual({"challenge": "challenge-token"}, challenge)

        event = self.client.post(
            "/api/feishu/events",
            json={
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {
                        "sender_id": {"open_id": "ou_user_1"},
                        "sender_type": "user",
                    },
                    "message": {
                        "message_id": "om_test_message_1",
                        "chat_id": "oc_test_chat_1",
                        "message_type": "text",
                        "content": json.dumps({"text": "Redis connection refused in logs"}, ensure_ascii=False),
                        "create_time": "1712640000000",
                    },
                },
            },
        ).json()
        self.assertTrue(event["ok"])
        self.assertTrue(event["accepted"])
        self.assertEqual("coordinator:feishu:local-feishu:oc_test_chat_1", event["session_key"])

        self._wait_until(lambda: bool(self.feishu_messages), timeout_s=5)

        self.assertTrue(self.feishu_messages)
        self.assertEqual("oc_test_chat_1", self.feishu_messages[0]["peer_id"])
        self.assertTrue(self.feishu_messages[0]["text"])

        detail = self.client.get("/api/session", params={"session_key": event["session_key"]}).json()
        self.assertTrue(detail["ok"])
        self.assertEqual(2, len(detail["messages"]))
        self.assertEqual("user", detail["messages"][0]["role"])
        self.assertEqual("assistant", detail["messages"][1]["role"])

    def _wait_until(self, predicate, timeout_s: float) -> None:  # noqa: ANN001
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        self.fail("condition was not met before timeout")


if __name__ == "__main__":
    unittest.main()
