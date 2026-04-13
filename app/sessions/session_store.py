from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.models import SessionMemoryItem

ERROR_TITLE_PATTERN = re.compile(r"\b[A-Za-z0-9_.]+(?:Exception|Error)\b|\bHTTP\s*[45]\d{2}\b", re.IGNORECASE)


class SessionStore:
    def __init__(self, sessions_root: Path) -> None:
        self.sessions_root = sessions_root

    def append_message(
        self,
        agent_id: str,
        session_key: str,
        role: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        session_file = self._session_file(agent_id, session_key)
        session_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "role": role,
            "text": text,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "metadata": metadata or {},
        }
        with session_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._write_meta(agent_id, session_key, entry)
        return entry

    def load_messages(
        self,
        agent_id: str,
        session_key: str,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        session_file = self._session_file(agent_id, session_key)
        if not session_file.exists():
            return []
        rows: list[dict[str, object]] = []
        with session_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows[-limit:] if limit is not None else rows

    def list_sessions(self, limit: int = 20, main_only: bool = True) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for path in self.sessions_root.rglob("*.meta.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if main_only and not self.is_main_session(str(meta.get("session_key", "")), str(meta.get("agent_id", ""))):
                continue
            items.append(meta)
        items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return items[:limit]

    def get_session_meta(self, agent_id: str, session_key: str) -> dict[str, object]:
        meta_file = self._meta_file(agent_id, session_key)
        if not meta_file.exists():
            return {
                "session_key": session_key,
                "agent_id": agent_id,
                "created_at": "",
                "updated_at": "",
                "total_messages": 0,
                "preview": "",
                "title": "",
                "summary": {},
                "durable_memory": [],
            }
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if not isinstance(meta.get("summary"), dict):
            meta["summary"] = {}
        meta["durable_memory"] = self._normalize_durable_memory(meta.get("durable_memory"))
        return meta

    def load_session(self, agent_id: str, session_key: str, limit: int = 100) -> dict[str, object]:
        return {
            "meta": self.get_session_meta(agent_id, session_key),
            "messages": self.load_messages(agent_id, session_key, limit=limit),
        }

    def compact_session(self, agent_id: str, session_key: str, summary: dict[str, Any]) -> dict[str, object]:
        meta = self.get_session_meta(agent_id, session_key)
        meta["summary"] = summary
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._meta_file(agent_id, session_key).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return meta

    def merge_durable_memory(
        self,
        agent_id: str,
        session_key: str,
        items: list[SessionMemoryItem],
        *,
        limit: int = 24,
    ) -> list[SessionMemoryItem]:
        meta = self.get_session_meta(agent_id, session_key)
        existing = self._normalize_durable_memory(meta.get("durable_memory"))
        merged: dict[str, SessionMemoryItem] = {item["memory_id"]: item for item in existing}
        for item in self._normalize_durable_memory(items):
            current = merged.get(item["memory_id"])
            if current is None:
                merged[item["memory_id"]] = item
                continue
            merged[item["memory_id"]] = {
                **current,
                "kind": item["kind"],
                "title": item["title"],
                "content": item["content"],
                "checksum": item["checksum"],
                "updated_at": item["updated_at"],
            }

        ordered = sorted(merged.values(), key=lambda item: item["updated_at"], reverse=True)[:limit]
        ordered.sort(key=lambda item: item["created_at"])
        meta["durable_memory"] = ordered
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        meta_path = self._meta_file(agent_id, session_key)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return ordered

    def get_durable_memory(self, agent_id: str, session_key: str) -> list[SessionMemoryItem]:
        meta = self.get_session_meta(agent_id, session_key)
        return self._normalize_durable_memory(meta.get("durable_memory"))

    def is_main_session(self, session_key: str, agent_id: str | None = None) -> bool:
        if "::" in session_key:
            return False
        if agent_id and agent_id != "coordinator":
            return False
        return True

    def normalize_main_session_key(self, session_key: str) -> str:
        return session_key.split("::", 1)[0]

    def delete_session_group(self, session_key: str) -> dict[str, object]:
        main_session_key = self.normalize_main_session_key(session_key)
        removed_files = 0
        removed_session_keys: list[str] = []
        dirs_to_prune: set[Path] = set()

        for path in self.sessions_root.rglob("*.meta.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            agent_id = str(meta.get("agent_id", "")).strip()
            candidate_key = str(meta.get("session_key", "")).strip()
            if not agent_id or not candidate_key or not self._belongs_to_session_group(main_session_key, candidate_key):
                continue

            removed_session_keys.append(candidate_key)
            session_file = self._session_file(agent_id, candidate_key)
            meta_file = self._meta_file(agent_id, candidate_key)
            for target in (session_file, meta_file):
                if target.exists():
                    target.unlink()
                    removed_files += 1
                    dirs_to_prune.add(target.parent)

        for path in sorted(dirs_to_prune, key=lambda item: len(item.parts), reverse=True):
            self._prune_empty_dirs(path)

        return {
            "session_key": main_session_key,
            "removed_session_keys": sorted(set(removed_session_keys)),
            "removed_files": removed_files,
        }

    def _write_meta(self, agent_id: str, session_key: str, entry: dict[str, object]) -> None:
        meta = self.get_session_meta(agent_id, session_key)
        if not meta["created_at"]:
            meta["created_at"] = entry["created_at"]
        if not meta.get("title") and entry["role"] == "user":
            meta["title"] = self._build_session_title(str(entry["text"]))
        meta.update(
            {
                "session_key": session_key,
                "agent_id": agent_id,
                "updated_at": entry["created_at"],
                "total_messages": int(meta.get("total_messages", 0)) + 1,
                "preview": self._build_preview(str(entry["text"])),
            }
        )
        meta_path = self._meta_file(agent_id, session_key)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_session_title(self, text: str) -> str:
        normalized = self._normalize_text(text)
        if not normalized:
            return "新会话"
        matched_error = ERROR_TITLE_PATTERN.search(normalized)
        if matched_error:
            return self._trim_title(matched_error.group(0), limit=24)
        return self._trim_title(normalized, limit=20)

    def _build_preview(self, text: str) -> str:
        return self._trim_title(self._normalize_text(text), limit=56)

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.replace("```", " ").split())

    def _trim_title(self, text: str, limit: int) -> str:
        compact = text.strip()
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    def _session_id(self, session_key: str) -> str:
        return hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:20]

    def _session_file(self, agent_id: str, session_key: str) -> Path:
        return self.sessions_root / agent_id / f"{self._session_id(session_key)}.jsonl"

    def _meta_file(self, agent_id: str, session_key: str) -> Path:
        return self.sessions_root / agent_id / f"{self._session_id(session_key)}.meta.json"

    def _belongs_to_session_group(self, main_session_key: str, candidate_key: str) -> bool:
        return candidate_key == main_session_key or candidate_key.startswith(f"{main_session_key}::")

    def _prune_empty_dirs(self, path: Path) -> None:
        root = self.sessions_root.resolve()
        current = path.resolve()
        while current != root and current.exists():
            if any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent

    def _normalize_durable_memory(self, value: object) -> list[SessionMemoryItem]:
        if not isinstance(value, list):
            return []
        normalized: list[SessionMemoryItem] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            memory_id = str(item.get("memory_id", "")).strip()
            checksum = str(item.get("checksum", "")).strip()
            created_at = str(item.get("created_at", "")).strip()
            updated_at = str(item.get("updated_at", "")).strip()
            kind = str(item.get("kind", "")).strip() or "fact"
            if not title or not content or not memory_id or not checksum:
                continue
            normalized.append(
                SessionMemoryItem(
                    memory_id=memory_id,
                    kind=kind,
                    title=title,
                    content=content,
                    checksum=checksum,
                    created_at=created_at or updated_at or datetime.now().isoformat(timespec="seconds"),
                    updated_at=updated_at or created_at or datetime.now().isoformat(timespec="seconds"),
                )
            )
        return normalized
