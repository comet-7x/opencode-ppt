"""PPT 全管线 API schemas（多阶段任务：教案 →(门) slides →(门) 渲染）。

复用通用任务契约 TaskStatus / TaskDetailBase；PPT 特有的是阶段(PptStage)、确认门
(ReviewArtifact / ConfirmRequest) 和产物(pptx)。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.generator import SlideContent
from app.schemas.generator.task import TaskDetailBase


class PptStage(StrEnum):
    LESSON_PLAN = "lesson_plan"  # 生成教案
    SLIDES = "slides"            # 生成逐页内容
    RENDER = "render"            # 渲染 pptx（阶段二）


class ConfirmAction(StrEnum):
    APPROVE = "approve"          # 采纳（可带编辑）→ 进入下一阶段
    REGENERATE = "regenerate"    # 让模型带反馈重生成本阶段


class RuntimeMode(StrEnum):
    DOCKER = "docker"            # container-per-job（阶段二）
    LOCAL = "local"              # 直连宿主机 opencode server


# ————————————————————— 创建 ————————————————————— #
class CreateJobRequest(BaseModel):
    """创建 PPT 全管线任务。知识来源二选一（inline / path），或直接给 source_markdown 跳到渲染。"""

    name: str = Field(..., description="项目名，用于工作目录/容器名（建议英文/数字）")
    language: str = Field("zh", description="PPT 语言")

    # —— 入口物料（三选一，越靠上游越完整）——
    knowledge_json: dict[str, Any] | None = Field(None, description="内联 knowledge.json")
    knowledge_path: str | None = Field(None, description="服务端 knowledge.json 路径")
    point_ids: list[str] = Field(default_factory=list, description="选中的知识点 ID")
    source_markdown: str | None = Field(None, description="直接给成品 source.md → 跳到渲染")

    # —— 教案参数（全管线用）——
    session_title: str = Field("", description="课题")
    periods: int = Field(2, description="学时数")
    # 教学模式（英文键，与 models.SessionParams 一致）
    plan_mode: Literal["new_teach", "review_comprehensive", "unit_summary"] = "new_teach"

    # —— 门控 ——
    confirm_lesson_plan: bool = Field(False, description="教案是否也设确认门")
    confirm_slides: bool = Field(True, description="逐页内容确认门（默认开）")

    # —— 渲染 ——
    runtime: RuntimeMode = RuntimeMode.DOCKER

    @model_validator(mode="after")
    def _check_entry(self) -> CreateJobRequest:
        if not self.name.strip():
            raise ValueError("name 不能为空")
        has_knowledge = bool(self.knowledge_json or self.knowledge_path)
        if self.source_markdown:
            return self  # 直接渲染入口
        if not has_knowledge:
            raise ValueError(
                "需提供 knowledge_json/knowledge_path（全管线）或 source_markdown（直接渲染）"
            )
        if not self.point_ids:
            raise ValueError("全管线需提供 point_ids（选中的知识点）")
        if not self.session_title.strip():
            raise ValueError("全管线需提供 session_title（课题）")
        return self


# ————————————————————— 确认门 ————————————————————— #
class ReviewArtifact(BaseModel):
    stage: PptStage
    content: str | None = None                 # lesson_plan 阶段：整块教案 markdown
    slides: list[SlideContent] | None = None   # slides 阶段：逐页


class ConfirmRequest(BaseModel):
    action: ConfirmAction = ConfirmAction.APPROVE
    edited_content: str | None = None                 # lesson_plan：改后的教案
    edited_slides: list[SlideContent] | None = None   # slides：改后整份
    feedback: str | None = None                       # regenerate：给模型的意见


# ————————————————————— 查询/响应 ————————————————————— #
class PptResult(BaseModel):
    """渲染产物（阶段二）。"""

    pptx_path: str
    pptx_size_kb: int | None = None


class PptTaskDetail(TaskDetailBase):
    review: ReviewArtifact | None = None       # AWAITING_REVIEW 时有
    result: PptResult | None = None            # SUCCEEDED 时有（阶段二）
    source_markdown: str | None = None         # 内容管线产物（已组装的 source.md）
