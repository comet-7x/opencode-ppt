"""通用任务运行时单测：CRUD、按 kind 过滤、更新时间戳、后台推进。"""

from __future__ import annotations

import asyncio

from app.components.task import InMemoryTaskStore, TaskStatus


def test_create_assigns_id_and_defaults() -> None:
    store = InMemoryTaskStore()
    t = store.create("lesson_plan", request={"a": 1})
    assert t.task_id and len(t.task_id) == 12
    assert t.kind == "lesson_plan"
    assert t.request == {"a": 1}
    assert t.status is TaskStatus.PENDING
    assert t.stage is None and t.result is None and t.error is None
    assert t.context == {}


def test_create_accepts_extra_fields() -> None:
    store = InMemoryTaskStore()
    t = store.create("ppt", request=None, stage="render", context={"source_markdown": "x"})
    assert t.stage == "render"
    assert t.context["source_markdown"] == "x"


def test_context_is_not_shared_between_tasks() -> None:
    store = InMemoryTaskStore()
    a, b = store.create("k"), store.create("k")
    a.context["x"] = 1
    assert b.context == {}  # default_factory，不是共享的可变默认值


def test_get_and_missing() -> None:
    store = InMemoryTaskStore()
    t = store.create("k")
    assert store.get(t.task_id) is t
    assert store.get("nope") is None


def test_list_filters_by_kind_and_sorts_newest_first() -> None:
    store = InMemoryTaskStore()
    a = store.create("lesson_plan")
    b = store.create("homework")
    c = store.create("lesson_plan")
    assert {t.task_id for t in store.list("lesson_plan")} == {a.task_id, c.task_id}
    assert [t.task_id for t in store.list("homework")] == [b.task_id]
    assert len(store.list()) == 3
    # 最新在前
    assert store.list()[0].created_at >= store.list()[-1].created_at


def test_update_sets_fields_and_bumps_updated_at() -> None:
    store = InMemoryTaskStore()
    t = store.create("k")
    before = t.updated_at
    store.update(t, status=TaskStatus.SUCCEEDED, result={"ok": True})
    assert t.status is TaskStatus.SUCCEEDED
    assert t.result == {"ok": True}
    assert t.updated_at >= before


def test_delete() -> None:
    store = InMemoryTaskStore()
    t = store.create("k")
    assert store.delete(t.task_id) is True
    assert store.get(t.task_id) is None
    assert store.delete(t.task_id) is False  # 幂等：第二次返回 False


async def test_spawn_runs_in_background_and_wait_all_drains() -> None:
    store = InMemoryTaskStore()
    done: list[str] = []

    async def work(tag: str) -> None:
        await asyncio.sleep(0)
        done.append(tag)

    store.spawn(work("a"))
    store.spawn(work("b"))
    await store.wait_all()
    assert sorted(done) == ["a", "b"]


async def test_wait_all_also_drains_tasks_spawned_by_tasks() -> None:
    """阶段推进会在任务里再 spawn 任务，wait_all 必须一路等干净。"""
    store = InMemoryTaskStore()
    done: list[str] = []

    async def inner() -> None:
        done.append("inner")

    async def outer() -> None:
        store.spawn(inner())
        done.append("outer")

    store.spawn(outer())
    await store.wait_all()
    assert sorted(done) == ["inner", "outer"]


async def test_spawn_swallows_task_exception_without_breaking_wait_all() -> None:
    store = InMemoryTaskStore()

    async def boom() -> None:
        raise RuntimeError("背景任务炸了")

    store.spawn(boom())
    await store.wait_all()  # 不应把异常抛给调用方
