from __future__ import annotations

from app.sessions.context_guard import ContextGuard
from app.sessions.session_store import SessionStore
from app.sessions.summarizer import SessionSummarizer


class SessionCompactor:
    def __init__(
        self,
        session_store: SessionStore,
        context_guard: ContextGuard,
        summarizer: SessionSummarizer,
    ) -> None:
        self.session_store = session_store
        self.context_guard = context_guard
        self.summarizer = summarizer

    def maybe_compact(self, agent_id: str, session_key: str) -> dict[str, object]:
        messages = self.session_store.load_messages(agent_id, session_key)
        meta = self.session_store.get_session_meta(agent_id, session_key)
        previous_summary = meta.get("summary", {}) if isinstance(meta.get("summary", {}), dict) else {}

        if not self.context_guard.needs_compaction(messages):
            return previous_summary

        head = messages[:-8] if len(messages) > 8 else messages[:-2]
        summary = self.summarizer.summarize(previous_summary, head)
        self.session_store.compact_session(agent_id, session_key, dict(summary))
        return dict(summary)
