from __future__ import annotations


AGENT_RESEARCH_REPORT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "focus": {"type": "string"},
        "summary": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                    "source_type": {"type": "string"},
                    "title": {"type": "string"},
                    "location": {"type": "string"},
                    "url": {"type": "string"},
                    "snippet": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["type", "title", "snippet"],
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "raw_markdown": {"type": "string"},
    },
    "required": ["focus", "summary", "evidence", "gaps", "recommended_actions", "raw_markdown"],
}


EVIDENCE_JUDGE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "support_level": {"type": "string", "enum": ["sufficient", "weak", "none"]},
        "summary": {"type": "string"},
        "selected_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "ranked_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["keep", "reject"]},
                    "relevance_score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "decision", "relevance_score", "reason"],
            },
        },
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "raw_markdown": {"type": "string"},
    },
    "required": [
        "support_level",
        "summary",
        "selected_evidence_ids",
        "ranked_evidence",
        "missing_evidence",
        "raw_markdown",
    ],
}


SESSION_SUMMARY_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overview": {"type": "string"},
        "known_facts": {"type": "array", "items": {"type": "string"}},
        "attempted_actions": {"type": "array", "items": {"type": "string"}},
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
        "important_references": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "overview",
        "known_facts",
        "attempted_actions",
        "unresolved_questions",
        "important_references",
    ],
}


DIAGNOSTIC_RESULT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_type": {"type": "string"},
        "problem_category": {"type": "string"},
        "summary": {"type": "string"},
        "candidate_root_causes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "confidence": {"type": "string"},
                },
                "required": ["title", "reasoning", "confidence"],
            },
        },
        "troubleshooting_steps": {"type": "array", "items": {"type": "string"}},
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                    "source_type": {"type": "string"},
                    "title": {"type": "string"},
                    "location": {"type": "string"},
                    "url": {"type": "string"},
                    "snippet": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["type", "title", "snippet"],
            },
        },
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "agents_used": {"type": "array", "items": {"type": "string"}},
        "reply_markdown": {"type": "string"},
    },
    "required": [
        "task_type",
        "problem_category",
        "summary",
        "candidate_root_causes",
        "troubleshooting_steps",
        "references",
        "missing_information",
        "agents_used",
        "reply_markdown",
    ],
}
