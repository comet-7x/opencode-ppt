"""任务态存储：接口 + 内存实现 + 后台推进（产品无关）。

`TaskStore` 是「会变的边界」：本版 `InMemoryTaskStore`（进程内 dict + asyncio 后台任务），
将来换 `RedisTaskStore` + 任务队列只换实现，runner/api 不动。
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from typing import Any

from app.components.task.models import Task, utcnow


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
        task.updated_at = utcnow()
        return task

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        bg = asyncio.create_task(coro)
        self._bg.add(bg)
        bg.add_done_callback(self._bg.discard)

    async def wait_all(self) -> None:
        """等所有后台任务跑完（供测试/优雅关停；会等派生任务）。"""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
