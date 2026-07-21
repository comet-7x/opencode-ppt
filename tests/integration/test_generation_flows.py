"""全栈集成测试：只把 ChatAgent（模型调用）换掉，其余 api→service→logic→解析→渲染→任务态 真跑。

与 test_api_wiring.py 的区别：那里在**服务层**打桩（跳过业务逻辑），这里打桩边界下移到
**模型调用**，因此 prompt 选择、英文枚举 JSON 解析、分值回填、markdown 渲染、任务态流转
全部被真实覆盖。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.components.task import InMemoryTaskStore
from app.main import app
from tests.conftest import err_msg, unwrap

_KN = {
    "course": {"title": "海洋管理概论", "profile": {"授课对象": "本科生"}, "objectives": []},
    "points": [
        {"id": "K001", "name": "海洋管理的概念", "importance": 5, "difficulty": 3,
         "definition": "对海洋事务的综合管理", "key_points_and_difficulties": "易混淆",
         "detailed_explanation": "演变", "examples": "里约", "related": {"K002": "相关"}},
        {"id": "K002", "name": "海洋资源管理", "importance": 3, "difficulty": 5,
         "definition": "资源分类与配置", "key_points_and_difficulties": "策略易混",
         "detailed_explanation": "差异", "examples": "配额", "related": {"K001": "相关"}},
    ],
}
_BODY = {"knowledge_json": _KN, "point_ids": ["K001", "K002"], "session_title": "导论"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _submit_and_poll(path: str, body: dict, store: InMemoryTaskStore) -> dict[str, Any]:
    """提交 → 等后台跑完 → 取详情。"""
    async with _client() as c:
        task_id = unwrap(await c.post(f"/{path}/create", json=body))["task_id"]
        await store.wait_all()
        r = await c.get(f"/{path}/get/{task_id}")
    return unwrap(r)


# ————————————————————— 教案：全栈 ————————————————————— #
async def test_lesson_plan_flow(task_store: InMemoryTaskStore, fake_agent: Any) -> None:
    fake_agent.reset(default="# 海洋管理概论 教案 · 导论\n## 教学目标\n- 理解概念\n")
    detail = await _submit_and_poll("lesson-plan", _BODY, task_store)

    assert detail["status"] == "succeeded"
    assert detail["result"]["course_title"] == "海洋管理概论"   # 真的从 knowledge 取
    assert detail["result"]["lesson_plan_md"].startswith("# 海洋管理概论 教案")
    # 用的是教案 prompt，且选中的知识点真的进了 user message
    assert "教学设计专家" in fake_agent.seen_system_messages[0]
    assert "K001" in fake_agent.seen_prompts[0]


# ————————————————————— slides：全栈（英文 layout 解析 + source.md 组装）——————————— #
async def test_slides_flow_parses_english_layout_and_assembles_source(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    slides_json = json.dumps({"slides": [
        {"index": 1, "title": "封面", "body": "", "notes": "开场", "layout": "cover"},
        {"index": 2, "title": "概念", "body": "- 定义", "notes": "讲定义", "layout": "content_text"},
        {"index": 3, "title": "结束", "body": "", "notes": "谢谢", "layout": "end"},
    ]}, ensure_ascii=False)
    fake_agent.reset(default=f"```json\n{slides_json}\n```")

    detail = await _submit_and_poll(
        "slides", {"lesson_plan_md": "# 教案", "deck_title": "导论"}, task_store
    )

    assert detail["status"] == "succeeded"
    res = detail["result"]
    assert [s["layout"] for s in res["slides"]] == ["cover", "content_text", "end"]
    # 一节一页：source.md 里 ## 标题数 = 页数
    assert res["source_markdown"].count("## ") == 3
    assert "(layout: content_text)" in res["source_markdown"]
    assert "<!-- notes: 讲定义 -->" in res["source_markdown"]


async def test_slides_index_is_renumbered(task_store: InMemoryTaskStore, fake_agent: Any) -> None:
    """模型给的 index 乱序/跳号时，logic 应重排成连续 1..N。"""
    bad = json.dumps({"slides": [
        {"index": 7, "title": "A", "layout": "cover"},
        {"index": 99, "title": "B", "layout": "end"},
    ]}, ensure_ascii=False)
    fake_agent.reset(default=f"```json\n{bad}\n```")
    detail = await _submit_and_poll("slides", {"lesson_plan_md": "# x"}, task_store)
    assert [s["index"] for s in detail["result"]["slides"]] == [1, 2]


# ————————————————————— 作业：全栈（英文键解析 + 分值回填 + 中文渲染）——————————— #
def _hw_json(n_choice: int = 2) -> str:
    qs = [
        {"type": "choice", "stem": f"选择题{i}", "score": 0, "difficulty": "advanced",
         "source_point_ids": ["K001"],
         "options": [{"label": lab, "text": f"选项{lab}", "is_correct": lab == "A",
                      "analysis": f"{lab}的解析"} for lab in "ABCD"]}
        for i in range(1, n_choice + 1)
    ]
    return "```json\n" + json.dumps({"questions": qs, "total_score": 0}, ensure_ascii=False) + "\n```"


async def test_homework_flow_without_verify(task_store: InMemoryTaskStore, fake_agent: Any) -> None:
    fake_agent.reset(default=_hw_json(2))
    body = {**_BODY, "mc": [2, 4], "fill": [0, 0], "subjective": [0, 0], "verify": False}
    detail = await _submit_and_poll("homework", body, task_store)

    assert detail["status"] == "succeeded"
    res = detail["result"]
    assert [q["type"] for q in res["questions"]] == ["choice", "choice"]  # 英文键
    assert [q["score"] for q in res["questions"]] == [4, 4]               # 程序确定性回填
    assert res["total_score"] == 8
    assert res["difficulty"] == "advanced"                                # 结构化结果保持英文键
    # 给人看的 markdown 转中文标签，且卷子不含答案
    assert "一、选择题" in res["worksheet_md"] and "难度：进阶" in res["worksheet_md"]
    assert "【答案】" not in res["worksheet_md"]
    assert "【答案】A" in res["answer_key_md"]
    assert res["verify_results"] is None


async def test_homework_flow_with_verify_closes_the_loop(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    """1 道题 → 生成 1 次 + 校验 1 次，校验通过则不重生成。"""
    verifier_ok = '```json\n{"independent_answer":"A","grounded":true,' \
                  '"unsupported_claims":[],"notes":"有依据"}\n```'
    fake_agent.reset(replies=[_hw_json(1), verifier_ok])
    body = {**_BODY, "mc": [1, 5], "fill": [0, 0], "subjective": [0, 0], "verify": True}
    detail = await _submit_and_poll("homework", body, task_store)

    assert detail["status"] == "succeeded"
    vr = detail["result"]["verify_results"]
    assert vr is not None and len(vr) == 1
    assert vr[0]["verdict"] == "pass"          # 独立作答 A == 真答案 A 且 grounded
    assert vr[0]["generator_answer"] == "A"
    assert vr[0]["l1_ids"] == ["K001"]
    # 用了命题 prompt + 校验 prompt 两种
    assert any("命题专家" in m for m in fake_agent.seen_system_messages)
    assert any("命题校验专家" in m for m in fake_agent.seen_system_messages)


async def test_homework_verify_detects_answer_conflict(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    """校验独立作答与原答案冲突 → verdict=fail，并触发一次重生成。"""
    verifier_conflict = '```json\n{"independent_answer":"C","grounded":true,' \
                        '"unsupported_claims":[],"notes":"答案应为C"}\n```'
    regenerated = '```json\n{"type":"choice","stem":"改后的题","score":0,"difficulty":"advanced",' \
                  '"source_point_ids":["K001"],"options":[' \
                  '{"label":"A","text":"a","is_correct":false,"analysis":"x"},' \
                  '{"label":"C","text":"c","is_correct":true,"analysis":"对"}]}\n```'
    verifier_ok = '```json\n{"independent_answer":"C","grounded":true,' \
                  '"unsupported_claims":[],"notes":"ok"}\n```'
    fake_agent.reset(replies=[_hw_json(1), verifier_conflict, regenerated, verifier_ok])
    body = {**_BODY, "mc": [1, 5], "fill": [0, 0], "subjective": [0, 0], "verify": True}
    detail = await _submit_and_poll("homework", body, task_store)

    assert detail["status"] == "succeeded"
    assert detail["result"]["questions"][0]["stem"] == "改后的题"   # 已被重生成替换
    assert detail["result"]["verify_results"][0]["verdict"] == "pass"


async def test_homework_verify_warns_when_ungrounded(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    """答案对但有"无依据论断" → verdict=warn，同样触发重生成闭环。"""
    warn = '```json\n{"independent_answer":"A","grounded":false,' \
           '"unsupported_claims":["编了个考试占比"],"notes":"有无依据论断"}\n```'
    regenerated = '```json\n{"type":"choice","stem":"去掉臆造后的题","score":0,' \
                  '"difficulty":"advanced","source_point_ids":["K001"],"options":[' \
                  '{"label":"A","text":"a","is_correct":true,"analysis":"有依据"},' \
                  '{"label":"B","text":"b","is_correct":false,"analysis":"x"}]}\n```'
    ok = '```json\n{"independent_answer":"A","grounded":true,' \
         '"unsupported_claims":[],"notes":"ok"}\n```'
    fake_agent.reset(replies=[_hw_json(1), warn, regenerated, ok])
    body = {**_BODY, "mc": [1, 5], "fill": [0, 0], "subjective": [0, 0], "verify": True}
    detail = await _submit_and_poll("homework", body, task_store)

    assert detail["status"] == "succeeded"
    assert detail["result"]["questions"][0]["stem"] == "去掉臆造后的题"
    assert detail["result"]["verify_results"][0]["verdict"] == "pass"   # 重生成后转 pass


# ————————————————————— 错误路径：真的走到 logic 才抛 ————————————————————— #
@pytest.mark.parametrize("path", ["slides", "homework"])
async def test_unparseable_model_output_lands_as_parse_error(
    task_store: InMemoryTaskStore, fake_agent: Any, path: str
) -> None:
    fake_agent.reset(default="模型今天不想输出 JSON")
    body = {"lesson_plan_md": "# x"} if path == "slides" else {**_BODY, "verify": False}
    detail = await _submit_and_poll(path, body, task_store)

    assert detail["status"] == "failed"
    assert "ParseError" in detail["error"]


async def test_chinese_enum_output_is_rejected(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    """模型若吐中文题型键，必须解析失败——这正是英文枚举约定的保护。"""
    bad = '```json\n{"questions":[{"type":"选择","stem":"x","score":1,' \
          '"difficulty":"进阶","source_point_ids":["K001"]}],"total_score":1}\n```'
    fake_agent.reset(default=bad)
    detail = await _submit_and_poll("homework", {**_BODY, "verify": False}, task_store)
    assert detail["status"] == "failed"
    assert "ParseError" in detail["error"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("lesson-plan", _BODY),
        ("slides", {"lesson_plan_md": "# x"}),
        ("homework", {**_BODY, "verify": False}),
    ],
)
async def test_empty_model_response_lands_as_model_error(
    task_store: InMemoryTaskStore, fake_agent: Any, path: str, body: dict
) -> None:
    """模型返回空 msgs → 三条产线都应抛 ModelError 并落进 task.error。"""
    fake_agent.reset(empty=True)
    detail = await _submit_and_poll(path, body, task_store)
    assert detail["status"] == "failed"
    assert "ModelError" in detail["error"]


@pytest.mark.parametrize("path", ["lesson-plan", "slides", "homework"])
async def test_get_unknown_task_404(path: str, task_store: InMemoryTaskStore) -> None:
    """不存在的 id：HTTP 仍是 200，业务码 404 在信封里。"""
    async with _client() as c:
        r = await c.get(f"/{path}/get/nope")
    assert err_msg(r, 404).endswith("nope 不存在")


@pytest.mark.parametrize("path", ["lesson-plan", "slides", "homework"])
async def test_cross_kind_task_id_is_404(path: str, task_store: InMemoryTaskStore) -> None:
    """用别的 kind 的 id 去查，必须 404，不能串号。"""
    other = task_store.create("ppt")
    async with _client() as c:
        r = await c.get(f"/{path}/get/{other.task_id}")
    err_msg(r, 404)


async def test_bad_knowledge_path_lands_as_knowledge_error(
    task_store: InMemoryTaskStore, fake_agent: Any
) -> None:
    """真的走 FileKnowledgeSource → KnowledgeError → 落 task.error。"""
    body = {"knowledge_path": "/no/such/k.json", "point_ids": ["K001"], "session_title": "x"}
    detail = await _submit_and_poll("lesson-plan", body, task_store)
    assert detail["status"] == "failed"
    assert "KnowledgeError" in detail["error"]
