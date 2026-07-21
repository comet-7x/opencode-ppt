"""单阶段产线服务基类：统一 context + 一行 submit（建任务→后台推进→返回 TaskRef）。

子类只需：设 `KIND`、实现 `run`（纯用例）和 `run_task`（runner）。接口层一行调用 `submit`。
依赖走全局 AppContext 单例（构造时可传自定义 context，测试用）。

`run_task` 用 `@abstractmethod`：子类漏实现会在**实例化时**就 TypeError（模块级单例导致
import 即触发），而不是等后台任务跑起来才抛 NotImplementedError 被吞进 task.error。
`KIND` 用 `__init_subclass__` 校验，漏设会在**类定义时**报错。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.components.task import Task, TaskStore
from app.core.context import AppContext
from app.core.context import context as app_context
from app.schemas.generator.task import TaskRef


class BaseGenService(ABC):
    KIND: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 注意：此刻 ABCMeta 还没设 cls.__abstractmethods__，只能自己探测抽象方法。
        # 仍含抽象方法 = 中间抽象类，放行；具体子类必须设 KIND（否则 kind 会是空串，
        # 建出的任务 GET 时 kind 对不上而 404，属难查的静默 bug）。
        still_abstract = any(
            getattr(getattr(cls, name, None), "__isabstractmethod__", False)
            for name in dir(cls)
        )
        if not still_abstract and not getattr(cls, "KIND", ""):
            raise TypeError(f"{cls.__name__} 必须设置类属性 KIND")

    def __init__(self, context: AppContext | None = None) -> None:
        self.context = context if context is not None else app_context

    def submit(self, request: Any) -> TaskRef:
        """接口层一行调用：创建任务 + 后台推进 + 返回引用。"""
        store = self.context.task_store
        task = store.create(self.KIND, request=request)
        store.spawn(self.run_task(task, store))
        return TaskRef(task_id=task.task_id, kind=task.kind, status=task.status)

    @abstractmethod
    async def run_task(self, task: Task, store: TaskStore) -> None:
        """runner：管任务态流转 + 异常兜底进 task.error。子类必须实现。"""
        ...
