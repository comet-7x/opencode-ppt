"""独立教案端点的 schemas。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.generator.task import TaskDetailBase


class LessonPlanGenRequest(BaseModel):
    """给知识点 + 课次参数，生成一份 Markdown 教案。"""

    # 知识来源二选一
    knowledge_json: dict[str, Any] | None = None
    knowledge_path: str | None = None
    point_ids: list[str] = Field(..., description="选中的知识点 ID")

    session_title: str = Field(..., description="课题")
    periods: int = Field(2, description="学时数")
    # 教学模式（英文键，与 models.SessionParams 一致）
    plan_mode: Literal["new_teach", "review_comprehensive", "unit_summary"] = "new_teach"

    @model_validator(mode="after")
    def _check(self) -> LessonPlanGenRequest:
        if not (self.knowledge_json or self.knowledge_path):
            raise ValueError("需提供 knowledge_json 或 knowledge_path")
        if not self.point_ids:
            raise ValueError("需提供 point_ids")
        return self


class LessonPlanResult(BaseModel):
    course_title: str
    session_title: str
    lesson_plan_md: str


class LessonPlanTaskDetail(TaskDetailBase):
    result: LessonPlanResult | None = None  # SUCCEEDED 时有
