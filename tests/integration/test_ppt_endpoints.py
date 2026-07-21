"""PPT 其余端点与边界分支：list/download/delete、非确认门 confirm、缺 source_markdown。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.components.task import InMemoryTaskStore, TaskStatus
from app.main import app
from app.schemas.generator.ppt import PptStage
from app.services.generator.ppt.service import PptService
from tests.conftest import err_msg, unwrap

_KN = {"course": {"title": "海洋管理概论"}, "points": [
    {"id": "K001", "name": "概念", "definition": "d"}]}
_CREATE = {"name": "proj", "knowledge_json": _KN, "point_ids": ["K001"], "session_title": "导论"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_list_filters_to_ppt_kind_only(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    fake_agent.reset(default="不是JSON")   # 让它快速失败即可，本用例只关心列表
    task_store.create("lesson_plan")       # 混入别的 kind，不应出现在 /ppt/list
    async with _client() as c:
        job = unwrap(await c.post("/ppt/create", json=_CREATE))["task_id"]
        await task_store.wait_all()
        rows = unwrap(await c.get("/ppt/list"))
    assert [r["task_id"] for r in rows] == [job]


async def test_download_409_before_artifact_exists(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    fake_agent.reset(default="不是JSON")
    async with _client() as c:
        job = unwrap(await c.post("/ppt/create", json=_CREATE))["task_id"]
        await task_store.wait_all()
        r = await c.get(f"/ppt/download/{job}")
    assert "尚无产物" in err_msg(r, 409)


async def test_download_serves_file_when_result_present(
    task_store: InMemoryTaskStore, tmp_path: Any
) -> None:
    """阶段二产出 pptx 后的下载路径（此处直接构造 result 验证端点逻辑）。"""
    pptx = tmp_path / "out.pptx"
    pptx.write_bytes(b"PK\x03\x04fake")
    from app.schemas.generator.ppt import CreateJobRequest, PptResult

    task = task_store.create(
        "ppt", request=CreateJobRequest(name="proj", source_markdown="## x"),
        stage=PptStage.RENDER,
    )
    task_store.update(task, status=TaskStatus.SUCCEEDED,
                      result=PptResult(pptx_path=str(pptx), pptx_size_kb=1))
    async with _client() as c:
        r = await c.get(f"/ppt/download/{task.task_id}")
    assert r.status_code == 200
    assert r.content == b"PK\x03\x04fake"
    assert "proj.pptx" in r.headers["content-disposition"]


async def test_delete_then_404(task_store: InMemoryTaskStore, fake_agent: Any) -> None:
    fake_agent.reset(default="不是JSON")
    async with _client() as c:
        job = unwrap(await c.post("/ppt/create", json=_CREATE))["task_id"]
        await task_store.wait_all()
        assert unwrap(await c.delete(f"/ppt/delete/{job}")) is None
        err_msg(await c.get(f"/ppt/get/{job}"), 404)
        err_msg(await c.delete(f"/ppt/delete/{job}"), 404)


async def test_confirm_when_not_at_gate_returns_409(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    fake_agent.reset(default="不是JSON")   # slides 解析失败 → FAILED，不在确认门
    async with _client() as c:
        job = unwrap(await c.post("/ppt/create", json=_CREATE))["task_id"]
        await task_store.wait_all()
        r = await c.post(f"/ppt/confirm/{job}", json={"action": "approve"})
    assert "不在确认门" in err_msg(r, 409)


async def test_cross_kind_access_is_404(task_store: InMemoryTaskStore) -> None:
    """拿别的 kind 的 task_id 访问 /ppt/* 必须 404，不能串号。"""
    other = task_store.create("homework")
    async with _client() as c:
        err_msg(await c.get(f"/ppt/get/{other.task_id}"), 404)
        err_msg(await c.delete(f"/ppt/delete/{other.task_id}"), 404)
        err_msg(await c.get(f"/ppt/download/{other.task_id}"), 404)
        r = await c.post(f"/ppt/confirm/{other.task_id}", json={"action": "approve"})
    err_msg(r, 404)


async def test_result_stored_as_dict_is_coerced(task_store: InMemoryTaskStore) -> None:
    """阶段二若把 result 以 dict 落库，详情接口也应能还原成 PptResult。"""
    from app.schemas.generator.ppt import CreateJobRequest

    task = task_store.create("ppt", request=CreateJobRequest(name="p", source_markdown="## x"),
                             stage=PptStage.RENDER)
    task_store.update(task, status=TaskStatus.SUCCEEDED,
                      result={"pptx_path": "/fake/a.pptx", "pptx_size_kb": 12})
    async with _client() as c:
        d = unwrap(await c.get(f"/ppt/get/{task.task_id}"))
    assert d["result"] == {"pptx_path": "/fake/a.pptx", "pptx_size_kb": 12}


async def test_review_is_null_when_awaiting_at_render_stage(
    task_store: InMemoryTaskStore,
) -> None:
    """渲染阶段没有确认门产物，review 应为 null 而不是报错。"""
    from app.schemas.generator.ppt import CreateJobRequest

    task = task_store.create("ppt", request=CreateJobRequest(name="p", source_markdown="## x"),
                             stage=PptStage.RENDER)
    task_store.update(task, status=TaskStatus.AWAITING_REVIEW)
    async with _client() as c:
        d = unwrap(await c.get(f"/ppt/get/{task.task_id}"))
    assert d["review"] is None


async def test_lesson_plan_stage_failure_is_captured(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    """教案阶段自身失败（模型空响应）也要被兜进 task.error，停在 lesson_plan 阶段。"""
    fake_agent.reset(empty=True)
    async with _client() as c:
        job = unwrap(await c.post("/ppt/create", json=_CREATE))["task_id"]
        await task_store.wait_all()
        d = unwrap(await c.get(f"/ppt/get/{job}"))
    assert d["status"] == "failed" and d["stage"] == "lesson_plan"
    assert "ModelError" in d["error"]


# ————————————————————— service 层边界分支 ————————————————————— #
async def test_render_without_source_markdown_fails_clearly(
    task_store: InMemoryTaskStore,
) -> None:
    from app.schemas.generator.ppt import CreateJobRequest

    svc = PptService()
    task = task_store.create(
        "ppt", request=CreateJobRequest(name="p", knowledge_json=_KN,
                                        point_ids=["K001"], session_title="x"),
        stage=PptStage.RENDER,      # 直接进渲染但 context 里没有 source_markdown
    )
    svc.start(task, task_store)
    await task_store.wait_all()
    assert task.status is TaskStatus.FAILED
    assert "缺少 source_markdown" in (task.error or "")


async def test_confirm_at_render_stage_has_no_gate(task_store: InMemoryTaskStore) -> None:
    from app.schemas.generator.ppt import ConfirmRequest, CreateJobRequest

    svc = PptService()
    task = task_store.create(
        "ppt", request=CreateJobRequest(name="p", source_markdown="## x"),
        stage=PptStage.RENDER,
    )
    task_store.update(task, status=TaskStatus.AWAITING_REVIEW)  # 人为造出"渲染阶段却在等确认"
    with pytest.raises(ValueError, match="无确认门"):
        svc.confirm(task, task_store, ConfirmRequest())
