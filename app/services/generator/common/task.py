"""通用任务运行时：任务记录 + 存储接口（内存实现）+ 后台推进。

产品无关：每条产线（教案/作业/slides/ppt）都是一个 Task，由各自的 runner 驱动。
- 单阶段产线（教案/作业/slides）：runner 跑完把结果放 result、状态置 SUCCEEDED。
- 多阶段产线（ppt）：runner 是状态机，用 context 存中间态、review 存确认门产物、
  stage 记当前阶段，跑到门停在 AWAITING_REVIEW。

`TaskStore` 是「会变的边界」接口：本版 `InMemoryTaskStore`；将来换 RedisTaskStore +
任务队列只换实现，runner/api 不动。
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.schemas.generator.task import TaskStatus


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Task:
    task_id: str
    kind: str                                     # "lesson_plan" | "homework" | "slides" | "ppt"
    request: Any = None                           # 原始请求（供 runner 读参数 / 重跑）
    status: TaskStatus = TaskStatus.PENDING
    stage: str | None = None                      # 多阶段产品的当前阶段名
    context: dict[str, Any] = field(default_factory=dict)  # runner 中间态（多阶段用）
    result: Any = None                            # 最终产物（产品的 Result 模型）
    review: Any = None                            # 确认门产物（ppt 用）
    error: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


def base_detail_fields(task: Task) -> dict[str, Any]:
    """抽出 TaskDetailBase 的公共字段，供各产线端点拼自己的 TaskDetail。"""
    return {
        "task_id": task.task_id,
        "kind": task.kind,
        "status": task.status,
        "stage": task.stage,
        "error": task.error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


class TaskStore(ABC):
    """任务态存储接口。换持久化实现（Redis/DB）时保持此签名不变。"""

    @abstractmethod
    def create(self, kind: str, request: Any = None, **fields: Any) -> Task: ...

    @abstractmethod
    def get(self, task_id: str) -> Task | None: ...

    @abstractmethod
    def list(self, kind: str | None = None) -> list[Task]: ...

    @abstractmethod
    def delete(self, task_id: str) -> bool: ...

    @abstractmethod
    def update(self, task: Task, **fields: Any) -> Task: ...

    @abstractmethod
    def spawn(self, coro: Coroutine[Any, Any, None]) -> None: ...


class InMemoryTaskStore(TaskStore):
    """进程内任务表（重启即丢）。后台任务用 asyncio.create_task。"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._bg: set[asyncio.Task[None]] = set()  # 持引用防 GC

    def create(self, kind: str, request: Any = None, **fields: Any) -> Task:
        task = Task(task_id=uuid.uuid4().hex[:12], kind=kind, request=request, **fields)
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list(self, kind: str | None = None) -> list[Task]:
        tasks = [t for t in self._tasks.values() if kind is None or t.kind == kind]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def delete(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None

    def update(self, task: Task, **fields: Any) -> Task:
        for k, v in fields.items():
            setattr(task, k, v)
        task.updated_at = _now()
        return task

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        bg = asyncio.create_task(coro)
        self._bg.add(bg)
        bg.add_done_callback(self._bg.discard)

    async def wait_all(self) -> None:
        """等所有后台任务跑完（供测试/优雅关停；会等派生任务）。"""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
