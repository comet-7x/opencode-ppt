"""BaseGenService 契约单测：ABC 强制、KIND 强制、submit 三步语义。"""

from __future__ import annotations

from abc import abstractmethod
from types import SimpleNamespace
from typing import Any

import pytest

from app.components.task import InMemoryTaskStore, Task, TaskStatus, TaskStore
from app.services.generator.common.service import BaseGenService


class _Recording(BaseGenService):
    """最小可用子类：记录 run_task 被调用。"""

    KIND = "demo"

    def __init__(self, context: Any) -> None:
        super().__init__(context)
        self.ran: list[str] = []

    async def run_task(self, task: Task, store: TaskStore) -> None:
        self.ran.append(task.task_id)
        store.update(task, status=TaskStatus.SUCCEEDED, result={"ok": True})


def _ctx(store: InMemoryTaskStore) -> Any:
    return SimpleNamespace(task_store=store, model_provider=None, knowledge_source=None)


# ————————————————————— 契约强制 ————————————————————— #
def test_missing_run_task_fails_at_instantiation() -> None:
    """漏实现 run_task：实例化即 TypeError，而不是等后台任务跑起来才炸。"""

    class NoRunTask(BaseGenService):
        KIND = "x"

    with pytest.raises(TypeError, match="abstract"):
        NoRunTask()  # type: ignore[abstract]


def test_missing_kind_fails_at_class_definition() -> None:
    """漏设 KIND：类定义即报错（否则 kind 为空串，GET 时对不上而 404）。"""
    with pytest.raises(TypeError, match="必须设置类属性 KIND"):

        class NoKind(BaseGenService):
            async def run_task(self, task: Task, store: TaskStore) -> None: ...


def test_intermediate_abstract_subclass_is_allowed() -> None:
    """中间抽象类（仍有抽象方法）不要求 KIND。"""

    class Mid(BaseGenService):
        @abstractmethod
        async def extra(self) -> None: ...

    assert Mid.__name__ == "Mid"


# ————————————————————— context 注入 ————————————————————— #
def test_explicit_context_wins_over_global() -> None:
    store = InMemoryTaskStore()
    ctx = _ctx(store)
    assert _Recording(ctx).context is ctx


def test_defaults_to_global_context() -> None:
    from app.core.context import context as global_ctx

    class Plain(BaseGenService):
        KIND = "plain"

        async def run_task(self, task: Task, store: TaskStore) -> None: ...

    assert Plain().context is global_ctx


# ————————————————————— submit 三步语义 ————————————————————— #
async def test_submit_creates_task_spawns_runner_and_returns_ref() -> None:
    store = InMemoryTaskStore()
    svc = _Recording(_ctx(store))

    ref = svc.submit({"payload": 1})

    # 1) 立即返回引用，状态是 pending（后台还没跑）
    assert ref.kind == "demo"
    assert ref.status is TaskStatus.PENDING
    # 2) 任务已入库，带上原始 request
    task = store.get(ref.task_id)
    assert task is not None and task.request == {"payload": 1}
    # 3) runner 在后台跑完
    await store.wait_all()
    assert svc.ran == [ref.task_id]
    assert task.status is TaskStatus.SUCCEEDED
    assert task.result == {"ok": True}


async def test_submit_uses_context_task_store() -> None:
    """submit 必须用 self.context 里的 store，而不是别处的。"""
    store = InMemoryTaskStore()
    svc = _Recording(_ctx(store))
    ref = svc.submit(None)
    await store.wait_all()
    assert store.get(ref.task_id) is not None


async def test_each_submit_creates_distinct_task() -> None:
    store = InMemoryTaskStore()
    svc = _Recording(_ctx(store))
    a, b = svc.submit(None), svc.submit(None)
    await store.wait_all()
    assert a.task_id != b.task_id
    assert len(store.list("demo")) == 2
