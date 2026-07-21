"""通用任务运行时单测：CRUD、按 kind 过滤、更新时间戳、后台推进、取消。"""

from __future__ import annotations

import asyncio
import logging

import pytest

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

    store.spawn(store.create("k"), work("a"))
    store.spawn(store.create("k"), work("b"))
    await store.wait_all()
    assert sorted(done) == ["a", "b"]


async def test_wait_all_also_drains_tasks_spawned_by_tasks() -> None:
    """阶段推进会在任务里再 spawn 任务，wait_all 必须一路等干净。"""
    store = InMemoryTaskStore()
    done: list[str] = []

    async def inner() -> None:
        done.append("inner")

    async def outer() -> None:
        store.spawn(store.create("k"), inner())
        done.append("outer")

    store.spawn(store.create("k"), outer())
    await store.wait_all()
    assert sorted(done) == ["inner", "outer"]


async def test_spawn_swallows_task_exception_without_breaking_wait_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """runner 自身的 bug 逃逸出来时：不打断 wait_all，但必须被 done-callback 记下来。"""
    store = InMemoryTaskStore()

    async def boom() -> None:
        raise RuntimeError("背景任务炸了")

    with caplog.at_level(logging.ERROR, logger="app.task"):
        store.spawn(store.create("k"), boom())
        await store.wait_all()  # 不应把异常抛给调用方
    assert "背景任务炸了" in caplog.text


# ————————————————————— 取消 ————————————————————— #
async def test_cancel_running_task_stops_the_coroutine() -> None:
    store = InMemoryTaskStore()
    task = store.create("k")
    reached_end = False

    async def long_work() -> None:
        nonlocal reached_end
        await asyncio.sleep(10)      # 取消在这个 await 点生效
        reached_end = True

    store.spawn(task, long_work())
    await asyncio.sleep(0)           # 让协程真正跑起来并挂在 sleep 上
    assert store.cancel(task.task_id) is True
    await store.wait_all()

    assert reached_end is False
    assert task.status is TaskStatus.CANCELLED
    assert task.error == "任务被主动取消"


async def test_cancel_at_review_gate_has_no_coroutine_but_still_marks_cancelled() -> None:
    """停在确认门时后台协程早已结束——没得可掐，但业务状态照样要作废。"""
    store = InMemoryTaskStore()
    task = store.create("ppt")
    store.update(task, status=TaskStatus.AWAITING_REVIEW)

    assert store.cancel(task.task_id) is True
    assert task.status is TaskStatus.CANCELLED


def test_cancel_is_rejected_for_terminal_and_missing_tasks() -> None:
    store = InMemoryTaskStore()
    for terminal in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        task = store.create("k")
        store.update(task, status=terminal)
        assert store.cancel(task.task_id) is False
        assert task.status is terminal      # 状态没被改写
    assert store.cancel("nope") is False


async def test_cancel_all_returns_count_of_actually_cancelled() -> None:
    store = InMemoryTaskStore()
    store.create("k")                                   # pending → 可取消
    store.update(store.create("k"), status=TaskStatus.RUNNING)   # 可取消
    store.update(store.create("k"), status=TaskStatus.SUCCEEDED)  # 已终态，跳过

    assert store.cancel_all() == 2
    assert [t.status for t in store.list()].count(TaskStatus.CANCELLED) == 2


async def test_delete_also_kills_the_running_coroutine() -> None:
    """删任务后协程不该继续跑着更新一个已被摘掉的 Task。"""
    store = InMemoryTaskStore()
    task = store.create("k")
    reached_end = False

    async def long_work() -> None:
        nonlocal reached_end
        await asyncio.sleep(10)
        reached_end = True

    store.spawn(task, long_work())
    await asyncio.sleep(0)
    assert store.delete(task.task_id) is True
    await store.wait_all()
    assert reached_end is False


def test_is_terminal_covers_exactly_the_end_states() -> None:
    assert [s.is_terminal for s in TaskStatus] == [
        False,   # pending
        False,   # running
        False,   # awaiting_review
        True,    # succeeded
        True,    # failed
        True,    # cancelled
    ]
