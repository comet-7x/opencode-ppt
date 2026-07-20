"""API 接线测试：注入假服务（不连模型），验证 提交→轮询、错误落 task.error、ppt 确认门。

用 httpx.ASGITransport：POST 后台任务与测试跑在同一事件循环，可用 store.wait_all() 等它跑完。
假服务是真服务类子类（只 override 连模型的 run；context 用轻量 SimpleNamespace）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

import app.services.generator.lesson_plan.service as lp_service_mod
from app.dependencies import (
    get_homework_service,
    get_lesson_plan_service,
    get_ppt_service,
    get_slides_service,
    get_task_store,
)
from app.main import app
from app.models.generator.lesson_plan import LessonPlanResponse
from app.models.generator.slides import SlideContent, SlideLayout
from app.schemas.generator.homework import HomeworkResult
from app.services.generator.common.task import InMemoryTaskStore
from app.services.generator.homework.service import HomeworkService
from app.services.generator.lesson_plan.service import LessonPlanService
from app.services.generator.ppt.service import PptService
from app.services.generator.slides.service import SlidesService

_KN_BODY = {
    "knowledge_json": {"course": {"title": "海洋管理概论"}, "points": []},
    "point_ids": ["K001"],
    "session_title": "导论",
}


def _fake_context() -> Any:
    return SimpleNamespace(knowledge_source=None, model_provider=None, task_store=None)


def _slides() -> list[SlideContent]:
    return [
        SlideContent(index=1, title="封面", layout=SlideLayout.COVER),
        SlideContent(index=2, title="内容", body="- a", layout=SlideLayout.CONTENT_TEXT),
    ]


class FakeLessonPlanService(LessonPlanService):
    def __init__(self, exc: Exception | None = None) -> None:
        self.context = _fake_context()
        self._exc = exc

    async def run(self, *_a: Any, **_k: Any) -> LessonPlanResponse:
        if self._exc:
            raise self._exc
        return LessonPlanResponse(lesson_plan_md="LP教案")


class FakeSlidesService(SlidesService):
    def __init__(self, exc: Exception | None = None) -> None:
        self.context = _fake_context()
        self._exc = exc

    async def run(self, *_a: Any, **_k: Any) -> list[SlideContent]:
        if self._exc:
            raise self._exc
        return _slides()


class FakeHomeworkService(HomeworkService):
    def __init__(self, exc: Exception | None = None) -> None:
        self.context = _fake_context()
        self._exc = exc

    async def run(self, *_a: Any, **_k: Any) -> HomeworkResult:
        if self._exc:
            raise self._exc
        return HomeworkResult(
            course_title="海洋管理概论", session_title="导论", total_score=21,
            difficulty="advanced", questions=[], worksheet_md="# 卷", answer_key_md="# 答案",
            verify_results=None,
        )


@pytest.fixture
def store() -> InMemoryTaskStore:
    return InMemoryTaskStore()


@pytest.fixture(autouse=True)
def _wire(store: InMemoryTaskStore):
    app.dependency_overrides[get_task_store] = lambda: store
    app.dependency_overrides[get_lesson_plan_service] = lambda: FakeLessonPlanService()
    app.dependency_overrides[get_slides_service] = lambda: FakeSlidesService()
    app.dependency_overrides[get_homework_service] = lambda: FakeHomeworkService()
    app.dependency_overrides[get_ppt_service] = lambda: PptService(
        context=_fake_context(),
        lesson_plan=FakeLessonPlanService(),
        slides=FakeSlidesService(),
    )
    yield
    app.dependency_overrides.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ————————————————————— 健康 ————————————————————— #
async def test_health() -> None:
    async with _client() as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["model"] == "Qwen/Qwen3.5-27B"


# ————————————————————— 独立端点：提交→轮询 ————————————————————— #
async def test_lesson_plan_submit_poll(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        r = await c.post("/lesson-plan/create", json=_KN_BODY)
        assert r.status_code == 202
        assert r.json()["status"] == "pending"
        task_id = r.json()["task_id"]
        await store.wait_all()
        r = await c.get(f"/lesson-plan/get/{task_id}")
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["result"]["course_title"] == "海洋管理概论"
    assert body["result"]["lesson_plan_md"] == "LP教案"


async def test_slides_submit_poll(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        r = await c.post("/slides/create", json={"lesson_plan_md": "# 教案", "deck_title": "导论"})
        task_id = r.json()["task_id"]
        await store.wait_all()
        r = await c.get(f"/slides/get/{task_id}")
    body = r.json()
    assert body["status"] == "succeeded"
    assert len(body["result"]["slides"]) == 2
    assert body["result"]["source_markdown"].count("## ") == 2


async def test_homework_submit_poll(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        r = await c.post("/homework/create", json=_KN_BODY)
        task_id = r.json()["task_id"]
        await store.wait_all()
        r = await c.get(f"/homework/get/{task_id}")
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["result"]["total_score"] == 21


async def test_lesson_plan_validation_422() -> None:
    async with _client() as c:
        r = await c.post("/lesson-plan/create", json={"point_ids": ["K001"], "session_title": "导论"})
    assert r.status_code == 422


async def test_get_unknown_task_404() -> None:
    async with _client() as c:
        r = await c.get("/lesson-plan/get/nope")
    assert r.status_code == 404


# ————————————————————— 错误：异步任务里落 task.error ————————————————————— #
async def test_parse_error_lands_in_task(store: InMemoryTaskStore) -> None:
    from app.services.generator.common.errors import ParseError

    app.dependency_overrides[get_lesson_plan_service] = lambda: FakeLessonPlanService(
        exc=ParseError("模型吐了非法 JSON")
    )
    async with _client() as c:
        r = await c.post("/lesson-plan/create", json=_KN_BODY)
        task_id = r.json()["task_id"]
        await store.wait_all()
        r = await c.get(f"/lesson-plan/get/{task_id}")
    body = r.json()
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
        task_id = r.json()["task_id"]
        await store.wait_all()
        r = await c.get(f"/lesson-plan/get/{task_id}")
    assert r.json()["status"] == "failed"
    assert "KnowledgeError" in r.json()["error"]


async def test_upstream_model_error_lands_in_task(store: InMemoryTaskStore) -> None:
    from openai import APIConnectionError

    app.dependency_overrides[get_homework_service] = lambda: FakeHomeworkService(
        exc=APIConnectionError(request=httpx.Request("POST", "http://model"))
    )
    async with _client() as c:
        r = await c.post("/homework/create", json=_KN_BODY)
        task_id = r.json()["task_id"]
        await store.wait_all()
        r = await c.get(f"/homework/get/{task_id}")
    assert r.json()["status"] == "failed"
    assert "APIConnectionError" in r.json()["error"]


# ————————————————————— PPT 全管线 ————————————————————— #
async def test_ppt_job_runs_to_slides_gate_then_confirm(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        r = await c.post("/ppt/create", json={"name": "proj", **_KN_BODY})
        assert r.status_code == 202
        job_id = r.json()["task_id"]
        await store.wait_all()

        r = await c.get(f"/ppt/get/{job_id}")
        detail = r.json()
        assert detail["status"] == "awaiting_review"
        assert detail["stage"] == "slides"
        assert len(detail["review"]["slides"]) == 2

        r = await c.post(f"/ppt/confirm/{job_id}", json={"action": "approve"})
        assert r.status_code == 200
        await store.wait_all()

        r = await c.get(f"/ppt/get/{job_id}")
        detail = r.json()
    assert detail["stage"] == "render"
    assert detail["status"] == "failed"
    assert "阶段二" in detail["error"]
    assert detail["source_markdown"].count("## ") == 2


async def test_ppt_job_list_and_delete(store: InMemoryTaskStore) -> None:
    async with _client() as c:
        r = await c.post("/ppt/create", json={"name": "proj", **_KN_BODY})
        job_id = r.json()["task_id"]
        await store.wait_all()

        r = await c.get("/ppt/list")
        assert any(j["task_id"] == job_id for j in r.json())

        r = await c.delete(f"/ppt/delete/{job_id}")
        assert r.status_code == 204
        r = await c.get(f"/ppt/get/{job_id}")
        assert r.status_code == 404


async def test_confirm_missing_job_404() -> None:
    async with _client() as c:
        r = await c.post("/ppt/confirm/nope", json={"action": "approve"})
    assert r.status_code == 404


def test_file_knowledge_source_raises_knowledge_error() -> None:
    from app.services.generator.common.errors import KnowledgeError
    from app.services.generator.common.knowledge import FileKnowledgeSource

    with pytest.raises(KnowledgeError):
        FileKnowledgeSource().fetch("/no/such/file.json")
