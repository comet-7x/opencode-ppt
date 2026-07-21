"""API 接线测试（无 DI，全局单例）：配置全局 context + 打桩 router 层服务单例。

假服务是真服务类子类（只 override 连模型的 run），经继承的 __init__ 用全局 context；
故 submit / run_task / GET 都读同一个（测试设置的）全局 task_store。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import ASGITransport

import app.api.generator.homework as hw_router
import app.api.generator.lesson_plan as lp_router
import app.api.generator.ppt as ppt_router
import app.api.generator.slides as sl_router
import app.services.generator.lesson_plan.service as lp_service_mod
from app.components.task import InMemoryTaskStore
from app.core.context import context
from app.main import app
from app.models.generator.lesson_plan import LessonPlanResponse
from app.models.generator.slides import SlideContent, SlideLayout
from app.schemas.generator.homework import HomeworkResult
from app.services.generator.homework.service import HomeworkService
from app.services.generator.lesson_plan.service import LessonPlanService
from app.services.generator.ppt.service import PptService
from app.services.generator.slides.service import SlidesService
from tests.conftest import err_msg, unwrap

_KN_BODY = {
    "knowledge_json": {"course": {"title": "海洋管理概论"}, "points": []},
    "point_ids": ["K001"],
    "session_title": "导论",
}


def _slides() -> list[SlideContent]:
    return [
        SlideContent(index=1, title="封面", layout=SlideLayout.COVER),
        SlideContent(index=2, title="内容", body="- a", layout=SlideLayout.CONTENT_TEXT),
    ]


class FakeLessonPlanService(LessonPlanService):
    def __init__(self, exc: Exception | None = None) -> None:
        super().__init__()
        self._exc = exc

    async def run(self, *_a: Any, **_k: Any) -> LessonPlanResponse:
        if self._exc:
            raise self._exc
        return LessonPlanResponse(lesson_plan_md="LP教案")


class FakeSlidesService(SlidesService):
    def __init__(self, exc: Exception | None = None) -> None:
        super().__init__()
        self._exc = exc

    async def run(self, *_a: Any, **_k: Any) -> list[SlideContent]:
        if self._exc:
            raise self._exc
        return _slides()


class FakeHomeworkService(HomeworkService):
    def __init__(self, exc: Exception | None = None) -> None:
        super().__init__()
        self._exc = exc

    async def run(self, *_a: Any, **_k: Any) -> HomeworkResult:
        if self._exc:
            raise self._exc
        return HomeworkResult(
            course_title="海洋管理概论", session_title="导论", total_score=21,
            difficulty="advanced", questions=[], worksheet_md="# 卷", answer_key_md="# 答案",
            verify_results=None,
        )


@pytest.fixture(autouse=True)
def _wire(monkeypatch: pytest.MonkeyPatch):
    context.initialize()  # 幂等：建真 provider/source/store（不触网，直到调模型）
    context._task_store = InMemoryTaskStore()  # 每个测试一份干净的
    monkeypatch.setattr(lp_router, "lesson_plan_service", FakeLessonPlanService())
    monkeypatch.setattr(sl_router, "slides_service", FakeSlidesService())
    monkeypatch.setattr(hw_router, "homework_service", FakeHomeworkService())
    monkeypatch.setattr(
        ppt_router, "ppt_service",
        PptService(lesson_plan=FakeLessonPlanService(), slides=FakeSlidesService()),
    )
    yield


@pytest.fixture
def store(_wire: None) -> InMemoryTaskStore:
    return context.task_store  # type: ignore[return-value]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ————————————————————— 健康 ————————————————————— #
async def test_health() -> None:
    async with _client() as c:
        r = await c.get("/health")
    assert unwrap(r)["model"] == "Qwen/Qwen3.5-27B"


# ————————————————————— 独立端点：提交→轮询 ————————————————————— #
async def test_lesson_plan_submit_poll(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        ref = unwrap(await c.post("/lesson-plan/create", json=_KN_BODY))
        assert ref["status"] == "pending"
        task_id = ref["task_id"]
        await store.wait_all()
        r = await c.get(f"/lesson-plan/get/{task_id}")
    body = unwrap(r)
    assert body["status"] == "succeeded"
    assert body["result"]["course_title"] == "海洋管理概论"
    assert body["result"]["lesson_plan_md"] == "LP教案"


async def test_slides_submit_poll(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        r = await c.post("/slides/create", json={"lesson_plan_md": "# 教案", "deck_title": "导论"})
        task_id = unwrap(r)["task_id"]
        await store.wait_all()
        r = await c.get(f"/slides/get/{task_id}")
    body = unwrap(r)
    assert body["status"] == "succeeded"
    assert len(body["result"]["slides"]) == 2
    assert body["result"]["source_markdown"].count("## ") == 2


async def test_homework_submit_poll(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        r = await c.post("/homework/create", json=_KN_BODY)
        task_id = unwrap(r)["task_id"]
        await store.wait_all()
        r = await c.get(f"/homework/get/{task_id}")
    body = unwrap(r)
    assert body["status"] == "succeeded"
    assert body["result"]["total_score"] == 21


async def test_lesson_plan_validation_422() -> None:
    async with _client() as c:
        r = await c.post("/lesson-plan/create", json={"point_ids": ["K001"], "session_title": "导论"})
    assert "请求参数校验失败" in err_msg(r, 422)


async def test_get_unknown_task_404() -> None:
    async with _client() as c:
        r = await c.get("/lesson-plan/get/nope")
    err_msg(r, 404)


# ————————————————————— 错误：异步任务里落 task.error ————————————————————— #
async def test_parse_error_lands_in_task(
    store: InMemoryTaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.generator.common.errors import ParseError

    monkeypatch.setattr(lp_router, "lesson_plan_service", FakeLessonPlanService(exc=ParseError("坏 JSON")))
    async with _client() as c:
        r = await c.post("/lesson-plan/create", json=_KN_BODY)
        task_id = unwrap(r)["task_id"]
        await store.wait_all()
        r = await c.get(f"/lesson-plan/get/{task_id}")
    body = unwrap(r)
    assert body["status"] == "failed"
    assert "ParseError" in body["error"]


async def test_knowledge_error_lands_in_task(
    store: InMemoryTaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.generator.common.errors import KnowledgeError

    def boom(*_a: Any, **_k: Any) -> Any:
        raise KnowledgeError("knowledge 文件不存在")

    monkeypatch.setattr(lp_service_mod, "resolve_knowledge", boom)
    async with _client() as c:
        r = await c.post("/lesson-plan/create", json=_KN_BODY)
        task_id = unwrap(r)["task_id"]
        await store.wait_all()
        body = unwrap(await c.get(f"/lesson-plan/get/{task_id}"))
    assert body["status"] == "failed"
    assert "KnowledgeError" in body["error"]


async def test_upstream_model_error_lands_in_task(
    store: InMemoryTaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openai import APIConnectionError

    monkeypatch.setattr(
        hw_router, "homework_service",
        FakeHomeworkService(exc=APIConnectionError(request=httpx.Request("POST", "http://model"))),
    )
    async with _client() as c:
        r = await c.post("/homework/create", json=_KN_BODY)
        task_id = unwrap(r)["task_id"]
        await store.wait_all()
        body = unwrap(await c.get(f"/homework/get/{task_id}"))
    assert body["status"] == "failed"
    assert "APIConnectionError" in body["error"]


# ————————————————————— PPT 全管线 ————————————————————— #
async def test_ppt_job_runs_to_slides_gate_then_confirm(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        job_id = unwrap(await c.post("/ppt/create", json={"name": "proj", **_KN_BODY}))["task_id"]
        await store.wait_all()

        detail = unwrap(await c.get(f"/ppt/get/{job_id}"))
        assert detail["status"] == "awaiting_review"
        assert detail["stage"] == "slides"
        assert len(detail["review"]["slides"]) == 2

        unwrap(await c.post(f"/ppt/confirm/{job_id}", json={"action": "approve"}))
        await store.wait_all()

        detail = unwrap(await c.get(f"/ppt/get/{job_id}"))
    assert detail["stage"] == "render"
    assert detail["status"] == "failed"
    assert "阶段二" in detail["error"]
    assert detail["source_markdown"].count("## ") == 2


async def test_ppt_job_list_and_delete(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        job_id = unwrap(await c.post("/ppt/create", json={"name": "proj", **_KN_BODY}))["task_id"]
        await store.wait_all()

        rows = unwrap(await c.get("/ppt/list"))
        assert any(j["task_id"] == job_id for j in rows)

        assert unwrap(await c.delete(f"/ppt/delete/{job_id}")) is None
        err_msg(await c.get(f"/ppt/get/{job_id}"), 404)


async def test_confirm_missing_job_404() -> None:
    async with _client() as c:
        r = await c.post("/ppt/confirm/nope", json={"action": "approve"})
    err_msg(r, 404)


def test_file_knowledge_source_raises_knowledge_error() -> None:
    from app.services.generator.common.errors import KnowledgeError
    from app.services.generator.common.knowledge import FileKnowledgeSource

    with pytest.raises(KnowledgeError):
        FileKnowledgeSource().fetch("/no/such/file.json")
