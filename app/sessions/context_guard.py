from __future__ import annotations


class ContextGuard:
    def __init__(self, max_chars: int = 24000, compact_threshold_chars: int = 16000) -> None:
        self.max_chars = max_chars
        self.compact_threshold_chars = compact_threshold_chars

    def prepare_context(self, messages: list[dict[str, object]], summary: dict[str, object] | None = None) -> dict[str, object]:
        summary = summary or {}
        summary_text = self._summary_to_text(summary)
        selected: list[dict[str, object]] = []
        used = len(summary_text)
        action = "ok"

        for item in reversed(messages):
            text = str(item.get("text", ""))
            if used + len(text) > self.max_chars:
                action = "truncated"
                if not selected:
                    copied = dict(item)
                    copied["text"] = text[-self.max_chars :]
                    selected.append(copied)
                break
            selected.append(item)
            used += len(text)

        selected.reverse()
        if used > int(self.max_chars * 0.8) and action == "ok":
            action = "warn"
        return {
            "messages": selected,
            "summary": summary,
            "action": action,
            "estimated_tokens": max(1, used // 4),
        }

    def needs_compaction(self, messages: list[dict[str, object]]) -> bool:
        return sum(len(str(item.get("text", ""))) for item in messages) > self.compact_threshold_chars

    def _summary_to_text(self, summary: dict[str, object]) -> str:
        values: list[str] = []
        for key, value in summary.items():
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            else:
                values.append(str(value))
        return " ".join(values)
