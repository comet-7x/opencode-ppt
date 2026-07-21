"""单阶段产线服务基类：统一 context + 一行 submit（建任务→后台推进→返回 TaskRef）。

子类只需：设 `KIND`、实现 `run`（纯用例）和 `run_task`（runner）。接口层一行调用 `submit`。
依赖走全局 AppContext 单例（构造时可传自定义 context，测试用）。
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.components.task import Task, TaskStore
from app.core.context import AppContext
from app.core.context import context as app_context
from app.schemas.generator.task import TaskRef


class BaseGenService:
    KIND: ClassVar[str] = ""

    def __init__(self, context: AppContext | None = None) -> None:
        self.context = context if context is not None else app_context

    def submit(self, request: Any) -> TaskRef:
        """接口层一行调用：创建任务 + 后台推进 + 返回引用。"""
        store = self.context.task_store
        task = store.create(self.KIND, request=request)
        store.spawn(self.run_task(task, store))
        return TaskRef(task_id=task.task_id, kind=task.kind, status=task.status)

    async def run_task(self, task: Task, store: TaskStore) -> None:  # 子类实现
        raise NotImplementedError
