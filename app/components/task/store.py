"""任务态存储：接口 + 内存实现 + 后台推进（产品无关）。

`TaskStore` 是「会变的边界」：本版 `InMemoryTaskStore`（进程内 dict + asyncio 后台任务），
将来换 `RedisTaskStore` + 任务队列只换实现，runner/api 不动。

**取消语义**：`cancel(task_id)` 是「请求取消」而非「保证已停」——
- 有活着的后台协程 → `asyncio.Task.cancel()`（真正生效要等协程走到下一个 await 点）；
- 停在确认门（`AWAITING_REVIEW`，后台协程早已结束在等人）→ 没有可取消的协程，
  但业务状态照样打成 `CANCELLED`，确认门端点据此拒绝续跑。
两种情况都**同步**把 Task 打成 `CANCELLED`，调用方立刻能查到。
换 Redis/队列实现时，同一签名可用「取消标志位 + worker 轮询」实现。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from typing import Any

from app.components.task.models import Task, TaskStatus, utcnow

logger = logging.getLogger("app.task")


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
    def spawn(self, task: Task, coro: Coroutine[Any, Any, None]) -> None:
        """把 runner 丢后台推进，并与业务 task 绑定（供 cancel 定位）。"""

    @abstractmethod
    def cancel(self, task_id: str) -> bool:
        """请求取消。可取消→True；任务不存在或已是终态→False。"""


class InMemoryTaskStore(TaskStore):
    """进程内任务表（重启即丢）。后台任务用 asyncio.create_task。"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._bg: dict[str, asyncio.Task[None]] = {}  # task_id → 在跑的协程（持引用防 GC）

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
        """删任务前先掐掉在跑的协程，避免它继续更新一个已被摘掉的 Task。"""
        self._cancel_bg(task_id)
        return self._tasks.pop(task_id, None) is not None

    def update(self, task: Task, **fields: Any) -> Task:
        for k, v in fields.items():
            setattr(task, k, v)
        task.updated_at = utcnow()
        return task

    # ————————————————————— 后台推进 ————————————————————— #
    def spawn(self, task: Task, coro: Coroutine[Any, Any, None]) -> None:
        bg = asyncio.create_task(coro, name=f"{task.kind}:{task.task_id}")
        # 同一业务任务多阶段续跑（如 ppt 确认门后）会覆盖上一条已结束的记录
        self._bg[task.task_id] = bg
        bg.add_done_callback(self._on_done)

    def _on_done(self, bg: asyncio.Task[None]) -> None:
        """后台任务收尾：解绑 + 兜底记录异常。

        正常的业务失败由 runner 自己写进 `task.error`，走不到这里；这里接住的是
        **runner 自身的 bug**（比如 except 块里又抛了）——否则 asyncio 只会在 GC 时
        打一句 "Task exception was never retrieved"，极难定位。
        """
        for task_id, running in list(self._bg.items()):
            if running is bg:
                del self._bg[task_id]
                break
        if bg.cancelled():
            logger.info("后台任务 %s 已取消", bg.get_name())
            return
        exc = bg.exception()
        if exc is not None:
            logger.error("后台任务 %s 未捕获异常: %r", bg.get_name(), exc, exc_info=exc)

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status.is_terminal:
            return False
        self._cancel_bg(task_id)          # 停在确认门时没有协程可掐，只改状态
        self.update(task, status=TaskStatus.CANCELLED, error="任务被主动取消")
        return True

    def cancel_all(self) -> int:
        """批量取消（关停用）。返回真正被取消的任务数。"""
        return sum(self.cancel(task_id) for task_id in list(self._tasks))

    def _cancel_bg(self, task_id: str) -> None:
        bg = self._bg.get(task_id)
        if bg is not None and not bg.done():
            bg.cancel()

    async def wait_all(self) -> None:
        """等所有后台任务跑完（供测试/优雅关停；会等派生任务）。"""
        while self._bg:
            await asyncio.gather(*list(self._bg.values()), return_exceptions=True)
