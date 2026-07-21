"""共享 fixtures。

两类测试的打桩边界不同：
- unit/：不碰模型、不起 FastAPI，直接测纯函数。
- integration/：只把 **ChatAgent（模型调用）** 换掉，其余 api→service→logic→解析→渲染→任务态
  全部真跑，才能真正覆盖业务逻辑。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from camel.messages import BaseMessage
from camel.responses import ChatAgentResponse

from app.components.task import InMemoryTaskStore
from app.core.context import context


# ————————————————————— 样例知识点 ————————————————————— #
@pytest.fixture
def knowledge() -> dict[str, Any]:
    """两个知识点的最小 knowledge，字段齐全便于测选点/检索/prompt 组装。"""
    return {
        "course": {
            "title": "海洋管理概论",
            "profile": {"授课对象": "本科生"},
            "objectives": [{"text": "理解海洋治理"}, {"text": "掌握资源管理"}],
        },
        "points": [
            {
                "id": "K001",
                "name": "海洋管理的概念",
                "knowledge_domain": "基础理论",
                "point_type": "概念",
                "importance": 5,
                "difficulty": 3,
                "supports_objectives": [1],
                "definition": "对海洋事务的综合管理",
                "learning_significance": "奠定基础",
                "detailed_explanation": "从狭义到广义的演变",
                "examples": "里约会议",
                "key_points_and_difficulties": "易与海洋治理混淆",
                "prerequisites": {},
                "successors": {"K002": "延伸"},
                "related": {"K002": "相关"},
            },
            {
                "id": "K002",
                "name": "海洋资源管理",
                "knowledge_domain": "核心概念",
                "point_type": "制度",
                "importance": 3,
                "difficulty": 5,
                "supports_objectives": [2],
                "definition": "对海洋资源的分类与配置",
                "learning_significance": "支撑实践",
                "detailed_explanation": "生物与矿产资源差异",
                "examples": "渔业配额",
                "key_points_and_difficulties": "生物与矿产管理策略易混",
                "prerequisites": {"K001": "前置"},
                "successors": {},
                "related": {"K001": "相关"},
                "多余字段": "应被忽略",
            },
        ],
    }


# ————————————————————— 假 ChatAgent（集成测试用）————————————————————— #
def make_agent_response(content: str) -> ChatAgentResponse:
    msg = BaseMessage.make_assistant_message(role_name="assistant", content=content)
    return ChatAgentResponse(msgs=[msg], terminated=False, info={})


class FakeChatAgent:
    """替换 camel 的 ChatAgent：按预设队列依次返回内容。

    `replies` 为空时返回 `default`；设 `empty=True` 模拟"模型返回空响应"。
    每次实例化都记录 system_message，便于断言 prompt 选对了。
    """

    replies: ClassVar[list[str]] = []
    default: str = ""
    empty: bool = False
    seen_system_messages: ClassVar[list[str]] = []
    seen_prompts: ClassVar[list[str]] = []

    def __init__(self, model: Any = None, system_message: str = "", **_kw: Any) -> None:
        type(self).seen_system_messages.append(system_message)

    async def astep(self, prompt: str) -> Any:
        cls = type(self)
        cls.seen_prompts.append(prompt)
        if cls.empty:
            return ChatAgentResponse(msgs=[], terminated=False, info={})
        content = cls.replies.pop(0) if cls.replies else cls.default
        return make_agent_response(content)

    @classmethod
    def reset(cls, *, replies: list[str] | None = None, default: str = "", empty: bool = False) -> None:
        cls.replies = list(replies or [])
        cls.default = default
        cls.empty = empty
        cls.seen_system_messages = []
        cls.seen_prompts = []


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> type[FakeChatAgent]:
    """把三条产线 logic 里的 ChatAgent 全换成假的（只挡模型，其余真跑）。"""
    import app.services.generator.homework.logic as hw_logic
    import app.services.generator.lesson_plan.logic as lp_logic
    import app.services.generator.slides.logic as sl_logic

    FakeChatAgent.reset()
    for mod in (lp_logic, sl_logic, hw_logic):
        monkeypatch.setattr(mod, "ChatAgent", FakeChatAgent)
    return FakeChatAgent


# ————————————————————— 干净的全局 context ————————————————————— #
@pytest.fixture
def task_store() -> Iterator[InMemoryTaskStore]:
    """给全局 context 换一份干净的 task_store（测试间互不干扰）。"""
    context.initialize()  # 幂等；构建真 provider/source（不触网）
    original = context._task_store
    store = InMemoryTaskStore()
    context._task_store = store
    yield store
    context._task_store = original
