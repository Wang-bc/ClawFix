# ClawFix Multi-Agent Guide

The current project uses 4 agents with clear responsibilities and isolated sub-sessions.

## 1. coordinator

- Receives the user problem and owns the main session.
- Coordinates the full diagnostic flow.
- Merges internal retrieval, external research, and evidence judgement outputs.
- Produces the final structured diagnosis.
- Falls back to a conservative answer when evidence is weak or absent.

## 2. internal_retriever

- Searches workspace knowledge, prior cases, and indexed internal memory.
- Focuses on directly relevant internal evidence.
- Identifies internal evidence gaps and conflicts.
- Does not produce the final answer.

## 3. external_researcher

- Searches external technical sources and extracts useful evidence.
- Prefers official docs, standards, issues, and high-quality technical references.
- Marks uncertainty when external evidence is weak or incomplete.
- Does not produce the final answer.

## 4. evidence_judge

- Reviews candidate evidence from internal and external agents.
- Treats internal and external evidence with equal priority.
- Approves only evidence that directly supports the current diagnosis.
- Rejects weakly related background material.
- Returns a strict evidence allow-list for the coordinator.

## Collaboration Rules

1. Every sub-agent uses its own isolated sub-session. Sub-agent context must not pollute the coordinator main session.
2. The runtime executes `internal_retriever`, `external_researcher`, and `evidence_judge` as separate stages before the coordinator finalizes the answer.
3. The coordinator may only cite references approved by `evidence_judge`.
4. If no directly usable evidence exists, the coordinator should still answer, but without references and with an explicit conservative tone.
5. All agents should preserve reusable evidence trails for later case review and knowledge accumulation.
