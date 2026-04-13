from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config.models import QueuedDelivery
from app.config.settings import Settings
from app.delivery.chunking import chunk_text
from app.delivery.retry import due_for_retry, next_attempt_at
from app.delivery.sender import SenderRegistry


class DeliveryQueue:
    def __init__(self, settings: Settings, sender_registry: SenderRegistry) -> None:
        self.settings = settings
        self.delivery_root = settings.delivery_dir
        self.dead_letter_root = settings.dead_letter_dir
        self.sender_registry = sender_registry
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._run_worker, name="delivery-runner", daemon=True)
        self._lock_guard = threading.Lock()
        self._file_locks: dict[str, threading.Lock] = {}

    def start(self) -> None:
        if not self._worker.is_alive():
            self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=2)

    def enqueue_and_send(
        self,
        *,
        run_id: str,
        channel: str,
        account_id: str,
        peer_id: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        delivery = QueuedDelivery(
            delivery_id=uuid4().hex,
            run_id=run_id,
            channel=channel,
            account_id=account_id,
            peer_id=peer_id,
            chunks=chunk_text(text),
            retry_count=0,
            next_attempt_at=datetime.now().isoformat(timespec="seconds"),
            status="queued",
            metadata=metadata or {},
        )
        path = self.delivery_root / f"{delivery['delivery_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write(path, delivery)
        return self._process_file(path)

    def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                for path in sorted(self.delivery_root.glob("*.json")):
                    self._process_file(path, background=True)
            except Exception:
                pass
            self._stop_event.wait(self.settings.delivery_poll_interval_s)

    def _process_file(self, path: Path, background: bool = False) -> dict[str, object]:
        lock = self._file_lock(path)
        with lock:
            delivery = json.loads(path.read_text(encoding="utf-8"))
            status = delivery.get("status")
            if status == "delivered":
                return delivery
            if status == "dead-letter":
                return delivery
            if background and not due_for_retry(str(delivery["next_attempt_at"])):
                return delivery

            results: list[dict[str, object]] = []
            try:
                for chunk in delivery["chunks"]:
                    result = self.sender_registry.send(
                        delivery["channel"],
                        delivery["account_id"],
                        delivery["peer_id"],
                        chunk,
                        delivery.get("metadata", {}),
                    )
                    results.append(result)
                    if not bool(result.get("sent")):
                        raise RuntimeError(self._failure_reason(result))
                delivery["status"] = "delivered"
                delivery["results"] = results
                self._write(path, delivery)
                return delivery
            except Exception as exc:  # noqa: BLE001
                delivery["retry_count"] = int(delivery.get("retry_count", 0)) + 1
                delivery["results"] = results + [{"sent": False, "error": str(exc)}]
                if delivery["retry_count"] > self.settings.max_delivery_retries:
                    delivery["status"] = "dead-letter"
                    dead_path = self.dead_letter_root / path.name
                    self._write(dead_path, delivery)
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    return delivery

                delivery["status"] = "retrying"
                delivery["next_attempt_at"] = next_attempt_at(int(delivery["retry_count"]))
                self._write(path, delivery)
                return delivery

    def _file_lock(self, path: Path) -> threading.Lock:
        key = str(path.resolve())
        with self._lock_guard:
            lock = self._file_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._file_locks[key] = lock
            return lock

    def _write(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _failure_reason(self, result: dict[str, object]) -> str:
        for key in ("reason", "error", "msg", "body"):
            value = str(result.get(key, "")).strip()
            if value:
                return value
        return "delivery_sender_returned_sent_false"
