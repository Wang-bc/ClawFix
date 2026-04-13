from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from app.config.models import DiagnosticReference, DiagnosticResult

SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class CaseStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.cases_dir = workspace_root / "cases"
        self.daily_dir = workspace_root / "memory" / "daily"

    def record_analysis_note(self, session_key: str, user_text: str, result: DiagnosticResult) -> Path:
        if "::" in session_key:
            return self.daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        date_key = datetime.now().strftime("%Y-%m-%d")
        path = self.daily_dir / f"{date_key}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = self._compact_text(result["summary"], limit=220)
        user_preview = self._compact_text(user_text, limit=220)
        top_cause = self._compact_text(
            next((item.get("title", "") for item in result["candidate_root_causes"]), ""),
            limit=80,
        )
        note = (
            f"\n## {datetime.now().strftime('%H:%M:%S')} | 会话 {session_key}\n"
            f"- 问题分类：{result['problem_category']}\n"
            f"- 诊断摘要：{summary}\n"
            f"- 首要候选根因：{top_cause or '暂无'}\n"
            f"- 用户描述：{user_preview}\n"
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(note)
        return path

    def write_case(
        self,
        title: str,
        phenomenon: str,
        result: DiagnosticResult,
        session_key: str,
        final_root_cause: str = "",
        actual_fix: str = "",
        source: str = "web",
    ) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug_seed = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
        path = self.cases_dir / f"{timestamp}_{slug_seed}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        root_causes = "\n".join(
            f"{index}. {item['title']}：{item['reasoning']}"
            for index, item in enumerate(result["candidate_root_causes"], start=1)
        )
        steps = "\n".join(
            f"{index}. {item}" for index, item in enumerate(result["troubleshooting_steps"], start=1)
        )
        references = self._format_references(result["references"])

        content = (
            f"# {title}\n\n"
            f"- 创建时间：{datetime.now().isoformat(timespec='seconds')}\n"
            f"- 会话：{session_key}\n"
            f"- 来源：{source}\n"
            f"- 问题分类：{result['problem_category']}\n\n"
            f"## 现象描述\n{phenomenon.strip()}\n\n"
            f"## 候选根因\n{root_causes or '1. 暂无'}\n\n"
            f"## 排查步骤\n{steps or '1. 暂无'}\n\n"
            f"## 参考资料\n{references or '- 暂无'}\n\n"
            f"## 最终结论\n{final_root_cause.strip() or '待补录'}\n\n"
            f"## 实际修复\n{actual_fix.strip() or '待补录'}\n"
        )
        path.write_text(content, encoding="utf-8")
        return path

    def list_cases(self, limit: int = 20) -> list[dict[str, object]]:
        files = sorted(self.cases_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
        return [self._parse_case(path) for path in files[:limit]]

    def delete_case(self, relative_path: str) -> Path:
        target = self.resolve_case_path(relative_path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(relative_path)
        target.unlink()
        return target

    def resolve_case_path(self, relative_path: str) -> Path:
        raw = relative_path.strip().replace("\\", "/")
        if raw.startswith("cases/"):
            raw = raw[len("cases/") :]
        if not raw:
            raise ValueError("case path cannot be empty")
        candidate = Path(raw)
        if candidate.suffix.lower() != ".md":
            raise ValueError("case documents only support .md")
        resolved = (self.cases_dir / candidate).resolve()
        cases_root = self.cases_dir.resolve()
        if not str(resolved).startswith(str(cases_root)):
            raise ValueError("case path must stay within workspace/cases")
        return resolved

    def _parse_case(self, path: Path) -> dict[str, object]:
        content = path.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        title = next((line[2:].strip() for line in lines if line.startswith("# ")), path.stem)
        category = next((line.split("：", 1)[1] for line in lines if line.startswith("- 问题分类：")), "未分类")
        created_at = next((line.split("：", 1)[1] for line in lines if line.startswith("- 创建时间：")), "")
        return {
            "title": title,
            "category": category,
            "created_at": created_at,
            "path": path.relative_to(self.workspace_root).as_posix(),
        }

    def _format_references(self, references: list[DiagnosticReference]) -> str:
        rows: list[str] = []
        for item in references:
            if item.get("url"):
                rows.append(f"- [{item.get('type', '外部资料')}] {item.get('title', '未命名')} - {item['url']}")
            else:
                rows.append(
                    f"- [{item.get('type', '内部资料')}] {item.get('title', '未命名')} - {item.get('location', '')}"
                )
        return "\n".join(rows)

    def _compact_text(self, text: str, limit: int = 200) -> str:
        normalized = " ".join(text.split())
        return normalized[:limit]


class KnowledgeStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.knowledge_dir = workspace_root / "knowledge"

    def import_document(
        self,
        *,
        content: str,
        title: str = "",
        relative_path: str = "",
        tags: list[str] | None = None,
        overwrite: bool = True,
    ) -> Path:
        normalized_content = content.replace("\r\n", "\n").strip()
        if not normalized_content:
            raise ValueError("knowledge content cannot be empty")

        target = self.resolve_managed_path(relative_path or self._default_relative_path(title, normalized_content))
        if target.exists() and not overwrite:
            raise FileExistsError(f"knowledge document already exists: {target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = self._render_content(
            target=target,
            title=title.strip(),
            content=normalized_content,
            tags=tags or [],
        )
        target.write_text(payload.rstrip() + "\n", encoding="utf-8")
        return target

    def delete_document(self, relative_path: str) -> Path:
        target = self.resolve_managed_path(relative_path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(relative_path)
        target.unlink()
        self._prune_empty_dirs(target.parent)
        return target

    def list_documents(self, limit: int = 50) -> list[dict[str, object]]:
        if not self.knowledge_dir.exists():
            return []
        files = sorted(
            (
                path
                for path in self.knowledge_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in {".md", ".txt"}
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return [self._parse_document(path) for path in files[:limit]]

    def resolve_managed_path(self, relative_path: str) -> Path:
        raw = relative_path.strip().replace("\\", "/")
        if raw.startswith("knowledge/"):
            raw = raw[len("knowledge/") :]
        if not raw:
            raise ValueError("knowledge path cannot be empty")

        candidate = Path(raw)
        if not candidate.suffix:
            candidate = candidate.with_suffix(".md")
        if candidate.suffix.lower() not in {".md", ".txt"}:
            raise ValueError("knowledge documents only support .md or .txt")

        resolved = (self.knowledge_dir / candidate).resolve()
        knowledge_root = self.knowledge_dir.resolve()
        if not str(resolved).startswith(str(knowledge_root)):
            raise ValueError("knowledge path must stay within workspace/knowledge")
        return resolved

    def _default_relative_path(self, title: str, content: str) -> str:
        seed = title.strip() or next((line.strip() for line in content.splitlines() if line.strip()), "knowledge")
        slug = SAFE_FILENAME_PATTERN.sub("-", seed.lower()).strip("._-")
        if not slug:
            slug = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
        return f"{slug}.md"

    def _render_content(self, *, target: Path, title: str, content: str, tags: list[str]) -> str:
        if target.suffix.lower() == ".txt":
            return content
        if content.lstrip().startswith("# "):
            return content

        lines = [f"# {title or target.stem}"]
        cleaned_tags = [item.strip() for item in tags if item.strip()]
        if cleaned_tags:
            lines.append("")
            lines.append(f"- Tags: {', '.join(cleaned_tags)}")
        lines.extend(["", content])
        return "\n".join(lines)

    def _parse_document(self, path: Path) -> dict[str, object]:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            content = ""
        title = self._extract_title(path, content)
        return {
            "title": title,
            "path": path.relative_to(self.workspace_root).as_posix(),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "format": path.suffix.lower().lstrip("."),
        }

    def _extract_title(self, path: Path, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip() or path.stem
        return path.stem

    def _prune_empty_dirs(self, path: Path) -> None:
        knowledge_root = self.knowledge_dir.resolve()
        current = path.resolve()
        while current != knowledge_root and current.exists():
            if any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent
