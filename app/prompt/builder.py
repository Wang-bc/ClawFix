from __future__ import annotations

from datetime import datetime

from app.prompt.bootstrap_loader import BootstrapLoader
from app.prompt.memory_recall import MemoryRecall
from app.prompt.skill_loader import SkillLoader


class PromptBuilder:
    def __init__(
        self,
        bootstrap_loader: BootstrapLoader,
        memory_recall: MemoryRecall,
        skill_loader: SkillLoader,
    ) -> None:
        self.bootstrap_loader = bootstrap_loader
        self.memory_recall = memory_recall
        self.skill_loader = skill_loader

    def build(
        self,
        agent_id: str,
        user_text: str,
        recent_messages: list[dict[str, object]],
        session_key: str | None = None,
        session_summary: dict[str, object] | None = None,
        extra_sections: list[str] | None = None,
        include_session_memory: bool = False,
    ) -> dict[str, object]:
        bootstrap = self.bootstrap_loader.load(agent_id)
        recalled = self.memory_recall.recall(session_key, user_text) if include_session_memory and session_key else []
        skills = self.skill_loader.load()
        sections = [
            f"# Agent\n当前 Agent: {agent_id}",
            f"# 当前时间\n{datetime.now().isoformat(timespec='seconds')}",
        ]

        if bootstrap:
            sections.append(
                "# 工作区引导\n" + "\n\n".join(f"## {name}\n{content}" for name, content in bootstrap.items())
            )
        if session_summary:
            sections.append(
                "# 会话摘要\n"
                f"概览：{session_summary.get('overview', '')}\n"
                f"已知事实：{'; '.join(session_summary.get('known_facts', []))}\n"
                f"已尝试动作：{'; '.join(session_summary.get('attempted_actions', []))}\n"
                f"未决问题：{'; '.join(session_summary.get('unresolved_questions', []))}"
            )
        if recalled:
            sections.append(
                "# 相关记忆\n"
                + "\n".join(f"- {item['title']} ({item['path']}): {item['snippet']}" for item in recalled)
            )
        if skills:
            sections.append("# 技能提示\n" + "\n".join(f"- 已安装技能：{name}" for name in skills))
        if recent_messages:
            sections.append(
                "# 最近消息\n"
                + "\n".join(f"- {item['role']}: {str(item['text'])[:180]}" for item in recent_messages[-8:])
            )
        if extra_sections:
            sections.append("# 额外上下文\n" + "\n\n".join(extra_sections))

        return {
            "system_prompt": "\n\n".join(sections),
            "recalled_memories": recalled,
            "skills": skills,
            "bootstrap": bootstrap,
            "session_summary": session_summary or {},
        }
