"""独立逐页 slides 端点的 schemas。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.generator.slides import SlideContent
from app.schemas.generator.task import TaskDetailBase


class SlidesGenRequest(BaseModel):
    """给一份 Markdown 教案，拆成逐页结构化内容。"""

    lesson_plan_md: str = Field(..., description="Markdown 教案")
    language: str = Field("中文", description="目标语言")
    deck_title: str = Field("", description="组装 source.md 时的整体标题")


class SlidesResult(BaseModel):
    slides: list[SlideContent]
    source_markdown: str  # 组装好的 source.md（一节一页）


class SlidesTaskDetail(TaskDetailBase):
    result: SlidesResult | None = None  # SUCCEEDED 时有
