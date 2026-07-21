"""PptService 状态机单测：注入假子服务 + 假 context（不连模型），验证状态转移与确认门。

依赖注入让测试很干净：给 PptService 传假的 LessonPlanService/SlidesService；
context 用轻量 SimpleNamespace（knowledge_source=None，inline knowledge 直通不触碰它）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.components.task import InMemoryTaskStore, Task
from app.models.generator.lesson_plan import LessonPlanResponse
from app.models.generator.slides import SlideContent, SlideLayout
from app.schemas.generator.ppt import (
    ConfirmAction,
    ConfirmRequest,
    CreateJobRequest,
    PptStage,
)
from app.schemas.generator.task import TaskStatus
from app.services.generator.lesson_plan.service import LessonPlanService
from app.services.generator.ppt.service import PptService
from app.services.generator.slides.service import SlidesService


class FakeLessonPlanService(LessonPlanService):
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *_a: Any, **_k: Any) -> LessonPlanResponse:
        self.calls += 1
        return LessonPlanResponse(lesson_plan_md=f"LP#{self.calls}")


class FakeSlidesService(SlidesService):
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *_a: Any, **_k: Any) -> list[SlideContent]:
        self.calls += 1
        return [
            SlideContent(index=1, title="封面", layout=SlideLayout.COVER),
            SlideContent(index=2, title="内容", body="- a", layout=SlideLayout.CONTENT_TEXT),
        ]


def _fake_context(store: InMemoryTaskStore) -> Any:
    return SimpleNamespace(knowledge_source=None, model_provider=None, task_store=store)


@pytest.fixture
def pipe() -> tuple[InMemoryTaskStore, PptService, FakeLessonPlanService, FakeSlidesService]:
    store = InMemoryTaskStore()
    lp = FakeLessonPlanService()
    sl = FakeSlidesService()
    svc = PptService(context=_fake_context(store), lesson_plan=lp, slides=sl)
    return store, svc, lp, sl


def _req(**over: Any) -> CreateJobRequest:
    base: dict[str, Any] = {
        "name": "proj",
        "knowledge_json": {"course": {}, "points": []},
        "point_ids": ["K001"],
        "session_title": "海洋管理导论",
    }
    base.update(over)
    return CreateJobRequest(**base)


async def _drive_start(store: InMemoryTaskStore, svc: PptService, req: CreateJobRequest) -> Task:
    start_stage = PptStage.RENDER if req.source_markdown else PptStage.LESSON_PLAN
    context = {"source_markdown": req.source_markdown} if req.source_markdown else {}
    task = store.create("ppt", request=req, stage=start_stage, context=context)
    svc.start(task, store)
    await store.wait_all()
    return task


# ————————————————————— 无门：一路跑到渲染桩 ————————————————————— #
async def test_auto_runs_to_render_stub(pipe: Any) -> None:
    store, svc, _lp, _sl = pipe
    task = await _drive_start(store, svc, _req(confirm_lesson_plan=False, confirm_slides=False))

    assert task.context["lesson_plan_md"] == "LP#1"
    assert len(task.context["slides"]) == 2
    assert task.context["source_markdown"].count("## ") == 2
    assert task.stage == PptStage.RENDER
    assert task.status == TaskStatus.FAILED
    assert "阶段二" in (task.error or "")


# ————————————————————— slides 确认门（默认）————————————————————— #
async def test_slides_gate_then_approve(pipe: Any) -> None:
    store, svc, _lp, _sl = pipe
    task = await _drive_start(store, svc, _req(confirm_slides=True))

    assert task.stage == PptStage.SLIDES
    assert task.status == TaskStatus.AWAITING_REVIEW
    assert len(task.context["slides"]) == 2

    svc.confirm(task, store, ConfirmRequest(action=ConfirmAction.APPROVE))
    await store.wait_all()
    assert task.context["source_markdown"] and task.stage == PptStage.RENDER
    assert task.status == TaskStatus.FAILED


async def test_slides_gate_edit_then_approve(pipe: Any) -> None:
    store, svc, _lp, _sl = pipe
    task = await _drive_start(store, svc, _req(confirm_slides=True))

    edited = [SlideContent(index=1, title="改后唯一页", body="- x", layout=SlideLayout.END)]
    svc.confirm(task, store, ConfirmRequest(action=ConfirmAction.APPROVE, edited_slides=edited))
    await store.wait_all()
    assert task.context["slides"] == edited
    assert "改后唯一页" in task.context["source_markdown"]


async def test_slides_gate_regenerate(pipe: Any) -> None:
    store, svc, _lp, sl = pipe
    task = await _drive_start(store, svc, _req(confirm_slides=True))

    svc.confirm(task, store, ConfirmRequest(action=ConfirmAction.REGENERATE))
    await store.wait_all()
    assert task.stage == PptStage.SLIDES
    assert task.status == TaskStatus.AWAITING_REVIEW
    assert sl.calls == 2


# ————————————————————— 教案确认门 ————————————————————— #
async def test_lesson_plan_gate_then_approve(pipe: Any) -> None:
    store, svc, _lp, sl = pipe
    task = await _drive_start(store, svc, _req(confirm_lesson_plan=True, confirm_slides=True))

    assert task.stage == PptStage.LESSON_PLAN
    assert task.status == TaskStatus.AWAITING_REVIEW
    assert task.context["lesson_plan_md"] == "LP#1"
    assert sl.calls == 0

    svc.confirm(task, store, ConfirmRequest(action=ConfirmAction.APPROVE))
    await store.wait_all()
    assert task.stage == PptStage.SLIDES
    assert task.status == TaskStatus.AWAITING_REVIEW
    assert sl.calls == 1


async def test_lesson_plan_gate_edit_and_regenerate(pipe: Any) -> None:
    store, svc, lp, _sl = pipe
    task = await _drive_start(store, svc, _req(confirm_lesson_plan=True))

    svc.confirm(task, store, ConfirmRequest(action=ConfirmAction.REGENERATE, feedback="更详细"))
    await store.wait_all()
    assert lp.calls == 2
    assert task.context["lesson_plan_md"] == "LP#2"
    assert task.status == TaskStatus.AWAITING_REVIEW

    svc.confirm(task, store, ConfirmRequest(action=ConfirmAction.APPROVE, edited_content="人工改稿"))
    await store.wait_all()
    assert task.context["lesson_plan_md"] == "人工改稿"


# ————————————————————— 直接渲染入口 ————————————————————— #
async def test_direct_source_markdown_enters_render(pipe: Any) -> None:
    store, svc, _lp, _sl = pipe
    task = await _drive_start(store, svc, _req(source_markdown="## 一页\n\n- x\n"))

    assert task.stage == PptStage.RENDER
    assert task.status == TaskStatus.FAILED
    assert task.context["source_markdown"] == "## 一页\n\n- x\n"


# ————————————————————— 非确认门上 confirm 报错 ————————————————————— #
async def test_confirm_when_not_awaiting_raises(pipe: Any) -> None:
    store, svc, _lp, _sl = pipe
    task = store.create("ppt", request=_req(), stage=PptStage.LESSON_PLAN)
    with pytest.raises(ValueError, match="不在确认门"):
        svc.confirm(task, store, ConfirmRequest())
