from __future__ import annotations

import threading
import time
import tempfile
import unittest
from pathlib import Path

from app.config.settings import Settings
from app.delivery.queue import DeliveryQueue
from app.delivery.sender import SenderRegistry


class DeliveryQueueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.settings = Settings(
            workspace_dir=root / "workspace",
            public_dir=root / "public",
            max_delivery_retries=0,
        )
        self.settings.ensure_directories()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_failed_delivery_goes_to_dead_letter(self) -> None:
        registry = SenderRegistry()

        def broken_sender(account_id, peer_id, text, metadata=None):  # noqa: ANN001
            raise RuntimeError("send failed")

        registry.register("feishu", broken_sender)
        queue = DeliveryQueue(self.settings, registry)

        result = queue.enqueue_and_send(
            run_id="run-1",
            channel="feishu",
            account_id="acc",
            peer_id="peer",
            text="hello",
        )

        self.assertEqual("dead-letter", result["status"])
        files = list(self.settings.dead_letter_dir.glob("*.json"))
        self.assertEqual(1, len(files))

    def test_sent_false_result_goes_to_dead_letter(self) -> None:
        registry = SenderRegistry()

        def soft_failed_sender(account_id, peer_id, text, metadata=None):  # noqa: ANN001
            return {"sent": False, "reason": "missing_credentials"}

        registry.register("feishu", soft_failed_sender)
        queue = DeliveryQueue(self.settings, registry)

        result = queue.enqueue_and_send(
            run_id="run-2",
            channel="feishu",
            account_id="acc",
            peer_id="peer",
            text="hello",
        )

        self.assertEqual("dead-letter", result["status"])
        self.assertEqual("missing_credentials", result["results"][0]["reason"])

    def test_same_delivery_file_is_not_sent_twice_concurrently(self) -> None:
        registry = SenderRegistry()
        send_count = 0
        send_guard = threading.Lock()

        def slow_sender(account_id, peer_id, text, metadata=None):  # noqa: ANN001
            nonlocal send_count
            time.sleep(0.2)
            with send_guard:
                send_count += 1
            return {"sent": True, "channel": "feishu", "preview": text[:120]}

        registry.register("feishu", slow_sender)
        queue = DeliveryQueue(self.settings, registry)
        path = self.settings.delivery_dir / "concurrent.json"
        queue._write(
            path,
            {
                "delivery_id": "concurrent",
                "run_id": "run-3",
                "channel": "feishu",
                "account_id": "acc",
                "peer_id": "peer",
                "chunks": ["hello"],
                "retry_count": 0,
                "next_attempt_at": "2026-04-10T00:00:00",
                "status": "queued",
                "metadata": {},
            },
        )

        results: list[dict[str, object]] = []

        def worker() -> None:
            results.append(queue._process_file(path))

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        second.start()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertEqual(2, len(results))
        self.assertEqual(1, send_count)
        self.assertTrue(all(item["status"] == "delivered" for item in results))


if __name__ == "__main__":
    unittest.main()
