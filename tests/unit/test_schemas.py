"""API 请求校验器单测：非法入参必须在建任务前就被 pydantic 拦下（422 的来源）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.generator.homework import HomeworkGenRequest
from app.schemas.generator.lesson_plan import LessonPlanGenRequest
from app.schemas.generator.ppt import CreateJobRequest, PptStage
from app.schemas.generator.task import TaskDetailBase, TaskRef, base_detail_fields

_KN = {"course": {"title": "t"}, "points": []}


# ————————————————————— CreateJobRequest（PPT 全管线）————————————————————— #
def test_ppt_full_pipeline_request_ok() -> None:
    req = CreateJobRequest(name="p", knowledge_json=_KN, point_ids=["K001"], session_title="导论")
    assert req.plan_mode == "new_teach"      # 英文键默认
    assert req.language == "zh"
    assert req.confirm_slides is True        # slides 门默认开
    assert req.confirm_lesson_plan is False


def test_ppt_source_markdown_shortcut_skips_knowledge_checks() -> None:
    """直接给 source_markdown 走渲染入口，无需 knowledge/point_ids/session_title。"""
    req = CreateJobRequest(name="p", source_markdown="## 一页\n")
    assert req.source_markdown is not None


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"name": "  "}, "name 不能为空"),
        ({"name": "p"}, "需提供 knowledge"),
        ({"name": "p", "knowledge_json": _KN}, "point_ids"),
        ({"name": "p", "knowledge_json": _KN, "point_ids": ["K001"]}, "session_title"),
    ],
)
def test_ppt_request_rejects_incomplete(kwargs: dict, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        CreateJobRequest(**kwargs)


def test_ppt_request_rejects_chinese_plan_mode() -> None:
    with pytest.raises(ValidationError):
        CreateJobRequest(name="p", knowledge_json=_KN, point_ids=["K001"],
                         session_title="x", plan_mode="新授")  # type: ignore[arg-type]


# ————————————————————— 教案 / 作业 ————————————————————— #
def test_lesson_plan_request_requires_knowledge_and_points() -> None:
    with pytest.raises(ValidationError, match="需提供 knowledge_json 或 knowledge_path"):
        LessonPlanGenRequest(point_ids=["K001"], session_title="x")
    with pytest.raises(ValidationError, match="需提供 point_ids"):
        LessonPlanGenRequest(knowledge_json=_KN, point_ids=[], session_title="x")


def test_lesson_plan_request_accepts_path_only() -> None:
    req = LessonPlanGenRequest(knowledge_path="/k.json", point_ids=["K001"], session_title="x")
    assert req.periods == 2


def test_homework_request_defaults_and_validation() -> None:
    req = HomeworkGenRequest(knowledge_json=_KN, point_ids=["K001"], session_title="x")
    assert req.difficulty == "advanced"   # 英文键
    assert req.mc == (3, 4) and req.fill == (2, 3) and req.subjective == (1, 10)
    assert req.verify is True
    with pytest.raises(ValidationError, match="需提供 knowledge"):
        HomeworkGenRequest(point_ids=["K001"], session_title="x")
    with pytest.raises(ValidationError, match="需提供 point_ids"):
        HomeworkGenRequest(knowledge_json=_KN, point_ids=[], session_title="x")


def test_homework_request_rejects_chinese_difficulty() -> None:
    with pytest.raises(ValidationError):
        HomeworkGenRequest(knowledge_json=_KN, point_ids=["K001"],
                           session_title="x", difficulty="进阶")  # type: ignore[arg-type]


# ————————————————————— 任务 DTO ————————————————————— #
def test_base_detail_fields_maps_task(task_store) -> None:
    task = task_store.create("lesson_plan", request=None, stage=PptStage.LESSON_PLAN)
    fields = base_detail_fields(task)
    assert set(fields) == {
        "task_id", "kind", "status", "stage", "error", "created_at", "updated_at"
    }
    detail = TaskDetailBase(**fields)   # 能直接喂给 DTO
    assert detail.kind == "lesson_plan"
    assert detail.stage == "lesson_plan"


def test_task_ref_serializes_status_as_str(task_store) -> None:
    task = task_store.create("homework")
    ref = TaskRef(task_id=task.task_id, kind=task.kind, status=task.status)
    assert ref.model_dump()["status"] == "pending"
