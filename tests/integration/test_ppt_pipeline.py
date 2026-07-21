"""PPT 全管线全栈集成：只挡 ChatAgent，真跑 教案→slides→确认门→组装 source.md→渲染桩。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from httpx import ASGITransport

from app.components.task import InMemoryTaskStore
from app.main import app

_KN = {
    "course": {"title": "海洋管理概论", "profile": {"授课对象": "本科生"}, "objectives": []},
    "points": [
        {"id": "K001", "name": "海洋管理的概念", "importance": 5, "difficulty": 3,
         "definition": "对海洋事务的综合管理"},
    ],
}
_CREATE = {"name": "proj", "knowledge_json": _KN, "point_ids": ["K001"], "session_title": "导论"}

_LESSON_MD = "# 海洋管理概论 教案 · 导论\n## 教学目标\n- 理解概念\n"
_SLIDES_JSON = "```json\n" + json.dumps({"slides": [
    {"index": 1, "title": "封面", "body": "", "notes": "开场", "layout": "cover"},
    {"index": 2, "title": "概念", "body": "- 定义", "notes": "讲定义", "layout": "content_text"},
]}, ensure_ascii=False) + "\n```"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_full_pipeline_stops_at_slides_gate_then_renders(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    fake_agent.reset(replies=[_LESSON_MD, _SLIDES_JSON])

    async with _client() as c:
        r = await c.post("/ppt/create", json=_CREATE)
        assert r.status_code == 202
        job = r.json()["task_id"]
        await task_store.wait_all()

        # 教案无门自动过 → 停在 slides 确认门，review 里是真解析出来的 slides
        d = (await c.get(f"/ppt/get/{job}")).json()
        assert d["status"] == "awaiting_review" and d["stage"] == "slides"
        assert [s["layout"] for s in d["review"]["slides"]] == ["cover", "content_text"]

        # approve → 真的 assemble_source_md → 渲染桩
        r = await c.post(f"/ppt/confirm/{job}", json={"action": "approve"})
        assert r.status_code == 200
        await task_store.wait_all()

        d = (await c.get(f"/ppt/get/{job}")).json()

    assert d["stage"] == "render" and d["status"] == "failed"
    assert "阶段二" in d["error"]
    assert d["source_markdown"].count("## ") == 2          # 一节一页
    assert "(layout: cover)" in d["source_markdown"]


async def test_lesson_plan_gate_exposes_real_markdown(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    fake_agent.reset(replies=[_LESSON_MD, _SLIDES_JSON])

    async with _client() as c:
        r = await c.post("/ppt/create", json={**_CREATE, "confirm_lesson_plan": True})
        job = r.json()["task_id"]
        await task_store.wait_all()

        d = (await c.get(f"/ppt/get/{job}")).json()
        assert d["stage"] == "lesson_plan" and d["status"] == "awaiting_review"
        assert d["review"]["content"].startswith("# 海洋管理概论 教案")

        # approve → 进 slides 阶段，停在 slides 门
        await c.post(f"/ppt/confirm/{job}", json={"action": "approve"})
        await task_store.wait_all()
        d = (await c.get(f"/ppt/get/{job}")).json()

    assert d["stage"] == "slides" and d["status"] == "awaiting_review"
    assert len(d["review"]["slides"]) == 2


async def test_edited_slides_are_used_for_source_md(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    """人在确认门改稿后，组装 source.md 必须用改后的内容（而不是模型原稿）。"""
    fake_agent.reset(replies=[_LESSON_MD, _SLIDES_JSON])

    async with _client() as c:
        job = (await c.post("/ppt/create", json=_CREATE)).json()["task_id"]
        await task_store.wait_all()

        edited = [{"index": 1, "title": "人工改过的唯一页", "body": "- 手写要点", "layout": "end"}]
        await c.post(f"/ppt/confirm/{job}",
                     json={"action": "approve", "edited_slides": edited})
        await task_store.wait_all()
        d = (await c.get(f"/ppt/get/{job}")).json()

    assert "人工改过的唯一页" in d["source_markdown"]
    assert d["source_markdown"].count("## ") == 1
    assert "概念" not in d["source_markdown"]      # 模型原稿已被替换


async def test_regenerate_calls_model_again(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    second_slides = "```json\n" + json.dumps({"slides": [
        {"index": 1, "title": "重生成后的页", "layout": "cover"},
    ]}, ensure_ascii=False) + "\n```"
    fake_agent.reset(replies=[_LESSON_MD, _SLIDES_JSON, second_slides])

    async with _client() as c:
        job = (await c.post("/ppt/create", json=_CREATE)).json()["task_id"]
        await task_store.wait_all()

        await c.post(f"/ppt/confirm/{job}", json={"action": "regenerate"})
        await task_store.wait_all()
        d = (await c.get(f"/ppt/get/{job}")).json()

    # 仍停在 slides 门，但内容换成了第二次生成的
    assert d["stage"] == "slides" and d["status"] == "awaiting_review"
    assert d["review"]["slides"][0]["title"] == "重生成后的页"


async def test_direct_source_markdown_skips_generation(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    """直接给 source_markdown 应跳到渲染，完全不调模型。"""
    fake_agent.reset()
    async with _client() as c:
        job = (await c.post("/ppt/create", json={
            "name": "p", "source_markdown": "## 一页\n\n- x\n"
        })).json()["task_id"]
        await task_store.wait_all()
        d = (await c.get(f"/ppt/get/{job}")).json()

    assert d["stage"] == "render" and d["status"] == "failed"
    assert d["source_markdown"] == "## 一页\n\n- x\n"
    assert fake_agent.seen_prompts == []          # 一次模型都没调


async def test_model_failure_in_stage_lands_in_task_error(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    fake_agent.reset(default="不是 JSON")   # 教案随便过，slides 解析失败
    async with _client() as c:
        job = (await c.post("/ppt/create", json=_CREATE)).json()["task_id"]
        await task_store.wait_all()
        d = (await c.get(f"/ppt/get/{job}")).json()

    assert d["status"] == "failed"
    assert "ParseError" in d["error"]
    assert d["stage"] == "slides"        # 停在出错的阶段
