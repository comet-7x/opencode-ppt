"""作业生成端点的 schemas（独立于 PPT 管线）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.generator.homework import Difficulty, Question, VerifyResult
from app.schemas.generator.task import TaskDetailBase


class HomeworkGenRequest(BaseModel):
    """给知识点 + 题型配额，生成一套作业（可选 L1/L2 校验+重生成）。"""

    # 知识来源二选一
    knowledge_json: dict[str, Any] | None = None
    knowledge_path: str | None = None
    point_ids: list[str] = Field(..., description="选中的知识点 ID")

    session_title: str = Field(..., description="课题")
    # 各题型 (数量, 每题分)
    mc: tuple[int, int] = Field(default=(3, 4), description="选择题 (数量, 每题分)")
    fill: tuple[int, int] = Field(default=(2, 3), description="填空题 (数量, 每题分)")
    subjective: tuple[int, int] = Field(default=(1, 10), description="主观题 (数量, 每题分)")
    difficulty: Difficulty = "advanced"
    lesson_plan_md: str | None = Field(default=None, description="教案，供对齐重难点（可选）")
    verify: bool = Field(default=True, description="是否走 L1/L2 溯源校验 + 重生成闭环")

    @model_validator(mode="after")
    def _check(self) -> HomeworkGenRequest:
        if not (self.knowledge_json or self.knowledge_path):
            raise ValueError("需提供 knowledge_json 或 knowledge_path")
        if not self.point_ids:
            raise ValueError("需提供 point_ids")
        return self


class HomeworkResult(BaseModel):
    course_title: str
    session_title: str
    total_score: int
    difficulty: str
    questions: list[Question]  # 结构化题目（含答案/解析）
    worksheet_md: str  # 作业卷 markdown（无答案）
    answer_key_md: str  # 参考答案与解析 markdown
    verify_results: list[VerifyResult] | None = None  # 校验结论（verify=True 时）


class HomeworkTaskDetail(TaskDetailBase):
    result: HomeworkResult | None = None  # SUCCEEDED 时有
