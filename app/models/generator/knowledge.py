"""知识点数据契约 + 载入 knowledge.json。

来自 draft POC 的 KnowledgePoint（两份 POC 里结构一致，这里统一一份）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KnowledgePoint:
    id: str
    name: str
    knowledge_domain: str = ""
    point_type: str = ""
    importance: int = 3
    difficulty: int = 3
    supports_objectives: list[int] = field(default_factory=list)
    definition: str = ""
    learning_significance: str = ""
    detailed_explanation: str = ""
    examples: str = ""
    key_points_and_difficulties: str = ""
    prerequisites: dict[str, str] = field(default_factory=dict)
    successors: dict[str, str] = field(default_factory=dict)
    related: dict[str, str] = field(default_factory=dict)
